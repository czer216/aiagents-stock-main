"""
龙虎榜历史统计驱动服务（2026）
用途：
1) 将 2026 年内信号池股票（日快照）增量入库
2) 生成按交易日的题材强度聚合
3) 为当日候选池提供"同类低位补涨"统计参考
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from data_source_manager import data_source_manager
from longhubang_data import LonghubangDataFetcher
from tushare_proxy_rate_limit import call_tushare_with_timeout
from trade_calendar_service import TradeCalendarService, TradeCalendarError


class LonghubangHistoryService:
    """2026 历史统计驱动服务。"""

    def __init__(self, db_path: str = "longhubang.db"):
        self.db_path = db_path
        self.data_fetcher = LonghubangDataFetcher()
        self.trade_calendar = TradeCalendarService()
        self.logger = logging.getLogger(__name__)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        # 并发写入场景下提升可用性（读写并发、降低锁冲突）
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        return conn

    def _init_tables(self) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_daily_snapshot_2026 (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                pct_chg REAL,
                close REAL,
                vol REAL,
                amount REAL,
                is_limit_up INTEGER DEFAULT 0,
                is_limit_down INTEGER DEFAULT 0,
                is_lhb INTEGER DEFAULT 0,
                themes TEXT,
                source_flags TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, stock_code)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_stock_theme_map_2026 (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                theme TEXT NOT NULL,
                PRIMARY KEY (trade_date, stock_code, theme)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_theme_daily_2026 (
                trade_date TEXT NOT NULL,
                theme TEXT NOT NULL,
                stock_count INTEGER DEFAULT 0,
                limit_up_count INTEGER DEFAULT 0,
                avg_pct_chg REAL DEFAULT 0,
                strength_score REAL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, theme)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lhb_snapshot_2026_trade_date
            ON longhubang_daily_snapshot_2026(trade_date)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lhb_theme_map_2026_theme_date
            ON longhubang_stock_theme_map_2026(theme, trade_date)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lhb_theme_daily_2026_trade_date
            ON longhubang_theme_daily_2026(trade_date)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_cooccur_group_2026 (
                group_id TEXT PRIMARY KEY,
                trade_date TEXT NOT NULL,
                theme TEXT NOT NULL,
                member_count INTEGER DEFAULT 0,
                avg_pct_chg REAL DEFAULT 0,
                limit_up_count INTEGER DEFAULT 0,
                group_strength REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cooccur_group_trade_date
            ON longhubang_cooccur_group_2026(trade_date)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cooccur_group_theme
            ON longhubang_cooccur_group_2026(theme)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_cooccur_member_2026 (
                group_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                pct_chg REAL DEFAULT 0,
                is_limit_up INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, stock_code),
                FOREIGN KEY (group_id) REFERENCES longhubang_cooccur_group_2026(group_id)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cooccur_member_stock_code
            ON longhubang_cooccur_member_2026(stock_code)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS longhubang_group_performance_2026 (
                group_id TEXT PRIMARY KEY,
                next_1d_avg REAL DEFAULT 0,
                next_3d_avg REAL DEFAULT 0,
                next_5d_avg REAL DEFAULT 0,
                max_drawdown_5d REAL DEFAULT 0,
                win_rate_1d REAL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES longhubang_cooccur_group_2026(group_id)
            )
            """
        )
        conn.commit()
        conn.close()

    def sync_2026_history_until(
        self,
        end_date: str,
        max_days_per_run: int = 8,
        max_workers: int = 10,
    ) -> Dict[str, Any]:
        """
        增量同步 2026 年数据到指定日期（含）。
        注意：单次最多补 max_days_per_run 个交易日，避免一次分析阻塞过久。
        """
        end_compact = self._to_date8(end_date)
        if not end_compact:
            return {"data_success": False, "error": "invalid_end_date"}
        if end_compact < "20260101":
            return {"data_success": False, "error": "end_date_before_2026"}
        try:
            self.trade_calendar.normalize_or_raise(end_compact)
        except TradeCalendarError as e:
            return {
                "data_success": False,
                "error": str(e),
                "error_code": e.code,
                "nearest_open_day": e.nearest_open_day,
            }

        trade_dates = self.trade_calendar.open_days_between("20260101", end_compact)
        if not trade_dates:
            return {"data_success": True, "updated_days": 0, "skipped_days": 0}

        existing = self._get_existing_trade_dates()
        missing = [d for d in trade_dates if d not in existing]
        if max_days_per_run > 0 and len(missing) > max_days_per_run:
            missing = missing[: max(int(max_days_per_run), 1)]

        updated_days = 0
        updated_stocks = 0
        failed_days: List[str] = []
        if missing:
            self.logger.info(
                "[历史驱动] 并发同步启动 | missing_days=%s | workers=%s",
                len(missing),
                max(int(max_workers or 10), 1),
            )

        def _run_one(date8: str) -> Tuple[str, Dict[str, Any]]:
            date_dash = self._fmt_date(date8)
            self.logger.info("[历史驱动] 同步中 | trade_date=%s", date_dash)
            retry = 0
            while retry < 3:
                try:
                    day_result = self._sync_single_day(date_dash)
                    return date_dash, day_result
                except Exception as e:
                    text = str(e)
                    retry += 1
                    if "database is locked" in text.lower() and retry < 3:
                        time.sleep(0.2 * retry)
                        continue
                    return date_dash, {"data_success": False, "error": text}
            return date_dash, {"data_success": False, "error": "unknown_error"}

        workers = max(1, min(int(max_workers or 10), 10))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, date8) for date8 in missing]
            for fut in as_completed(futures):
                date_dash, day_result = fut.result()
                if not day_result.get("data_success", False):
                    failed_days.append(date_dash)
                    self.logger.warning(
                        "[历史驱动] 单日同步失败 | trade_date=%s | err=%s",
                        date_dash,
                        str(day_result.get("error", "")),
                    )
                    continue
                updated_days += 1
                updated_stocks += int(day_result.get("stock_count", 0) or 0)

        latest_date = self._get_latest_trade_date()
        return {
            "data_success": True,
            "updated_days": int(updated_days),
            "updated_stocks": int(updated_stocks),
            "skipped_days": int(len(trade_dates) - len(missing)),
            "pending_days": int(max(len([d for d in trade_dates if d not in self._get_existing_trade_dates()]), 0)),
            "latest_trade_date": self._fmt_date(latest_date) if latest_date else "",
            "failed_days": failed_days[:20],
            "missing_days_requested": int(len(missing)),
        }

    def build_peer_rebound_candidates(
        self,
        trade_date: str,
        candidate_stocks: List[Dict[str, Any]],
        active_themes: List[str],
        mainboard_limit_like_pct: float = 6.0,
        top_n: int = 12,
        excluded_codes: Optional[Set[str]] = None,
        max_expand: int = 120,
        concept_flow_counter: Optional[Counter] = None,
        industry_flow_counter: Optional[Counter] = None,
        use_cooccur_groups: bool = True,
        diversify_themes: bool = True,
        max_per_theme_ratio: float = 0.5,
    ) -> Dict[str, Any]:
        """
        基于历史统计，给当前候选池打"同类低位补涨"参考分。
        新增：use_cooccur_groups=True 时，利用历史共现群组增强推荐
        新增：diversify_themes=True 时，题材均匀化选择
        """
        date8 = self._to_date8(trade_date)
        if not date8:
            return {"data_success": False, "error": "invalid_trade_date"}
        if not candidate_stocks:
            return {"data_success": False, "error": "empty_candidates"}

        active = [x for x in (active_themes or []) if str(x or "").strip()]
        active_keys = [self._norm_theme_key(x) for x in active]
        active_weight: Dict[str, float] = {}
        for idx, key in enumerate(active_keys):
            if not key:
                continue
            # 越靠前权重越高
            active_weight[key] = max(0.35, 1.0 - idx * 0.18)

        pool_ctx = self._build_peer_candidate_pool(
            date8=date8,
            candidate_stocks=candidate_stocks,
            active_themes=active,
            excluded_codes=excluded_codes or set(),
            max_expand=max_expand,
            mainboard_limit_like_pct=mainboard_limit_like_pct,
        )
        peer_candidate_pool = pool_ctx.get("pool", []) or []
        if not peer_candidate_pool:
            return {
                "data_success": False,
                "error": "empty_peer_candidate_pool",
                "trade_date": self._fmt_date(date8),
                "active_themes": active[:10],
                "pool_source_breakdown": pool_ctx.get("pool_source_breakdown", {}),
                "expanded_codes_sample": pool_ctx.get("expanded_codes_sample", []),
                "dropped_no_daily_count": int(pool_ctx.get("dropped_no_daily_count", 0) or 0),
            }

        hist_theme_stats = self._get_theme_historical_stats(before_date=date8, themes=active)
        theme_today_strength = self._get_theme_today_strength(date8=date8, themes=active)

        # 查询共现历史（如果启用）
        cooccur_map: Dict[str, Dict[str, Any]] = {}
        if use_cooccur_groups:
            cooccur_map = self._query_cooccur_history(
                candidate_stocks=peer_candidate_pool,
                active_themes=active,
                before_date=date8,
            )

        # 收集历史收益路径特征（批量）
        return_profile_map: Dict[str, Dict[str, Any]] = {}
        for stock in (peer_candidate_pool or []):
            code = self._normalize_code(stock.get("code", ""))
            if not code:
                continue
            is_expanded = bool(stock.get("is_peer_pool_expanded", False))
            if is_expanded:
                profile = self._calc_post_lhb_return_profile(code=code, lhb_date=date8, lookback_days=60)
                if profile.get("data_available"):
                    return_profile_map[code] = profile

        rows: List[Dict[str, Any]] = []
        peer_score_map: Dict[str, float] = {}
        hist_return_5d_sum = 0.0
        hist_max_dd_sum = 0.0
        hist_return_samples = 0
        for stock in (peer_candidate_pool or []):
            code = self._normalize_code(stock.get("code", ""))
            if not code:
                continue
            name = str(stock.get("name", "") or "").strip()
            pct_raw = stock.get("pct_chg", None)
            pct = self._safe_float(pct_raw)
            is_limit_like = bool(stock.get("is_limit_like_for_quota", False))
            if pct_raw is not None:
                try:
                    is_limit_like = bool(stock.get("is_limit_like_for_quota", False))
                except Exception:
                    is_limit_like = False
            themes = self._split_theme_tokens(stock.get("theme_tokens", ""))
            if not themes:
                continue
            matched = []
            for t in themes:
                t_key = self._norm_theme_key(t)
                if not t_key:
                    continue
                # 只考虑主线相关题材
                if active_weight and t_key not in active_weight:
                    continue
                matched.append(t)
            if not matched:
                continue

            # 收益路径特征
            profile = return_profile_map.get(code, {})
            if profile:
                hist_return_5d_sum += float(profile.get("return_5d", 0.0) or 0.0)
                hist_max_dd_sum += abs(float(profile.get("max_drawdown_10d", 0.0) or 0.0))
                hist_return_samples += 1

            hist_win = 0.0
            hist_next = 0.0
            today_strength = 0.0
            match_weight = 0.0
            for t in matched:
                key = self._norm_theme_key(t)
                st = hist_theme_stats.get(key, {})
                td = float(theme_today_strength.get(key, 0.0) or 0.0)
                wt = float(active_weight.get(key, 0.55) or 0.55)
                hist_win = max(hist_win, float(st.get("win_rate", 0.0) or 0.0))
                hist_next = max(hist_next, float(st.get("avg_next_pct", 0.0) or 0.0))
                today_strength = max(today_strength, td)
                match_weight = max(match_weight, wt)

            low_pos = 0.0
            theme_diffusion_strength = self._calc_theme_diffusion_strength(
                matched_themes=matched,
                concept_flow_counter=concept_flow_counter or Counter(),
                industry_flow_counter=industry_flow_counter or Counter(),
            )
            low_anchor_pct = self._calc_low_position_anchor_pct(
                base_threshold_pct=float(mainboard_limit_like_pct),
                theme_strength_today=float(today_strength),
                theme_diffusion_strength=float(theme_diffusion_strength),
            )
            if pct < low_anchor_pct:
                low_pos = (low_anchor_pct - pct) / max(low_anchor_pct, 1.0)
                low_pos = max(0.0, min(low_pos, 1.0))
            next_scale = max(min((hist_next + 2.0) / 8.0, 1.0), 0.0)
            high_zone_penalty, high_zone_reason = self._calc_high_zone_penalty(
                pct_chg=pct,
                low_anchor_pct=float(low_anchor_pct),
                stock=stock,
                is_limit_like=bool(is_limit_like),
            )
            hist_samples = int(self._calc_hist_samples_for_matched_themes(matched, hist_theme_stats))
            # 计算平均收益路径特征用于置信度
            hist_return_5d_avg = (hist_return_5d_sum / max(hist_return_samples, 1)) if hist_return_samples > 0 else 0.0
            hist_max_dd_avg = (hist_max_dd_sum / max(hist_return_samples, 1)) if hist_return_samples > 0 else 0.0
            confidence_factor = self._calc_peer_confidence_factor(
                stock=stock,
                hist_samples=hist_samples,
                today_strength=float(today_strength),
                match_weight=float(match_weight),
                hist_return_5d_avg=float(hist_return_5d_avg),
                hist_max_dd_avg=float(hist_max_dd_avg),
            )
            score = (
                45.0 * today_strength
                + 28.0 * hist_win
                + 22.0 * low_pos
                + 5.0 * next_scale
            ) * max(match_weight, 0.45)
            score = score * confidence_factor - high_zone_penalty

            # 共现增强
            cooccur_score = 0.0
            cooccur_frequency = 0
            cooccur_win_rate = 0.0
            cooccur_avg_return = 0.0
            if code in cooccur_map:
                cooccur_data = cooccur_map[code]
                cooccur_frequency = int(cooccur_data.get("frequency", 0))
                cooccur_win_rate = float(cooccur_data.get("win_rate", 0.0))
                cooccur_avg_return = float(cooccur_data.get("avg_return", 0.0))

                # 计算共现强度分
                freq_norm = min(cooccur_frequency / 5.0, 1.0)
                return_norm = max(min((cooccur_avg_return + 2.0) / 8.0, 1.0), 0.0)
                cooccur_score = 0.4 * freq_norm + 0.3 * cooccur_win_rate + 0.3 * return_norm
                cooccur_score = cooccur_score * 100.0

                # 融合共现分到最终得分
                score = 0.7 * score + 0.3 * cooccur_score

            if is_limit_like:
                score *= 0.72
            score = round(max(min(score, 100.0), 0.0), 2)
            if score <= 0:
                continue
            peer_score_map[code] = score
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "pct_chg": round(pct, 2),
                    "is_peer_pool_expanded": bool(stock.get("is_peer_pool_expanded", False)),
                    "is_limit_like_for_quota": bool(is_limit_like),
                    "theme_tokens": "、".join(matched[:4]),
                    "hist_win_rate": round(hist_win, 3),
                    "hist_avg_next_pct": round(hist_next, 3),
                    "hist_samples": int(hist_samples),
                    "hist_return_1d_avg": round(hist_return_5d_avg, 2),
                    "hist_return_5d_avg": round(hist_return_5d_avg, 2),
                    "hist_max_dd_avg": round(hist_max_dd_avg, 2),
                    "theme_strength_today": round(today_strength, 3),
                    "theme_diffusion_strength": round(theme_diffusion_strength, 3),
                    "low_position_anchor_pct": round(float(low_anchor_pct), 2),
                    "low_position_score": round(low_pos, 3),
                    "peer_confidence_factor": round(float(confidence_factor), 3),
                    "high_zone_penalty": round(float(high_zone_penalty), 2),
                    "high_zone_reason": str(high_zone_reason or ""),
                    "cooccur_frequency": cooccur_frequency,
                    "cooccur_win_rate": round(cooccur_win_rate, 3),
                    "cooccur_avg_return": round(cooccur_avg_return, 2),
                    "peer_rebound_score": score,
                }
            )

        rows.sort(
            key=lambda x: (
                float(x.get("peer_rebound_score", 0.0) or 0.0),
                float(x.get("low_position_score", 0.0) or 0.0),
                -abs(float(x.get("pct_chg", 0.0) or 0.0)),
            ),
            reverse=True,
        )

        # 题材均匀化选择
        if diversify_themes:
            final_candidates = self._diversify_candidates_by_theme(
                candidates=rows,
                top_n=int(top_n or 12),
                max_per_theme_ratio=float(max_per_theme_ratio),
            )
        else:
            final_candidates = rows[: max(1, int(top_n or 12))]

        top_codes = [x.get("code", "") for x in final_candidates[:6] if x.get("code")]
        self.logger.info(
            "[历史补涨] 评分完成%s | active_themes=%s | pool=%s | scored=%s | selected=%s | top_codes=%s",
            "（题材均匀化）" if diversify_themes else "",
            "、".join(active[:6]) if active else "-",
            len(peer_candidate_pool),
            len(rows),
            len(final_candidates),
            ",".join(top_codes) if top_codes else "-",
        )
        return {
            "data_success": bool(final_candidates),
            "trade_date": self._fmt_date(date8),
            "active_themes": active[:10],
            "candidates": final_candidates,
            "peer_score_map": peer_score_map,
            "pool_source_breakdown": pool_ctx.get("pool_source_breakdown", {}),
            "expanded_codes_sample": pool_ctx.get("expanded_codes_sample", []),
            "dropped_no_daily_count": int(pool_ctx.get("dropped_no_daily_count", 0) or 0),
        }

    def _build_peer_candidate_pool(
        self,
        date8: str,
        candidate_stocks: List[Dict[str, Any]],
        active_themes: List[str],
        excluded_codes: Set[str],
        max_expand: int,
        mainboard_limit_like_pct: float,
    ) -> Dict[str, Any]:
        """
        仅供"历史补涨模块"使用的扩池构建：
        - 保留原候选池（仅用于补涨，不回写主推荐池）
        - 按 active_themes 从历史题材映射扩入同题材股票
        - 对扩入股票回填当日daily，缺失则剔除
        """
        base_map: Dict[str, Dict[str, Any]] = {}
        for row in (candidate_stocks or []):
            code = self._normalize_code(row.get("code", ""))
            if not code:
                continue
            base_map[code] = dict(row or {})

        active = [x for x in (active_themes or []) if str(x or "").strip()]
        expanded = self._fetch_peer_expanded_stocks_from_theme_map(
            date8=date8,
            active_themes=active,
            excluded_codes={self._normalize_code(x) for x in (excluded_codes or set()) if self._normalize_code(x)},
            existing_codes=set(base_map.keys()),
            max_expand=max_expand,
            mainboard_limit_like_pct=mainboard_limit_like_pct,
        )
        expanded_codes = [self._normalize_code(x.get("code", "")) for x in expanded if self._normalize_code(x.get("code", ""))]

        daily_map = self._fetch_daily_map_for_trade_date(self._fmt_date(date8))
        dropped_no_daily = 0

        # 回填原候选池缺失的当日pct，保持补涨模块判断一致性
        for code, row in base_map.items():
            if row.get("pct_chg", None) is None and code in daily_map:
                row["pct_chg"] = self._safe_float((daily_map.get(code, {}) or {}).get("pct_chg", 0.0))
                row["is_limit_like_for_quota"] = self._calc_limit_like_for_quota(
                    code=code,
                    name=str(row.get("name", "") or ""),
                    pct_chg=row.get("pct_chg", None),
                    mainboard_limit_like_pct=mainboard_limit_like_pct,
                )

        # 扩入股票必须有当日daily
        kept_expanded: List[Dict[str, Any]] = []
        for row in expanded:
            code = self._normalize_code(row.get("code", ""))
            d = daily_map.get(code, {}) if code else {}
            if not d:
                dropped_no_daily += 1
                continue
            pct = self._safe_float(d.get("pct_chg", 0.0))
            row["pct_chg"] = pct
            row["close"] = self._safe_float(d.get("close", 0.0))
            row["vol"] = self._safe_float(d.get("vol", 0.0))
            row["amount"] = self._safe_float(d.get("amount", 0.0))
            row["is_limit_like_for_quota"] = self._calc_limit_like_for_quota(
                code=code,
                name=str(row.get("name", "") or ""),
                pct_chg=pct,
                mainboard_limit_like_pct=mainboard_limit_like_pct,
            )
            kept_expanded.append(row)

        merged_pool = list(base_map.values()) + kept_expanded
        self.logger.info(
            "[历史补涨] 扩池构建 | base=%s | expanded_hit=%s | expanded_kept=%s | dropped_no_daily=%s",
            len(base_map),
            len(expanded_codes),
            len(kept_expanded),
            dropped_no_daily,
        )
        return {
            "pool": merged_pool,
            "pool_source_breakdown": {
                "base_count": len(base_map),
                "expanded_count": len(kept_expanded),
                "dropped_count": int(max(len(expanded_codes) - len(kept_expanded), 0)),
            },
            "expanded_codes_sample": [c for c in expanded_codes[:20] if c],
            "dropped_no_daily_count": int(dropped_no_daily),
        }

    def _fetch_peer_expanded_stocks_from_theme_map(
        self,
        date8: str,
        active_themes: List[str],
        excluded_codes: Set[str],
        existing_codes: Set[str],
        max_expand: int,
        mainboard_limit_like_pct: float,
    ) -> List[Dict[str, Any]]:
        if not active_themes:
            return []
        theme_list = [
            str(x).strip()
            for x in active_themes
            if str(x).strip() and not self._is_noise_theme_label(str(x).strip())
        ]
        if not theme_list:
            return []
        placeholders = ",".join(["?"] * len(theme_list))
        conn = self._get_connection()
        sql = f"""
            WITH matched AS (
                SELECT m.stock_code, MAX(m.trade_date) AS latest_trade_date
                FROM longhubang_stock_theme_map_2026 m
                WHERE m.trade_date <= ?
                  AND m.theme IN ({placeholders})
                GROUP BY m.stock_code
            )
            SELECT mm.stock_code, mm.latest_trade_date, s.stock_name, s.themes
            FROM matched mm
            JOIN longhubang_daily_snapshot_2026 s
              ON s.trade_date = mm.latest_trade_date AND s.stock_code = mm.stock_code
            ORDER BY mm.latest_trade_date DESC
            LIMIT 3000
            """
        params: List[Any] = [date8] + theme_list
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        if not rows:
            return []

        active_key_set = {self._norm_theme_key(x) for x in active_themes if self._norm_theme_key(x)}
        out: List[Dict[str, Any]] = []
        for r in rows:
            code = self._normalize_code(r["stock_code"])
            if not code or code in existing_codes or code in excluded_codes:
                continue
            if not self._is_mainboard_code(code):
                continue
            name = str(r["stock_name"] or "").strip()
            if self._is_st_stock_name(name):
                continue
            raw_themes = self._json_to_list(r["themes"])
            matched = []
            for t in raw_themes:
                t_text = str(t or "").strip()
                if not t_text or self._is_noise_theme_label(t_text):
                    continue
                key = self._norm_theme_key(t_text)
                if key and key in active_key_set:
                    matched.append(t_text)
            if not matched:
                continue
            out.append(
                {
                    "code": code,
                    "name": name,
                    "net_inflow": 0.0,
                    "pct_chg": None,
                    "is_today_limit_up": False,
                    "is_limit_like_for_quota": False,
                    "limit_quality_score": 0.0,
                    "p1_score": 0.0,
                    "theme_tokens": "、".join(matched[:6]),
                    "kline_trend_stage": "",
                    "kline_change_7d_pct": 0.0,
                    "kline_latest_change_pct": 0.0,
                    "kline_drawdown_from_high_pct": 0.0,
                    "kline_vol_ratio": 0.0,
                    "data_completeness": 0.0,
                    "signal_consistency": 0.0,
                    "data_quality_grade": "C",
                    "is_peer_pool_expanded": True,
                }
            )
            if len(out) >= max(10, int(max_expand or 120)):
                break
        return out

    def _calc_low_position_anchor_pct(
        self,
        base_threshold_pct: float,
        theme_strength_today: float,
        theme_diffusion_strength: float = 0.0,
    ) -> float:
        """
        低位确认锚点（联动主线强度与题材扩散）：
        - 主线越强，低位阈值越宽松（锚点上移）
        - 题材扩散强时，进一步放宽阈值
        """
        base = max(1.0, min(float(base_threshold_pct or 6.0), 10.0))
        strength = max(0.0, min(float(theme_strength_today or 0.0), 1.0))
        diffusion = max(0.0, min(float(theme_diffusion_strength or 0.0), 1.0))
        # strength=0 -> base; strength=1 -> base+2.0; diffusion额外+1.0
        anchor = base + 2.0 * strength + 1.0 * diffusion
        return round(max(1.0, min(anchor, 12.0)), 2)

    def _calc_hist_samples_for_matched_themes(
        self,
        matched_themes: List[str],
        hist_theme_stats: Dict[str, Dict[str, Any]],
    ) -> int:
        best = 0
        for t in (matched_themes or []):
            key = self._norm_theme_key(t)
            if not key:
                continue
            st = hist_theme_stats.get(key, {}) or {}
            samples = int(st.get("samples", 0) or 0)
            if samples > best:
                best = samples
        return max(best, 0)

    def _calc_theme_diffusion_strength(
        self,
        matched_themes: List[str],
        concept_flow_counter: Counter,
        industry_flow_counter: Counter,
    ) -> float:
        """
        题材扩散强度（0-1）：
        - 匹配题材在概念/行业资金流中的排名与权重
        - 排名越靠前、资金流越大，扩散强度越高
        """
        if not matched_themes:
            return 0.0
        concept_top = [k for k, _ in concept_flow_counter.most_common(20)]
        industry_top = [k for k, _ in industry_flow_counter.most_common(20)]
        strength = 0.0
        for t in matched_themes:
            key = self._norm_theme_key(t)
            if not key:
                continue
            if key in concept_top:
                rank = concept_top.index(key) + 1
                strength += max(0.0, 1.0 - rank / 20.0) * 0.6
            if key in industry_top:
                rank = industry_top.index(key) + 1
                strength += max(0.0, 1.0 - rank / 20.0) * 0.4
        return round(max(0.0, min(strength, 1.0)), 3)

    def _calc_peer_confidence_factor(
        self,
        stock: Dict[str, Any],
        hist_samples: int,
        today_strength: float,
        match_weight: float,
        hist_return_5d_avg: float = 0.0,
        hist_max_dd_avg: float = 0.0,
    ) -> float:
        completeness = float(stock.get("data_completeness", 0.0) or 0.0)
        consistency = float(stock.get("signal_consistency", 0.0) or 0.0)
        sample_scale = max(0.0, min(float(hist_samples) / 40.0, 1.0))
        strength_scale = max(0.0, min(float(today_strength or 0.0), 1.0))
        match_scale = max(0.45, min(float(match_weight or 0.45), 1.0))

        # 收益路径增强：高收益低回撤提升置信度
        return_boost = 0.0
        if hist_return_5d_avg > 3.0:
            return_boost += 0.03
        if hist_return_5d_avg > 6.0:
            return_boost += 0.02
        dd_penalty = 0.0
        if hist_max_dd_avg > 8.0:
            dd_penalty -= 0.04
        if hist_max_dd_avg > 15.0:
            dd_penalty -= 0.03

        factor = (
            0.55
            + 0.18 * completeness
            + 0.15 * consistency
            + 0.07 * sample_scale
            + 0.03 * strength_scale
            + 0.02 * match_scale
            + return_boost
            + dd_penalty
        )
        return round(max(0.65, min(factor, 1.12)), 3)

    def _calc_high_zone_penalty(
        self,
        pct_chg: float,
        low_anchor_pct: float,
        stock: Dict[str, Any],
        is_limit_like: bool,
    ) -> Any:
        penalty = 0.0
        reasons: List[str] = []

        kline_change = float(stock.get("kline_change_7d_pct", 0.0) or 0.0)
        drawdown = float(stock.get("kline_drawdown_from_high_pct", 0.0) or 0.0)
        trend_stage = str(stock.get("kline_trend_stage", "") or "").strip()
        p1_score = float(stock.get("p1_score", 0.0) or 0.0)

        if pct_chg >= max(low_anchor_pct + 2.0, 8.0):
            penalty += 3.2
            reasons.append("pct_high")
        if kline_change >= 14.0 and drawdown >= -3.5:
            penalty += 2.6
            reasons.append("kline_near_high")
        if trend_stage in {"加速期", "高位震荡"} and p1_score < 0.72:
            penalty += 1.8
            reasons.append("late_stage")
        if is_limit_like:
            penalty += 0.8
            reasons.append("limit_like")

        return round(max(0.0, min(penalty, 9.5)), 2), "|".join(reasons)

    def _calc_limit_like_for_quota(
        self,
        code: str,
        name: str,
        pct_chg: Optional[float],
        mainboard_limit_like_pct: float,
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

    def _is_mainboard_code(self, code: str) -> bool:
        c = self._normalize_code(code)
        if not c:
            return False
        return not (c.startswith(("300", "301", "688", "4", "8")))

    def _is_st_stock_name(self, name: str) -> bool:
        nm = str(name or "").strip().upper()
        if not nm:
            return False
        return ("ST" in nm) or ("*ST" in nm)

    def _json_to_list(self, value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return [str(x).strip() for x in obj if str(x).strip()]
        except Exception:
            pass
        return self._split_theme_tokens(text)

    def _calc_post_lhb_return_profile(
        self,
        code: str,
        lhb_date: str,
        lookback_days: int = 60,
    ) -> Dict[str, Any]:
        """
        计算上榜后收益路径与回撤特征
        Args:
            code: 股票代码
            lhb_date: 上榜日(YYYYMMDD)
            lookback_days: 向后看天数
        Returns:
            return_1d/3d/5d/10d, max_drawdown_10d, drawdown_speed
        """
        result = {
            "return_1d": 0.0,
            "return_3d": 0.0,
            "return_5d": 0.0,
            "return_10d": 0.0,
            "max_drawdown_10d": 0.0,
            "drawdown_speed": 0.0,
            "data_available": False,
        }
        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None:
            return result
        ts_code = self._to_ts_code(code)
        if not ts_code:
            return result
        try:
            lhb_dt = datetime.strptime(lhb_date, "%Y%m%d")
        except Exception:
            return result
        end_dt = lhb_dt + timedelta(days=lookback_days + 10)
        end_date_str = end_dt.strftime("%Y%m%d")
        try:
            df = call_tushare_with_timeout(
                pro.daily,
                ts_code=ts_code,
                start_date=lhb_date,
                end_date=end_date_str,
            )
        except Exception:
            return result
        if df is None or df.empty:
            return result
        df = df.sort_values("trade_date").reset_index(drop=True)
        if len(df) < 2:
            return result
        base_close = float(df.iloc[0]["close"])
        if base_close <= 0:
            return result
        result["data_available"] = True
        max_idx = min(len(df) - 1, 10)
        for i, target_day in enumerate([1, 3, 5, 10], start=1):
            if target_day < len(df):
                target_close = float(df.iloc[target_day]["close"])
                ret = ((target_close - base_close) / base_close) * 100.0
                result[f"return_{target_day}d"] = round(ret, 2)
        high_peak = base_close
        max_dd = 0.0
        dd_days = 0
        for i in range(1, max_idx + 1):
            if i >= len(df):
                break
            cur_close = float(df.iloc[i]["close"])
            if cur_close > high_peak:
                high_peak = cur_close
            dd = ((cur_close - high_peak) / high_peak) * 100.0
            if dd < max_dd:
                max_dd = dd
                dd_days = i
        result["max_drawdown_10d"] = round(max_dd, 2)
        result["drawdown_speed"] = round(abs(max_dd) / max(dd_days, 1), 2) if dd_days > 0 else 0.0
        return result

    def _to_ts_code(self, code: str) -> str:
        c = self._normalize_code(code)
        if not c or len(c) != 6:
            return ""
        if c.startswith(("6",)):
            return f"{c}.SH"
        elif c.startswith(("0", "3", "4", "8")):
            return f"{c}.SZ"
        return ""

    def _sync_single_day(self, trade_date: str) -> Dict[str, Any]:
        date8 = self._to_date8(trade_date)
        if not date8 or not date8.startswith("2026"):
            return {"data_success": False, "error": "date_not_in_2026"}

        source_map: Dict[str, Dict[str, Any]] = {}
        # 1) 龙虎榜（含top_list+stockapi gl）
        lhb = self.data_fetcher.get_longhubang_data(trade_date) or {}
        lhb_rows = list(lhb.get("data", []) or [])
        for row in lhb_rows:
            code = self._normalize_code(row.get("gpdm") or row.get("股票代码") or "")
            if not code:
                continue
            item = source_map.setdefault(code, self._new_stock_row(code))
            item["stock_name"] = str(row.get("gpmc") or item["stock_name"] or "").strip()
            item["pct_chg"] = self._choose_pct(item.get("pct_chg"), row.get("pct_chg"))
            item["is_lhb"] = 1
            gl = str(row.get("gl") or "").strip()
            if gl:
                item["gl_tokens"].extend(self._split_theme_tokens(gl))

        # 2) 同花顺涨停池全类型
        limit_rows = self._fetch_limit_list_ths_all_types(trade_date)
        for row in limit_rows:
            code = self._normalize_code(row.get("code") or row.get("ts_code") or row.get("股票代码") or "")
            if not code:
                continue
            item = source_map.setdefault(code, self._new_stock_row(code))
            item["stock_name"] = str(row.get("name") or item["stock_name"] or "").strip()
            item["pct_chg"] = self._choose_pct(item.get("pct_chg"), row.get("pct_chg"))
            item["limit_tokens"].extend(self._split_theme_tokens(row.get("lu_desc", "")))
            status = str(row.get("status", "") or "").strip()
            if "跌停" in status:
                item["is_limit_down_hint"] = 1
            if "涨停" in status or "首板" in status or "板" in status:
                item["is_limit_up_hint"] = 1

        # 3) 开盘啦（涨停/自然涨停/炸板/跌停）
        kpl_rows = self._fetch_kpl_list_all_tags(trade_date)
        for row in kpl_rows:
            code = self._normalize_code(row.get("code") or row.get("ts_code") or row.get("股票代码") or "")
            if not code:
                continue
            item = source_map.setdefault(code, self._new_stock_row(code))
            item["stock_name"] = str(row.get("name") or item["stock_name"] or "").strip()
            item["pct_chg"] = self._choose_pct(item.get("pct_chg"), row.get("pct_chg"))
            item["kpl_tokens"].extend(self._split_theme_tokens(row.get("theme", "")))
            tag = str(row.get("tag", "") or "").strip()
            if "跌停" in tag:
                item["is_limit_down_hint"] = 1
            if "涨停" in tag:
                item["is_limit_up_hint"] = 1

        if not source_map:
            return {"data_success": False, "error": "empty_source_rows"}

        # 4) 同日行情（优先 daily 的 pct/close/vol/amount 回填）
        daily_map = self._fetch_daily_map_for_trade_date(trade_date)
        for code, item in source_map.items():
            d = daily_map.get(code, {})
            if d:
                item["stock_name"] = str(d.get("name") or item["stock_name"] or "").strip()
                item["pct_chg"] = self._choose_pct(item.get("pct_chg"), d.get("pct_chg"))
                item["close"] = self._safe_float(d.get("close"))
                item["vol"] = self._safe_float(d.get("vol"))
                item["amount"] = self._safe_float(d.get("amount"))

            themes = []
            seen = set()
            for tok in (item["kpl_tokens"] + item["limit_tokens"]):
                if tok and tok not in seen:
                    seen.add(tok)
                    themes.append(tok)
            # 题材缺失时才使用 gl 兜底
            if not themes:
                for tok in item["gl_tokens"]:
                    if tok and tok not in seen:
                        seen.add(tok)
                        themes.append(tok)
            item["themes"] = themes[:10]

            pct = self._safe_float(item.get("pct_chg"))
            up_thr = self._get_limit_threshold_pct(code=code, name=item.get("stock_name", ""))
            item["is_limit_up"] = int(pct >= up_thr or item.get("is_limit_up_hint", 0) == 1)
            item["is_limit_down"] = int(pct <= -up_thr or item.get("is_limit_down_hint", 0) == 1)

        self._upsert_snapshot_and_theme_map(date8=date8, stock_map=source_map)
        self._rebuild_theme_daily_for_date(date8=date8)

        # 生成共现群组
        try:
            self._build_cooccur_groups_for_date(date8)
        except Exception as e:
            self.logger.warning("[共现群组] 生成失败 | trade_date=%s | err=%s", date8, str(e))

        return {"data_success": True, "stock_count": len(source_map)}

    def _upsert_snapshot_and_theme_map(self, date8: str, stock_map: Dict[str, Dict[str, Any]]) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        for code, item in (stock_map or {}).items():
            flags = {
                "lhb": int(item.get("is_lhb", 0) or 0),
                "has_kpl": int(bool(item.get("kpl_tokens"))),
                "has_limit": int(bool(item.get("limit_tokens"))),
                "has_gl_fallback": int(bool(item.get("gl_tokens"))),
            }
            themes = list(item.get("themes", []) or [])
            cur.execute(
                """
                INSERT OR REPLACE INTO longhubang_daily_snapshot_2026 (
                    trade_date, stock_code, stock_name, pct_chg, close, vol, amount,
                    is_limit_up, is_limit_down, is_lhb, themes, source_flags, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    date8,
                    code,
                    str(item.get("stock_name", "") or ""),
                    float(item.get("pct_chg", 0.0) or 0.0),
                    float(item.get("close", 0.0) or 0.0),
                    float(item.get("vol", 0.0) or 0.0),
                    float(item.get("amount", 0.0) or 0.0),
                    int(item.get("is_limit_up", 0) or 0),
                    int(item.get("is_limit_down", 0) or 0),
                    int(item.get("is_lhb", 0) or 0),
                    json.dumps(themes, ensure_ascii=False),
                    json.dumps(flags, ensure_ascii=False),
                ),
            )
            cur.execute(
                "DELETE FROM longhubang_stock_theme_map_2026 WHERE trade_date=? AND stock_code=?",
                (date8, code),
            )
            for t in themes:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO longhubang_stock_theme_map_2026 (trade_date, stock_code, theme)
                    VALUES (?, ?, ?)
                    """,
                    (date8, code, str(t)),
                )
        conn.commit()
        conn.close()

    def _rebuild_theme_daily_for_date(self, date8: str) -> None:
        conn = self._get_connection()
        cur = conn.cursor()
        df = pd.read_sql_query(
            """
            SELECT s.stock_code, s.pct_chg, s.is_limit_up, m.theme
            FROM longhubang_daily_snapshot_2026 s
            JOIN longhubang_stock_theme_map_2026 m
              ON s.trade_date = m.trade_date AND s.stock_code = m.stock_code
            WHERE s.trade_date = ?
            """,
            conn,
            params=[date8],
        )
        cur.execute("DELETE FROM longhubang_theme_daily_2026 WHERE trade_date=?", (date8,))
        if df.empty:
            conn.commit()
            conn.close()
            return

        stats: Dict[str, Dict[str, Any]] = {}
        for theme, g in df.groupby("theme"):
            cnt = int(len(g))
            up_cnt = int((g["is_limit_up"].fillna(0).astype(int) > 0).sum())
            avg_pct = float(g["pct_chg"].fillna(0.0).mean())
            stats[str(theme)] = {
                "stock_count": cnt,
                "limit_up_count": up_cnt,
                "avg_pct_chg": avg_pct,
            }

        max_cnt = max([v["stock_count"] for v in stats.values()] + [1])
        max_up = max([v["limit_up_count"] for v in stats.values()] + [1])
        for theme, row in stats.items():
            cnt_norm = float(row["stock_count"]) / max(max_cnt, 1)
            up_norm = float(row["limit_up_count"]) / max(max_up, 1)
            pct_norm = max(min((float(row["avg_pct_chg"]) + 2.0) / 10.0, 1.0), 0.0)
            strength = 50.0 * cnt_norm + 30.0 * pct_norm + 20.0 * up_norm
            cur.execute(
                """
                INSERT OR REPLACE INTO longhubang_theme_daily_2026 (
                    trade_date, theme, stock_count, limit_up_count, avg_pct_chg, strength_score, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    date8,
                    theme,
                    int(row["stock_count"]),
                    int(row["limit_up_count"]),
                    round(float(row["avg_pct_chg"]), 4),
                    round(float(strength), 3),
                ),
            )
        conn.commit()
        conn.close()

    def _build_cooccur_groups_for_date(self, trade_date: str) -> List[Dict[str, Any]]:
        """生成指定日期的共现群组"""
        date8 = self._to_date8(trade_date) if len(str(trade_date)) != 8 else str(trade_date)
        conn = self._get_connection()
        cur = conn.cursor()

        # 查询当日所有上榜股票
        rows = cur.execute(
            """
            SELECT stock_code, stock_name, pct_chg, is_limit_up, themes
            FROM longhubang_daily_snapshot_2026
            WHERE trade_date = ? AND is_lhb = 1
            """,
            (date8,),
        ).fetchall()

        if not rows:
            conn.close()
            return []

        # 解析题材并按单一题材聚类
        theme_stocks: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            code = str(row[0])
            name = str(row[1])
            pct_chg = float(row[2] or 0.0)
            is_limit_up = int(row[3] or 0)
            themes_json = str(row[4] or "[]")

            try:
                themes = json.loads(themes_json)
            except:
                themes = []

            for theme in themes:
                theme_text = str(theme or "").strip()
                if not theme_text or self._is_noise_theme_label(theme_text):
                    continue
                theme_key = self._norm_theme_key(theme_text)
                if not theme_key:
                    continue
                if theme_key not in theme_stocks:
                    theme_stocks[theme_key] = []
                theme_stocks[theme_key].append({
                    "code": code,
                    "name": name,
                    "pct_chg": pct_chg,
                    "is_limit_up": is_limit_up,
                })

        # 过滤成员数<3的群组
        valid_groups = {k: v for k, v in theme_stocks.items() if len(v) >= 3}

        if not valid_groups:
            conn.close()
            return []

        # 计算归一化参数
        all_member_counts = [len(v) for v in valid_groups.values()]
        all_avg_pcts = [sum(s["pct_chg"] for s in v) / len(v) for v in valid_groups.values()]
        max_member_count = max(all_member_counts)
        max_avg_pct = max(all_avg_pcts) if all_avg_pcts else 1.0

        # 生成群组记录
        generated_groups = []
        seq = 0
        for theme_key, members in valid_groups.items():
            seq += 1
            group_id = f"{date8}_{theme_key}_{seq}"
            member_count = len(members)
            avg_pct_chg = sum(s["pct_chg"] for s in members) / member_count
            limit_up_count = sum(1 for s in members if s["is_limit_up"] == 1)

            # 计算群组强度
            member_count_norm = member_count / max(max_member_count, 1)
            avg_pct_norm = max(min((avg_pct_chg + 2.0) / 10.0, 1.0), 0.0)
            limit_up_ratio = limit_up_count / member_count if member_count > 0 else 0.0
            group_strength = 50.0 * member_count_norm + 30.0 * avg_pct_norm + 20.0 * limit_up_ratio

            # 插入群组元数据
            cur.execute(
                """
                INSERT OR REPLACE INTO longhubang_cooccur_group_2026 (
                    group_id, trade_date, theme, member_count, avg_pct_chg,
                    limit_up_count, group_strength, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    group_id,
                    date8,
                    theme_key,
                    member_count,
                    round(avg_pct_chg, 4),
                    limit_up_count,
                    round(group_strength, 3),
                ),
            )

            # 批量插入成员
            for member in members:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO longhubang_cooccur_member_2026 (
                        group_id, stock_code, stock_name, pct_chg, is_limit_up
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        member["code"],
                        member["name"],
                        round(member["pct_chg"], 4),
                        member["is_limit_up"],
                    ),
                )

            generated_groups.append({
                "group_id": group_id,
                "theme": theme_key,
                "member_count": member_count,
                "group_strength": round(group_strength, 3),
            })

        conn.commit()
        conn.close()

        self.logger.info(
            "[共现群组] 生成完成 | trade_date=%s | groups=%s",
            date8,
            len(generated_groups),
        )
        return generated_groups

    def _calc_group_performance(self, group_id: str) -> Dict[str, Any]:
        """计算群组后续表现"""
        conn = self._get_connection()
        cur = conn.cursor()

        # 查询群组信息
        group_row = cur.execute(
            "SELECT trade_date FROM longhubang_cooccur_group_2026 WHERE group_id = ?",
            (group_id,),
        ).fetchone()

        if not group_row:
            conn.close()
            return {}

        trade_date = str(group_row[0])

        # 查询群组成员
        member_rows = cur.execute(
            "SELECT stock_code FROM longhubang_cooccur_member_2026 WHERE group_id = ?",
            (group_id,),
        ).fetchall()

        if not member_rows:
            conn.close()
            return {}

        # 计算每个成员的后续收益
        returns_1d = []
        returns_3d = []
        returns_5d = []
        drawdowns_5d = []

        for row in member_rows:
            code = str(row[0])
            profile = self._calc_post_lhb_return_profile(code, trade_date, lookback_days=10)
            if profile.get("data_available"):
                returns_1d.append(profile.get("return_1d", 0.0))
                returns_3d.append(profile.get("return_3d", 0.0))
                returns_5d.append(profile.get("return_5d", 0.0))
                drawdowns_5d.append(profile.get("max_drawdown_10d", 0.0))

        if not returns_1d:
            conn.close()
            return {}

        # 聚合计算
        next_1d_avg = sum(returns_1d) / len(returns_1d)
        next_3d_avg = sum(returns_3d) / len(returns_3d) if returns_3d else 0.0
        next_5d_avg = sum(returns_5d) / len(returns_5d) if returns_5d else 0.0
        max_drawdown_5d = sum(drawdowns_5d) / len(drawdowns_5d) if drawdowns_5d else 0.0
        win_rate_1d = sum(1 for r in returns_1d if r > 0) / len(returns_1d)

        # 插入群组表现
        cur.execute(
            """
            INSERT OR REPLACE INTO longhubang_group_performance_2026 (
                group_id, next_1d_avg, next_3d_avg, next_5d_avg,
                max_drawdown_5d, win_rate_1d, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                group_id,
                round(next_1d_avg, 4),
                round(next_3d_avg, 4),
                round(next_5d_avg, 4),
                round(max_drawdown_5d, 4),
                round(win_rate_1d, 4),
            ),
        )

        conn.commit()
        conn.close()

        return {
            "group_id": group_id,
            "next_1d_avg": round(next_1d_avg, 4),
            "next_5d_avg": round(next_5d_avg, 4),
            "win_rate_1d": round(win_rate_1d, 4),
        }

    def backfill_cooccur_groups(self, days: int = 90) -> Dict[str, Any]:
        """回填历史共现群组数据"""
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

        trade_dates = self.trade_calendar.open_days_between(start_date, end_date)
        if not trade_dates:
            return {"total_groups": 0, "total_days": 0, "error": "no_trade_dates"}

        # 只处理最近N天
        trade_dates = trade_dates[-days:] if len(trade_dates) > days else trade_dates

        total_groups = 0
        processed_days = 0

        self.logger.info("[共现群组回填] 开始 | days=%s | trade_dates=%s", days, len(trade_dates))

        for date8 in trade_dates:
            try:
                groups = self._build_cooccur_groups_for_date(date8)
                total_groups += len(groups)
                processed_days += 1

                if processed_days % 10 == 0:
                    self.logger.info(
                        "[共现群组回填] 进度 | processed=%s/%s | total_groups=%s",
                        processed_days,
                        len(trade_dates),
                        total_groups,
                    )
            except Exception as e:
                self.logger.warning("[共现群组回填] 单日失败 | date=%s | err=%s", date8, str(e))
                continue

        self.logger.info(
            "[共现群组回填] 完成 | total_days=%s | total_groups=%s | avg_groups_per_day=%.1f",
            processed_days,
            total_groups,
            total_groups / max(processed_days, 1),
        )

        return {
            "total_groups": total_groups,
            "total_days": processed_days,
            "avg_groups_per_day": round(total_groups / max(processed_days, 1), 1),
        }

    def _query_cooccur_history(
        self,
        candidate_stocks: List[Dict[str, Any]],
        active_themes: List[str],
        before_date: str,
    ) -> Dict[str, Dict[str, Any]]:
        """查询候选股票的历史共现数据"""
        if not candidate_stocks or not active_themes:
            return {}

        conn = self._get_connection()

        # 提取候选股票代码
        candidate_codes = [self._normalize_code(s.get("code", "")) for s in candidate_stocks]
        candidate_codes = [c for c in candidate_codes if c]

        if not candidate_codes:
            conn.close()
            return {}

        # 归一化题材
        theme_keys = [self._norm_theme_key(t) for t in active_themes]
        theme_keys = [k for k in theme_keys if k]

        if not theme_keys:
            conn.close()
            return {}

        # 查询候选股票参与的历史群组
        placeholders_codes = ",".join(["?"] * len(candidate_codes))
        placeholders_themes = ",".join(["?"] * len(theme_keys))

        query = f"""
            SELECT
                m.stock_code,
                COUNT(DISTINCT m.group_id) AS frequency,
                AVG(g.avg_pct_chg) AS avg_group_pct,
                AVG(g.group_strength) AS avg_strength
            FROM longhubang_cooccur_member_2026 m
            JOIN longhubang_cooccur_group_2026 g ON m.group_id = g.group_id
            WHERE m.stock_code IN ({placeholders_codes})
              AND g.theme IN ({placeholders_themes})
              AND g.trade_date < ?
            GROUP BY m.stock_code
        """

        params = candidate_codes + theme_keys + [before_date]

        try:
            rows = conn.execute(query, params).fetchall()
        except Exception as e:
            self.logger.warning("[共现查询] 失败 | err=%s", str(e))
            conn.close()
            return {}

        result = {}
        for row in rows:
            code = str(row[0])
            frequency = int(row[1] or 0)
            avg_group_pct = float(row[2] or 0.0)
            avg_strength = float(row[3] or 0.0)

            # 使用群组平均涨幅和强度作为替代指标
            # 将平均涨幅转换为类似胜率的指标（0-1）
            win_rate = max(0.0, min((avg_group_pct + 2.0) / 10.0, 1.0))
            # 使用群组强度作为收益指标
            avg_return = avg_group_pct

            result[code] = {
                "frequency": frequency,
                "win_rate": win_rate,
                "avg_return": avg_return,
            }

        conn.close()

        self.logger.info(
            "[共现查询] 完成 | candidates=%s | matched=%s",
            len(candidate_codes),
            len(result),
        )

        return result

    def _get_theme_historical_stats(self, before_date: str, themes: List[str]) -> Dict[str, Dict[str, Any]]:
        if not themes:
            return {}
        conn = self._get_connection()
        out: Dict[str, Dict[str, Any]] = {}
        for t in themes:
            t_key = self._norm_theme_key(t)
            if not t_key:
                continue
            row = conn.execute(
                """
                WITH daily AS (
                    SELECT
                        s.trade_date,
                        s.stock_code,
                        s.pct_chg,
                        LEAD(s.pct_chg) OVER (PARTITION BY s.stock_code ORDER BY s.trade_date) AS next_pct
                    FROM longhubang_daily_snapshot_2026 s
                    WHERE s.trade_date < ?
                )
                SELECT
                    COUNT(1) AS samples,
                    AVG(CASE WHEN d.next_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                    AVG(d.next_pct) AS avg_next_pct
                FROM daily d
                JOIN longhubang_stock_theme_map_2026 m
                  ON d.trade_date = m.trade_date AND d.stock_code = m.stock_code
                WHERE m.theme = ?
                  AND d.next_pct IS NOT NULL
                """,
                (before_date, t),
            ).fetchone()
            if row is None:
                continue
            samples = int(row["samples"] or 0)
            if samples <= 0:
                continue
            out[t_key] = {
                "theme": t,
                "samples": samples,
                "win_rate": float(row["win_rate"] or 0.0),
                "avg_next_pct": float(row["avg_next_pct"] or 0.0),
            }
        conn.close()
        return out

    def _diversify_candidates_by_theme(
        self,
        candidates: List[Dict[str, Any]],
        top_n: int = 12,
        max_per_theme_ratio: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        题材分配策略：Top3题材优先做均衡，强题材可获得更多席位，但避免单题材垄断。
        """
        if not candidates or top_n <= 0:
            return []

        theme_groups: Dict[str, List[Dict[str, Any]]] = {}
        for stock in candidates:
            themes = str(stock.get("theme_tokens", "")).split("、")
            primary_theme = (themes[0] if themes else "未知").strip() or "未知"
            theme_groups.setdefault(primary_theme, []).append(stock)

        if not theme_groups:
            return candidates[: max(1, int(top_n or 12))]

        # 候选本身已按总分降序；这里保持主题内顺序即可
        def _theme_head_score(theme_name: str) -> float:
            first = theme_groups[theme_name][0] if theme_groups.get(theme_name) else {}
            return float(first.get("peer_rebound_score", 0.0) or 0.0)

        ranked_themes = sorted(
            theme_groups.keys(),
            key=lambda t: (
                _theme_head_score(t),
                len(theme_groups.get(t, [])),
            ),
            reverse=True,
        )

        core_themes = ranked_themes[:3]
        theme_counters = {theme: 0 for theme in theme_groups}
        selected: List[Dict[str, Any]] = []

        max_per_theme = max(1, int(top_n * max_per_theme_ratio))
        if len(theme_groups) > 1:
            max_per_theme = min(max_per_theme, max(1, top_n - 1))

        def _pick_one(theme_name: str) -> bool:
            idx = int(theme_counters.get(theme_name, 0))
            group = theme_groups.get(theme_name, [])
            if idx >= len(group):
                return False
            if idx >= max_per_theme:
                return False
            selected.append(group[idx])
            theme_counters[theme_name] = idx + 1
            return True

        # 阶段1：Top3题材先各拿1席，避免结果被单一题材占满
        for theme in core_themes:
            if len(selected) >= top_n:
                break
            _pick_one(theme)

        if len(selected) >= top_n:
            return selected[:top_n]

        # 阶段2：Top3按强度加权分配剩余席位（强题材可多分）
        core_scores = {t: max(0.0, _theme_head_score(t)) for t in core_themes}
        score_sum = sum(core_scores.values())
        core_quota = {t: 0 for t in core_themes}
        remain_slots = top_n - len(selected)

        if core_themes and remain_slots > 0:
            for t in core_themes:
                if score_sum > 0:
                    ratio = core_scores[t] / score_sum
                else:
                    ratio = 1.0 / len(core_themes)
                core_quota[t] = int(remain_slots * ratio)

            used = sum(core_quota.values())
            if used < remain_slots:
                for t in sorted(core_themes, key=lambda x: core_scores[x], reverse=True):
                    if used >= remain_slots:
                        break
                    core_quota[t] += 1
                    used += 1

            for t in sorted(core_themes, key=lambda x: core_scores[x], reverse=True):
                while core_quota.get(t, 0) > 0 and len(selected) < top_n:
                    if not _pick_one(t):
                        break
                    core_quota[t] -= 1

        if len(selected) >= top_n:
            return selected[:top_n]

        # 阶段3：其余题材补齐（保持分散，仍受单题材上限约束）
        while len(selected) < top_n:
            added = False
            for t in ranked_themes:
                if _pick_one(t):
                    added = True
                    if len(selected) >= top_n:
                        break
            if not added:
                break

        return selected[:top_n]

    def _get_theme_today_strength(self, date8: str, themes: List[str]) -> Dict[str, float]:
        if not themes:
            return {}
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT theme, strength_score
            FROM longhubang_theme_daily_2026
            WHERE trade_date=?
            ORDER BY strength_score DESC
            """,
            (date8,),
        ).fetchall()
        conn.close()
        if not rows:
            return {}
        score_map = {
            self._norm_theme_key(str(r["theme"] or "")): float(r["strength_score"] or 0.0)
            for r in rows
        }
        max_score = max([v for v in score_map.values()] + [1.0])
        out: Dict[str, float] = {}
        for t in themes:
            key = self._norm_theme_key(t)
            if not key:
                continue
            raw = float(score_map.get(key, 0.0) or 0.0)
            out[key] = round(max(min(raw / max_score, 1.0), 0.0), 3)
        return out

    def _get_existing_trade_dates(self) -> Set[str]:
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM longhubang_daily_snapshot_2026 WHERE trade_date LIKE '2026%'"
        ).fetchall()
        conn.close()
        return {str(x["trade_date"]) for x in rows if str(x["trade_date"] or "")}

    def _get_latest_trade_date(self) -> str:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT MAX(trade_date) AS max_date FROM longhubang_daily_snapshot_2026 WHERE trade_date LIKE '2026%'"
        ).fetchone()
        conn.close()
        return str((row["max_date"] if row else "") or "")

    def _fetch_daily_map_for_trade_date(self, trade_date: str) -> Dict[str, Dict[str, Any]]:
        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "daily"):
            return {}
        date8 = self._to_date8(trade_date)
        if not date8:
            return {}
        candidates = [
            {"trade_date": date8},
            {"date": date8},
        ]
        df: Optional[pd.DataFrame] = None
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: pro.daily(**kwargs),
                    timeout_sec=60,
                    api_name="daily",
                )
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                break
        if df is None or df.empty:
            return {}

        code_col = self._find_col(df, ["ts_code", "code", "证券代码", "股票代码"])
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        pct_col = self._find_col(df, ["pct_chg", "pct_change", "涨跌幅"])
        close_col = self._find_col(df, ["close", "收盘"])
        vol_col = self._find_col(df, ["vol", "volume", "成交量"])
        amt_col = self._find_col(df, ["amount", "成交额"])
        if not code_col:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for _, r in df.iterrows():
            code = self._normalize_code(r.get(code_col))
            if not code:
                continue
            out[code] = {
                "name": str(r.get(name_col) or "").strip() if name_col else "",
                "pct_chg": self._safe_float(r.get(pct_col)) if pct_col else 0.0,
                "close": self._safe_float(r.get(close_col)) if close_col else 0.0,
                "vol": self._safe_float(r.get(vol_col)) if vol_col else 0.0,
                "amount": self._safe_float(r.get(amt_col)) if amt_col else 0.0,
            }
        return out

    def _fetch_limit_list_ths_all_types(self, trade_date: str) -> List[Dict[str, Any]]:
        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "limit_list_ths"):
            return []
        date8 = self._to_date8(trade_date)
        if not date8:
            return []

        limit_types = ["涨停池", "连板池", "冲刺涨停", "炸板池", "跌停池"]
        out: List[Dict[str, Any]] = []
        for lt in limit_types:
            kwargs = {"trade_date": date8, "limit_type": lt}
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda kw=kwargs: pro.limit_list_ths(**kw),
                    timeout_sec=60,
                    api_name="limit_list_ths",
                )
            except Exception:
                continue
            finally:
                time.sleep(0.1)
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            out.extend(self._extract_limit_rows(df, lt))
        return out

    def _fetch_kpl_list_all_tags(self, trade_date: str) -> List[Dict[str, Any]]:
        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "kpl_list"):
            return []
        date8 = self._to_date8(trade_date)
        if not date8:
            return []
        tags = ["涨停", "自然涨停", "炸板", "跌停"]
        out: List[Dict[str, Any]] = []
        for tag in tags:
            kwargs = {"trade_date": date8, "tag": tag}
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda kw=kwargs: pro.kpl_list(**kw),
                    timeout_sec=60,
                    api_name="kpl_list",
                )
            except Exception:
                continue
            finally:
                time.sleep(0.1)
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            out.extend(self._extract_kpl_rows(df, tag))
        return out

    def _extract_limit_rows(self, df: pd.DataFrame, limit_type: str) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "股票代码", "证券代码", "code"])
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        pct_col = self._find_col(df, ["pct_chg", "pct_change", "涨跌幅"])
        lu_col = self._find_col(df, ["lu_desc", "涨停原因", "原因", "题材"])
        status_col = self._find_col(df, ["status", "连板", "板"])
        if not code_col:
            return []
        rows: List[Dict[str, Any]] = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "code": self._normalize_code(r.get(code_col)),
                    "name": str(r.get(name_col) or "").strip() if name_col else "",
                    "pct_chg": self._safe_float(r.get(pct_col)) if pct_col else 0.0,
                    "lu_desc": str(r.get(lu_col) or "").strip() if lu_col else "",
                    "status": str(r.get(status_col) or "").strip() if status_col else "",
                    "limit_type": limit_type,
                }
            )
        return [x for x in rows if x.get("code")]

    def _extract_kpl_rows(self, df: pd.DataFrame, tag: str) -> List[Dict[str, Any]]:
        code_col = self._find_col(df, ["ts_code", "股票代码", "证券代码", "code"])
        name_col = self._find_col(df, ["name", "股票名称", "简称"])
        pct_col = self._find_col(df, ["pct_chg", "pct_change", "涨跌幅"])
        theme_col = self._find_col(df, ["theme", "题材", "概念", "板块"])
        if not code_col:
            return []
        rows: List[Dict[str, Any]] = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "code": self._normalize_code(r.get(code_col)),
                    "name": str(r.get(name_col) or "").strip() if name_col else "",
                    "pct_chg": self._safe_float(r.get(pct_col)) if pct_col else 0.0,
                    "theme": str(r.get(theme_col) or "").strip() if theme_col else "",
                    "tag": tag,
                }
            )
        return [x for x in rows if x.get("code")]

    def _new_stock_row(self, code: str) -> Dict[str, Any]:
        return {
            "stock_code": code,
            "stock_name": "",
            "pct_chg": 0.0,
            "close": 0.0,
            "vol": 0.0,
            "amount": 0.0,
            "is_lhb": 0,
            "is_limit_up_hint": 0,
            "is_limit_down_hint": 0,
            "kpl_tokens": [],
            "limit_tokens": [],
            "gl_tokens": [],
            "themes": [],
        }

    def _choose_pct(self, old_v: Any, new_v: Any) -> float:
        old_f = self._safe_float(old_v)
        new_f = self._safe_float(new_v)
        if abs(new_f) > abs(old_f):
            return round(new_f, 2)
        return round(old_f, 2)

    def _safe_float(self, v: Any) -> float:
        if v is None:
            return 0.0
        text = str(v).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return 0.0
        m = re.search(r"[-+]?\d*\.?\d+", text)
        if not m:
            return 0.0
        try:
            return float(m.group())
        except Exception:
            return 0.0

    def _normalize_code(self, value: Any) -> str:
        text = str(value or "").strip().upper()
        if "." in text:
            text = text.split(".", 1)[0]
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if m:
            return m.group(1)
        return text if len(text) == 6 and text.isdigit() else ""

    def _split_theme_tokens(self, raw: Any) -> List[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        parts = re.split(r"[+＋,，;；/|、\s]+", text)
        noise = {
            "涨停", "跌停", "题材", "概念", "板块", "-", "--", "N/A", "None", "nan",
            "融资融券", "沪股通", "深股通", "陆股通", "港股通",
            "东方财富热股", "最近多板", "机构重仓",
        }
        out: List[str] = []
        seen = set()
        for p in parts:
            t = str(p or "").strip()
            if not t or t in noise or t.isdigit() or len(t) < 2:
                continue
            if t.endswith("板块"):
                continue
            if self._is_st_theme(t):
                continue
            if self._is_noise_theme_label(t):
                continue
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _is_st_theme(self, theme: str) -> bool:
        s = str(theme or "").strip().upper()
        if not s:
            return False
        if "风险警示" in s:
            return True
        return bool(re.search(r"(^|\b)\*?ST(\b|$)", s))

    def _is_noise_theme_label(self, theme: str) -> bool:
        t = str(theme or "").strip()
        if not t:
            return True
        noise_exact = {
            "东方财富热股", "最近多板", "机构重仓", "融资融券", "沪股通", "深股通", "陆股通", "港股通",
            "标准普尔", "富时罗素", "MSCI概念", "中证500", "上证180", "沪深300",
        }
        if t in noise_exact:
            return True
        noise_keywords = ["热股", "多板", "重仓", "融资", "股通", "指数", "成分股"]
        return any(k in t for k in noise_keywords)

    def _get_limit_threshold_pct(self, code: str, name: str = "") -> float:
        upper_name = str(name or "").strip().upper()
        if "ST" in upper_name or "*ST" in upper_name:
            return 4.8
        if str(code).startswith(("3",)):
            return 19.5
        if str(code).startswith(("8", "4")):
            return 29.5
        return 9.5

    def _find_col(self, df: pd.DataFrame, keys: List[str]) -> str:
        for col in df.columns:
            c = str(col).lower()
            if any(k.lower() in c for k in keys):
                return col
        return ""

    def _to_date8(self, value: str) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "").replace(".", "")
        if re.fullmatch(r"\d{8}", text):
            return text
        return ""

    def _fmt_date(self, date8: str) -> str:
        if not date8 or len(date8) != 8:
            return date8
        return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"

    def _norm_theme_key(self, theme: str) -> str:
        text = str(theme or "").strip().lower()
        text = re.sub(r"\s+", "", text)
        return text

    def query_theme_peer_stocks(
        self,
        stock_code: str,
        before_date: Optional[str] = None,
        min_cooccur_count: int = 2,
        top_n: int = 20,
    ) -> Dict[str, Any]:
        """
        查询历史上与指定股票同题材一起上榜的其他股票

        Args:
            stock_code: 目标股票代码
            before_date: 查询截止日期（不含），默认为今天
            min_cooccur_count: 最小共现次数，默认2次
            top_n: 返回前N个结果，默认20

        Returns:
            {
                "success": bool,
                "stock_code": str,
                "stock_name": str,
                "themes": List[str],  # 目标股票参与的题材
                "peer_stocks": List[Dict],  # 同题材跟涨股票列表
                "error": str,
            }
        """
        code = self._normalize_code(stock_code)
        if not code:
            return {"success": False, "error": "无效的股票代码"}

        if not before_date:
            before_date = datetime.now().strftime("%Y%m%d")
        else:
            before_date = self._to_date8(before_date)

        conn = self._get_connection()

        # 1. 查询目标股票参与的群组和题材
        target_groups = conn.execute(
            """
            SELECT DISTINCT g.group_id, g.theme, g.trade_date, m.stock_name
            FROM longhubang_cooccur_member_2026 m
            JOIN longhubang_cooccur_group_2026 g ON m.group_id = g.group_id
            WHERE m.stock_code = ?
              AND g.trade_date < ?
            ORDER BY g.trade_date DESC
            """,
            (code, before_date),
        ).fetchall()

        if not target_groups:
            conn.close()
            return {
                "success": False,
                "stock_code": code,
                "stock_name": "",
                "themes": [],
                "peer_stocks": [],
                "error": "该股票未在历史共现群组中找到记录",
            }

        stock_name = str(target_groups[0][3]) if target_groups else ""
        group_ids = [str(row[0]) for row in target_groups]
        themes = []
        for row in target_groups:
            t = str(row[1] or "").strip()
            if not t or self._is_noise_theme_label(t):
                continue
            if t not in themes:
                themes.append(t)

        # 2. 查询这些群组中的其他成员股票
        placeholders = ",".join(["?"] * len(group_ids))
        query = f"""
            SELECT
                m.stock_code,
                m.stock_name,
                COUNT(DISTINCT m.group_id) AS cooccur_count,
                AVG(m.pct_chg) AS avg_pct_chg,
                GROUP_CONCAT(DISTINCT g.theme) AS themes
            FROM longhubang_cooccur_member_2026 m
            JOIN longhubang_cooccur_group_2026 g ON m.group_id = g.group_id
            WHERE m.group_id IN ({placeholders})
              AND m.stock_code != ?
            GROUP BY m.stock_code, m.stock_name
            HAVING cooccur_count >= ?
            ORDER BY avg_pct_chg DESC
            LIMIT ?
        """

        params = group_ids + [code, min_cooccur_count, top_n]

        try:
            rows = conn.execute(query, params).fetchall()
        except Exception as e:
            conn.close()
            return {
                "success": False,
                "stock_code": code,
                "stock_name": stock_name,
                "themes": themes,
                "peer_stocks": [],
                "error": f"查询失败: {str(e)}",
            }

        peer_stocks = []
        for row in rows:
            peer_code = str(row[0])
            peer_name = str(row[1])
            cooccur_count = int(row[2])
            avg_pct_chg = float(row[3] or 0.0)
            peer_themes = [
                str(t).strip()
                for t in str(row[4] or "").split(",")
                if str(t).strip() and not self._is_noise_theme_label(str(t).strip())
            ]

            peer_stocks.append({
                "code": peer_code,
                "name": peer_name,
                "cooccur_count": cooccur_count,
                "avg_pct_chg": round(avg_pct_chg, 2),
                "themes": peer_themes[:5],
            })

        conn.close()

        self.logger.info(
            "[同题材查询] 完成 | stock=%s | themes=%s | peers=%s",
            code,
            len(themes),
            len(peer_stocks),
        )

        return {
            "success": True,
            "stock_code": code,
            "stock_name": stock_name,
            "themes": themes,
            "peer_stocks": peer_stocks,
            "error": "",
        }
