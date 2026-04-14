"""
智瞰龙虎综合分析引擎
整合数据获取、AI分析、结果生成的核心引擎
"""

from longhubang_data import LonghubangDataFetcher
from longhubang_db import LonghubangDatabase
from longhubang_agents import LonghubangAgents
from longhubang_scoring import LonghubangScoring
from longhubang_limit_concept import LimitConceptRotationFetcher
from longhubang_hot_signals import (
    LimitStepHeatFetcher,
    THSHotHeatFetcher,
    KPLListHeatFetcher,
)
from longhubang_p1_signals import AdvancedP1SignalFetcher
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import config
from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout


class LonghubangEngine:
    """龙虎榜综合分析引擎"""
    
    def __init__(self, model=None, db_path='longhubang.db'):
        """
        初始化分析引擎
        
        Args:
            model: AI模型名称
            db_path: 数据库路径
        """
        self.data_fetcher = LonghubangDataFetcher()
        self.database = LonghubangDatabase(db_path)
        self.agents = LonghubangAgents(model=model)
        self.scoring = LonghubangScoring()
        self.limit_concept_fetcher = LimitConceptRotationFetcher()
        self.limit_step_fetcher = LimitStepHeatFetcher()
        self.ths_hot_fetcher = THSHotHeatFetcher()
        self.kpl_list_fetcher = KPLListHeatFetcher()
        self.p1_signal_fetcher = AdvancedP1SignalFetcher()
        # 初始化日志
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')
        self.logger.info("[智瞰龙虎] 分析引擎初始化完成")

    def _safe_fetch_with_timeout(
        self,
        task_name: str,
        func,
        timeout_sec: int = 18,
        default: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        为外部数据抓取增加超时保护，避免某个接口阻塞导致整条分析链卡死。
        超时/异常时返回 default（含 data_success=False），并继续后续流程。
        """
        fallback = default or {"data_success": False, "source": task_name, "error": "timeout_or_error"}
        self.logger.info(f"[阶段3] {task_name} 开始拉取（timeout={timeout_sec}s）")
        started_at = time.time()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(func)
        try:
            data = future.result(timeout=max(int(timeout_sec), 3))
            if isinstance(data, dict):
                elapsed = round(time.time() - started_at, 2)
                self.logger.info(
                    f"[阶段3] {task_name} 拉取完成 | elapsed={elapsed}s | "
                    f"data_success={data.get('data_success', 'N/A')}"
                )
                return data
            elapsed = round(time.time() - started_at, 2)
            self.logger.warning(
                f"[阶段3] {task_name} 返回非dict结果，已降级 | elapsed={elapsed}s"
            )
            return fallback
        except FuturesTimeoutError:
            self.logger.warning(f"[阶段3] {task_name} 超时（>{timeout_sec}s），已降级跳过")
            future.cancel()
            return {**fallback, "error": f"{task_name} timeout"}
        except Exception as e:
            self.logger.warning(f"[阶段3] {task_name} 抓取失败，已降级跳过: {e}")
            return {**fallback, "error": str(e)}
        finally:
            # 关键：超时时不等待后台线程，避免 shutdown(wait=True) 导致主流程仍然卡住
            executor.shutdown(wait=False, cancel_futures=True)
    
    def run_comprehensive_analysis(
        self, date=None, days=3, score_pool_source: str = "lhb_kpl"
    ) -> Dict[str, Any]:
        """
        运行完整的龙虎榜分析流程
        
        Args:
            date: 指定截止日期，格式 YYYY-MM-DD；为空则以当前日期为截止
            days: 分析最近几天的数据，默认3天
            
        Returns:
            完整的分析结果
        """
        self.logger.info("=" * 60)
        self.logger.info("🚀 智瞰龙虎综合分析系统启动")
        self.logger.info("=" * 60)
        
        results = {
            "success": False,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "data_info": {},
            "agents_analysis": {},
            "final_report": {},
            "recommended_stocks": []
        }
        
        try:
            # 阶段1: 获取龙虎榜数据
            self.logger.info("[阶段1] 获取龙虎榜数据...")
            self.logger.info("-" * 60)
            
            # 前端传入交易日时，基础龙虎榜仅取该交易日当天数据
            effective_days = 1 if date else max(int(days or 0), 3)
            if date:
                self.logger.info(f"[阶段1] 使用近{effective_days}个交易日窗口（截止 {date}）")
                data_list = self.data_fetcher.get_recent_days_data(days=effective_days, end_date=date)
            else:
                self.logger.info(f"[阶段1] 使用近{effective_days}个交易日窗口（截止当前）")
                data_list = self.data_fetcher.get_recent_days_data(days=effective_days)

            raw_count = len(data_list)
            data_list = self.data_fetcher.prioritize_latest_stock_records(data_list)
            self.logger.info(
                f"[阶段1] 同股跨天去重完成（保留最近榜单）| raw={raw_count} | dedup={len(data_list)}"
            )

            # 评分池来源仅控制“股票并入来源”，不改变其它分析链路
            source_mode = str(score_pool_source or "lhb_kpl").strip().lower()
            if source_mode not in {"lhb_only", "lhb_limit", "lhb_kpl"}:
                source_mode = "lhb_kpl"
            results["score_pool_source"] = source_mode

            kpl_pool_date = date if date else datetime.now().strftime("%Y-%m-%d")
            empty_limit_pool = {
                "data_success": False,
                "source": "tushare.limit_list_ths",
                "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trade_date": kpl_pool_date,
                "records": [],
                "summary": {},
                "error": "skipped_by_source_mode",
            }
            empty_kpl_pool = {
                "data_success": False,
                "source": "tushare.kpl_list",
                "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trade_date": kpl_pool_date,
                "records": [],
                "summary": {},
                "error": "skipped_by_source_mode",
            }

            results["kpl_list_pool"] = empty_kpl_pool
            results["limit_list_ths_pool"] = empty_limit_pool

            if source_mode == "lhb_limit":
                limit_pool_data = self._fetch_limit_list_ths_all_types(kpl_pool_date)
                results["limit_list_ths_pool"] = limit_pool_data
                if limit_pool_data.get("data_success"):
                    extra_records = limit_pool_data.get("records", []) or []
                    existing_codes = {
                        self._normalize_code(x.get("股票代码") or x.get("gpdm") or "")
                        for x in (data_list or [])
                    }
                    existing_codes.discard("")
                    appended = 0
                    for rec in extra_records:
                        code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
                        if not code or code in existing_codes:
                            continue
                        data_list.append(rec)
                        existing_codes.add(code)
                        appended += 1
                    if appended > 0:
                        self.logger.info(
                            f"[阶段1] 评分池来源=龙虎榜+limit_list_ths，已并入 {appended} 只"
                        )
            elif source_mode == "lhb_kpl":
                kpl_pool_data = self._fetch_kpl_list_all_types(kpl_pool_date)
                results["kpl_list_pool"] = kpl_pool_data
                if kpl_pool_data.get("data_success"):
                    extra_records = kpl_pool_data.get("records", []) or []
                    existing_codes = {
                        self._normalize_code(x.get("股票代码") or x.get("gpdm") or "")
                        for x in (data_list or [])
                    }
                    existing_codes.discard("")
                    appended = 0
                    for rec in extra_records:
                        code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
                        if not code or code in existing_codes:
                            continue
                        data_list.append(rec)
                        existing_codes.add(code)
                        appended += 1
                    if appended > 0:
                        self.logger.info(
                            f"[阶段1] 评分池来源=龙虎榜+kpl_list，已并入 {appended} 只"
                        )
                else:
                    # 仅在该模式下，kpl_list不可用时回退 limit_list_ths
                    limit_pool_data = self._fetch_limit_list_ths_all_types(kpl_pool_date)
                    results["limit_list_ths_pool"] = limit_pool_data
                    if limit_pool_data.get("data_success"):
                        extra_records = limit_pool_data.get("records", []) or []
                        existing_codes = {
                            self._normalize_code(x.get("股票代码") or x.get("gpdm") or "")
                            for x in (data_list or [])
                        }
                        existing_codes.discard("")
                        appended = 0
                        for rec in extra_records:
                            code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
                            if not code or code in existing_codes:
                                continue
                            data_list.append(rec)
                            existing_codes.add(code)
                            appended += 1
                        if appended > 0:
                            self.logger.info(
                                f"[阶段1] kpl_list不可用，已回退并入 limit_list_ths 股票 {appended} 只"
                            )
            
            if not data_list:
                self.logger.error("未获取到龙虎榜数据")
                results["error"] = "未获取到龙虎榜数据"
                return results

            # 按用户交易权限过滤：排除创业板/科创板
            before_filter_count = len(data_list)
            data_list = self._filter_untradable_boards(data_list)
            filtered_count = before_filter_count - len(data_list)
            if filtered_count > 0:
                self.logger.info(
                    f"[阶段1] 已按交易权限过滤创业板/科创板股票 {filtered_count} 条记录"
                )

            self.logger.info(f"成功获取 {len(data_list)} 条龙虎榜记录")
            
            # 阶段2: 保存数据到数据库
            self.logger.info("[阶段2] 保存数据到数据库...")
            self.logger.info("-" * 60)
            saved_count = self.database.save_longhubang_data(data_list)
            self.logger.info(f"保存 {saved_count} 条记录")
            
            # 阶段3: 数据分析和统计
            self.logger.info("[阶段3] 数据分析和统计...")
            self.logger.info("-" * 60)
            summary = self.data_fetcher.analyze_data_summary(data_list)
            formatted_data = self.data_fetcher.format_data_for_ai(data_list, summary)
            
            # 补充：概念主线（优先 Tushare dc_concept_cons，失败回退 kpl_concept_cons）
            stock_codes = self._extract_stock_codes(data_list)
            concept_rotation_data = self._safe_fetch_with_timeout(
                task_name="concept_rotation",
                # 这里放宽为任务级保护，单次请求60s超时由接口调用层控制
                timeout_sec=1800,
                func=lambda: self.limit_concept_fetcher.get_rotation_data(
                    days=max(days, 5),
                    end_date=date,
                    stock_codes=stock_codes,
                ),
                default={
                    "data_success": False,
                    "source": "tushare.dc_concept_cons|kpl_concept_cons",
                    "error": "concept_rotation skipped",
                },
            )
            results["concept_rotation"] = concept_rotation_data
            
            # 补充：连板晋级热度（Tushare limit_step）
            limit_step_data = self._safe_fetch_with_timeout(
                task_name="limit_step",
                timeout_sec=60,
                func=lambda: self.limit_step_fetcher.get_heat_data(
                    days=1 if date else max(days, 5),
                    end_date=date,
                    exact_trade_date_only=bool(date),
                ),
                default={
                    "data_success": False,
                    "source": "tushare.limit_step",
                    "error": "limit_step skipped",
                },
            )
            results["limit_step_heat"] = limit_step_data

            # 补充：同花顺A股热榜（Tushare ths_hot）
            # 前端传入交易日时，仅取该交易日当天数据，用于次日预期分析
            ths_end_date = date if date else datetime.now().strftime("%Y-%m-%d")
            ths_hot_data = self._safe_fetch_with_timeout(
                task_name="ths_hot",
                timeout_sec=60,
                func=lambda: self.ths_hot_fetcher.get_hot_data(
                    days=1 if date else max(min(days, 5), 3),
                    end_date=ths_end_date,
                    exact_trade_date_only=bool(date),
                ),
                default={
                    "data_success": False,
                    "source": "tushare.ths_hot",
                    "error": "ths_hot skipped",
                },
            )
            results["ths_hot_heat"] = ths_hot_data

            # 补充：开盘啦热度（Tushare kpl_list）
            kpl_list_data = self._safe_fetch_with_timeout(
                task_name="kpl_list",
                # 这里放宽为任务级保护，单次请求60s超时由接口调用层控制
                timeout_sec=1800,
                func=lambda: self.kpl_list_fetcher.get_heat_data(
                    days=1 if date else max(min(days, 5), 3),
                    end_date=date,
                    stock_codes=stock_codes,
                    exact_trade_date_only=bool(date),
                ),
                default={
                    "data_success": False,
                    "source": "tushare.kpl_list",
                    "error": "kpl_list skipped",
                },
            )
            results["kpl_list_heat"] = kpl_list_data

            # 概念主线融合：kpl_list 提供板块骨架，limit_list_ths.lu_desc 提供细分题材增强
            concept_trade_date = date if date else datetime.now().strftime("%Y-%m-%d")
            fused_rotation_data = self._build_concept_rotation_fusion(
                kpl_list_data=kpl_list_data,
                end_date=concept_trade_date,
                stock_codes=stock_codes,
                days_hint=1 if date else max(int(days or 0), 1),
            )
            if fused_rotation_data.get("data_success"):
                concept_rotation_data = fused_rotation_data
                results["concept_rotation"] = concept_rotation_data

            # 补充：P1增强信号（top_list/top_inst + moneyflow_* + limit_list_*）
            p1_signal_data = self._safe_fetch_with_timeout(
                task_name="p1_advanced_signals",
                # 该任务含多接口组合，放宽任务级超时；单次请求超时由调用层60s控制
                timeout_sec=1800,
                func=lambda: self.p1_signal_fetcher.get_signal_data(
                    days=1 if date else max(min(days, 5), 3),
                    end_date=date,
                    stock_codes=stock_codes,
                    next_day_focus=bool(date),
                ),
                default={
                    "data_success": False,
                    "source": "tushare.p1_advanced_signals",
                    "error": "p1_advanced_signals skipped",
                },
            )
            results["p1_advanced_signals"] = p1_signal_data

            results["data_info"] = {
                "total_records": summary.get('total_records', 0),
                "total_stocks": summary.get('total_stocks', 0),
                "total_youzi": summary.get('total_youzi', 0),
                "summary": summary
            }
            self.logger.info("数据统计完成")
            
            # 阶段3.5: AI智能评分排名
            self.logger.info("[阶段3.5] AI智能评分排名...")
            self.logger.info("-" * 60)
            scoring_start = time.time()
            scoring_df = self.scoring.score_all_stocks(
                data_list,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                p1_signal_data=p1_signal_data,
            )
            self.logger.info(
                f"[阶段3.5] 评分计算结束 | elapsed={round(time.time() - scoring_start, 2)}s"
            )
            # 转换为可序列化格式以避免UI/存储类型问题
            scoring_ranking_data: List[Dict[str, Any]] = []
            try:
                if scoring_df is not None and hasattr(scoring_df, 'to_dict'):
                    scoring_ranking_data = scoring_df.to_dict('records')
                    self.logger.info(f"完成 {len(scoring_ranking_data)} 只股票的智能评分排名")
                else:
                    self.logger.warning("评分结果为空或格式不支持转换")
            except Exception as e:
                self.logger.exception(f"评分排名数据转换失败: {e}", exc_info=True)
                scoring_ranking_data = []
            results["scoring_ranking"] = scoring_ranking_data
            
            # 阶段4: AI分析师团队分析
            self.logger.info("[阶段4] AI分析师团队工作中...")
            self.logger.info("-" * 60)
            self.logger.info("[阶段4] 即将发起AI模型请求（5位分析师 + 首席策略师）")
            
            agents_results = {}
            
            # 1. 游资行为分析师
            self.logger.info("1/5 游资行为分析师...")
            youzi_result = self.agents.youzi_behavior_analyst(
                formatted_data,
                summary,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
            )
            agents_results["youzi"] = youzi_result
            
            # 2. 个股潜力分析师
            self.logger.info("2/5 个股潜力分析师...")
            stock_result = self.agents.stock_potential_analyst(
                formatted_data,
                summary,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
            )
            agents_results["stock"] = stock_result
            
            # 3. 题材追踪分析师
            self.logger.info("3/5 题材追踪分析师...")
            theme_result = self.agents.theme_tracker_analyst(
                formatted_data,
                summary,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
            )
            agents_results["theme"] = theme_result
            
            # 4. 风险控制专家
            self.logger.info("4/5 风险控制专家...")
            risk_result = self.agents.risk_control_specialist(
                formatted_data,
                summary,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
            )
            agents_results["risk"] = risk_result
            
            # 5. 首席策略师综合
            self.logger.info("5/5 首席策略师综合分析...")
            all_analyses = [youzi_result, stock_result, theme_result, risk_result]
            chief_result = self.agents.chief_strategist(
                all_analyses,
                summary=summary,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
            )
            agents_results["chief"] = chief_result
            
            results["agents_analysis"] = agents_results
            self.logger.info("所有AI分析师分析完成")
            
            # 阶段5: 提取推荐股票
            self.logger.info("[阶段5] 提取推荐股票...")
            self.logger.info("-" * 60)
            recommended_stocks = self._extract_recommended_stocks(
                chief_result.get('analysis', ''),
                stock_result.get('analysis', ''),
                summary
            )
            results["recommended_stocks"] = recommended_stocks
            self.logger.info(f"提取 {len(recommended_stocks)} 只推荐股票")
            
            # 阶段6: 生成最终报告
            self.logger.info("[阶段6] 生成最终报告...")
            self.logger.info("-" * 60)
            final_report = self._generate_final_report(agents_results, summary, recommended_stocks)
            results["final_report"] = final_report
            self.logger.info("最终报告生成完成")
            
            # 阶段7: 保存完整分析报告到数据库
            self.logger.info("[阶段7] 保存完整分析报告...")
            self.logger.info("-" * 60)
            data_date_range = self._get_date_range(data_list)
            
            # 转换评分排名数据为可序列化格式
            # 复用前面转换的评分数据
            # 若前面转换失败，此处不再重复转换，避免错误
            
            # 构建完整的分析内容（结构化）
            full_analysis_content = {
                "agents_analysis": agents_results,
                "data_info": results["data_info"],
                "concept_rotation": concept_rotation_data,
                "limit_step_heat": limit_step_data,
                "ths_hot_heat": ths_hot_data,
                "kpl_list_heat": kpl_list_data,
                "p1_advanced_signals": p1_signal_data,
                "scoring_ranking": scoring_ranking_data,
                "final_report": final_report,
                "timestamp": results["timestamp"]
            }
            
            report_id = self.database.save_analysis_report(
                data_date_range=data_date_range,
                analysis_content=full_analysis_content,  # 保存完整的结构化数据
                recommended_stocks=recommended_stocks,
                summary=final_report.get('summary', ''),
                full_result=results  # 传入完整结果
            )
            results["report_id"] = report_id
            self.logger.info(f"完整报告已保存 (ID: {report_id})")

            # 阶段7.5: 推送首席策略师报告到Webhook（按分层结构）
            self.logger.info("[阶段7.5] 推送首席策略师报告到Webhook...")
            self.logger.info("-" * 60)
            webhook_status = self._send_chief_report_webhook(
                chief_result=chief_result,
                report_id=report_id,
                data_date_range=data_date_range,
                timestamp=results.get("timestamp", ""),
                recommended_stocks=recommended_stocks,
            )
            results["chief_webhook"] = webhook_status
            self.logger.info(
                f"[阶段7.5] Webhook推送结果 | enabled={webhook_status.get('enabled')} | "
                f"sent={webhook_status.get('sent')} | success={webhook_status.get('success')}"
            )
            
            results["success"] = True
            
            self.logger.info("=" * 60)
            self.logger.info("✓ 智瞰龙虎综合分析完成！")
            self.logger.info("=" * 60)
            
        except Exception as e:
            self.logger.exception(f"分析过程出错: {e}", exc_info=True)
            results["error"] = str(e)

        return results

    def _extract_stock_codes(self, data_list: List[Dict[str, Any]]) -> List[str]:
        """从龙虎榜原始记录提取去重后的6位股票代码"""
        out: List[str] = []
        seen = set()
        for item in data_list or []:
            raw = item.get("股票代码") or item.get("gpdm") or ""
            code = self._normalize_code(raw)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def _normalize_code(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        return text if len(text) == 6 and text.isdigit() else ""

    def _is_excluded_board(self, code: str) -> bool:
        """是否属于需排除的板块：创业板/科创板。"""
        if not code or len(code) != 6 or not code.isdigit():
            return False
        return code.startswith(("300", "301", "688"))

    def _filter_untradable_boards(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤不参与后续评分/推荐的股票：
        - 创业板：300/301
        - 科创板：688
        """
        out: List[Dict[str, Any]] = []
        for item in data_list or []:
            code = self._normalize_code(item.get("股票代码") or item.get("gpdm") or "")
            if code and self._is_excluded_board(code):
                continue
            out.append(item)
        return out

    def _to_compact_trade_date(self, value: str) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "").replace(".", "")
        return text if re.fullmatch(r"\d{8}", text) else ""

    def _get_limit_list_ths_types(self) -> List[str]:
        """limit_list_ths 尝试拉取的全类型列表（按常用优先，异常类型会自动跳过）。"""
        return [
            "涨停池",
            "连板池",
            "连扳池",
            "冲刺涨停",
            "炸板池",
            "跌停池",
            "强势股池",
        ]

    def _fetch_limit_list_ths_frames(self, trade_date: str):
        pro = data_source_manager.tushare_api
        if (
            not data_source_manager.tushare_available
            or pro is None
            or not hasattr(pro, "limit_list_ths")
        ):
            return []
        frames = []
        for limit_type in self._get_limit_list_ths_types():
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda lt=limit_type: pro.limit_list_ths(
                        trade_date=trade_date,
                        limit_type=lt,
                    ),
                    timeout_sec=60,
                    api_name="limit_list_ths",
                )
            except Exception:
                df = None
            if df is not None and not df.empty:
                frames.append((limit_type, df.copy()))
        return frames

    def _fetch_limit_list_ths_all_types(self, end_date: str) -> Dict[str, Any]:
        """
        拉取 limit_list_ths 全类型股票，并转换为与龙虎榜记录兼容的补充记录。
        这些股票会与龙虎榜股票合并后共同参与筛选/评分/推荐。
        """
        out = {
            "data_success": False,
            "source": "tushare.limit_list_ths",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": end_date,
            "records": [],
            "summary": {},
        }
        trade_date = self._to_compact_trade_date(end_date)
        if not trade_date:
            out["error"] = "invalid_trade_date"
            return out

        frames = self._fetch_limit_list_ths_frames(trade_date)
        if not frames:
            out["error"] = "no_limit_list_ths_data"
            return out

        merged = {}
        type_counter = Counter()
        for limit_type, df in frames:
            code_col = self._find_df_col(df, ["ts_code", "code", "股票代码", "证券代码"])
            name_col = self._find_df_col(df, ["name", "股票名称", "简称"])
            lu_col = self._find_df_col(df, ["lu_desc", "涨停原因", "原因", "题材"])
            pct_col = self._find_df_col(df, ["pct_chg", "涨跌幅", "change"])
            status_col = self._find_df_col(df, ["status", "连板", "板"])
            if not code_col:
                continue
            for _, row in df.iterrows():
                code = self._normalize_code(row.get(code_col))
                if not code:
                    continue
                name = str(row.get(name_col) or "").strip() if name_col else ""
                lu_desc = str(row.get(lu_col) or "").strip() if lu_col else ""
                pct = self._safe_float_num(row.get(pct_col)) if pct_col else 0.0
                status = str(row.get(status_col) or "").strip() if status_col else ""
                item = merged.setdefault(
                    code,
                    {
                        "code": code,
                        "name": name,
                        "reasons": set(),
                        "limit_types": set(),
                        "pct_chg": pct,
                        "status": status,
                    },
                )
                if name and not item.get("name"):
                    item["name"] = name
                if lu_desc:
                    item["reasons"].add(lu_desc)
                item["limit_types"].add(limit_type)
                if abs(pct) > abs(float(item.get("pct_chg", 0.0) or 0.0)):
                    item["pct_chg"] = pct
                if status and not item.get("status"):
                    item["status"] = status
                type_counter[limit_type] += 1

        records = []
        trade_date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        for code, item in merged.items():
            reasons = "；".join(list(item.get("reasons", set()))[:3])
            if not reasons:
                reasons = "limit_list_ths补充"
            types_join = ",".join(sorted(list(item.get("limit_types", set()))))
            tag = "涨停"
            if "跌停池" in types_join:
                tag = "跌停"
            elif "炸板池" in types_join:
                tag = "炸板"
            records.append(
                {
                    "rq": trade_date_fmt,
                    "gpdm": code,
                    "gpmc": item.get("name", ""),
                    "yzmc": "同花顺涨停池补充",
                    "yyb": f"limit_list_ths/{types_join}" if types_join else "limit_list_ths",
                    "sblx": "同花顺涨停池全类型补充",
                    "mrje": 0.0,
                    "mcje": 0.0,
                    "jlrje": 0.0,
                    "gl": reasons,
                    "data_source": "tushare.limit_list_ths",
                    "pct_chg": round(float(item.get("pct_chg", 0.0) or 0.0), 2),
                    "status": item.get("status", ""),
                    "limit_types": types_join,
                    "tag": tag,
                }
            )
        records.sort(key=lambda x: abs(float(x.get("pct_chg", 0.0) or 0.0)), reverse=True)
        out["data_success"] = bool(records)
        out["records"] = records
        out["summary"] = {
            "trade_date": trade_date_fmt,
            "total_stocks": len(records),
            "type_coverage": dict(type_counter),
        }
        return out

    def _get_kpl_list_tags(self) -> List[str]:
        """kpl_list 尝试拉取的全类型标签。"""
        return ["涨停", "炸板", "跌停", "自然涨停", "竞价"]

    def _fetch_kpl_list_all_types(self, end_date: str) -> Dict[str, Any]:
        """
        拉取 kpl_list 多标签股票，并转换为与龙虎榜记录兼容的补充记录。
        这些股票会与龙虎榜股票合并后共同参与筛选/评分/推荐。
        """
        out = {
            "data_success": False,
            "source": "tushare.kpl_list",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": end_date,
            "records": [],
            "summary": {},
        }
        trade_date = self._to_compact_trade_date(end_date)
        if not trade_date:
            out["error"] = "invalid_trade_date"
            return out

        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "kpl_list"):
            out["error"] = "kpl_list_unavailable"
            return out

        frames = []
        for tag in self._get_kpl_list_tags():
            got = None
            for kwargs in [
                {"trade_date": trade_date, "tag": tag},
                {"trade_date": trade_date},
                {"date": trade_date, "tag": tag},
                {"date": trade_date},
            ]:
                try:
                    df = call_tushare_with_timeout(
                        api_callable=lambda p=kwargs: pro.kpl_list(**p),
                        timeout_sec=60,
                        api_name="kpl_list",
                    )
                except Exception:
                    df = None
                if df is not None and not df.empty:
                    got = df.copy()
                    break
            if got is not None and not got.empty:
                frames.append((tag, got))
        if not frames:
            out["error"] = "no_kpl_list_data"
            return out

        merged = {}
        tag_counter = Counter()
        for tag, df in frames:
            code_col = self._find_df_col(df, ["ts_code", "stock_code", "股票代码", "证券代码", "code"])
            name_col = self._find_df_col(df, ["stock_name", "name", "股票名称", "简称", "gpmc"])
            theme_col = self._find_df_col(df, ["theme", "题材", "概念", "板块", "concept"])
            reason_col = self._find_df_col(df, ["lu_desc", "reason", "解读", "原因", "逻辑", "备注"])
            pct_col = self._find_df_col(df, ["pct", "pct_chg", "涨跌幅", "change", "涨幅"])
            if not code_col:
                continue
            for _, row in df.iterrows():
                code = self._normalize_code(row.get(code_col))
                if not code:
                    continue
                name = str(row.get(name_col) or "").strip() if name_col else ""
                theme = str(row.get(theme_col) or "").strip() if theme_col else ""
                reason = str(row.get(reason_col) or "").strip() if reason_col else ""
                pct = self._safe_float_num(row.get(pct_col)) if pct_col else 0.0
                item = merged.setdefault(
                    code,
                    {
                        "code": code,
                        "name": name,
                        "themes": set(),
                        "reasons": set(),
                        "tags": set(),
                        "pct_chg": pct,
                    },
                )
                if name and not item.get("name"):
                    item["name"] = name
                if theme:
                    item["themes"].add(theme)
                if reason:
                    item["reasons"].add(reason)
                item["tags"].add(tag)
                if abs(pct) > abs(float(item.get("pct_chg", 0.0) or 0.0)):
                    item["pct_chg"] = pct
                tag_counter[tag] += 1

        records = []
        trade_date_fmt = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        for code, item in merged.items():
            themes = "、".join(list(item.get("themes", set()))[:3])
            reasons = "；".join(list(item.get("reasons", set()))[:2])
            gl = themes or reasons or "kpl_list补充"
            tags = ",".join(sorted(list(item.get("tags", set()))))
            records.append(
                {
                    "rq": trade_date_fmt,
                    "gpdm": code,
                    "gpmc": item.get("name", ""),
                    "yzmc": "开盘啦热度补充",
                    "yyb": f"kpl_list/{tags}" if tags else "kpl_list",
                    "sblx": "开盘啦多标签补充",
                    "mrje": 0.0,
                    "mcje": 0.0,
                    "jlrje": 0.0,
                    "gl": gl,
                    "data_source": "tushare.kpl_list",
                    "pct_chg": round(float(item.get("pct_chg", 0.0) or 0.0), 2),
                    "kpl_tags": tags,
                }
            )
        records.sort(key=lambda x: abs(float(x.get("pct_chg", 0.0) or 0.0)), reverse=True)
        out["data_success"] = bool(records)
        out["records"] = records
        out["summary"] = {
            "trade_date": trade_date_fmt,
            "total_stocks": len(records),
            "tag_coverage": dict(tag_counter),
        }
        return out

    def _build_concept_rotation_from_kpl_list(
        self, kpl_list_data: Dict[str, Any], days_hint: int = 1
    ) -> Dict[str, Any]:
        """
        使用 kpl_list 的题材数据构建概念主线结果：
        - 当日题材按“广度+动量+龙头位置+集中度-分化惩罚”评估强度
        - 输出为与 concept_rotation_data 兼容的结构，供评分与前端复用
        """
        base = {
            "data_success": False,
            "source": "tushare.kpl_list",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strongest_today": {},
            "daily_rankings": [],
            "rotation_summary": {},
            "concept_strength_map": {},
            "stock_concept_map": {},
            "concept_field": "kpl_list.theme",
            "concept_source": "kpl_list",
        }
        if not isinstance(kpl_list_data, dict) or not kpl_list_data.get("data_success"):
            return base

        daily_stats = kpl_list_data.get("daily_kpl_stats", []) or []
        stock_kpl_map = kpl_list_data.get("stock_kpl_map", {}) or {}
        if not daily_stats:
            return base

        today = daily_stats[0] if daily_stats else {}
        # 题材强度统计放宽为全市场 kpl_list；个股评分仍只对评分池股票
        today_list = today.get("top_list_all", []) or today.get("top_list", []) or []
        if not today_list:
            return base

        theme_counter: Counter = Counter()
        theme_chg_sum = defaultdict(float)
        theme_chg_cnt = Counter()
        theme_chg_sq_sum = defaultdict(float)
        theme_best_rank = {}
        theme_top20_hits = Counter()
        stock_concept_map: Dict[str, List[str]] = {}

        for row in today_list:
            code = self._normalize_code(row.get("code", ""))
            themes = row.get("themes", []) or []
            change_pct = float(row.get("change_pct", 0.0) or 0.0)
            rank = int(row.get("rank", 9999) or 9999)
            for theme in themes:
                clean = str(theme or "").strip()
                if not clean:
                    continue
                theme_counter[clean] += 1
                theme_chg_sum[clean] += change_pct
                theme_chg_cnt[clean] += 1
                theme_chg_sq_sum[clean] += change_pct * change_pct
                old_best = int(theme_best_rank.get(clean, 9999) or 9999)
                if rank < old_best:
                    theme_best_rank[clean] = rank
                if rank <= 20:
                    theme_top20_hits[clean] += 1
            if code and themes:
                stock_concept_map[code] = [str(x).strip() for x in themes if str(x).strip()]

        # 兜底：用 stock_kpl_map 补齐未进入今日top_list但有题材的目标股
        for code, detail in stock_kpl_map.items():
            code6 = self._normalize_code(code)
            if not code6:
                continue
            themes = detail.get("themes", []) or []
            if code6 not in stock_concept_map and themes:
                stock_concept_map[code6] = [str(x).strip() for x in themes if str(x).strip()]

        if not theme_counter:
            return base

        total_rows = max(len(today_list), 1)
        max_count = max([int(x) for x in theme_counter.values()] + [1])
        max_top20 = max([int(x) for x in theme_top20_hits.values()] + [1])
        max_std = 0.0
        tmp_std_map: Dict[str, float] = {}
        for theme in theme_counter.keys():
            n = max(int(theme_chg_cnt.get(theme, 0) or 0), 1)
            mean = float(theme_chg_sum.get(theme, 0.0) or 0.0) / n
            mean_sq = float(theme_chg_sq_sum.get(theme, 0.0) or 0.0) / n
            var = max(mean_sq - mean * mean, 0.0)
            std = var ** 0.5
            tmp_std_map[theme] = std
            if std > max_std:
                max_std = std

        theme_rows = []
        for theme, cnt in theme_counter.items():
            count = int(cnt)
            avg_pct = float(theme_chg_sum.get(theme, 0.0) or 0.0) / max(int(theme_chg_cnt.get(theme, 0) or 0), 1)
            best_rank = int(theme_best_rank.get(theme, 9999) or 9999)
            top20_hits = int(theme_top20_hits.get(theme, 0) or 0)
            std_pct = float(tmp_std_map.get(theme, 0.0) or 0.0)

            # 1) breadth: 出现广度
            breadth = count / max(total_rows, 1)
            # 2) momentum: 板块内平均涨跌幅（截断后归一化）
            clipped_pct = max(min(avg_pct, 10.0), -5.0)
            momentum_norm = (clipped_pct + 5.0) / 15.0
            # 3) head_strength: 龙头位置（rank越小越强）
            head_strength = max(0.0, 1.0 - (best_rank - 1) / 50.0)
            # 4) concentration: 前20集中度
            concentration = top20_hits / max(max_top20, 1)
            # 5) dispersion_penalty: 分化惩罚（波动越大越扣分）
            dispersion_penalty = 0.0 if max_std <= 0 else std_pct / max_std

            strength_100 = (
                35.0 * breadth
                + 25.0 * momentum_norm
                + 20.0 * head_strength
                + 15.0 * concentration
                - 10.0 * dispersion_penalty
            )
            strength_100 = max(0.0, min(strength_100, 100.0))

            theme_rows.append(
                {
                    "concept": theme,
                    "count": count,
                    "pct_chg": round(avg_pct, 2),
                    "best_rank": best_rank,
                    "top20_hits": top20_hits,
                    "std_pct": round(std_pct, 3),
                    "strength_100": round(strength_100, 2),
                }
            )
        # 强度优先排序；若同分，再看出现次数与涨跌幅
        theme_rows.sort(
            key=lambda x: (x["strength_100"], x["count"], x["pct_chg"]),
            reverse=True,
        )
        top10 = theme_rows[:10]

        max_strength = max([float(x["strength_100"]) for x in top10] + [1.0])
        concept_strength_map: Dict[str, float] = {}
        for item in top10:
            score = min((float(item["strength_100"]) / max_strength) * 4.0, 4.0)
            concept_strength_map[item["concept"]] = round(score, 2)

        strongest = top10[0]
        trade_date = str(today.get("trade_date") or "")
        daily_rankings = [
            {
                "trade_date": trade_date,
                "top_concepts": [
                    {
                        "concept": x["concept"],
                        "score": concept_strength_map.get(x["concept"], 0.0),
                        "count": x["count"],
                        "pct_chg": x["pct_chg"],
                        "best_rank": x["best_rank"],
                        "top20_hits": x["top20_hits"],
                        "strength_100": x["strength_100"],
                    }
                    for x in top10
                ],
                "strongest_concept": strongest["concept"],
                "strongest_count": strongest["count"],
                "strongest_hot_num": strongest["count"],
            }
        ]
        active_concepts = len(theme_counter)
        if strongest["count"] >= 3:
            flow_signal = "主线集中，资金偏抱团"
        elif active_concepts >= 10:
            flow_signal = "题材分散，轮动较快"
        else:
            flow_signal = "结构轮动，主线与支线并行"

        base.update(
            {
                "data_success": True,
                "strongest_today": {
                    "trade_date": trade_date,
                    "concept": strongest["concept"],
                    "limit_up_count": int(strongest["count"]),
                    "stock_count": int(strongest["count"]),
                    "hot_num_total": int(strongest["count"]),
                    "hot_num_avg": round(float(strongest["count"]), 2),
                    "pct_chg": strongest["pct_chg"],
                    "best_rank": strongest["best_rank"],
                    "strength_100": strongest["strength_100"],
                },
                "daily_rankings": daily_rankings,
                "rotation_summary": {
                    "days": int(max(days_hint, 1)),
                    "active_concepts": int(active_concepts),
                    "tracked_stocks": int(len(stock_concept_map)),
                    "requested_stocks": int((kpl_list_data.get("kpl_summary", {}) or {}).get("requested_stocks", 0)),
                    "coverage_ratio": round(
                        int(len(stock_concept_map))
                        / max(int((kpl_list_data.get("kpl_summary", {}) or {}).get("requested_stocks", 0)), 1),
                        2,
                    ),
                    "concept_source": "kpl_list",
                    "leading_concepts": [
                        {
                            "concept": x["concept"],
                            "days_as_top": int(x["count"]),
                            "hot_num_total": float(x["count"]),
                            "pct_chg": x["pct_chg"],
                            "best_rank": x["best_rank"],
                            "strength_100": x["strength_100"],
                        }
                        for x in top10[:5]
                    ],
                    "hot_strength_top5": [
                        f"{x['concept']}({concept_strength_map.get(x['concept'], 0.0)})"
                        for x in top10[:5]
                    ],
                    "capital_flow_signal": flow_signal,
                    "strongest_concept": strongest["concept"],
                    "strongest_count": int(strongest["count"]),
                    "strongest_hot_num": int(strongest["count"]),
                },
                "concept_strength_map": concept_strength_map,
                "stock_concept_map": stock_concept_map,
                "effective_trade_date": trade_date,
                "board_top10": [
                    {
                        "concept": x["concept"],
                        "stock_count": int(x["count"]),
                        "hot_num_total": int(x["count"]),
                        "hot_num_avg": round(float(x["count"]), 2),
                        "pct_chg": x["pct_chg"],
                        "best_rank": x["best_rank"],
                        "strength_100": x["strength_100"],
                    }
                    for x in top10
                ],
            }
        )
        return base

    def _build_concept_rotation_from_limit_list_ths(
        self, end_date: str, stock_codes: List[str]
    ) -> Dict[str, Any]:
        """
        当 kpl_list 当天不可用时，使用 limit_list_ths 兜底构建概念主线：
        - 从 lu_desc 中提取题材关键词
        - 按题材覆盖度、平均涨跌幅、连板强度综合评估
        """
        base = {
            "data_success": False,
            "source": "tushare.limit_list_ths",
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strongest_today": {},
            "daily_rankings": [],
            "rotation_summary": {},
            "concept_strength_map": {},
            "stock_concept_map": {},
            "concept_field": "limit_list_ths.lu_desc",
            "concept_source": "limit_list_ths",
        }
        trade_date = self._to_compact_trade_date(end_date)
        if not trade_date:
            return base

        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "limit_list_ths"):
            return base

        target_codes = {self._normalize_code(x) for x in (stock_codes or []) if self._normalize_code(x)}
        if not target_codes:
            return base

        frames = [df for _, df in self._fetch_limit_list_ths_frames(trade_date)]
        if not frames:
            return base

        import pandas as pd

        df_all = pd.concat(frames, ignore_index=True).drop_duplicates()
        code_col = self._find_df_col(df_all, ["ts_code", "code", "股票代码", "证券代码"])
        lu_col = self._find_df_col(df_all, ["lu_desc", "涨停原因", "原因", "题材"])
        pct_col = self._find_df_col(df_all, ["pct_chg", "涨跌幅", "change"])
        status_col = self._find_df_col(df_all, ["status", "连板", "板"])
        type_col = self._find_df_col(df_all, ["limit_type", "板单类别"])
        if not code_col or not lu_col:
            return base

        theme_counter = Counter()
        theme_pct_sum = defaultdict(float)
        theme_pct_cnt = Counter()
        theme_streak_sum = defaultdict(float)
        theme_streak_cnt = Counter()
        stock_concept_map: Dict[str, List[str]] = {}

        for _, row in df_all.iterrows():
            code = self._normalize_code(row.get(code_col))
            if code not in target_codes:
                continue
            lu_desc = str(row.get(lu_col) or "").strip()
            if not lu_desc:
                continue
            pct = self._safe_float_num(row.get(pct_col)) if pct_col else 0.0
            status_text = str(row.get(status_col) or "").strip() if status_col else ""
            type_text = str(row.get(type_col) or "").strip() if type_col else ""
            streak = self._parse_streak(status_text)
            themes = self._split_lu_desc_themes(lu_desc)
            if not themes:
                continue
            stock_concept_map[code] = themes[:8]
            type_weight = 1.0
            if "连扳" in type_text:
                type_weight = 1.15
            elif "冲刺" in type_text:
                type_weight = 0.85
            elif "炸板" in type_text:
                type_weight = 0.65
            for theme in themes:
                theme_counter[theme] += 1
                theme_pct_sum[theme] += pct * type_weight
                theme_pct_cnt[theme] += 1
                if streak > 0:
                    theme_streak_sum[theme] += streak
                    theme_streak_cnt[theme] += 1

        if not theme_counter:
            return base

        total_stock = max(len(stock_concept_map), 1)
        max_count = max([int(x) for x in theme_counter.values()] + [1])
        max_streak = max(
            [
                (float(theme_streak_sum[t]) / max(int(theme_streak_cnt[t]), 1))
                for t in theme_counter.keys()
            ]
            + [1.0]
        )
        theme_rows = []
        for theme, cnt in theme_counter.items():
            avg_pct = float(theme_pct_sum[theme]) / max(int(theme_pct_cnt[theme]), 1)
            avg_streak = float(theme_streak_sum[theme]) / max(int(theme_streak_cnt[theme]), 1)
            breadth = int(cnt) / max(total_stock, 1)
            momentum = (max(min(avg_pct, 10.0), -5.0) + 5.0) / 15.0
            streak_norm = min(avg_streak / max(max_streak, 1e-6), 1.0)
            count_norm = int(cnt) / max(max_count, 1)
            strength_100 = 40.0 * breadth + 30.0 * momentum + 20.0 * streak_norm + 10.0 * count_norm
            theme_rows.append(
                {
                    "concept": theme,
                    "count": int(cnt),
                    "pct_chg": round(avg_pct, 2),
                    "avg_streak": round(avg_streak, 2),
                    "strength_100": round(max(0.0, min(strength_100, 100.0)), 2),
                }
            )
        theme_rows.sort(key=lambda x: (x["strength_100"], x["count"], x["pct_chg"]), reverse=True)
        top10 = theme_rows[:10]

        max_strength = max([float(x["strength_100"]) for x in top10] + [1.0])
        concept_strength_map = {
            x["concept"]: round(min(float(x["strength_100"]) / max_strength * 4.0, 4.0), 2)
            for x in top10
        }
        strongest = top10[0]
        fmt_trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"

        base.update(
            {
                "data_success": True,
                "strongest_today": {
                    "trade_date": fmt_trade_date,
                    "concept": strongest["concept"],
                    "stock_count": int(strongest["count"]),
                    "hot_num_total": int(strongest["count"]),
                    "hot_num_avg": round(float(strongest["count"]), 2),
                    "pct_chg": strongest["pct_chg"],
                    "strength_100": strongest["strength_100"],
                },
                "daily_rankings": [
                    {
                        "trade_date": fmt_trade_date,
                        "top_concepts": [
                            {
                                "concept": x["concept"],
                                "score": concept_strength_map.get(x["concept"], 0.0),
                                "count": x["count"],
                                "pct_chg": x["pct_chg"],
                                "avg_streak": x["avg_streak"],
                                "strength_100": x["strength_100"],
                            }
                            for x in top10
                        ],
                        "strongest_concept": strongest["concept"],
                        "strongest_count": strongest["count"],
                        "strongest_hot_num": strongest["count"],
                    }
                ],
                "rotation_summary": {
                    "days": 1,
                    "active_concepts": int(len(theme_counter)),
                    "tracked_stocks": int(len(stock_concept_map)),
                    "requested_stocks": int(len(target_codes)),
                    "coverage_ratio": round(len(stock_concept_map) / max(len(target_codes), 1), 2),
                    "concept_source": "limit_list_ths",
                    "leading_concepts": [
                        {
                            "concept": x["concept"],
                            "days_as_top": int(x["count"]),
                            "hot_num_total": float(x["count"]),
                            "pct_chg": x["pct_chg"],
                            "strength_100": x["strength_100"],
                        }
                        for x in top10[:5]
                    ],
                    "hot_strength_top5": [
                        f"{x['concept']}({concept_strength_map.get(x['concept'], 0.0)})"
                        for x in top10[:5]
                    ],
                    "capital_flow_signal": "涨停池主导，次日关注龙头延续",
                    "strongest_concept": strongest["concept"],
                    "strongest_count": int(strongest["count"]),
                    "strongest_hot_num": int(strongest["count"]),
                },
                "concept_strength_map": concept_strength_map,
                "stock_concept_map": stock_concept_map,
                "effective_trade_date": fmt_trade_date,
                "board_top10": [
                    {
                        "concept": x["concept"],
                        "stock_count": int(x["count"]),
                        "hot_num_total": int(x["count"]),
                        "hot_num_avg": round(float(x["count"]), 2),
                        "pct_chg": x["pct_chg"],
                        "avg_streak": x["avg_streak"],
                        "strength_100": x["strength_100"],
                    }
                    for x in top10
                ],
            }
        )
        return base

    def _normalize_theme_alias(self, theme: str) -> str:
        text = str(theme or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[\s\-_+/|,，;；、]+", "", text)
        alias_map = {
            "ai": "人工智能",
            "aigc": "人工智能",
            "chatgpt": "人工智能",
            "算力租赁": "算力",
            "智算": "算力",
            "智能算力": "算力",
            "光模块cpo": "光模块",
            "cpo": "光模块",
            "机器人概念": "机器人",
            "人形机器人": "机器人",
            "自动驾驶": "智能驾驶",
            "无人驾驶": "智能驾驶",
            "低空": "低空经济",
        }
        return alias_map.get(text, text)

    def _concept_match_score(self, concept_a: str, concept_b: str) -> float:
        a = self._normalize_theme_alias(concept_a)
        b = self._normalize_theme_alias(concept_b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
            return 0.75
        return 0.0

    def _build_concept_rotation_fusion(
        self,
        kpl_list_data: Dict[str, Any],
        end_date: str,
        stock_codes: List[str],
        days_hint: int = 1,
    ) -> Dict[str, Any]:
        """
        题材融合模型：
        - kpl_list 提供主板块强度（结构稳定）
        - limit_list_ths.lu_desc 提供细分题材热度（信息更细）
        """
        kpl_rotation = self._build_concept_rotation_from_kpl_list(
            kpl_list_data=kpl_list_data,
            days_hint=days_hint,
        )
        limit_rotation = self._build_concept_rotation_from_limit_list_ths(
            end_date=end_date,
            stock_codes=stock_codes,
        )

        # 任一源可用时直接回传，保证鲁棒性
        if not kpl_rotation.get("data_success") and not limit_rotation.get("data_success"):
            return kpl_rotation if isinstance(kpl_rotation, dict) else limit_rotation
        if not kpl_rotation.get("data_success"):
            return limit_rotation
        if not limit_rotation.get("data_success"):
            return kpl_rotation

        top_concepts = (
            (kpl_rotation.get("daily_rankings", [{}]) or [{}])[0].get("top_concepts", []) or []
        )
        if not top_concepts:
            return kpl_rotation

        kpl_stock_map = kpl_rotation.get("stock_concept_map", {}) or {}
        limit_stock_map = limit_rotation.get("stock_concept_map", {}) or {}
        limit_strength100_map = self._extract_rotation_strength100_map(limit_rotation)

        concept_to_codes = defaultdict(set)
        for code, concepts in kpl_stock_map.items():
            code6 = self._normalize_code(code)
            if not code6:
                continue
            for c in concepts or []:
                concept = str(c or "").strip()
                if concept:
                    concept_to_codes[concept].add(code6)

        fused_rows = []
        for row in top_concepts:
            concept = str(row.get("concept") or "").strip()
            if not concept:
                continue
            kpl_strength = float(row.get("strength_100", 0.0) or 0.0)
            codes = concept_to_codes.get(concept, set())

            hit_codes = 0
            limit_theme_counter = Counter()
            lu_strength_hit_sum = 0.0
            lu_strength_hit_cnt = 0

            for code in codes:
                themes = limit_stock_map.get(code, []) or []
                if not themes:
                    continue
                hit_codes += 1
                for t in themes:
                    clean = str(t or "").strip()
                    if not clean:
                        continue
                    limit_theme_counter[clean] += 1
                    best_match = 0.0
                    for limit_theme, strength100 in limit_strength100_map.items():
                        match = self._concept_match_score(clean, limit_theme)
                        if match <= 0:
                            continue
                        matched_strength = match * (float(strength100) / 100.0)
                        if matched_strength > best_match:
                            best_match = matched_strength
                    if best_match > 0:
                        lu_strength_hit_sum += best_match
                        lu_strength_hit_cnt += 1

            coverage = hit_codes / max(len(codes), 1)
            lu_strength_norm = lu_strength_hit_sum / max(lu_strength_hit_cnt, 1)
            lu_boost_100 = 100.0 * (0.6 * coverage + 0.4 * lu_strength_norm)
            fused_strength = max(0.0, min(100.0, 0.7 * kpl_strength + 0.3 * lu_boost_100))

            top_lu_tags = [x[0] for x in limit_theme_counter.most_common(3)]
            fused_rows.append(
                {
                    **row,
                    "kpl_strength_100": round(kpl_strength, 2),
                    "lu_desc_coverage": round(coverage, 2),
                    "lu_desc_boost_100": round(lu_boost_100, 2),
                    "lu_desc_top3": top_lu_tags,
                    "strength_100": round(fused_strength, 2),
                }
            )

        if not fused_rows:
            return kpl_rotation

        fused_rows.sort(
            key=lambda x: (
                float(x.get("strength_100", 0.0) or 0.0),
                int(x.get("count", 0) or 0),
                float(x.get("pct_chg", 0.0) or 0.0),
            ),
            reverse=True,
        )
        top10 = fused_rows[:10]
        strongest = top10[0]
        max_strength = max([float(x.get("strength_100", 0.0) or 0.0) for x in top10] + [1.0])
        fused_strength_map = {
            str(x.get("concept")): round(
                min(float(x.get("strength_100", 0.0) or 0.0) / max_strength * 4.0, 4.0), 2
            )
            for x in top10
            if str(x.get("concept") or "").strip()
        }

        merged = dict(kpl_rotation)
        merged["source"] = "tushare.kpl_list+limit_list_ths"
        merged["concept_field"] = "kpl_list.theme + limit_list_ths.lu_desc"
        merged["concept_source"] = "kpl_list+limit_list_ths"
        merged["concept_strength_map"] = fused_strength_map

        # 更新 strongest_today / daily_rankings / board_top10，供打分与前端展示
        strongest_today = dict(merged.get("strongest_today", {}) or {})
        strongest_today.update(
            {
                "concept": strongest.get("concept", ""),
                "pct_chg": strongest.get("pct_chg", 0.0),
                "strength_100": strongest.get("strength_100", 0.0),
                "lu_desc_coverage": strongest.get("lu_desc_coverage", 0.0),
                "lu_desc_top3": strongest.get("lu_desc_top3", []),
            }
        )
        merged["strongest_today"] = strongest_today

        daily_rankings = list(merged.get("daily_rankings", []) or [])
        if daily_rankings:
            daily_rankings[0] = {
                **(daily_rankings[0] or {}),
                "top_concepts": [
                    {
                        **x,
                        "score": fused_strength_map.get(str(x.get("concept", "")), 0.0),
                    }
                    for x in top10
                ],
                "strongest_concept": strongest.get("concept", ""),
                "strongest_count": int(strongest.get("count", 0) or 0),
                "strongest_hot_num": int(strongest.get("count", 0) or 0),
            }
            merged["daily_rankings"] = daily_rankings

        merged["board_top10"] = [
            {
                "concept": x.get("concept", ""),
                "stock_count": int(x.get("count", 0) or 0),
                "hot_num_total": int(x.get("count", 0) or 0),
                "hot_num_avg": round(float(x.get("count", 0) or 0), 2),
                "pct_chg": float(x.get("pct_chg", 0.0) or 0.0),
                "best_rank": int(x.get("best_rank", 9999) or 9999),
                "strength_100": float(x.get("strength_100", 0.0) or 0.0),
                "kpl_strength_100": float(x.get("kpl_strength_100", 0.0) or 0.0),
                "lu_desc_coverage": float(x.get("lu_desc_coverage", 0.0) or 0.0),
                "lu_desc_top3": x.get("lu_desc_top3", []),
            }
            for x in top10
        ]

        rotation_summary = dict(merged.get("rotation_summary", {}) or {})
        rotation_summary.update(
            {
                "concept_source": "kpl_list+limit_list_ths",
                "fusion_enabled": True,
                "fusion_weights": {"kpl_strength": 0.7, "lu_desc_enhance": 0.3},
                "leading_concepts": [
                    {
                        "concept": x.get("concept", ""),
                        "days_as_top": int(x.get("count", 0) or 0),
                        "hot_num_total": float(x.get("count", 0) or 0),
                        "pct_chg": float(x.get("pct_chg", 0.0) or 0.0),
                        "strength_100": float(x.get("strength_100", 0.0) or 0.0),
                        "lu_desc_coverage": float(x.get("lu_desc_coverage", 0.0) or 0.0),
                    }
                    for x in top10[:5]
                ],
                "hot_strength_top5": [
                    f"{x.get('concept', '')}({fused_strength_map.get(str(x.get('concept', '')), 0.0)})"
                    for x in top10[:5]
                ],
            }
        )
        merged["rotation_summary"] = rotation_summary
        return merged

    def _find_df_col(self, df, keywords: List[str]) -> str:
        for col in df.columns:
            name = str(col).lower()
            if any(k.lower() in name for k in keywords):
                return col
        return ""

    def _extract_rotation_strength100_map(
        self, concept_rotation_data: Dict[str, Any] = None
    ) -> Dict[str, float]:
        """提取概念主线中的 strength_100（0-100）映射。"""
        if not concept_rotation_data or not concept_rotation_data.get("data_success"):
            return {}
        out: Dict[str, float] = {}
        daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
        if daily_rankings:
            top_concepts = (daily_rankings[0] or {}).get("top_concepts", []) or []
            for item in top_concepts:
                concept = str(item.get("concept", "") or "").strip()
                if not concept:
                    continue
                val = float(item.get("strength_100", 0.0) or 0.0)
                if val > 0:
                    out[concept] = max(out.get(concept, 0.0), val)
        if out:
            return out
        board_top10 = concept_rotation_data.get("board_top10", []) or []
        for item in board_top10:
            concept = str(item.get("concept", "") or "").strip()
            if not concept:
                continue
            val = float(item.get("strength_100", 0.0) or 0.0)
            if val > 0:
                out[concept] = max(out.get(concept, 0.0), val)
        return out

    def _safe_float_num(self, value: Any) -> float:
        if value is None:
            return 0.0
        text = str(value).strip().replace(",", "")
        m = re.search(r"[-+]?\d*\.?\d+", text)
        if not m:
            return 0.0
        try:
            return float(m.group())
        except Exception:
            return 0.0

    def _parse_streak(self, status_text: str) -> int:
        text = str(status_text or "").strip()
        m = re.search(r"(\d+)\s*天\s*(\d+)\s*板", text)
        if m:
            try:
                return int(m.group(2))
            except Exception:
                return 0
        m2 = re.search(r"(\d+)\s*连板", text)
        if m2:
            try:
                return int(m2.group(1))
            except Exception:
                return 0
        if "首板" in text:
            return 1
        return 0

    def _split_lu_desc_themes(self, lu_desc: str) -> List[str]:
        text = str(lu_desc or "").strip()
        if not text:
            return []
        parts = re.split(r"[+＋,，;；/|、\s]+", text)
        noise = {"涨停", "跌停", "概念", "题材", "板块", "原因", "N/A", "None", "nan", "-"}
        out: List[str] = []
        seen = set()
        for part in parts:
            theme = str(part or "").strip()
            if not theme or theme in noise or theme.isdigit() or len(theme) < 2:
                continue
            if theme not in seen:
                seen.add(theme)
                out.append(theme)
        return out[:10]
    
    def _extract_recommended_stocks(self, chief_analysis: str, stock_analysis: str, summary: Dict) -> List[Dict]:
        """
        从AI分析中提取推荐股票
        
        Args:
            chief_analysis: 首席策略师分析
            stock_analysis: 个股潜力分析师分析
            summary: 数据摘要
            
        Returns:
            推荐股票列表
        """
        recommended = []
        
        # 从摘要中获取TOP股票作为基础
        if summary.get('top_stocks'):
            for idx, stock in enumerate(summary['top_stocks'][:10], 1):
                recommended.append({
                    'rank': idx,
                    'code': stock['code'],
                    'name': stock['name'],
                    'net_inflow': stock['net_inflow'],
                    'reason': f"资金净流入 {stock['net_inflow']:,.2f} 元",
                    'confidence': '中',
                    'buy_price': '待定',
                    'target_price': '待定',
                    'stop_loss': '待定',
                    'hold_period': '短线'
                })
        
        return recommended
    
    def _generate_final_report(self, agents_results: Dict, summary: Dict, 
                               recommended_stocks: List[Dict]) -> Dict:
        """
        生成最终报告
        
        Args:
            agents_results: 所有分析师的分析结果
            summary: 数据摘要
            recommended_stocks: 推荐股票列表
            
        Returns:
            最终报告字典
        """
        report = {
            'title': '智瞰龙虎榜综合分析报告',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'summary': '',
            'data_overview': {
                'total_records': summary.get('total_records', 0),
                'total_stocks': summary.get('total_stocks', 0),
                'total_youzi': summary.get('total_youzi', 0),
                'total_net_inflow': summary.get('total_net_inflow', 0)
            },
            'recommended_stocks_count': len(recommended_stocks),
            'agents_count': len(agents_results)
        }
        
        # 生成摘要
        summary_parts = []
        summary_parts.append(f"本次分析共涵盖 {summary.get('total_records', 0)} 条龙虎榜记录")
        summary_parts.append(f"涉及 {summary.get('total_stocks', 0)} 只股票")
        summary_parts.append(f"涉及 {summary.get('total_youzi', 0)} 个游资席位")
        summary_parts.append(f"共推荐 {len(recommended_stocks)} 只潜力股票")
        
        report['summary'] = "，".join(summary_parts) + "。"
        
        return report
    
    def _get_date_range(self, data_list: List[Dict]) -> str:
        """
        获取数据的日期范围
        
        Args:
            data_list: 数据列表
            
        Returns:
            日期范围字符串
        """
        if not data_list:
            return "未知"
        
        dates = []
        for record in data_list:
            date = record.get('rq') or record.get('日期')
            if date:
                dates.append(date)
        
        if not dates:
            return "未知"
        
        dates = sorted(set(dates))
        if len(dates) == 1:
            return dates[0]
        else:
            return f"{dates[0]} 至 {dates[-1]}"

    def _send_chief_report_webhook(
        self,
        chief_result: Dict[str, Any],
        report_id: Any,
        data_date_range: str,
        timestamp: str,
        recommended_stocks: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        发送龙虎榜首席策略师报告到Webhook（失败不影响主流程）。
        """
        status = {
            "enabled": False,
            "sent": False,
            "success": False,
            "error": "",
        }
        try:
            from notification_service import notification_service

            webhook_cfg = notification_service.get_webhook_config_status()
            enabled = bool(webhook_cfg.get("enabled"))
            configured = bool(webhook_cfg.get("configured"))
            status["enabled"] = enabled and configured
            if not status["enabled"]:
                status["error"] = "webhook_not_enabled_or_not_configured"
                self.logger.info("[阶段7.5] Webhook未启用或未配置，跳过推送")
                return status

            status["sent"] = True
            ok = notification_service.send_longhubang_chief_report(
                chief_result=chief_result,
                report_meta={
                    "report_id": report_id,
                    "date_range": data_date_range,
                    "timestamp": timestamp,
                    "recommended_stocks": recommended_stocks or [],
                },
            )
            status["success"] = bool(ok)
            if not ok:
                status["error"] = "send_longhubang_chief_report_failed"
            return status
        except Exception as e:
            status["sent"] = True
            status["success"] = False
            status["error"] = str(e)
            self.logger.warning(f"[阶段7.5] Webhook推送异常: {e}")
            return status
    
    def get_historical_reports(self, limit=10):
        """
        获取历史分析报告
        
        Args:
            limit: 返回数量
            
        Returns:
            报告列表
        """
        return self.database.get_analysis_reports(limit)
    
    def get_report_detail(self, report_id):
        """
        获取报告详情
        
        Args:
            report_id: 报告ID
            
        Returns:
            报告详情
        """
        return self.database.get_analysis_report(report_id)
    
    def get_statistics(self):
        """
        获取数据库统计信息
        
        Returns:
            统计信息
        """
        return self.database.get_statistics()
    
    def get_top_youzi(self, start_date=None, end_date=None, limit=20):
        """
        获取活跃游资排名
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            limit: 返回数量
            
        Returns:
            游资排名
        """
        return self.database.get_top_youzi(start_date, end_date, limit)
    
    def get_top_stocks(self, start_date=None, end_date=None, limit=20):
        """
        获取热门股票排名
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            limit: 返回数量
            
        Returns:
            股票排名
        """
        return self.database.get_top_stocks(start_date, end_date, limit)


# 测试函数
if __name__ == "__main__":
    print("=" * 60)
    print("测试智瞰龙虎分析引擎")
    print("=" * 60)
    
    # 创建引擎实例
    engine = LonghubangEngine()
    
    # 运行综合分析（分析昨天的数据）
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    results = engine.run_comprehensive_analysis(date=yesterday)
    
    if results.get('success'):
        print("\n" + "=" * 60)
        print("分析成功！")
        print("=" * 60)
        print(f"数据记录: {results['data_info']['total_records']}")
        print(f"涉及股票: {results['data_info']['total_stocks']}")
        print(f"推荐股票: {len(results['recommended_stocks'])}")
    else:
        print(f"\n分析失败: {results.get('error', '未知错误')}")
