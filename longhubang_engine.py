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
from longhubang_kline_signals import SevenDayKlineTrendFetcher
from longhubang_history import LonghubangHistoryService
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import re
import time
import logging
import sys
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import config
from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout
from trade_calendar_service import TradeCalendarService, TradeCalendarError


class LonghubangEngine:
    """龙虎榜综合分析引擎"""

    @staticmethod
    def resolve_db_path(db_path: Optional[str] = None) -> str:
        """解析龙虎榜数据库路径：参数优先，其次环境变量，再回退默认值。"""
        candidates = [
            db_path,
            os.getenv("LONGHUBANG_DB_PATH"),
            os.getenv("LONGHUBANG_DB"),
            "longhubang.db",
        ]
        for item in candidates:
            text = str(item or "").strip()
            if text:
                return text
        return "longhubang.db"

    def __init__(self, model=None, db_path='longhubang.db'):
        """
        初始化分析引擎
        
        Args:
            model: AI模型名称
            db_path: 数据库路径
        """
        resolved_db_path = self.resolve_db_path(db_path)
        self.data_fetcher = LonghubangDataFetcher()
        self.database = LonghubangDatabase(resolved_db_path)
        self.agents = LonghubangAgents(model=model)
        self.scoring = LonghubangScoring()
        self.limit_concept_fetcher = LimitConceptRotationFetcher()
        self.limit_step_fetcher = LimitStepHeatFetcher()
        self.ths_hot_fetcher = THSHotHeatFetcher()
        self.kpl_list_fetcher = KPLListHeatFetcher()
        self.p1_signal_fetcher = AdvancedP1SignalFetcher()
        self.kline_trend_fetcher = SevenDayKlineTrendFetcher()
        self.history_service = LonghubangHistoryService(db_path=resolved_db_path)
        self.trade_calendar = TradeCalendarService()
        # 初始化日志
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
            )
            self.logger.addHandler(handler)
        self.logger.propagate = False
        self.logger.info(f"[智瞰龙虎] 分析引擎初始化完成 | db_path={resolved_db_path}")

    def _get_debug_stock_targets(self) -> Dict[str, set]:
        """
        调试跟踪目标股票（用于排查“提供给AI的数据”）：
        - 环境变量 LONGHUBANG_DEBUG_STOCKS，逗号分隔，支持代码/名称
        - 默认跟踪：002565、顺灏股份
        """
        raw = str(os.getenv("LONGHUBANG_DEBUG_STOCKS", "") or "").strip()
        items = [x.strip() for x in raw.split(",") if x.strip()]
        if not items:
            items = ["002565", "顺灏股份"]
        codes = set()
        names = set()
        for item in items:
            code = self._normalize_code(item)
            if code:
                codes.add(code)
            else:
                names.add(str(item).strip())
        return {"codes": codes, "names": names}

    def _is_debug_stock(self, code: str, name: str, targets: Dict[str, set]) -> bool:
        c = self._normalize_code(code)
        n = str(name or "").strip()
        if c and c in (targets.get("codes", set()) or set()):
            return True
        if n and n in (targets.get("names", set()) or set()):
            return True
        return False

    def _log_debug_stock_trace(
        self,
        *,
        data_list_for_ai: List[Dict[str, Any]],
        summary_for_ai: Dict[str, Any],
        recommendation_candidate_stocks: List[Dict[str, Any]],
        p1_signal_data: Dict[str, Any],
        latest_pct_map: Dict[str, float],
        concept_rotation_data: Dict[str, Any],
        mainboard_limit_like_pct: float,
    ) -> None:
        targets = self._get_debug_stock_targets()
        codes = set(targets.get("codes", set()) or set())
        names = set(targets.get("names", set()) or set())
        if not codes and not names:
            return

        self.logger.info(
            f"[调试跟踪] 启用单票跟踪 | codes={sorted(list(codes))} | names={sorted(list(names))}"
        )

        # 从原始AI上下文记录中定位目标股票
        matched_rows = []
        for row in (data_list_for_ai or []):
            code = self._normalize_code(row.get("gpdm") or row.get("股票代码") or "")
            name = str(row.get("gpmc") or row.get("股票名称") or "").strip()
            if self._is_debug_stock(code, name, targets):
                matched_rows.append(row)
                if code:
                    codes.add(code)
                if name:
                    names.add(name)

        if not matched_rows:
            self.logger.warning("[调试跟踪] 目标股票未出现在 data_list_for_ai 中")
            return

        self.logger.info(f"[调试跟踪] data_list_for_ai 命中记录数: {len(matched_rows)}")
        for i, row in enumerate(matched_rows[:12], 1):
            self.logger.info(
                "[调试跟踪] raw_row_%s | code=%s | name=%s | rq=%s | pct_chg=%s | pct_source=%s | "
                "data_source=%s | tag=%s | limit_types=%s | status=%s | gl=%s",
                i,
                self._normalize_code(row.get("gpdm") or row.get("股票代码") or ""),
                str(row.get("gpmc") or row.get("股票名称") or "").strip(),
                str(row.get("rq") or row.get("交易日期") or ""),
                str(row.get("pct_chg", "")),
                str(row.get("pct_source", "")),
                str(row.get("data_source", "")),
                str(row.get("tag", "")),
                str(row.get("limit_types", "")),
                str(row.get("status", "")),
                str(row.get("gl") or row.get("概念") or ""),
            )

        # summary中的个股题材线索
        top_stock_lu_desc = list((summary_for_ai or {}).get("top_stock_lu_desc", []) or [])
        for row in top_stock_lu_desc:
            code = self._normalize_code(row.get("code", ""))
            name = str(row.get("name", "") or "").strip()
            if self._is_debug_stock(code, name, targets):
                self.logger.info(
                    f"[调试跟踪] summary.top_stock_lu_desc | code={code} | name={name} | "
                    f"lu_desc_top={row.get('lu_desc_top', [])} | theme_top={row.get('theme_top', [])}"
                )

        # 题材词池最终映射
        clue_map = self._build_theme_clue_map_from_summary(summary_for_ai or {})
        for code in sorted(list(codes)):
            if code in clue_map:
                self.logger.info(
                    f"[调试跟踪] clue_map | code={code} | tokens={clue_map.get(code, [])}"
                )

        # 候选池中该股票的结构化字段
        for row in (recommendation_candidate_stocks or []):
            code = self._normalize_code(row.get("code", ""))
            name = str(row.get("name", "") or "").strip()
            if self._is_debug_stock(code, name, targets):
                self.logger.info(
                    f"[调试跟踪] candidate_pool | code={code} | name={name} | "
                    f"pct_chg={row.get('pct_chg')} | is_today_limit_up={row.get('is_today_limit_up')} | "
                    f"is_limit_like_for_quota={row.get('is_limit_like_for_quota')} | "
                    f"theme_tokens={row.get('theme_tokens')} | data_quality_grade={row.get('data_quality_grade')}"
                )

        # P1与主线映射
        p1_map = (p1_signal_data or {}).get("stock_signal_map", {}) or {}
        stock_concept_map = (concept_rotation_data or {}).get("stock_concept_map", {}) or {}
        for code in sorted(list(codes)):
            p1 = p1_map.get(code, {}) or {}
            if p1:
                self.logger.info(
                    f"[调试跟踪] p1_signal | code={code} | score={p1.get('score')} | "
                    f"limit_quality_score={p1.get('limit_quality_score')} | "
                    f"today_limit_up_hit={p1.get('today_limit_up_hit')} | top_reason={p1.get('top_reason')}"
                )
            themes = stock_concept_map.get(code, []) or []
            if themes:
                self.logger.info(
                    f"[调试跟踪] concept_rotation.stock_concept_map | code={code} | themes={themes}"
                )
            pct = latest_pct_map.get(code, None)
            is_limit_like = self._is_limit_like_for_quota(
                code=code,
                name="",
                pct_chg=pct,
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )
            self.logger.info(
                f"[调试跟踪] quota_tag | code={code} | latest_pct_chg={pct} | "
                f"is_limit_like_for_quota={is_limit_like}"
            )

    def _log_theme_pool_trace(self, summary_for_ai: Dict[str, Any]) -> None:
        """
        打印题材池全量日志，便于逐条排查：
        - 当前生效题材池：theme_source_priority
        - 全市场备份题材池：theme_source_priority_full_market（若存在）
        """
        summary = summary_for_ai or {}
        effective_rows = list(summary.get("theme_source_priority", []) or [])
        full_rows = list(summary.get("theme_source_priority_full_market", []) or [])

        self.logger.info(f"[题材池跟踪] 生效题材池条数: {len(effective_rows)}")
        for idx, row in enumerate(effective_rows, 1):
            theme = str((row or {}).get("theme", "") or "").strip()
            cnt = int((row or {}).get("count", 0) or 0)
            self.logger.info(f"[题材池跟踪] effective_{idx:03d} | theme={theme} | count={cnt}")

        if full_rows:
            self.logger.info(f"[题材池跟踪] 全市场备份池条数: {len(full_rows)}")
            for idx, row in enumerate(full_rows, 1):
                theme = str((row or {}).get("theme", "") or "").strip()
                cnt = int((row or {}).get("count", 0) or 0)
                self.logger.info(f"[题材池跟踪] full_{idx:03d} | theme={theme} | count={cnt}")

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

    def _calc_9d_trend_adjust(self, detail: Dict[str, Any]) -> float:
        """将9日趋势信号映射为小幅评分修正（限制在[-2, 2]）。"""
        if not isinstance(detail, dict):
            return 0.0
        stage = str(detail.get("trend_stage", "") or "").strip()
        base_map = {
            "加速期": 1.2,
            "启动期": 0.6,
            "高位震荡": -0.2,
            "震荡期": 0.0,
            "退潮期": -1.2,
        }
        adjust = float(base_map.get(stage, 0.0))
        change_9d = float(detail.get("change_7d_pct", 0.0) or 0.0)
        up_streak = int(detail.get("up_streak", 0) or 0)
        drawdown = float(detail.get("drawdown_from_high_pct", 0.0) or 0.0)
        vol_ratio = float(detail.get("vol_ratio", 0.0) or 0.0)

        if up_streak >= 3 and change_9d >= 6.0:
            adjust += 0.4
        if drawdown <= -8.0:
            adjust -= 0.6
        if vol_ratio >= 1.8 and stage in {"高位震荡", "退潮期"}:
            adjust -= 0.4

        return round(max(-2.0, min(2.0, adjust)), 2)

    def _apply_9d_kline_overlay(
        self,
        scoring_df,
        kline_data: Dict[str, Any],
        overlay_scale: float = 1.0,
    ):
        """
        对评分结果做轻量趋势微调：
        - 仅作小幅修正，不改变原评分体系主导关系
        - 修正值限制在[-2, 2]
        """
        if scoring_df is None or getattr(scoring_df, "empty", True):
            return scoring_df
        trend_map = (kline_data or {}).get("stock_trend_map", {}) or {}
        if not trend_map:
            return scoring_df

        df = scoring_df.copy()
        if "趋势微调" not in df.columns:
            df["趋势微调"] = 0.0
        if "9日趋势" not in df.columns:
            df["9日趋势"] = "-"
        if "9日涨幅%" not in df.columns:
            df["9日涨幅%"] = 0.0

        scale = max(0.0, min(float(overlay_scale or 1.0), 10.0))
        for idx, row in df.iterrows():
            code = self._normalize_code(row.get("股票代码") or "")
            detail = trend_map.get(code, {})
            if not detail:
                continue
            adjust = self._calc_9d_trend_adjust(detail) * scale
            adjust = round(max(-10.0, min(10.0, adjust)), 2)
            old_score = float(row.get("综合评分", 0.0) or 0.0)
            df.at[idx, "综合评分"] = round(old_score + adjust, 1)
            df.at[idx, "趋势微调"] = round(adjust, 2)
            df.at[idx, "9日趋势"] = str(detail.get("trend_stage", "") or "-")
            df.at[idx, "9日涨幅%"] = round(float(detail.get("change_7d_pct", 0.0) or 0.0), 2)

        df = df.sort_values("综合评分", ascending=False).reset_index(drop=True)
        df["排名"] = range(1, len(df) + 1)
        df["排名_display"] = df["排名"].astype(str)
        if len(df) >= 1:
            df.loc[0, "排名_display"] = "🥇 1"
        if len(df) >= 2:
            df.loc[1, "排名_display"] = "🥈 2"
        if len(df) >= 3:
            df.loc[2, "排名_display"] = "🥉 3"
        return df

    def _normalize_lu_desc_items(self, text: str) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        parts = re.split(r"[；;|、/]+", raw)
        out: List[str] = []
        seen = set()
        for p in parts:
            item = str(p or "").strip()
            if not item:
                continue
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out[:8]

    def _enrich_summary_with_limit_lu_desc(
        self,
        summary: Dict[str, Any],
        limit_pool_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        使用 limit_list_ths 的 lu_desc（已映射到 records.gl）增强推荐股票题材线索。
        仅补充 AI 推荐上下文，不参与评分权重计算。
        """
        if not isinstance(summary, dict):
            return summary or {}
        top_stocks = list(summary.get("top_stocks", []) or [])
        if not top_stocks:
            return summary
        records = (limit_pool_data or {}).get("records", []) or []
        if not records:
            return summary

        lu_map: Dict[str, List[str]] = {}
        for rec in records:
            code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
            if not code:
                continue
            clues = self._normalize_lu_desc_items(rec.get("gl") or "")
            if not clues:
                continue
            old = lu_map.get(code, [])
            merged = []
            seen = set()
            for x in (old + clues):
                if x and x not in seen:
                    seen.add(x)
                    merged.append(x)
            lu_map[code] = merged[:8]

        old_rows = {
            self._normalize_code(x.get("code", "")): x
            for x in (summary.get("top_stock_lu_desc", []) or [])
            if self._normalize_code(x.get("code", ""))
        }
        merged_rows = []
        for item in top_stocks[:20]:
            code = self._normalize_code(item.get("code", ""))
            if not code:
                continue
            old = old_rows.get(code, {})
            limit_clues = lu_map.get(code, [])
            old_clues = list(old.get("lu_desc_top", []) or [])
            old_themes = list(old.get("theme_top", []) or [])
            # 优先使用 limit_list_ths 的 lu_desc 原始线索，再补旧链路线索
            clue_merged = []
            seen = set()
            for x in (limit_clues + old_clues):
                val = str(x or "").strip()
                if not val or val in seen:
                    continue
                seen.add(val)
                clue_merged.append(val)
            theme_merged = []
            seen_theme = set()
            for x in (limit_clues + old_themes):
                val = str(x or "").strip()
                if not val or val in seen_theme:
                    continue
                seen_theme.add(val)
                theme_merged.append(val)
            merged_rows.append(
                {
                    "code": code,
                    "name": str(item.get("name", "") or ""),
                    "lu_desc_top": clue_merged[:4],
                    "theme_top": theme_merged[:6],
                }
            )

        if merged_rows:
            summary["top_stock_lu_desc"] = merged_rows
        return summary
    
    def run_comprehensive_analysis(
        self,
        date=None,
        days=3,
        score_pool_source: str = "lhb_kpl",
        enable_9d_trend_overlay: bool = True,
        trend_overlay_scale: float = 1.0,
        recommendation_count: int = 8,
        recommendation_limit_up_ratio: float = 0.4,
        enable_recommendation_quota: bool = True,
        mainboard_limit_up_threshold_pct: float = 6.0,
        enable_manual_mainline_override: bool = False,
        manual_mainline_concepts: Optional[List[str]] = None,
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
        analysis_started_at = time.time()
        
        results = {
            "success": False,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "data_info": {},
            "agents_analysis": {},
            "final_report": {},
            "recommended_stocks": []
        }
        rec_count = max(3, min(int(recommendation_count or 8), 12))
        limit_ratio = max(0.0, min(float(recommendation_limit_up_ratio or 0.4), 1.0))
        mainboard_limit_up_threshold = max(
            1.0, min(float(mainboard_limit_up_threshold_pct or 6.0), 10.0)
        )
        results["recommendation_quota_config"] = {
            "enabled": bool(enable_recommendation_quota),
            "recommendation_count": rec_count,
            "limit_up_ratio": round(limit_ratio, 3),
        }
        results["limit_up_threshold_config"] = {
            "mainboard_pct": round(mainboard_limit_up_threshold, 2),
            "chi_next_pct": 19.5,
            "bse_pct": 29.5,
            "st_pct": 4.8,
        }
        target_trade_date = ""
        if date:
            try:
                date8 = self.trade_calendar.normalize_or_raise(date)
                target_trade_date = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
            except TradeCalendarError as e:
                self.logger.warning("[TradeCal] invalid_trade_date | input=%s | nearest=%s", date, e.nearest_open_day)
                results["error"] = str(e)
                results["error_code"] = e.code
                results["nearest_open_day"] = e.nearest_open_day
                return results
        else:
            latest = self.trade_calendar.recent_open_days(datetime.now().strftime("%Y%m%d"), 1)
            if latest:
                d8 = latest[0]
                target_trade_date = f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
            else:
                target_trade_date = datetime.now().strftime("%Y-%m-%d")
        
        try:
            # 阶段1: 获取龙虎榜数据
            stage_started_at = time.time()
            self.logger.info("[阶段1] 获取龙虎榜数据...")
            self.logger.info("-" * 60)
            
            # 前端传入交易日时，基础龙虎榜仅取该交易日当天数据
            effective_days = 1 if date else max(int(days or 0), 3)
            if date:
                self.logger.info(f"[阶段1] 使用近{effective_days}个交易日窗口（截止 {target_trade_date}）")
                data_list = self.data_fetcher.get_recent_days_data(days=effective_days, end_date=target_trade_date)
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

            kpl_pool_date = target_trade_date
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

            def _merge_extra_records_with_pct_backfill(extra_records: List[Dict[str, Any]]) -> Dict[str, int]:
                """
                合并补充来源股票：
                - 新代码：并入 data_list
                - 已有代码：仅当原记录 pct_chg 缺失时，用补充记录 pct_chg 回填
                """
                code_to_idx: Dict[str, int] = {}
                for i, row in enumerate(data_list or []):
                    code = self._normalize_code(row.get("股票代码") or row.get("gpdm") or "")
                    if code and code not in code_to_idx:
                        code_to_idx[code] = i

                appended = 0
                backfilled = 0
                for rec in extra_records or []:
                    code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
                    if not code:
                        continue
                    if code not in code_to_idx:
                        data_list.append(rec)
                        code_to_idx[code] = len(data_list) - 1
                        appended += 1
                        continue

                    idx = code_to_idx.get(code)
                    if idx is None:
                        continue
                    existing_row = data_list[idx] or {}
                    existing_pct = existing_row.get("pct_chg", None)
                    if existing_pct is None or existing_pct == "":
                        incoming_pct = rec.get("pct_chg", None)
                        if incoming_pct is not None and incoming_pct != "":
                            existing_row["pct_chg"] = incoming_pct
                            incoming_src = rec.get("pct_source", None) or rec.get("data_source", None)
                            if incoming_src:
                                existing_row["pct_source"] = incoming_src
                            backfilled += 1
                return {"appended": appended, "backfilled_pct_chg": backfilled}

            if source_mode == "lhb_limit":
                limit_pool_data = self._fetch_limit_list_ths_all_types(kpl_pool_date)
                results["limit_list_ths_pool"] = limit_pool_data
                if limit_pool_data.get("data_success"):
                    extra_records = limit_pool_data.get("records", []) or []
                    merge_stats = _merge_extra_records_with_pct_backfill(extra_records)
                    appended = int(merge_stats.get("appended", 0) or 0)
                    backfilled = int(merge_stats.get("backfilled_pct_chg", 0) or 0)
                    if appended > 0 or backfilled > 0:
                        self.logger.info(
                            f"[阶段1] 评分池来源=龙虎榜+limit_list_ths，已并入 {appended} 只，"
                            f"回填pct_chg {backfilled} 只"
                        )
            elif source_mode == "lhb_kpl":
                kpl_pool_data = self._fetch_kpl_list_all_types(kpl_pool_date)
                results["kpl_list_pool"] = kpl_pool_data
                if kpl_pool_data.get("data_success"):
                    extra_records = kpl_pool_data.get("records", []) or []
                    merge_stats = _merge_extra_records_with_pct_backfill(extra_records)
                    appended = int(merge_stats.get("appended", 0) or 0)
                    backfilled = int(merge_stats.get("backfilled_pct_chg", 0) or 0)
                    if appended > 0 or backfilled > 0:
                        self.logger.info(
                            f"[阶段1] 评分池来源=龙虎榜+kpl_list，已并入 {appended} 只，"
                            f"回填pct_chg {backfilled} 只"
                        )
                else:
                    # 仅在该模式下，kpl_list不可用时回退 limit_list_ths
                    limit_pool_data = self._fetch_limit_list_ths_all_types(kpl_pool_date)
                    results["limit_list_ths_pool"] = limit_pool_data
                    if limit_pool_data.get("data_success"):
                        extra_records = limit_pool_data.get("records", []) or []
                        merge_stats = _merge_extra_records_with_pct_backfill(extra_records)
                        appended = int(merge_stats.get("appended", 0) or 0)
                        backfilled = int(merge_stats.get("backfilled_pct_chg", 0) or 0)
                        if appended > 0 or backfilled > 0:
                            self.logger.info(
                                f"[阶段1] kpl_list不可用，已回退并入 limit_list_ths 股票 {appended} 只，"
                                f"回填pct_chg {backfilled} 只"
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
            self.logger.info(f"[阶段1] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 阶段2: 保存数据到数据库
            stage_started_at = time.time()
            self.logger.info("[阶段2] 保存数据到数据库...")
            self.logger.info("-" * 60)
            saved_count = self.database.save_longhubang_data(data_list)
            self.logger.info(f"保存 {saved_count} 条记录")
            self.logger.info(f"[阶段2] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 先提取跌停池股票代码：用于AI上下文和最终推荐双重剔除
            excluded_drop_limit_codes = self._collect_drop_limit_pool_codes(
                data_list,
                target_trade_date=date,
            )

            # 阶段3: 数据分析和统计
            stage_started_at = time.time()
            self.logger.info("[阶段3] 数据分析和统计...")
            self.logger.info("-" * 60)
            summary = self.data_fetcher.analyze_data_summary(data_list)
            # 历史统计驱动（2026增量入库）
            history_sync_info = self.history_service.sync_2026_history_until(
                end_date=target_trade_date,
                max_days_per_run=8,
            )
            results["history_sync"] = history_sync_info
            self.logger.info(
                "[阶段3] 历史同步 | updated_days=%s | pending_days=%s | latest=%s",
                history_sync_info.get("updated_days", 0),
                history_sync_info.get("pending_days", 0),
                history_sync_info.get("latest_trade_date", ""),
            )

            # AI上下文不包含当日跌停池股票，避免模型文本推荐这类标的
            data_list_for_ai = [
                row for row in (data_list or [])
                if self._normalize_code(row.get("股票代码") or row.get("gpdm") or "") not in excluded_drop_limit_codes
            ]
            if excluded_drop_limit_codes:
                self.logger.info(
                    f"[阶段3] AI上下文剔除跌停池股票 {len(excluded_drop_limit_codes)} 只，"
                    f"上下文记录 {len(data_list)} -> {len(data_list_for_ai)}"
                )
            summary_for_ai = self.data_fetcher.analyze_data_summary(data_list_for_ai)
            # 为AI推荐理由补充 limit_list_ths.lu_desc 线索（不参与评分）
            limit_pool_for_reason = results.get("limit_list_ths_pool", {}) or {}
            if not limit_pool_for_reason.get("data_success"):
                reason_date = target_trade_date
                limit_pool_for_reason = self._safe_fetch_with_timeout(
                    task_name="limit_list_ths_reason",
                    timeout_sec=90,
                    func=lambda: self._fetch_limit_list_ths_all_types(reason_date),
                    default={
                        "data_success": False,
                        "source": "tushare.limit_list_ths",
                        "error": "reason_lu_desc_skipped",
                        "records": [],
                    },
                )
                # 仅用于推荐理由上下文，不覆盖评分池来源结果展示
                results["limit_list_ths_reason"] = limit_pool_for_reason
            summary = self._enrich_summary_with_limit_lu_desc(summary, limit_pool_for_reason)
            summary_for_ai = self._enrich_summary_with_limit_lu_desc(summary_for_ai, limit_pool_for_reason)
            formatted_data_for_ai = self.data_fetcher.format_data_for_ai(data_list_for_ai, summary_for_ai)
            
            # 补充：概念主线（优先 Tushare dc_concept_cons，失败回退 kpl_concept_cons）
            stock_codes = self._extract_stock_codes(data_list)
            concept_rotation_data = self._safe_fetch_with_timeout(
                task_name="concept_rotation",
                # 这里放宽为任务级保护，单次请求60s超时由接口调用层控制
                timeout_sec=1800,
                func=lambda: self.limit_concept_fetcher.get_rotation_data(
                    days=1 if date else max(days, 5),
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
            ths_end_date = target_trade_date
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
            concept_trade_date = target_trade_date
            fused_rotation_data = self._build_concept_rotation_fusion(
                kpl_list_data=kpl_list_data,
                end_date=concept_trade_date,
                stock_codes=stock_codes,
                days_hint=1 if date else max(int(days or 0), 1),
            )
            if fused_rotation_data.get("data_success"):
                concept_rotation_data = fused_rotation_data
                results["concept_rotation"] = concept_rotation_data

            # 输出题材强度候选，并支持“主观主线题材覆盖”（默认关闭）
            concept_candidates = self._extract_concept_strength_candidates(concept_rotation_data)
            cleaned_manual = [
                str(x or "").strip()
                for x in (manual_mainline_concepts or [])
                if str(x or "").strip()
            ][:3]
            use_manual = bool(enable_manual_mainline_override and cleaned_manual)
            if use_manual:
                concept_rotation_data = self._apply_manual_mainline_override(
                    concept_rotation_data=concept_rotation_data,
                    manual_mainline_concepts=cleaned_manual,
                )
                concept_candidates = self._extract_concept_strength_candidates(concept_rotation_data)
            results["concept_rotation"] = concept_rotation_data
            results["concept_strength_candidates"] = concept_candidates
            results["mainline_selection_info"] = {
                "mode": "manual" if use_manual else "auto",
                "manual_selected": cleaned_manual if use_manual else [],
                "effective_concepts": (
                    concept_rotation_data.get("effective_mainline_concepts", []) or []
                )
                if isinstance(concept_rotation_data, dict)
                else [],
            }

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
                    mainboard_limit_up_threshold_pct=mainboard_limit_up_threshold,
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
            self.logger.info(f"[阶段3] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 阶段3.5: AI智能评分排名
            self.logger.info("[阶段3.5] AI智能评分排名...")
            self.logger.info("-" * 60)
            scoring_start = time.time()
            empty_kline_trend = {
                "data_success": False,
                "source": "tushare.daily",
                "error": "kline_9d skipped",
                "window_days": 9,
            }
            kline_9d_data = empty_kline_trend
            if not bool(enable_9d_trend_overlay):
                kline_9d_data = {
                    **empty_kline_trend,
                    "error": "disabled_by_user",
                }
            elif stock_codes:
                # 先拉取评分池股票的9日K线，供“硬性扣分”与趋势微调共同使用
                kline_9d_data = self._safe_fetch_with_timeout(
                    task_name="kline_9d_trend_all",
                    timeout_sec=180,
                    func=lambda: self.kline_trend_fetcher.get_trend_data(
                        days=9,
                        end_date=date,
                        stock_codes=stock_codes,
                    ),
                    default=empty_kline_trend,
                )

            scoring_df = self.scoring.score_all_stocks(
                data_list,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                p1_signal_data=p1_signal_data,
            )
            # 阶段3.6: 候选股票9日K线趋势（轻量微调，不改变主评分框架）
            if bool(enable_9d_trend_overlay) and scoring_df is not None and not getattr(scoring_df, "empty", True):
                candidate_n = min(int(len(scoring_df)), 50)
                candidate_codes = [
                    self._normalize_code(x)
                    for x in scoring_df["股票代码"].head(candidate_n).tolist()
                    if self._normalize_code(x)
                ]
                if candidate_codes and not kline_9d_data.get("data_success"):
                    kline_9d_data = self._safe_fetch_with_timeout(
                        task_name="kline_9d_trend",
                        timeout_sec=180,
                        func=lambda: self.kline_trend_fetcher.get_trend_data(
                            days=9,
                            end_date=date,
                            stock_codes=candidate_codes,
                        ),
                        default=empty_kline_trend,
                    )
                if kline_9d_data.get("data_success"):
                    scoring_df = self._apply_9d_kline_overlay(
                        scoring_df,
                        kline_9d_data,
                        overlay_scale=trend_overlay_scale,
                    )
            results["kline_9d_trend"] = kline_9d_data
            results["kline_9d_overlay_config"] = {
                "enabled": bool(enable_9d_trend_overlay),
                "scale": round(max(0.0, min(float(trend_overlay_scale or 1.0), 10.0)), 2),
            }
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
            source_completeness = {
                "kpl": bool((kpl_list_data or {}).get("data_success", False)),
                "ths": bool((ths_hot_data or {}).get("data_success", False)),
                "limit": bool((results.get("limit_list_ths_pool", {}) or {}).get("data_success", False)),
            }
            latest_pct_map = self._build_latest_pct_change_map(data_list)
            recommendation_candidate_stocks = self._build_recommendation_candidate_stocks(
                scoring_ranking_data=scoring_ranking_data,
                summary=summary,
                latest_pct_map=latest_pct_map,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                summary_for_ai=summary_for_ai,
                source_completeness=source_completeness,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
                max_candidates=80,
            )
            summary_for_ai = self._ensure_theme_clues_for_candidates(
                summary=summary_for_ai,
                data_list=data_list_for_ai,
                candidate_stocks=recommendation_candidate_stocks,
            )
            self._log_theme_pool_trace(summary_for_ai)
            recommendation_candidate_stocks = self._refresh_candidate_theme_tokens(
                candidate_stocks=recommendation_candidate_stocks,
                summary_for_ai=summary_for_ai,
            )
            # 历史统计驱动：同题材低位补涨候选（仅增强解释与排序参考，不改主流程口径）
            active_themes = self._extract_active_themes_for_history_peer(
                concept_rotation_data=concept_rotation_data,
                summary=summary_for_ai,
            )
            peer_ctx = self.history_service.build_peer_rebound_candidates(
                trade_date=target_trade_date,
                candidate_stocks=recommendation_candidate_stocks,
                active_themes=active_themes,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
                top_n=15,
                excluded_codes=excluded_drop_limit_codes,
                max_expand=120,
            )
            recommendation_candidate_stocks = self._merge_history_peer_into_candidates(
                candidate_stocks=recommendation_candidate_stocks,
                peer_context=peer_ctx,
            )
            results["history_peer_rebound"] = peer_ctx
            self._log_debug_stock_trace(
                data_list_for_ai=data_list_for_ai,
                summary_for_ai=summary_for_ai,
                recommendation_candidate_stocks=recommendation_candidate_stocks,
                p1_signal_data=p1_signal_data,
                latest_pct_map=latest_pct_map,
                concept_rotation_data=concept_rotation_data,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
            )
            candidate_pool_mainline = self._build_candidate_pool_mainline(
                concept_rotation_data=concept_rotation_data,
                candidate_stocks=recommendation_candidate_stocks,
            )
            results["mainline_dual_track"] = {
                "full_market": concept_rotation_data,
                "candidate_pool": candidate_pool_mainline,
            }
            results["recommendation_candidate_pool"] = {
                "total": len(recommendation_candidate_stocks),
                "sample": recommendation_candidate_stocks[:20],
                "all": recommendation_candidate_stocks,
                "source": "scoring_ranking+summary_top_stocks",
            }
            
            # 阶段4: AI分析师团队分析
            stage_started_at = time.time()
            self.logger.info("[阶段4] AI分析师团队工作中...")
            self.logger.info("-" * 60)
            self.logger.info("[阶段4] 即将发起AI模型请求（5位分析师 + 首席策略师）")
            
            agents_results = {}
            
            # 1. 游资行为分析师
            self.logger.info("1/5 游资行为分析师...")
            agent_started = time.time()
            youzi_result = self.agents.youzi_behavior_analyst(
                formatted_data_for_ai,
                summary_for_ai,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                trend_overlay_scale=trend_overlay_scale,
                fixed_candidate_stocks=recommendation_candidate_stocks,
            )
            agents_results["youzi"] = youzi_result
            self.logger.info(f"1/5 游资行为分析师完成 | elapsed={round(time.time() - agent_started, 2)}s")
            
            # 2. 个股潜力分析师
            self.logger.info("2/5 个股潜力分析师...")
            agent_started = time.time()
            stock_result = self.agents.stock_potential_analyst(
                formatted_data_for_ai,
                summary_for_ai,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                trend_overlay_scale=trend_overlay_scale,
                fixed_candidate_stocks=recommendation_candidate_stocks,
            )
            agents_results["stock"] = stock_result
            self.logger.info(f"2/5 个股潜力分析师完成 | elapsed={round(time.time() - agent_started, 2)}s")
            
            # 3. 题材追踪分析师
            self.logger.info("3/5 题材追踪分析师...")
            agent_started = time.time()
            theme_result = self.agents.theme_tracker_analyst(
                formatted_data_for_ai,
                summary_for_ai,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                trend_overlay_scale=trend_overlay_scale,
                fixed_candidate_stocks=recommendation_candidate_stocks,
            )
            agents_results["theme"] = theme_result
            self.logger.info(f"3/5 题材追踪分析师完成 | elapsed={round(time.time() - agent_started, 2)}s")
            
            # 4. 风险控制专家
            self.logger.info("4/5 风险控制专家...")
            agent_started = time.time()
            risk_result = self.agents.risk_control_specialist(
                formatted_data_for_ai,
                summary_for_ai,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                trend_overlay_scale=trend_overlay_scale,
                fixed_candidate_stocks=recommendation_candidate_stocks,
            )
            agents_results["risk"] = risk_result
            self.logger.info(f"4/5 风险控制专家完成 | elapsed={round(time.time() - agent_started, 2)}s")
            
            # 5. 首席策略师综合
            self.logger.info("5/5 首席策略师综合分析...")
            agent_started = time.time()
            all_analyses = [youzi_result, stock_result, theme_result, risk_result]
            chief_result = self.agents.chief_strategist(
                all_analyses,
                summary=summary_for_ai,
                concept_rotation_data=concept_rotation_data,
                limit_step_data=limit_step_data,
                ths_hot_data=ths_hot_data,
                kpl_list_data=kpl_list_data,
                p1_signal_data=p1_signal_data,
                kline_9d_data=kline_9d_data,
                trend_overlay_scale=trend_overlay_scale,
                recommendation_count=rec_count,
                recommendation_limit_up_ratio=limit_ratio,
                enable_recommendation_quota=bool(enable_recommendation_quota),
                candidate_quota_context=self._build_chief_candidate_quota_context(
                    summary=summary_for_ai,
                    candidate_stocks=recommendation_candidate_stocks,
                    latest_pct_map=latest_pct_map,
                    p1_signal_data=p1_signal_data,
                    recommendation_count=rec_count,
                    limit_up_ratio=limit_ratio,
                    mainboard_limit_like_pct=mainboard_limit_up_threshold,
                    enable_recommendation_quota=bool(enable_recommendation_quota),
                ),
            )
            agents_results["chief"] = chief_result
            chief_quota_check = self._evaluate_chief_quota_alignment(
                chief_analysis=chief_result.get("analysis", ""),
                summary=summary_for_ai,
                latest_pct_map=latest_pct_map,
                recommendation_count=rec_count,
                limit_up_ratio=limit_ratio,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
                enable_recommendation_quota=bool(enable_recommendation_quota),
            )
            results["chief_quota_check"] = chief_quota_check
            if bool(enable_recommendation_quota) and not bool(chief_quota_check.get("non_limit_target_met", True)):
                self.logger.warning(
                    "[阶段4] 首席推荐未满足非近涨停最少目标，阶段5将按系统推荐表做定向替换回填 | "
                    f"target_min_non_limit={chief_quota_check.get('min_non_limit_like')} | "
                    f"actual_non_limit={chief_quota_check.get('chief_non_limit_like_count')}"
                )
            self.logger.info(f"5/5 首席策略师完成 | elapsed={round(time.time() - agent_started, 2)}s")
            
            results["agents_analysis"] = agents_results
            self.logger.info("所有AI分析师分析完成")
            self.logger.info(f"[阶段4] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 阶段5: 提取推荐股票
            stage_started_at = time.time()
            self.logger.info("[阶段5] 提取推荐股票...")
            self.logger.info("-" * 60)
            results["recommendation_filter_info"] = {
                "exclude_drop_limit_pool": True,
                "excluded_codes_count": len(excluded_drop_limit_codes),
                "excluded_codes_sample": sorted(list(excluded_drop_limit_codes))[:12],
                "latest_pct_coverage": len(latest_pct_map),
                "mainboard_limit_like_pct": round(mainboard_limit_up_threshold, 2),
            }
            recommended_stocks = self._extract_recommended_stocks(
                chief_result.get('analysis', ''),
                stock_result.get('analysis', ''),
                summary,
                candidate_stocks=recommendation_candidate_stocks,
                p1_signal_data=p1_signal_data,
                recommendation_count=rec_count,
                limit_up_ratio=limit_ratio,
                enable_quota=bool(enable_recommendation_quota),
                excluded_codes=excluded_drop_limit_codes,
                latest_pct_map=latest_pct_map,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
            )
            recommended_stocks = self._ensure_ai_trade_advice(
                recommended_stocks=recommended_stocks,
                chief_analysis=chief_result.get("analysis", ""),
            )
            # 若首席表未满足配额：按系统推荐表定向替换缺口数量（而非整表回填）
            quota_patch = self._patch_chief_table_with_recommended_quota(
                chief_analysis=chief_result.get("analysis", ""),
                recommended_stocks=recommended_stocks,
                summary=summary_for_ai,
                latest_pct_map=latest_pct_map,
                recommendation_count=rec_count,
                limit_up_ratio=limit_ratio,
                mainboard_limit_like_pct=mainboard_limit_up_threshold,
                enable_recommendation_quota=bool(enable_recommendation_quota),
            )
            if bool(quota_patch.get("applied", False)):
                chief_result["analysis"] = str(quota_patch.get("patched_analysis", "") or chief_result.get("analysis", ""))
                agents_results["chief"] = chief_result
                chief_quota_check = self._evaluate_chief_quota_alignment(
                    chief_analysis=chief_result.get("analysis", ""),
                    summary=summary_for_ai,
                    latest_pct_map=latest_pct_map,
                    recommendation_count=rec_count,
                    limit_up_ratio=limit_ratio,
                    mainboard_limit_like_pct=mainboard_limit_up_threshold,
                    enable_recommendation_quota=bool(enable_recommendation_quota),
                )
                results["chief_quota_check"] = chief_quota_check
                results["chief_quota_patch"] = {
                    "applied": True,
                    "deficit_non_limit": int(quota_patch.get("deficit_non_limit", 0) or 0),
                    "replaced_count": int(quota_patch.get("replaced_count", 0) or 0),
                    "replaced_from": quota_patch.get("replaced_from", []),
                    "replaced_to": quota_patch.get("replaced_to", []),
                }
                self.logger.info(
                    "[阶段5] 首席推荐表定向替换完成 | "
                    f"deficit_non_limit={quota_patch.get('deficit_non_limit')} | "
                    f"replaced_count={quota_patch.get('replaced_count')}"
                )
            else:
                results["chief_quota_patch"] = {
                    "applied": False,
                    "deficit_non_limit": int(quota_patch.get("deficit_non_limit", 0) or 0),
                    "replaced_count": 0,
                    "replaced_from": [],
                    "replaced_to": [],
                }
            # 仅报告展示：追加“历史统计驱动补涨推荐”模块，不影响首席AI原始选股逻辑
            chief_result["analysis"] = self._append_peer_rebound_report_section(
                chief_analysis=chief_result.get("analysis", ""),
                peer_context=results.get("history_peer_rebound", {}) or {},
            )
            agents_results["chief"] = chief_result
            results["agents_analysis"] = agents_results
            results["recommended_stocks"] = recommended_stocks
            self.logger.info(f"提取 {len(recommended_stocks)} 只推荐股票")
            self.logger.info(f"[阶段5] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 阶段6: 生成最终报告
            stage_started_at = time.time()
            self.logger.info("[阶段6] 生成最终报告...")
            self.logger.info("-" * 60)
            final_report = self._generate_final_report(agents_results, summary, recommended_stocks)
            results["final_report"] = final_report
            self.logger.info("最终报告生成完成")
            self.logger.info(f"[阶段6] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            # 阶段7: 保存完整分析报告到数据库
            stage_started_at = time.time()
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
                "kline_9d_trend": kline_9d_data,
                "p1_advanced_signals": p1_signal_data,
                "history_sync": results.get("history_sync", {}),
                "history_peer_rebound": results.get("history_peer_rebound", {}),
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
            self.logger.info(f"[阶段7] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")

            # 阶段7.5: 推送首席策略师报告到Webhook（按分层结构）
            stage_started_at = time.time()
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
            self.logger.info(f"[阶段7.5] 完成 | elapsed={round(time.time() - stage_started_at, 2)}s")
            
            results["success"] = True
            
            self.logger.info("=" * 60)
            self.logger.info("✓ 智瞰龙虎综合分析完成！")
            self.logger.info("=" * 60)
            self.logger.info(f"[全流程] 总耗时={round(time.time() - analysis_started_at, 2)}s")
            
        except Exception as e:
            self.logger.exception(f"分析过程出错: {e}", exc_info=True)
            results["error"] = str(e)

        return results

    def backfill_2026_history(
        self,
        end_date: Optional[str] = None,
        batch_days: int = 20,
        max_rounds: int = 40,
    ) -> Dict[str, Any]:
        """
        手动触发：立即补齐 2026 历史数据（循环增量，直到无待补或达到轮次上限）。
        """
        target_input = end_date if end_date else datetime.now().strftime("%Y-%m-%d")
        try:
            target8 = self.trade_calendar.normalize_or_raise(target_input)
            target_date = f"{target8[:4]}-{target8[4:6]}-{target8[6:8]}"
        except TradeCalendarError as e:
            self.logger.warning(
                "[TradeCal] invalid_trade_date | backfill_target=%s | nearest=%s",
                target_input,
                e.nearest_open_day,
            )
            return {
                "data_success": False,
                "error": str(e),
                "error_code": e.code,
                "nearest_open_day": e.nearest_open_day,
            }
        rounds = max(int(max_rounds or 40), 1)
        per_round = max(int(batch_days or 20), 1)

        total_updated_days = 0
        total_updated_stocks = 0
        pending_days = 0
        last_pending = None
        latest_trade_date = ""
        failed_days: List[str] = []
        started = time.time()
        self.logger.info(
            "[历史驱动] 手动补齐启动 | target=%s | batch_days=%s | max_rounds=%s",
            target_date,
            per_round,
            rounds,
        )

        for i in range(1, rounds + 1):
            one = self.history_service.sync_2026_history_until(
                end_date=target_date,
                max_days_per_run=per_round,
            )
            if not one.get("data_success", False):
                return {
                    "data_success": False,
                    "error": str(one.get("error", "history_sync_failed")),
                    "rounds": i,
                }

            upd_days = int(one.get("updated_days", 0) or 0)
            upd_stocks = int(one.get("updated_stocks", 0) or 0)
            pending_days = int(one.get("pending_days", 0) or 0)
            latest_trade_date = str(one.get("latest_trade_date", "") or latest_trade_date)
            failed_days.extend(list(one.get("failed_days", []) or []))
            total_updated_days += upd_days
            total_updated_stocks += upd_stocks

            self.logger.info(
                "[历史驱动] 手动补齐轮次 %s/%s | updated_days=%s | pending=%s | latest=%s",
                i,
                rounds,
                upd_days,
                pending_days,
                latest_trade_date,
            )
            if pending_days <= 0:
                break
            # 无进展则停止，避免空转
            if upd_days <= 0 and last_pending is not None and pending_days >= last_pending:
                self.logger.warning(
                    "[历史驱动] 手动补齐提前停止（无进展）| round=%s | pending=%s",
                    i,
                    pending_days,
                )
                break
            last_pending = pending_days

        elapsed = round(time.time() - started, 2)
        return {
            "data_success": True,
            "target_date": target_date,
            "updated_days": int(total_updated_days),
            "updated_stocks": int(total_updated_stocks),
            "pending_days": int(pending_days),
            "latest_trade_date": latest_trade_date,
            "failed_days": failed_days[:50],
            "elapsed_sec": elapsed,
        }

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

    def _is_mainboard_code(self, code: str) -> bool:
        """
        是否为沪深主板代码。
        当前按代码段白名单：
        - 上交所主板：600/601/603/605
        - 深交所主板：000/001/002/003
        """
        if not code or len(code) != 6 or not code.isdigit():
            return False
        return code.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))

    def _is_excluded_board(self, code: str) -> bool:
        """是否属于需排除的板块：非沪深主板（创业板/科创板/北交所等）。"""
        if not code or len(code) != 6 or not code.isdigit():
            return False
        return not self._is_mainboard_code(code)

    def _filter_untradable_boards(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤不参与后续评分/推荐的股票：
        - 仅保留沪深主板（000/001/002/003/600/601/603/605）
        - 排除创业板/科创板/北交所及其他非主板代码
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
        """limit_list_ths 固定拉取类型（按用户指定口径：当天5类）。"""
        return [
            "涨停池",
            "连扳池",
            "冲刺涨停",
            "炸板池",
            "跌停池",
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
            started = time.time()
            self.logger.info(
                f"[limit_list_ths] 开始请求 | trade_date={trade_date} | limit_type={limit_type}"
            )
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
            elapsed = round(time.time() - started, 2)
            if df is not None and not df.empty:
                self.logger.info(
                    f"[limit_list_ths] 请求成功 | limit_type={limit_type} | rows={len(df)} | elapsed={elapsed}s"
                )
                frames.append((limit_type, df.copy()))
            else:
                self.logger.info(
                    f"[limit_list_ths] 请求无数据 | limit_type={limit_type} | elapsed={elapsed}s"
                )
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
                    "pct_source": "tushare.limit_list_ths",
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
        return ["涨停", "自然涨停", "炸板", "跌停"]

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
            status_col = self._find_df_col(df, ["status", "连板", "板"])
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
                status = str(row.get(status_col) or "").strip() if status_col else ""
                pct = self._safe_float_num(row.get(pct_col)) if pct_col else 0.0
                item = merged.setdefault(
                    code,
                    {
                        "code": code,
                        "name": name,
                        "themes": set(),
                        "reasons": set(),
                        "tags": set(),
                        "status": status,
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
                if status:
                    old_status = str(item.get("status", "") or "").strip()
                    if (not old_status) or (self._parse_streak(status) > self._parse_streak(old_status)):
                        item["status"] = status
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
                    "pct_source": "tushare.kpl_list",
                    "status": item.get("status", ""),
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
                if not clean or self._is_st_theme(clean):
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
                stock_concept_map[code] = [
                    str(x).strip()
                    for x in themes
                    if str(x).strip() and not self._is_st_theme(str(x).strip())
                ]

        # 兜底：用 stock_kpl_map 补齐未进入今日top_list但有题材的目标股
        for code, detail in stock_kpl_map.items():
            code6 = self._normalize_code(code)
            if not code6:
                continue
            themes = detail.get("themes", []) or []
            if code6 not in stock_concept_map and themes:
                stock_concept_map[code6] = [
                    str(x).strip()
                    for x in themes
                    if str(x).strip() and not self._is_st_theme(str(x).strip())
                ]

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

    def _build_candidate_pool_mainline(
        self,
        concept_rotation_data: Dict[str, Any],
        candidate_stocks: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        基于“全市场主线”的 stock_concept_map，提取候选池口径主线。
        用于前端展示双轨主线，不替换评分主线。
        """
        base = {
            "data_success": False,
            "source": "candidate_pool.mainline",
            "concept_source": "candidate_pool",
            "daily_rankings": [],
            "strongest_today": {},
            "concept_strength_map": {},
            "rotation_summary": {},
            "board_top10": [],
        }
        if not isinstance(concept_rotation_data, dict) or not concept_rotation_data.get("data_success"):
            base["error"] = "full_market_mainline_unavailable"
            return base
        candidate_codes = {
            self._normalize_code(x.get("code", ""))
            for x in (candidate_stocks or [])
            if self._normalize_code(x.get("code", ""))
        }
        if not candidate_codes:
            base["error"] = "candidate_pool_empty"
            return base

        stock_concept_map = (concept_rotation_data.get("stock_concept_map", {}) or {})
        full_strength_map = (concept_rotation_data.get("concept_strength_map", {}) or {})
        concept_counter: Counter = Counter()
        strength_acc = defaultdict(float)
        matched_stock_cnt = 0
        for code in candidate_codes:
            themes = list(stock_concept_map.get(code, []) or [])
            if not themes:
                continue
            matched_stock_cnt += 1
            uniq_themes = []
            seen = set()
            for t in themes:
                theme = str(t or "").strip()
                if not theme or theme in seen:
                    continue
                seen.add(theme)
                uniq_themes.append(theme)
            for theme in uniq_themes:
                concept_counter[theme] += 1
                strength_acc[theme] += float(full_strength_map.get(theme, 0.0) or 0.0)

        if not concept_counter:
            base["error"] = "candidate_pool_theme_empty"
            return base

        rows = []
        max_count = max([int(v) for v in concept_counter.values()] + [1])
        for concept, cnt in concept_counter.items():
            avg_strength = float(strength_acc.get(concept, 0.0) or 0.0) / max(int(cnt), 1)
            strength_100 = (
                70.0 * (int(cnt) / max(max_count, 1))
                + 30.0 * (max(0.0, min(avg_strength, 4.0)) / 4.0)
            )
            rows.append(
                {
                    "concept": concept,
                    "count": int(cnt),
                    "score": round(float(avg_strength), 2),
                    "strength_100": round(float(strength_100), 2),
                }
            )
        rows.sort(key=lambda x: (x["strength_100"], x["count"], x["score"]), reverse=True)
        top10 = rows[:10]
        strongest = top10[0]
        max_strength = max([float(x.get("strength_100", 0.0) or 0.0) for x in top10] + [1.0])
        concept_strength_map = {
            x["concept"]: round(min(float(x["strength_100"]) / max_strength * 4.0, 4.0), 2)
            for x in top10
        }
        trade_date = str((concept_rotation_data.get("strongest_today", {}) or {}).get("trade_date", "") or "")
        base.update(
            {
                "data_success": True,
                "daily_rankings": [
                    {
                        "trade_date": trade_date,
                        "top_concepts": top10,
                        "strongest_concept": strongest["concept"],
                        "strongest_count": int(strongest["count"]),
                    }
                ],
                "strongest_today": {
                    "trade_date": trade_date,
                    "concept": strongest["concept"],
                    "stock_count": int(strongest["count"]),
                    "strength_100": float(strongest["strength_100"]),
                },
                "concept_strength_map": concept_strength_map,
                "board_top10": top10,
                "rotation_summary": {
                    "concept_source": "candidate_pool",
                    "tracked_stocks": int(len(candidate_codes)),
                    "matched_theme_stocks": int(matched_stock_cnt),
                    "active_concepts": int(len(concept_counter)),
                },
            }
        )
        return base

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

    def _extract_concept_strength_candidates(
        self, concept_rotation_data: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """标准化提取题材强度候选，供前端展示与主观选择。"""
        if not isinstance(concept_rotation_data, dict) or not concept_rotation_data.get("data_success"):
            return []
        source = str(concept_rotation_data.get("concept_source", "") or "").strip()
        rows = []
        daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
        top_concepts = []
        if daily_rankings:
            top_concepts = (daily_rankings[0] or {}).get("top_concepts", []) or []
        if not top_concepts:
            top_concepts = concept_rotation_data.get("board_top10", []) or []
        for idx, item in enumerate(top_concepts, 1):
            concept = str(item.get("concept", "") or "").strip()
            if not concept:
                continue
            rows.append(
                {
                    "rank": idx,
                    "concept": concept,
                    "strength_100": round(float(item.get("strength_100", 0.0) or 0.0), 2),
                    "count": int(item.get("count", item.get("stock_count", 0)) or 0),
                    "pct_chg": round(float(item.get("pct_chg", 0.0) or 0.0), 2),
                    "source": source,
                }
            )
        return rows

    def _apply_manual_mainline_override(
        self,
        concept_rotation_data: Dict[str, Any],
        manual_mainline_concepts: List[str],
    ) -> Dict[str, Any]:
        """
        使用用户主观主线覆盖程序自动主线：
        - 仅保留用户选择的题材作为主线强度映射
        - strongest_today/top_concepts 同步改为用户顺序
        """
        if not isinstance(concept_rotation_data, dict):
            return concept_rotation_data
        selected = [
            str(x or "").strip()
            for x in (manual_mainline_concepts or [])
            if str(x or "").strip()
        ][:3]
        if not selected:
            return concept_rotation_data

        candidates = self._extract_concept_strength_candidates(concept_rotation_data)
        if not candidates:
            return concept_rotation_data
        cand_map = {str(x.get("concept", "")): x for x in candidates if str(x.get("concept", "")).strip()}
        ordered = [cand_map[c] for c in selected if c in cand_map]
        if not ordered:
            return concept_rotation_data

        top_n = len(ordered)
        # 手动覆盖后，主线强度按用户顺序给高分，确保替代自动排序
        manual_strength_map: Dict[str, float] = {}
        for idx, row in enumerate(ordered):
            if top_n <= 1:
                score = 4.0
            else:
                score = max(2.8, 4.0 - idx * (1.2 / max(top_n - 1, 1)))
            manual_strength_map[str(row["concept"])] = round(score, 2)

        out = dict(concept_rotation_data)
        out["concept_strength_map"] = manual_strength_map
        out["mainline_selection_mode"] = "manual"
        out["manual_mainline_concepts"] = [x["concept"] for x in ordered]
        out["effective_mainline_concepts"] = [x["concept"] for x in ordered]

        strongest = ordered[0]
        strongest_today = dict(out.get("strongest_today", {}) or {})
        strongest_today.update(
            {
                "concept": strongest.get("concept", ""),
                "strength_100": float(strongest.get("strength_100", 0.0) or 0.0),
                "pct_chg": float(strongest.get("pct_chg", 0.0) or 0.0),
                "stock_count": int(strongest.get("count", 0) or 0),
                "hot_num_total": int(strongest.get("count", 0) or 0),
            }
        )
        out["strongest_today"] = strongest_today

        daily_rankings = list(out.get("daily_rankings", []) or [])
        top_rows = [
            {
                "concept": x["concept"],
                "score": manual_strength_map.get(x["concept"], 0.0),
                "count": int(x.get("count", 0) or 0),
                "pct_chg": float(x.get("pct_chg", 0.0) or 0.0),
                "strength_100": float(x.get("strength_100", 0.0) or 0.0),
            }
            for x in ordered
        ]
        if daily_rankings:
            daily_rankings[0] = {
                **(daily_rankings[0] or {}),
                "top_concepts": top_rows,
                "strongest_concept": strongest.get("concept", ""),
                "strongest_count": int(strongest.get("count", 0) or 0),
            }
            out["daily_rankings"] = daily_rankings
        else:
            out["daily_rankings"] = [{"trade_date": "", "top_concepts": top_rows}]

        rotation_summary = dict(out.get("rotation_summary", {}) or {})
        rotation_summary.update(
            {
                "mainline_selection_mode": "manual",
                "manual_mainline_concepts": [x["concept"] for x in ordered],
                "effective_mainline_concepts": [x["concept"] for x in ordered],
            }
        )
        out["rotation_summary"] = rotation_summary
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

    def _get_pct_source_rank(self, row: Dict[str, Any]) -> int:
        """
        pct_chg 优先级（值越小优先级越高）：
        1) tushare.top_list
        2) tushare.limit_list_ths
        3) tushare.kpl_list
        4) 其它来源（stockapi/未知）
        """
        pct_source = str(row.get("pct_source", "") or "").strip().lower()
        data_source = str(row.get("data_source", "") or "").strip().lower()
        merged = f"{pct_source}|{data_source}"
        if "tushare.top_list" in merged:
            return 1
        if "tushare.limit_list_ths" in merged:
            return 2
        if "tushare.kpl_list" in merged:
            return 3
        if "stockapi" in merged:
            return 4
        return 5

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
        noise = {
            "涨停", "跌停", "概念", "题材", "板块", "原因", "N/A", "None", "nan", "-",
            "limit_list_ths补充", "kpl_list补充", "limit_list_ths", "kpl_list",
            # 非题材属性噪声词（容易误导AI）
            "融资融券", "沪股通", "深股通", "陆股通", "港股通",
        }
        out: List[str] = []
        seen = set()
        for part in parts:
            theme = str(part or "").strip()
            if not theme or theme in noise or theme.isdigit() or len(theme) < 2:
                continue
            lower_theme = theme.lower()
            if ("limit_list_ths" in lower_theme) or ("kpl_list" in lower_theme):
                continue
            # 地域/市场标签过滤（如 上海板块、广东板块）
            if theme.endswith("板块"):
                continue
            if self._is_st_theme(theme):
                continue
            if theme not in seen:
                seen.add(theme)
                out.append(theme)
        return out[:10]

    def _normalize_theme_match_key(self, text: str) -> str:
        s = str(text or "").strip().lower()
        if not s:
            return ""
        s = re.sub(r"[\s\-\_＋+,，;；/|、]+", "", s)
        s = re.sub(r"(概念|题材|板块|方向)$", "", s)
        return s

    def _bigram_set(self, s: str) -> set:
        text = str(s or "").strip()
        if len(text) < 2:
            return set()
        return {text[i:i+2] for i in range(len(text) - 1)}

    def _theme_fuzzy_match_score(self, a: str, b: str) -> int:
        """
        题材模糊匹配分：
        - 完全一致 100
        - 包含关系 80
        - 二字片段有重叠 55~70
        """
        aa = self._normalize_theme_match_key(a)
        bb = self._normalize_theme_match_key(b)
        if not aa or not bb:
            return 0
        if aa == bb:
            return 100
        if (aa in bb) or (bb in aa):
            return 80
        a2 = self._bigram_set(aa)
        b2 = self._bigram_set(bb)
        inter = a2 & b2
        if not inter:
            return 0
        # 重叠越多分越高；至少给到55，便于“商业航天 vs 航天航空”命中
        overlap = len(inter)
        return min(70, 50 + overlap * 5)

    def _pick_gl_tokens_by_source_themes(
        self,
        raw_gl: str,
        summary: Dict[str, Any],
        max_items: int = 3,
    ) -> List[str]:
        """
        在 gl 拆词结果中，优先挑选与 kpl_list / limit_list_ths 题材主线最契合的词。
        """
        candidates = self._split_lu_desc_themes(raw_gl)
        if not candidates:
            return []
        source_rows = (summary.get("theme_source_priority", []) or [])
        preferred = [str(x.get("theme", "") or "").strip() for x in source_rows if str(x.get("theme", "") or "").strip()]
        if not preferred:
            return candidates[:max_items]

        pref_keys = [self._normalize_theme_match_key(x) for x in preferred if self._normalize_theme_match_key(x)]
        if not pref_keys:
            return candidates[:max_items]

        scored: List[tuple] = []
        for idx, token in enumerate(candidates):
            score = 0
            for src_theme in preferred:
                score = max(score, self._theme_fuzzy_match_score(token, src_theme))
            scored.append((token, score, idx))

        strong = [x for x in scored if x[1] > 0]
        strong.sort(key=lambda x: (-x[1], x[2]))
        selected: List[str] = []
        seen = set()
        for token, _, _ in strong:
            if token in seen:
                continue
            seen.add(token)
            selected.append(token)
            if len(selected) >= max_items:
                return selected

        # 若匹配不足，按 gl 原始顺序补齐
        for token, _, _ in scored:
            if token in seen:
                continue
            seen.add(token)
            selected.append(token)
            if len(selected) >= max_items:
                break
        return selected

    def _rank_tokens_by_mainline_pool(
        self,
        tokens: List[str],
        summary: Dict[str, Any],
    ) -> List[str]:
        """
        将个股题材词按“主线池契合度”筛选/重排：
        - 只要有契合（匹配分>0）即保留并按契合度排序；
        - 若一个契合词都没有，回退原始顺序（后续再截断）。
        """
        seq = [str(x or "").strip() for x in (tokens or []) if str(x or "").strip()]
        if not seq:
            return []
        source_rows = (summary.get("theme_source_priority", []) or [])
        source_themes = [str(x.get("theme", "") or "").strip() for x in source_rows if str(x.get("theme", "") or "").strip()]
        source_count_map = {
            str(x.get("theme", "") or "").strip(): int(x.get("count", 0) or 0)
            for x in source_rows
            if str(x.get("theme", "") or "").strip()
        }
        if not source_themes:
            return seq

        scored = []
        for idx, token in enumerate(seq):
            best_match_score = 0
            best_theme = ""
            for s in source_themes:
                m = self._theme_fuzzy_match_score(token, s)
                if m > best_match_score:
                    best_match_score = m
                    best_theme = s
            hot = source_count_map.get(best_theme, 0) if best_theme else 0
            scored.append((token, best_match_score, hot, idx))

        matched = [x for x in scored if int(x[1]) > 0]
        if matched:
            matched.sort(key=lambda x: (-x[1], -x[2], x[3]))
            ordered = matched
        else:
            # 无命中：保留原词序，后续由上层做截断
            ordered = sorted(scored, key=lambda x: x[3])

        out = []
        seen = set()
        for token, _, _, _ in ordered:
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    def _is_st_theme(self, theme: str) -> bool:
        text = str(theme or "").strip()
        if not text:
            return False
        upper = text.upper()
        if text == "ST板块" or "风险警示" in text:
            return True
        return bool(re.search(r"(^|\b)\*?ST(\b|$)", upper))

    def _build_theme_clue_map_from_summary(self, summary: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        从 summary.top_stock_lu_desc 构建“可展示题材词”映射。
        规则：
        - 默认仅使用统一字段 theme_top；
        - 当个股 theme_top 为空时，才允许使用该股 gl 兜底，并通过主线池过滤。
        """
        out: Dict[str, List[str]] = {}
        for row in (summary.get("top_stock_lu_desc", []) or []):
            code = self._normalize_code(row.get("code", ""))
            if not code:
                continue
            tokens: List[str] = []
            seen = set()
            for raw in (row.get("theme_top", []) or []):
                text = str(raw or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                tokens.append(text)
            # 仅当个股题材字段为空时，才允许用 gl 兜底（且不进入题材池本身）
            if not tokens:
                gl_fallback_map = (summary.get("stock_gl_fallback_map", {}) or {})
                raw_gl = str(gl_fallback_map.get(code, "") or "").strip()
                if raw_gl:
                    for text in self._pick_gl_tokens_by_source_themes(raw_gl, summary, max_items=6):
                        if not text or text in seen:
                            continue
                        seen.add(text)
                        tokens.append(text)
            # 关键：无论来源如何，先按主线池重排，再截断，避免泛词挤掉主线词
            tokens = self._rank_tokens_by_mainline_pool(tokens, summary)
            if tokens:
                # 扩大可用词池，供推荐理由有更高概率命中个股真实关键信息
                out[code] = tokens[:10]
        return out

    def _ensure_theme_clues_for_candidates(
        self,
        summary: Dict[str, Any],
        data_list: List[Dict[str, Any]],
        candidate_stocks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        将题材词池扩展到“全候选池股票”：
        - 仍只使用 theme/lu_desc/gl 拆词
        - 不做AI推断，不跨股借词
        """
        out = dict(summary or {})
        if not candidate_stocks:
            return out

        candidate_codes = {
            self._normalize_code(x.get("code", ""))
            for x in (candidate_stocks or [])
            if self._normalize_code(x.get("code", ""))
        }
        if not candidate_codes:
            return out

        old_rows = {
            self._normalize_code(x.get("code", "")): dict(x)
            for x in (out.get("top_stock_lu_desc", []) or [])
            if self._normalize_code(x.get("code", ""))
        }
        stock_gl_fallback_map = dict(out.get("stock_gl_fallback_map", {}) or {})

        source_rows_by_code: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in (data_list or []):
            code = self._normalize_code(row.get("gpdm") or row.get("股票代码") or "")
            if not code or code not in candidate_codes:
                continue
            if not self.data_fetcher._is_theme_source_record(row):  # 复用现有题材来源识别
                continue
            source_rows_by_code[code].append(row)
            raw_gl = str(row.get("gl") or row.get("概念") or "").strip()
            if raw_gl and code not in stock_gl_fallback_map:
                stock_gl_fallback_map[code] = raw_gl[:120]

        merged_rows: List[Dict[str, Any]] = []
        for stock in (candidate_stocks or []):
            code = self._normalize_code(stock.get("code", ""))
            if not code:
                continue
            name = str(stock.get("name", "") or "")
            old = old_rows.get(code, {}) or {}
            # 规则：lu_desc_top 仅保留“真实题材描述”来源，不沿用可能由 gl 兜底产生的旧值
            raw_clues: List[str] = []
            theme_top: List[str] = list(old.get("theme_top", []) or [])
            seen_raw = set()
            seen_theme = set([str(x).strip() for x in theme_top if str(x).strip()])

            for row in source_rows_by_code.get(code, []):
                raw = str(row.get("gl") or row.get("概念") or "").strip()
                source = str(row.get("data_source", "") or "").strip().lower()
                # 仅当来源为 kpl/limit（含明确题材描述）时写入 lu_desc_top；
                # top_list/stockapi 的 gl 仅用于 theme_top，不进入 lu_desc_top。
                if raw and (("tushare.limit_list_ths" in source) or ("tushare.kpl_list" in source)):
                    if raw not in seen_raw:
                        seen_raw.add(raw)
                        raw_clues.append(raw[:80])
                for token in self._split_lu_desc_themes(raw):
                    if token and token not in seen_theme:
                        seen_theme.add(token)
                        theme_top.append(token)
                if len(raw_clues) >= 4 and len(theme_top) >= 8:
                    break

            if not theme_top:
                raw_gl = str(stock_gl_fallback_map.get(code, "") or "").strip()
                if raw_gl:
                    for token in self._pick_gl_tokens_by_source_themes(raw_gl, out, max_items=6):
                        if token and token not in seen_theme:
                            seen_theme.add(token)
                            theme_top.append(token)

            merged_rows.append(
                {
                    "code": code,
                    "name": name or str(old.get("name", "") or ""),
                    "lu_desc_top": raw_clues[:4],
                    # 扩容题材词池，避免 gl 后半段关键信息（如商业航天/算力）被截断
                    "theme_top": theme_top[:10],
                }
            )

        if merged_rows:
            out["top_stock_lu_desc"] = merged_rows
        if stock_gl_fallback_map:
            out["stock_gl_fallback_map"] = stock_gl_fallback_map
        # 按用户要求：不再用 gl 拆词覆盖/构建题材池(theme_source_priority)
        return out

    def _calc_candidate_data_quality(
        self,
        *,
        net_inflow: float,
        pct_chg: Optional[float],
        p1_score: float,
        limit_quality_score: float,
        has_theme_tokens: bool,
        kline_trend_stage: str,
        source_completeness: Dict[str, bool],
    ) -> Dict[str, Any]:
        """
        候选股数据质量分层（A/B/C）：
        - 数据完整度：kpl/ths/limit来源可用 + 个股关键字段是否齐全
        - 信号一致性：资金、涨跌、封板质量、趋势方向是否共振
        """
        kpl_ok = bool(source_completeness.get("kpl", False))
        ths_ok = bool(source_completeness.get("ths", False))
        limit_ok = bool(source_completeness.get("limit", False))
        source_score = (float(kpl_ok) + float(ths_ok) + float(limit_ok)) / 3.0

        field_hits = 0.0
        field_hits += 1.0 if pct_chg is not None else 0.0
        field_hits += 1.0 if has_theme_tokens else 0.0
        field_hits += 1.0 if p1_score > 0 else 0.0
        field_hits += 1.0 if str(kline_trend_stage or "").strip() else 0.0
        field_score = field_hits / 4.0
        completeness = 0.6 * source_score + 0.4 * field_score

        consistency = 0.5
        if net_inflow > 0:
            consistency += 0.15
        if p1_score >= 0.65:
            consistency += 0.2
        elif p1_score >= 0.5:
            consistency += 0.1
        if limit_quality_score >= 0.8:
            consistency += 0.1
        elif limit_quality_score < 0.35:
            consistency -= 0.2

        if pct_chg is not None:
            if float(pct_chg) >= 0:
                consistency += 0.08
            else:
                consistency -= 0.08

        trend_stage = str(kline_trend_stage or "").strip()
        if trend_stage == "退潮期":
            consistency -= 0.15
        elif trend_stage in {"启动期", "加速期"}:
            consistency += 0.08

        completeness = max(0.0, min(1.0, completeness))
        consistency = max(0.0, min(1.0, consistency))
        confidence = 0.6 * completeness + 0.4 * consistency
        if confidence >= 0.78 and completeness >= 0.70:
            grade = "A"
        elif confidence >= 0.55:
            grade = "B"
        else:
            grade = "C"
        return {
            "data_completeness": round(completeness, 3),
            "signal_consistency": round(consistency, 3),
            "data_quality_grade": grade,
        }

    def _refresh_candidate_theme_tokens(
        self,
        candidate_stocks: List[Dict[str, Any]],
        summary_for_ai: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        使用最终 summary_for_ai 的 clue_map 回填候选池 theme_tokens，
        避免“候选池字段”与“最终给AI词池”不一致。
        """
        clue_map = self._build_theme_clue_map_from_summary(summary_for_ai or {})
        out: List[Dict[str, Any]] = []
        for row in (candidate_stocks or []):
            item = dict(row or {})
            code = self._normalize_code(item.get("code", ""))
            if code:
                item["theme_tokens"] = "、".join((clue_map.get(code, []) or [])[:6])
            out.append(item)
        return out

    def _extract_chief_table_map(self, chief_analysis: str) -> Dict[str, Dict[str, str]]:
        """
        从首席策略师Markdown推荐表中提取结构化字段：
        股票代码 -> {reason, confidence, buy_timing, trigger_condition, hold_period}
        """
        out: Dict[str, Dict[str, str]] = {}
        text = str(chief_analysis or "").strip()
        if not text:
            return out
        for line in text.splitlines():
            s = str(line or "").strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 8:
                continue
            if "股票代码" in s or "推荐理由" in s or s.startswith("|---"):
                continue
            code = self._normalize_code(cells[2] if len(cells) > 2 else "")
            if not code:
                continue
            if code not in out:
                out[code] = {
                    "reason": str(cells[3] if len(cells) > 3 else "").strip(),
                    "confidence": str(cells[4] if len(cells) > 4 else "").strip(),
                    "buy_timing": str(cells[5] if len(cells) > 5 else "").strip(),
                    "trigger_condition": str(cells[6] if len(cells) > 6 else "").strip(),
                    "hold_period": str(cells[7] if len(cells) > 7 else "").strip(),
                }
        return out

    def _extract_chief_selected_codes(self, chief_analysis: str) -> List[str]:
        """
        提取首席策略师推荐表中的股票代码顺序（按表格出现顺序）。
        """
        out: List[str] = []
        seen = set()
        text = str(chief_analysis or "").strip()
        if not text:
            return out
        for line in text.splitlines():
            s = str(line or "").strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 8:
                continue
            if "股票代码" in s or "推荐理由" in s or s.startswith("|---"):
                continue
            code = self._normalize_code(cells[2] if len(cells) > 2 else "")
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out

    def _is_placeholder_text(self, text: Any) -> bool:
        val = str(text or "").strip()
        if not val:
            return True
        bad_tokens = ["待定", "暂无", "n/a", "N/A", "观察", "未定", "-", "--"]
        return any(tok in val for tok in bad_tokens)

    def _is_reason_too_brief(self, text: Any) -> bool:
        val = str(text or "").strip()
        if not val:
            return True
        # 模板化短句或仅资金句，判定需要AI补全
        if val.startswith("资金净流入"):
            return True
        if len(val) < 28:
            return True
        return False

    def _extract_advice_map_from_markdown(self, markdown_text: str) -> Dict[str, Dict[str, str]]:
        """
        解析“建议字段补全器”返回的Markdown表：
        股票代码 | 推荐理由（详细） | 确定性评级 | 买入时机建议 | 交易触发条件 | 持有周期建议
        """
        out: Dict[str, Dict[str, str]] = {}
        lines = str(markdown_text or "").splitlines()
        for line in lines:
            s = str(line or "").strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 6:
                continue
            if "股票代码" in s or s.startswith("|---"):
                continue
            # 允许带序号列（7列）或不带序号列（6列）
            if len(cells) >= 7 and cells[0].isdigit():
                code_raw, reason, conf, buy_t, trig, hold = cells[1], cells[2], cells[3], cells[4], cells[5], cells[6]
            else:
                code_raw, reason, conf, buy_t, trig, hold = cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]
            code = self._normalize_code(code_raw)
            if not code:
                continue
            out[code] = {
                "reason": str(reason or "").strip(),
                "confidence": str(conf or "").strip(),
                "buy_price": str(buy_t or "").strip(),
                "target_price": str(trig or "").strip(),
                "hold_period": str(hold or "").strip(),
            }
        return out

    def _build_chief_candidate_quota_context(
        self,
        summary: Dict[str, Any],
        candidate_stocks: Optional[List[Dict[str, Any]]],
        latest_pct_map: Dict[str, float],
        p1_signal_data: Optional[Dict[str, Any]],
        recommendation_count: int,
        limit_up_ratio: float,
        mainboard_limit_like_pct: float,
        enable_recommendation_quota: bool,
    ) -> str:
        """
        构建首席策略师候选股明细上下文，显式提供：
        - pct_chg
        - is_today_limit_up
        - is_limit_like_for_quota
        - 非近涨停最小目标数量
        """
        rec_count = max(3, min(int(recommendation_count or 8), 12))
        ratio = max(0.0, min(float(limit_up_ratio or 0.4), 1.0))
        max_limit_like = max(0, min(rec_count, int(rec_count * ratio + 1e-9)))
        min_non_limit_like = max(0, rec_count - max_limit_like)

        rows = []
        base_candidates = list(candidate_stocks or []) or list((summary.get("top_stocks", []) or []))
        for idx, stock in enumerate(base_candidates[:60], 1):
            code = self._normalize_code(stock.get("code", ""))
            if not code or not self._is_mainboard_code(code):
                continue
            pct_chg = latest_pct_map.get(code, None)
            is_today_limit_up = self._is_today_limit_up(code, p1_signal_data)
            is_limit_like = self._is_limit_like_for_quota(
                code=code,
                name=stock.get("name", ""),
                pct_chg=pct_chg,
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )
            pct_text = "-" if pct_chg is None else f"{round(float(pct_chg), 2)}%"
            rows.append(
                f"{idx}. {stock.get('name', '')}({code}) | pct_chg={pct_text} | "
                f"is_today_limit_up={'是' if is_today_limit_up else '否'} | "
                f"is_limit_like_for_quota={'是' if is_limit_like else '否'}"
            )
        if not rows:
            rows = ["- 暂无可用候选股明细"]

        lines = [
            "\n【候选股涨跌与配额参考（系统计算）】",
            "- 字段定义：",
            "- pct_chg：候选股当日涨跌幅（%），来自系统聚合后的最新当日值。",
            "- is_today_limit_up：是否命中“当日真实涨停”标记（来自涨停识别链路）。",
            "- is_limit_like_for_quota：是否命中“近涨停标签”（仅用于分层配额，不等同真实涨停）。",
            f"- 推荐总数目标：{rec_count}",
            f"- 近涨停标签上限：{max_limit_like} 只（占比上限 {round(ratio * 100, 2)}%）",
            f"- 非近涨停最少目标：{min_non_limit_like} 只",
            f"- 配额开关：{'开启' if bool(enable_recommendation_quota) else '关闭'}",
            f"- 候选池规模：{len(base_candidates)}",
            "- 候选股明细（仅主板）：",
            *rows,
            "- 选股时优先满足“非近涨停最少目标”，并结合资金/题材/风险综合排序。",
        ]
        return "\n".join(lines)

    def _build_chief_candidate_kline_context(
        self,
        kline_9d_data: Optional[Dict[str, Any]],
        candidate_stocks: Optional[List[Dict[str, Any]]],
        max_rows: int = 60,
    ) -> str:
        """
        构建二次重选可用的候选池9日日线上下文（仅辅助，不主导）。
        """
        if not isinstance(kline_9d_data, dict) or not kline_9d_data.get("data_success"):
            return "\n【候选股9日日线快照】\n- 暂无可用9日日线数据。"
        trend_map = (kline_9d_data.get("stock_trend_map", {}) or {})
        if not trend_map:
            return "\n【候选股9日日线快照】\n- stock_trend_map 为空。"

        rows: List[str] = []
        base_candidates = list(candidate_stocks or [])
        for idx, stock in enumerate(base_candidates[:max_rows], 1):
            code = self._normalize_code(stock.get("code", ""))
            if not code:
                continue
            detail = trend_map.get(code, {}) or {}
            if not detail:
                continue
            rows.append(
                f"{idx}. {stock.get('name', '')}({code}) | "
                f"trend_stage={detail.get('trend_stage', '-')} | "
                f"change_7d_pct={round(float(detail.get('change_7d_pct', 0.0) or 0.0), 2)}% | "
                f"latest_change_pct={round(float(detail.get('latest_change_pct', 0.0) or 0.0), 2)}% | "
                f"drawdown_from_high_pct={round(float(detail.get('drawdown_from_high_pct', 0.0) or 0.0), 2)}% | "
                f"vol_ratio={round(float(detail.get('vol_ratio', 0.0) or 0.0), 2)}"
            )
        if not rows:
            return "\n【候选股9日日线快照】\n- 候选池股票未匹配到有效9日日线明细。"

        return "\n".join([
            "\n【候选股9日日线快照（仅辅助参考）】",
            "- 字段含义：trend_stage(趋势阶段)、change_7d_pct(近9日累计涨幅)、latest_change_pct(最新日涨跌幅)、drawdown_from_high_pct(相对近高回撤)、vol_ratio(最新量比)。",
            *rows,
            "- 使用原则：日线仅用于同层候选中的强弱微调，不得覆盖资金与题材主逻辑。",
        ])

    def _build_recommendation_candidate_stocks(
        self,
        scoring_ranking_data: Optional[List[Dict[str, Any]]],
        summary: Dict[str, Any],
        latest_pct_map: Optional[Dict[str, float]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        kline_9d_data: Optional[Dict[str, Any]] = None,
        summary_for_ai: Optional[Dict[str, Any]] = None,
        source_completeness: Optional[Dict[str, bool]] = None,
        mainboard_limit_like_pct: float = 6.0,
        max_candidates: int = 80,
    ) -> List[Dict[str, Any]]:
        """
        构建“推荐候选股票池”：
        - 优先使用评分池（scoring_ranking）中的主板股票，提升 <阈值 股票覆盖度
        - 再补充 summary.top_stocks，保留资金前排优先级
        """
        out: List[Dict[str, Any]] = []
        seen = set()
        cap = max(10, min(int(max_candidates or 80), 200))
        pct_map = latest_pct_map or {}
        p1_map = (p1_signal_data or {}).get("stock_signal_map", {}) or {}
        trend_map = (kline_9d_data or {}).get("stock_trend_map", {}) or {}
        clue_map = self._build_theme_clue_map_from_summary(summary_for_ai or summary or {})
        source_ready = dict(source_completeness or {})

        def _build_candidate_row(code: str, name: str, net_inflow: float) -> Dict[str, Any]:
            p1 = p1_map.get(code, {}) or {}
            trend = trend_map.get(code, {}) or {}
            pct = pct_map.get(code, None)
            is_limit_up = self._is_today_limit_up(code, p1_signal_data)
            is_limit_like = self._is_limit_like_for_quota(
                code=code,
                name=name,
                pct_chg=pct,
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )
            trend_stage = str(trend.get("trend_stage", "") or "")
            p1_score = round(float(p1.get("score", 0.0) or 0.0), 3)
            limit_quality_score = round(float(p1.get("limit_quality_score", 0.0) or 0.0), 3)
            theme_tokens = (clue_map.get(code, []) or [])[:6]
            quality = self._calc_candidate_data_quality(
                net_inflow=float(net_inflow or 0.0),
                pct_chg=pct,
                p1_score=float(p1_score),
                limit_quality_score=float(limit_quality_score),
                has_theme_tokens=bool(theme_tokens),
                kline_trend_stage=trend_stage,
                source_completeness=source_ready,
            )
            return {
                "code": code,
                "name": name,
                "net_inflow": float(net_inflow or 0.0),
                "pct_chg": None if pct is None else round(float(pct), 2),
                "is_today_limit_up": bool(is_limit_up),
                "is_limit_like_for_quota": bool(is_limit_like),
                "limit_quality_score": limit_quality_score,
                "p1_score": p1_score,
                "theme_tokens": "、".join(theme_tokens),
                "kline_trend_stage": trend_stage,
                "kline_change_7d_pct": round(float(trend.get("change_7d_pct", 0.0) or 0.0), 2),
                "kline_latest_change_pct": round(float(trend.get("latest_change_pct", 0.0) or 0.0), 2),
                "kline_drawdown_from_high_pct": round(float(trend.get("drawdown_from_high_pct", 0.0) or 0.0), 2),
                "kline_vol_ratio": round(float(trend.get("vol_ratio", 0.0) or 0.0), 2),
                "data_completeness": quality["data_completeness"],
                "signal_consistency": quality["signal_consistency"],
                "data_quality_grade": quality["data_quality_grade"],
            }

        for row in (scoring_ranking_data or []):
            code = self._normalize_code(row.get("股票代码") or row.get("code") or "")
            if not code or code in seen or not self._is_mainboard_code(code):
                continue
            seen.add(code)
            out.append(_build_candidate_row(
                code=code,
                name=str(row.get("股票名称") or row.get("name") or ""),
                net_inflow=float(row.get("净流入", 0.0) or row.get("net_inflow", 0.0) or 0.0),
            ))
            if len(out) >= cap:
                return out

        for row in (summary.get("top_stocks", []) or []):
            code = self._normalize_code(row.get("code", ""))
            if not code or code in seen or not self._is_mainboard_code(code):
                continue
            seen.add(code)
            out.append(_build_candidate_row(
                code=code,
                name=str(row.get("name", "") or ""),
                net_inflow=float(row.get("net_inflow", 0.0) or 0.0),
            ))
            if len(out) >= cap:
                break
        return out

    def _extract_active_themes_for_history_peer(
        self,
        concept_rotation_data: Optional[Dict[str, Any]],
        summary: Optional[Dict[str, Any]],
    ) -> List[str]:
        themes: List[str] = []
        seen = set()
        if isinstance(concept_rotation_data, dict) and concept_rotation_data.get("data_success"):
            daily_rankings = concept_rotation_data.get("daily_rankings", []) or []
            if daily_rankings:
                for row in (daily_rankings[0].get("top_concepts", []) or [])[:8]:
                    t = str(row.get("concept", "") or "").strip()
                    if t and t not in seen:
                        seen.add(t)
                        themes.append(t)
        if not themes:
            for row in ((summary or {}).get("theme_source_priority", []) or [])[:10]:
                t = str(row.get("theme", "") or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    themes.append(t)
        return themes[:10]

    def _merge_history_peer_into_candidates(
        self,
        candidate_stocks: List[Dict[str, Any]],
        peer_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        peer_map = {}
        for row in ((peer_context or {}).get("candidates", []) or []):
            code = self._normalize_code(row.get("code", ""))
            if code:
                peer_map[code] = row
        if not peer_map:
            return candidate_stocks
        out: List[Dict[str, Any]] = []
        for row in (candidate_stocks or []):
            item = dict(row or {})
            code = self._normalize_code(item.get("code", ""))
            peer = peer_map.get(code, {}) or {}
            if peer:
                item["peer_rebound_score"] = round(float(peer.get("peer_rebound_score", 0.0) or 0.0), 2)
                item["peer_hist_win_rate"] = round(float(peer.get("hist_win_rate", 0.0) or 0.0), 3)
                item["peer_hist_avg_next_pct"] = round(float(peer.get("hist_avg_next_pct", 0.0) or 0.0), 3)
                item["peer_theme_strength_today"] = round(float(peer.get("theme_strength_today", 0.0) or 0.0), 3)
                item["peer_low_position_score"] = round(float(peer.get("low_position_score", 0.0) or 0.0), 3)
                item["peer_theme_tokens"] = str(peer.get("theme_tokens", "") or "")
                item["cooccur_frequency"] = int(peer.get("cooccur_frequency", 0) or 0)
                item["cooccur_win_rate"] = round(float(peer.get("cooccur_win_rate", 0.0) or 0.0), 3)
                item["cooccur_avg_return"] = round(float(peer.get("cooccur_avg_return", 0.0) or 0.0), 2)
            else:
                item["peer_rebound_score"] = 0.0
                item["peer_hist_win_rate"] = 0.0
                item["peer_hist_avg_next_pct"] = 0.0
                item["peer_theme_strength_today"] = 0.0
                item["peer_low_position_score"] = 0.0
                item["peer_theme_tokens"] = ""
                item["cooccur_frequency"] = 0
                item["cooccur_win_rate"] = 0.0
                item["cooccur_avg_return"] = 0.0
            out.append(item)
        return out

    def _append_peer_rebound_report_section(
        self,
        chief_analysis: str,
        peer_context: Dict[str, Any],
    ) -> str:
        """
        在首席报告末尾追加“历史统计驱动补涨推荐”模块（纯展示，不参与AI决策）。
        """
        base = str(chief_analysis or "").strip()
        rows = list((peer_context or {}).get("candidates", []) or [])
        pool_breakdown = dict((peer_context or {}).get("pool_source_breakdown", {}) or {})
        base_cnt = int(pool_breakdown.get("base_count", 0) or 0)
        expanded_cnt = int(pool_breakdown.get("expanded_count", 0) or 0)
        dropped_cnt = int(pool_breakdown.get("dropped_count", 0) or 0)
        dropped_no_daily = int((peer_context or {}).get("dropped_no_daily_count", 0) or 0)
        if not rows:
            section = (
                "\n\n## 历史统计驱动补涨推荐（系统模块）\n"
                "当前无可用补涨候选（可能是历史样本不足或当日题材匹配度较低）。\n"
                f"候选池构成：原池 {base_cnt}，扩池 {expanded_cnt}，剔除 {dropped_cnt}（无当日daily {dropped_no_daily}）。"
            )
            return f"{base}{section}" if base else section.strip()

        # 优先展示“非近涨停”补涨票，保持与你的核心目标一致
        non_limit = [x for x in rows if not bool(x.get("is_limit_like_for_quota", False))]
        selected = (non_limit if non_limit else rows)[:6]
        active_themes = list((peer_context or {}).get("active_themes", []) or [])[:6]
        title_suffix = f"（主线：{'、'.join(active_themes)}）" if active_themes else ""

        header = (
            f"\n\n## 历史统计驱动补涨推荐（系统模块）{title_suffix}\n"
            "仅供参考：该模块为系统统计补充，不改变首席主推荐名单与排序。\n\n"
            f"候选池构成：原池 {base_cnt}，扩池 {expanded_cnt}，剔除 {dropped_cnt}（无当日daily {dropped_no_daily}）。\n\n"
            "| 序号 | 股票名称 | 股票代码 | 当日涨跌幅 | 命中题材 | 历史胜率 | 历史次日均涨幅 | 共现历史 | 补涨评分 |\n"
            "|---|---|---|---|---|---|---|---|---|"
        )
        body = []
        for idx, row in enumerate(selected, 1):
            name = self._escape_md_cell(row.get("name", ""))
            code = self._escape_md_cell(row.get("code", ""))
            pct = self._escape_md_cell(f"{round(float(row.get('pct_chg', 0.0) or 0.0), 2)}%")
            themes = self._escape_md_cell(row.get("theme_tokens", "") or "-")
            win_rate = self._escape_md_cell(f"{round(float(row.get('hist_win_rate', 0.0) or 0.0) * 100, 1)}%")
            avg_next = self._escape_md_cell(f"{round(float(row.get('hist_avg_next_pct', 0.0) or 0.0), 2)}%")

            # 共现历史列
            cooccur_freq = int(row.get("cooccur_frequency", 0) or 0)
            cooccur_wr = float(row.get("cooccur_win_rate", 0.0) or 0.0)
            cooccur_ret = float(row.get("cooccur_avg_return", 0.0) or 0.0)
            if cooccur_freq > 0:
                cooccur_text = f"共现{cooccur_freq}次 | 胜率{round(cooccur_wr * 100, 0):.0f}% | 平均{cooccur_ret:+.1f}%"
            else:
                cooccur_text = "-"
            cooccur_cell = self._escape_md_cell(cooccur_text)

            score = self._escape_md_cell(f"{round(float(row.get('peer_rebound_score', 0.0) or 0.0), 2)}")
            body.append(f"| {idx} | {name} | {code} | {pct} | {themes} | {win_rate} | {avg_next} | {cooccur_cell} | {score} |")
        section = header + "\n" + "\n".join(body)
        return f"{base}{section}" if base else section.strip()

    def _evaluate_chief_quota_alignment(
        self,
        chief_analysis: str,
        summary: Dict[str, Any],
        latest_pct_map: Dict[str, float],
        recommendation_count: int,
        limit_up_ratio: float,
        mainboard_limit_like_pct: float,
        enable_recommendation_quota: bool,
    ) -> Dict[str, Any]:
        """
        校验首席推荐文本与分层配额目标的一致性（仅提示，不触发重选）。
        """
        rec_count = max(3, min(int(recommendation_count or 8), 12))
        ratio = max(0.0, min(float(limit_up_ratio or 0.4), 1.0))
        max_limit_like = max(0, min(rec_count, int(rec_count * ratio + 1e-9)))
        min_non_limit_like = max(0, rec_count - max_limit_like)
        chief_map = self._extract_chief_table_map(chief_analysis)
        selected_codes = []
        for code in self._extract_chief_selected_codes(chief_analysis):
            if code in chief_map:
                selected_codes.append(code)
        if not selected_codes:
            selected_codes = list(chief_map.keys())

        name_map = {
            self._normalize_code(x.get("code", "")): str(x.get("name", "") or "")
            for x in (summary.get("top_stocks", []) or [])
        }
        limit_like_names = []
        non_limit_like_names = []
        for code in selected_codes:
            pct_chg = latest_pct_map.get(code, None)
            is_limit_like = self._is_limit_like_for_quota(
                code=code,
                name=name_map.get(code, ""),
                pct_chg=pct_chg,
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )
            nm = name_map.get(code, code)
            if is_limit_like:
                limit_like_names.append(nm)
            else:
                non_limit_like_names.append(nm)

        non_limit_ok = len(non_limit_like_names) >= min_non_limit_like
        return {
            "enabled": bool(enable_recommendation_quota),
            "chief_selected_count": len(selected_codes),
            "target_recommendation_count": rec_count,
            "max_limit_like": max_limit_like,
            "min_non_limit_like": min_non_limit_like,
            "chief_limit_like_count": len(limit_like_names),
            "chief_non_limit_like_count": len(non_limit_like_names),
            "non_limit_target_met": bool(non_limit_ok),
            "chief_limit_like_names": limit_like_names[:20],
            "chief_non_limit_like_names": non_limit_like_names[:20],
        }

    def _ensure_ai_trade_advice(
        self,
        recommended_stocks: List[Dict[str, Any]],
        chief_analysis: str = "",
    ) -> List[Dict[str, Any]]:
        """
        若建议字段仍有占位词，则触发一次AI补全并回填。
        """
        if not recommended_stocks:
            return recommended_stocks
        need_fix = False
        for x in recommended_stocks:
            if (
                self._is_reason_too_brief(x.get("reason", ""))
                or
                self._is_placeholder_text(x.get("confidence", ""))
                or self._is_placeholder_text(x.get("buy_price", ""))
                or self._is_placeholder_text(x.get("target_price", ""))
                or self._is_placeholder_text(x.get("hold_period", ""))
            ):
                need_fix = True
                break
        if not need_fix:
            return recommended_stocks

        self.logger.info("[阶段5] 检测到占位建议词，触发AI补全建议字段...")
        patch_md = self.agents.repair_recommendation_advice(
            recommended_stocks=recommended_stocks,
            chief_analysis=chief_analysis,
        )
        advice_map = self._extract_advice_map_from_markdown(patch_md)
        if not advice_map:
            self.logger.warning("[阶段5] AI补全建议字段未解析到有效表格，保留原值")
            return recommended_stocks
        for item in recommended_stocks:
            code = self._normalize_code(item.get("code", ""))
            if not code:
                continue
            row = advice_map.get(code, {}) or {}
            if not row:
                continue
            if not self._is_placeholder_text(row.get("reason", "")) and len(str(row.get("reason", ""))) >= 16:
                item["reason"] = row.get("reason", item.get("reason", ""))
            if not self._is_placeholder_text(row.get("confidence", "")):
                item["confidence"] = row.get("confidence", item.get("confidence", "中"))
            if not self._is_placeholder_text(row.get("buy_price", "")):
                item["buy_price"] = row.get("buy_price", item.get("buy_price", "待定"))
            if not self._is_placeholder_text(row.get("target_price", "")):
                item["target_price"] = row.get("target_price", item.get("target_price", "待定"))
            if not self._is_placeholder_text(row.get("hold_period", "")):
                item["hold_period"] = row.get("hold_period", item.get("hold_period", "短线"))
        return recommended_stocks
    
    def _extract_recommended_stocks(
        self,
        chief_analysis: str,
        stock_analysis: str,
        summary: Dict,
        candidate_stocks: Optional[List[Dict[str, Any]]] = None,
        p1_signal_data: Optional[Dict[str, Any]] = None,
        recommendation_count: int = 8,
        limit_up_ratio: float = 0.4,
        enable_quota: bool = True,
        excluded_codes: Optional[set] = None,
        latest_pct_map: Optional[Dict[str, float]] = None,
        mainboard_limit_like_pct: float = 6.0,
    ) -> List[Dict]:
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
        rec_count = max(3, min(int(recommendation_count or 8), 12))
        ratio = max(0.0, min(float(limit_up_ratio or 0.4), 1.0))
        excluded = {self._normalize_code(x) for x in (excluded_codes or set()) if self._normalize_code(x)}
        pct_map = latest_pct_map or {}
        clue_map = self._build_theme_clue_map_from_summary(summary or {})
        chief_table_map = self._extract_chief_table_map(chief_analysis)
        
        # 推荐池：优先按“首席AI推荐表顺序”取股；缺失时回退摘要TOP顺序
        candidate_stocks = list(candidate_stocks or []) or (summary.get("top_stocks") or [])
        stock_by_code: Dict[str, Dict[str, Any]] = {}
        summary_order_codes: List[str] = []
        for stock in candidate_stocks:
            code = self._normalize_code(stock.get("code", ""))
            if not code:
                continue
            if code not in stock_by_code:
                stock_by_code[code] = stock
                summary_order_codes.append(code)

        chief_order_codes = self._extract_chief_selected_codes(chief_analysis)
        ordered_codes = [c for c in chief_order_codes if c in stock_by_code]
        # 关键：即使首席先选了一批，也要把推荐池剩余候选补在后面，
        # 这样分层配额才能在“首席优先”的前提下补足非近涨停数量。
        tail_codes = [c for c in summary_order_codes if c not in set(ordered_codes)]
        ordered_codes = ordered_codes + tail_codes
        if not ordered_codes:
            ordered_codes = summary_order_codes

        if ordered_codes:
            for idx, code in enumerate(ordered_codes[:30], 1):
                stock = stock_by_code.get(code, {}) or {}
                if not code:
                    continue
                if not self._is_mainboard_code(code):
                    # 仅推荐沪深主板
                    continue
                if code in excluded:
                    # 跌停池股票不参与推荐
                    continue
                is_limit_up_real = self._is_today_limit_up(code, p1_signal_data)
                pct_chg = pct_map.get(code, None)
                is_limit_like_for_quota = self._is_limit_like_for_quota(
                    code=code,
                    name=stock.get("name", ""),
                    pct_chg=pct_chg,
                    mainboard_limit_like_pct=mainboard_limit_like_pct,
                )
                recommended.append({
                    'rank': idx,
                    'code': code,
                    'name': stock.get('name', ''),
                    'net_inflow': stock.get('net_inflow', 0.0),
                    'reason': "",
                    'confidence': '',
                    'buy_price': '',
                    'target_price': '',
                    'stop_loss': '待定',
                    'hold_period': '',
                    'is_today_limit_up': bool(is_limit_up_real),
                    'is_limit_like_for_quota': bool(is_limit_like_for_quota),
                    'latest_pct_chg': None if pct_chg is None else round(float(pct_chg), 2),
                })
                chief_row = chief_table_map.get(code, {}) or {}
                chief_reason = str(chief_row.get("reason", "") or "").strip()
                own_clues = (clue_map.get(code, []) or [])[:3]
                if chief_reason and (not self._is_placeholder_text(chief_reason)) and (not self._is_reason_too_brief(chief_reason)):
                    reason = chief_reason
                else:
                    net_amt = float(stock.get('net_inflow', 0.0) or 0.0)
                    if own_clues:
                        reason = (
                            f"该股当日净流入约{net_amt:,.2f}元，"
                            f"并与{ '、'.join(own_clues) }主线形成共振，"
                            "短线更关注次日承接与放量确认。"
                        )
                    else:
                        reason = (
                            f"该股当日净流入约{net_amt:,.2f}元，"
                            "资金承接尚可，若次日量价延续可继续跟踪。"
                        )
                recommended[-1]["reason"] = reason
                recommended[-1]["confidence"] = str(chief_row.get("confidence", "") or "中")
                recommended[-1]["buy_price"] = str(chief_row.get("buy_timing", "") or "待定")
                recommended[-1]["target_price"] = str(chief_row.get("trigger_condition", "") or "待定")
                recommended[-1]["hold_period"] = str(chief_row.get("hold_period", "") or "短线")
        if not bool(enable_quota):
            selected = recommended[:rec_count]
            for idx, item in enumerate(selected, 1):
                item["rank"] = idx
            return selected

        return self._apply_recommendation_quota(
            candidates=recommended,
            recommendation_count=rec_count,
            limit_up_ratio=ratio,
        )

    def _escape_md_cell(self, value: Any) -> str:
        return str(value or "").replace("|", "｜").replace("\n", " ").strip()

    def _build_chief_recommendation_table(self, recommended_stocks: List[Dict[str, Any]]) -> str:
        """
        基于后处理推荐池重建首席策略师“次日重点推荐股票”表，
        确保与配额规则结果一致。
        """
        header = (
            "| 序号 | 股票名称 | 股票代码 | 推荐理由（含题材线索） | 确定性评级 | 买入时机建议 | 交易触发条件 | 持有周期建议 |\n"
            "|---|---|---|---|---|---|---|---|"
        )
        rows = []
        for idx, item in enumerate(recommended_stocks or [], 1):
            rows.append(
                "| "
                + " | ".join(
                    [
                        str(idx),
                        self._escape_md_cell(item.get("name", "")),
                        self._escape_md_cell(item.get("code", "")),
                        self._escape_md_cell(item.get("reason", "题材线索不足（仅依据资金/形态）")),
                        self._escape_md_cell(item.get("confidence", "中")),
                        self._escape_md_cell(item.get("buy_price", "待定")),
                        self._escape_md_cell(item.get("trigger_condition", item.get("target_price", "待定"))),
                        self._escape_md_cell(item.get("hold_period", "短线")),
                    ]
                )
                + " |"
            )
        if not rows:
            rows.append("| 1 | 暂无 | - | 题材线索不足（仅依据资金/形态） | 低 | 待定 | 待定 | 观察 |")
        return header + "\n" + "\n".join(rows)

    def _replace_chief_recommendation_table_markdown(
        self,
        chief_analysis: str,
        table_markdown: str,
    ) -> str:
        """
        将首席文本中的“次日重点推荐股票”表格替换为给定Markdown表格。
        """
        text = str(chief_analysis or "").strip()
        table_md = str(table_markdown or "").strip()
        if not text or not table_md:
            return text

        lines = text.splitlines()
        n = len(lines)
        start = -1
        end = n
        for i, line in enumerate(lines):
            if "次日重点推荐股票" in line:
                start = i
                break
        if start == -1:
            return text + "\n\n## 次日重点推荐股票（TOP5-8）\n\n" + table_md

        for j in range(start + 1, n):
            if "高风险警示股票" in lines[j]:
                end = j
                break

        seg = lines[start:end]
        table_start = -1
        table_end = -1
        for k, line in enumerate(seg):
            if str(line).strip().startswith("|"):
                table_start = k
                table_end = k
                for t in range(k + 1, len(seg)):
                    if str(seg[t]).strip().startswith("|"):
                        table_end = t
                    else:
                        break
                break

        table_lines = table_md.splitlines()
        if table_start != -1:
            new_seg = seg[:table_start] + table_lines + seg[table_end + 1 :]
        else:
            new_seg = seg + [""] + table_lines

        new_lines = lines[:start] + new_seg + lines[end:]
        return "\n".join(new_lines)

    def _patch_chief_table_with_recommended_quota(
        self,
        chief_analysis: str,
        recommended_stocks: List[Dict[str, Any]],
        summary: Dict[str, Any],
        latest_pct_map: Dict[str, float],
        recommendation_count: int,
        limit_up_ratio: float,
        mainboard_limit_like_pct: float,
        enable_recommendation_quota: bool,
    ) -> Dict[str, Any]:
        """
        若首席表非近涨停数量不足，则按“系统推荐表”定向替换：
        - 仅替换缺口数量；
        - 替换来源=推荐表中排名靠前的非近涨停股票；
        - 替换目标=首席表尾部近涨停股票。
        """
        out = {
            "applied": False,
            "deficit_non_limit": 0,
            "replaced_count": 0,
            "replaced_from": [],
            "replaced_to": [],
            "patched_analysis": chief_analysis,
        }
        if not bool(enable_recommendation_quota):
            return out

        rec_count = max(3, min(int(recommendation_count or 8), 12))
        ratio = max(0.0, min(float(limit_up_ratio or 0.4), 1.0))
        max_limit_like = max(0, min(rec_count, int(rec_count * ratio + 1e-9)))
        min_non_limit_like = max(0, rec_count - max_limit_like)

        chief_codes = self._extract_chief_selected_codes(chief_analysis)[:rec_count]
        if not chief_codes:
            return out
        chief_table_map = self._extract_chief_table_map(chief_analysis)

        rec_by_code: Dict[str, Dict[str, Any]] = {}
        for row in (recommended_stocks or []):
            code = self._normalize_code(row.get("code", ""))
            if code and code not in rec_by_code:
                rec_by_code[code] = row

        name_map = {
            self._normalize_code(x.get("code", "")): str(x.get("name", "") or "")
            for x in (summary.get("top_stocks", []) or [])
            if self._normalize_code(x.get("code", ""))
        }

        def _is_limit_like(code: str) -> bool:
            rec_row = rec_by_code.get(code, {})
            if rec_row:
                return bool(rec_row.get("is_limit_like_for_quota", False))
            return self._is_limit_like_for_quota(
                code=code,
                name=name_map.get(code, ""),
                pct_chg=latest_pct_map.get(code, None),
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )

        chief_non_limit_cnt = sum(1 for c in chief_codes if not _is_limit_like(c))
        deficit = max(0, min_non_limit_like - chief_non_limit_cnt)
        out["deficit_non_limit"] = int(deficit)
        if deficit <= 0:
            return out

        replacement_pool = []
        for row in (recommended_stocks or []):
            code = self._normalize_code(row.get("code", ""))
            if not code or code in chief_codes:
                continue
            if bool(row.get("is_limit_like_for_quota", False)):
                continue
            replacement_pool.append(code)
            if len(replacement_pool) >= deficit:
                break
        if not replacement_pool:
            return out

        target_indices = [i for i, code in enumerate(chief_codes) if _is_limit_like(code)]
        if not target_indices:
            return out
        # 从尾部开始替换，尽量保留首席前排排序意图
        target_indices = list(reversed(target_indices))
        replace_n = min(deficit, len(replacement_pool), len(target_indices))
        if replace_n <= 0:
            return out

        replaced_from = []
        replaced_to = []
        patched_codes = list(chief_codes)
        for i in range(replace_n):
            tgt_idx = target_indices[i]
            old_code = patched_codes[tgt_idx]
            new_code = replacement_pool[i]
            patched_codes[tgt_idx] = new_code
            replaced_from.append(old_code)
            replaced_to.append(new_code)

        # 生成回填表：优先使用推荐表字段，缺失时兜底首席原字段
        rebuilt_rows: List[Dict[str, Any]] = []
        for rank, code in enumerate(patched_codes, 1):
            rec_row = rec_by_code.get(code, {})
            chief_row = chief_table_map.get(code, {})
            rebuilt_rows.append(
                {
                    "rank": rank,
                    "code": code,
                    "name": str(rec_row.get("name", "") or name_map.get(code, "") or ""),
                    "reason": str(
                        rec_row.get("reason", "")
                        or chief_row.get("reason", "")
                        or "资金与题材信号共振，关注次日承接"
                    ),
                    "confidence": str(rec_row.get("confidence", "") or chief_row.get("confidence", "") or "中"),
                    "buy_price": str(rec_row.get("buy_price", "") or chief_row.get("buy_timing", "") or "分歧低吸/回踩确认"),
                    "target_price": str(
                        rec_row.get("target_price", "")
                        or chief_row.get("trigger_condition", "")
                        or "放量突破前高确认"
                    ),
                    "hold_period": str(rec_row.get("hold_period", "") or chief_row.get("hold_period", "") or "短线"),
                }
            )

        new_table = self._build_chief_recommendation_table(rebuilt_rows)
        out["patched_analysis"] = self._replace_chief_recommendation_table_markdown(
            chief_analysis=chief_analysis,
            table_markdown=new_table,
        )
        out["applied"] = True
        out["replaced_count"] = int(replace_n)
        out["replaced_from"] = replaced_from
        out["replaced_to"] = replaced_to
        return out

    def _enforce_chief_recommendation_table(
        self,
        chief_analysis: str,
        recommended_stocks: List[Dict[str, Any]],
    ) -> str:
        """
        二次校验并强制覆盖首席策略师文本中的“次日重点推荐股票”表格，
        避免AI文本推荐与最终配额推荐不一致。
        """
        text = str(chief_analysis or "").strip()
        if not text:
            return text
        table_md = self._build_chief_recommendation_table(recommended_stocks or [])

        lines = text.splitlines()
        n = len(lines)
        start = -1
        end = n
        for i, line in enumerate(lines):
            if "次日重点推荐股票" in line:
                start = i
                break
        if start == -1:
            # 若未命中标题，则追加一个标准区块
            return text + "\n\n## 次日重点推荐股票（TOP5-8）\n\n" + table_md

        for j in range(start + 1, n):
            if "高风险警示股票" in lines[j]:
                end = j
                break

        seg = lines[start:end]
        table_start = -1
        table_end = -1
        for k, line in enumerate(seg):
            if str(line).strip().startswith("|"):
                table_start = k
                table_end = k
                for t in range(k + 1, len(seg)):
                    if str(seg[t]).strip().startswith("|"):
                        table_end = t
                    else:
                        break
                break

        table_lines = table_md.splitlines()
        if table_start != -1:
            new_seg = seg[:table_start] + table_lines + seg[table_end + 1 :]
        else:
            new_seg = seg + [""] + table_lines

        new_lines = lines[:start] + new_seg + lines[end:]
        return "\n".join(new_lines)

    def _append_system_quota_note(
        self,
        chief_analysis: str,
        recommended_stocks: List[Dict[str, Any]],
    ) -> str:
        """
        追加“系统口径校验说明”，避免AI文本中的近涨停/非近涨停分组与真实计算不一致。
        """
        text = str(chief_analysis or "").strip()
        limit_like = []
        non_limit_like = []
        for x in (recommended_stocks or []):
            name = str(x.get("name", "") or "").strip()
            if not name:
                continue
            if bool(x.get("is_limit_like_for_quota", False)):
                limit_like.append(name)
            else:
                non_limit_like.append(name)
        note = (
            "\n\n### 系统口径校验说明\n"
            f"- 近涨停标签（{len(limit_like)}只）："
            + ("、".join(limit_like) if limit_like else "无")
            + "\n"
            f"- 非近涨停标签（{len(non_limit_like)}只）："
            + ("、".join(non_limit_like) if non_limit_like else "无")
            + "\n"
            "- 以上分组基于后端实时计算字段 `is_limit_like_for_quota`，优先级高于AI自由文本描述。"
        )
        if not text:
            return note.strip()
        return text + note

    def _enforce_system_quota_explanation(
        self,
        chief_analysis: str,
        recommended_stocks: List[Dict[str, Any]],
    ) -> str:
        """
        强制校正首席文本中的“推荐说明”配额描述：
        - 删除AI可能写错的“前X只/后X只”分组文案
        - 注入系统实时计算的分组结果
        """
        text = str(chief_analysis or "").strip()
        if not text:
            return text

        limit_like_names = []
        non_limit_like_names = []
        for x in (recommended_stocks or []):
            name = str(x.get("name", "") or "").strip()
            if not name:
                continue
            if bool(x.get("is_limit_like_for_quota", False)):
                limit_like_names.append(name)
            else:
                non_limit_like_names.append(name)

        system_lines = [
            "推荐说明（系统校验）：",
            f"- 近涨停标签股票（{len(limit_like_names)}只）："
            + ("、".join(limit_like_names) if limit_like_names else "无"),
            f"- 非近涨停标签股票（{len(non_limit_like_names)}只）："
            + ("、".join(non_limit_like_names) if non_limit_like_names else "无"),
            "- 上述分组以 `is_limit_like_for_quota` 实时计算为准。",
        ]
        system_block = "\n".join(system_lines)

        lines = text.splitlines()
        filtered = []
        for line in lines:
            s = str(line or "").strip()
            # 清理AI容易写错的分层说明行
            if ("近涨停标签股票" in s or "非近涨停股票" in s) and ("前" in s or "后" in s):
                continue
            filtered.append(line)

        return "\n".join(filtered) + "\n\n" + system_block

    def _is_today_limit_up(self, code: str, p1_signal_data: Optional[Dict[str, Any]] = None) -> bool:
        c = self._normalize_code(code)
        if not c:
            return False
        if isinstance(p1_signal_data, dict) and p1_signal_data.get("data_success"):
            smap = (p1_signal_data.get("stock_signal_map", {}) or {})
            detail = smap.get(c, {}) or {}
            return bool(detail.get("today_limit_up_hit", False))
        return False

    def _apply_recommendation_quota(
        self,
        candidates: List[Dict[str, Any]],
        recommendation_count: int = 8,
        limit_up_ratio: float = 0.4,
    ) -> List[Dict[str, Any]]:
        """推荐阶段分层配额：限制当日涨停票占比，优先补充非涨停候选。"""
        if not candidates:
            return []

        total_n = max(1, min(int(recommendation_count or 8), len(candidates)))
        ratio = max(0.0, min(float(limit_up_ratio or 0.4), 1.0))
        max_limit_up = max(0, min(total_n, int(total_n * ratio + 1e-9)))

        # 分层配额仅使用“近涨停标签”而非真实涨停标记，避免影响真实业务口径
        limit_pool = [x for x in candidates if bool(x.get("is_limit_like_for_quota", False))]
        non_limit_pool = [x for x in candidates if not bool(x.get("is_limit_like_for_quota", False))]

        selected: List[Dict[str, Any]] = []
        selected.extend(non_limit_pool[:total_n - max_limit_up])
        selected.extend(limit_pool[:max_limit_up])

        if len(selected) < total_n:
            remain_limit = limit_pool[max_limit_up:]
            remain_non_limit = non_limit_pool[max(0, total_n - max_limit_up):]
            for item in (remain_non_limit + remain_limit):
                if len(selected) >= total_n:
                    break
                selected.append(item)

        # 二次约束：真实涨停（today_limit_up_hit）也尽量遵守同一配额比例。
        # 若可替换的非真实涨停候选不足，则保留现状以保证推荐数量。
        selected_codes = {
            self._normalize_code(x.get("code", ""))
            for x in selected
            if self._normalize_code(x.get("code", ""))
        }
        replacement_pool = [
            x for x in candidates
            if not bool(x.get("is_today_limit_up", False))
            and self._normalize_code(x.get("code", "")) not in selected_codes
        ]
        real_limit_cnt = sum(1 for x in selected if bool(x.get("is_today_limit_up", False)))
        replace_idx = 0
        if real_limit_cnt > max_limit_up and replacement_pool:
            for i, item in enumerate(selected):
                if real_limit_cnt <= max_limit_up or replace_idx >= len(replacement_pool):
                    break
                if bool(item.get("is_today_limit_up", False)):
                    selected[i] = replacement_pool[replace_idx]
                    replace_idx += 1
                    real_limit_cnt -= 1

        code_order = {self._normalize_code(x.get("code", "")): idx for idx, x in enumerate(candidates)}
        selected.sort(key=lambda x: code_order.get(self._normalize_code(x.get("code", "")), 9999))
        for idx, item in enumerate(selected, 1):
            item["rank"] = idx
        return selected

    def _build_latest_pct_change_map(self, data_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        从现有记录提取“最新交易日”的涨跌幅映射，供推荐阶段配额分层使用。
        该映射是推荐筛选标签，不代表真实涨停池状态。
        """
        latest_date_by_code: Dict[str, str] = {}
        best_rank_by_code: Dict[str, int] = {}
        pct_by_code: Dict[str, float] = {}
        for row in data_list or []:
            code = self._normalize_code(row.get("股票代码") or row.get("gpdm") or "")
            if not code:
                continue
            raw_pct = row.get("pct_chg", None)
            if raw_pct is None or raw_pct == "":
                continue
            try:
                pct = float(raw_pct)
            except Exception:
                continue
            raw_date = str(row.get("交易日期") or row.get("rq") or "").strip()
            date_key = self._to_compact_trade_date(raw_date) or "00000000"
            old_date = latest_date_by_code.get(code, "")
            old_rank = best_rank_by_code.get(code, 999)
            cur_rank = self._get_pct_source_rank(row)
            # 规则：
            # 1) 更晚交易日覆盖更早交易日
            # 2) 同交易日内，按来源优先级选择：top_list > limit_list_ths > kpl_list > 其它
            # 3) 同交易日同优先级时，才取绝对值更大者
            should_update = False
            if not old_date or date_key > old_date:
                should_update = True
            elif date_key == old_date:
                if cur_rank < old_rank:
                    should_update = True
                elif cur_rank == old_rank and abs(pct) > abs(float(pct_by_code.get(code, 0.0) or 0.0)):
                    should_update = True
            if should_update:
                latest_date_by_code[code] = date_key
                best_rank_by_code[code] = cur_rank
                pct_by_code[code] = pct
        return pct_by_code

    def _is_limit_like_for_quota(
        self,
        code: str,
        name: str,
        pct_chg: Optional[float],
        mainboard_limit_like_pct: float = 6.0,
    ) -> bool:
        if pct_chg is None:
            return False
        c = self._normalize_code(code)
        if not c:
            return False
        nm = str(name or "").upper()
        if "ST" in nm or "*ST" in nm:
            threshold = 4.8
        elif c.startswith(("300", "301", "688")):
            threshold = 19.5
        elif c.startswith(("4", "8")):
            threshold = 29.5
        else:
            threshold = max(1.0, min(float(mainboard_limit_like_pct or 6.0), 10.0))
        return float(pct_chg) >= threshold

    def _collect_drop_limit_pool_codes(
        self,
        data_list: List[Dict[str, Any]],
        target_trade_date: Optional[str] = None,
    ) -> set:
        """提取“跌停池”来源股票代码，推荐阶段强制剔除。"""
        out = set()
        target_compact = self._to_compact_trade_date(str(target_trade_date or "").strip())
        for row in data_list or []:
            code = self._normalize_code(row.get("股票代码") or row.get("gpdm") or "")
            if not code:
                continue
            if target_compact:
                raw_date = str(
                    row.get("交易日期")
                    or row.get("rq")
                    or row.get("trade_date")
                    or row.get("日期")
                    or ""
                ).strip()
                row_compact = self._to_compact_trade_date(raw_date)
                if row_compact != target_compact:
                    continue
            tag = str(row.get("tag", "") or "").strip()
            limit_types = str(row.get("limit_types", "") or "").strip()
            source = str(row.get("data_source", "") or "").strip().lower()
            sblx = str(row.get("上榜类型", "") or row.get("sblx", "") or "").strip()
            status = str(row.get("status", "") or "").strip()
            is_drop_pool = (
                ("跌停" in tag)
                or ("跌停池" in limit_types)
                or ("跌停池" in sblx)
                or ("跌停" in status and "涨停" not in status)
            )
            if is_drop_pool and ("limit_list_ths" in source or "同花顺涨停池" in sblx):
                out.add(code)
        return out
    
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

    def get_concept_strength_candidates(
        self,
        date: Optional[str] = None,
        days: int = 3,
        score_pool_source: str = "lhb_kpl",
    ) -> Dict[str, Any]:
        """
        预加载题材强度候选（用于前端主观主线多选）：
        - 复用与主流程一致的题材强度计算链路
        - 仅返回候选与题材数据，不触发AI分析
        """
        out: Dict[str, Any] = {
            "data_success": False,
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trade_date": date or datetime.now().strftime("%Y-%m-%d"),
            "candidates": [],
            "concept_rotation": {},
            "error": "",
        }
        try:
            target_trade_date = date
            if date:
                try:
                    d8 = self.trade_calendar.normalize_or_raise(date)
                    target_trade_date = f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
                    out["trade_date"] = target_trade_date
                except TradeCalendarError as e:
                    out["error"] = str(e)
                    out["error_code"] = e.code
                    out["nearest_open_day"] = e.nearest_open_day
                    self.logger.warning(
                        "[TradeCal] invalid_trade_date | preview_date=%s | nearest=%s",
                        date,
                        e.nearest_open_day,
                    )
                    return out
            else:
                latest = self.trade_calendar.recent_open_days(datetime.now().strftime("%Y%m%d"), 1)
                if latest:
                    d8 = latest[0]
                    target_trade_date = f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
                    out["trade_date"] = target_trade_date

            effective_days = 1 if date else max(int(days or 0), 3)
            if date:
                data_list = self.data_fetcher.get_recent_days_data(days=effective_days, end_date=target_trade_date)
            else:
                data_list = self.data_fetcher.get_recent_days_data(days=effective_days)

            data_list = self.data_fetcher.prioritize_latest_stock_records(data_list)
            data_list = self._filter_untradable_boards(data_list)
            if not data_list:
                out["error"] = "no_lhb_data"
                return out
            # 预览口径：保留原始题材词频统计，供候选“出现次数”对齐展示
            preview_summary = self.data_fetcher.analyze_data_summary(data_list)
            source_priority_rows = (preview_summary.get("theme_source_priority", []) or [])

            source_mode = str(score_pool_source or "lhb_kpl").strip().lower()
            if source_mode not in {"lhb_only", "lhb_limit", "lhb_kpl"}:
                source_mode = "lhb_kpl"
            pool_date = target_trade_date if target_trade_date else datetime.now().strftime("%Y-%m-%d")

            def _append_new_codes(extra_records: List[Dict[str, Any]]) -> int:
                existing_codes = {
                    self._normalize_code(x.get("股票代码") or x.get("gpdm") or "")
                    for x in (data_list or [])
                }
                existing_codes.discard("")
                added = 0
                for rec in (extra_records or []):
                    code = self._normalize_code(rec.get("gpdm") or rec.get("股票代码") or "")
                    if not code or code in existing_codes:
                        continue
                    data_list.append(rec)
                    existing_codes.add(code)
                    added += 1
                return added

            if source_mode == "lhb_limit":
                limit_pool = self._fetch_limit_list_ths_all_types(pool_date)
                if limit_pool.get("data_success"):
                    _append_new_codes(limit_pool.get("records", []) or [])
            elif source_mode == "lhb_kpl":
                kpl_pool = self._fetch_kpl_list_all_types(pool_date)
                if kpl_pool.get("data_success"):
                    _append_new_codes(kpl_pool.get("records", []) or [])
                else:
                    limit_pool = self._fetch_limit_list_ths_all_types(pool_date)
                    if limit_pool.get("data_success"):
                        _append_new_codes(limit_pool.get("records", []) or [])

            stock_codes = self._extract_stock_codes(data_list)
            kpl_list_data = self._safe_fetch_with_timeout(
                task_name="kpl_list_preview",
                timeout_sec=300,
                func=lambda: self.kpl_list_fetcher.get_heat_data(
                    days=1 if date else max(min(days, 5), 3),
                    end_date=target_trade_date,
                    stock_codes=stock_codes,
                    exact_trade_date_only=bool(date),
                ),
                default={"data_success": False, "source": "tushare.kpl_list", "error": "kpl_list_preview_skipped"},
            )
            concept_trade_date = target_trade_date if target_trade_date else datetime.now().strftime("%Y-%m-%d")
            concept_rotation = self._build_concept_rotation_fusion(
                kpl_list_data=kpl_list_data,
                end_date=concept_trade_date,
                stock_codes=stock_codes,
                days_hint=1 if date else max(int(days or 0), 1),
            )
            if not concept_rotation.get("data_success"):
                out["error"] = "concept_rotation_unavailable"
                out["concept_rotation"] = concept_rotation
                return out

            candidates = self._extract_concept_strength_candidates(concept_rotation)
            # 对齐“出现次数”口径：优先使用 source_priority 的原始词频计数
            if candidates and source_priority_rows:
                count_map: Dict[str, int] = {}
                for x in source_priority_rows:
                    theme = str(x.get("theme", "") or "").strip()
                    if not theme:
                        continue
                    key = self._normalize_theme_alias(theme)
                    if not key:
                        continue
                    cnt = int(x.get("count", 0) or 0)
                    if cnt > int(count_map.get(key, 0) or 0):
                        count_map[key] = cnt
                for row in candidates:
                    concept = str(row.get("concept", "") or "").strip()
                    if not concept:
                        continue
                    key = self._normalize_theme_alias(concept)
                    raw_cnt = int(count_map.get(key, 0) or 0)
                    if raw_cnt > 0:
                        row["count"] = raw_cnt
                        row["raw_count"] = raw_cnt
            out["data_success"] = bool(candidates)
            out["candidates"] = candidates
            out["concept_rotation"] = concept_rotation
            out["summary_preview"] = {
                "theme_source_priority_top20": source_priority_rows[:20],
            }
            if not out["data_success"]:
                out["error"] = "no_candidates"
            return out
        except Exception as e:
            self.logger.exception(f"预加载题材强度候选失败: {e}", exc_info=True)
            out["error"] = str(e)
            return out
    
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
