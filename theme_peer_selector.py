"""
同题材低位补涨推荐器

输入一个股票代码，尝试识别其题材线索（优先同花顺涨停池/开盘啦），
并从更大范围候选中寻找同题材且相对低位的补涨候选。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
import re
import json

import pandas as pd

from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout
from deepseek_client import DeepSeekClient


@dataclass
class PeerCandidate:
    symbol: str
    ts_code: str
    name: str
    themes: List[str]
    theme_overlap: List[str]
    theme_score: float
    low_position_score: float
    activation_score: float
    risk_penalty: float
    total_score: float
    price_position_20d: Optional[float]
    pct_chg_20d: Optional[float]
    pct_chg_today: Optional[float]
    ai_score: Optional[float]
    ai_grade: str
    ai_reason: str
    reason: str


class ThemePeerSelector:
    """同题材低位补涨推荐"""

    KPL_TAGS = [
        "涨停",
        "连板",
        "炸板",
        "跌停",
        "强势",
        "活跃",
    ]

    LIMIT_TYPES = [
        "涨停",
        "连板",
        "炸板",
        "跌停",
    ]

    def __init__(self):
        self.pro = data_source_manager.tushare_api if data_source_manager.tushare_available else None
        self._stock_basic_cache: Optional[pd.DataFrame] = None
        self._ai_client: Optional[DeepSeekClient] = None

    def recommend(
        self,
        target_symbol: str,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        exclude_boards: bool = True,
        ai_second_review: bool = False,
        ai_top_k: int = 15,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "target_symbol": self._normalize_symbol(target_symbol),
            "trade_date": self._normalize_trade_date(trade_date),
            "target_name": "",
            "target_themes": [],
            "source": {
                "target_theme": [],
                "peer_pool": [],
            },
            "ai_second_review": bool(ai_second_review),
            "ai_review_success": False,
            "ai_review_error": "",
            "candidates": [],
            "error": "",
        }

        if not self.pro:
            out["error"] = "Tushare 不可用，请检查 token/代理配置"
            return out

        symbol = out["target_symbol"]
        if not symbol:
            out["error"] = "请输入6位A股代码"
            return out

        stock_map = self._get_stock_name_map()
        ts_code = self._to_ts_code(symbol)
        out["target_name"] = stock_map.get(symbol, "")

        target_themes, target_theme_sources = self._get_target_themes(symbol, out["trade_date"])
        out["target_themes"] = target_themes
        out["source"]["target_theme"] = target_theme_sources

        if not target_themes:
            out["error"] = "未识别到目标股票题材线索（当日开盘啦/同花顺数据可能缺失）"
            return out

        peer_symbols, peer_sources = self._build_peer_pool(target_themes, out["trade_date"])
        out["source"]["peer_pool"] = peer_sources

        if symbol in peer_symbols:
            peer_symbols.remove(symbol)

        if exclude_boards:
            peer_symbols = {x for x in peer_symbols if not self._is_filtered_market(x)}

        if not peer_symbols:
            out["error"] = "未找到同题材候选股票"
            return out

        ranked = self._rank_candidates(
            target_symbol=symbol,
            target_ts_code=ts_code,
            target_themes=target_themes,
            peer_symbols=sorted(peer_symbols),
            trade_date=out["trade_date"],
            top_n=top_n,
            stock_map=stock_map,
        )
        if not ranked:
            out["error"] = "候选打分后为空（可能行情数据缺失）"
            return out

        if ai_second_review:
            ranked, ai_meta = self._apply_ai_second_review(
                target_symbol=symbol,
                target_name=out["target_name"],
                target_themes=target_themes,
                trade_date=out["trade_date"],
                ranked=ranked,
                ai_top_k=ai_top_k,
            )
            out["ai_review_success"] = bool(ai_meta.get("success"))
            out["ai_review_error"] = str(ai_meta.get("error") or "")

        out["success"] = True
        out["candidates"] = [self._candidate_to_dict(x) for x in ranked]
        return out

    def _candidate_to_dict(self, item: PeerCandidate) -> Dict[str, Any]:
        return {
            "symbol": item.symbol,
            "ts_code": item.ts_code,
            "name": item.name,
            "themes": item.themes,
            "theme_overlap": item.theme_overlap,
            "theme_score": round(item.theme_score, 2),
            "low_position_score": round(item.low_position_score, 2),
            "activation_score": round(item.activation_score, 2),
            "risk_penalty": round(item.risk_penalty, 2),
            "total_score": round(item.total_score, 2),
            "price_position_20d": None if item.price_position_20d is None else round(item.price_position_20d, 4),
            "pct_chg_20d": None if item.pct_chg_20d is None else round(item.pct_chg_20d, 2),
            "pct_chg_today": None if item.pct_chg_today is None else round(item.pct_chg_today, 2),
            "ai_score": None if item.ai_score is None else round(item.ai_score, 2),
            "ai_grade": item.ai_grade,
            "ai_reason": item.ai_reason,
            "reason": item.reason,
        }

    def _apply_ai_second_review(
        self,
        target_symbol: str,
        target_name: str,
        target_themes: List[str],
        trade_date: str,
        ranked: List[PeerCandidate],
        ai_top_k: int = 15,
    ) -> Tuple[List[PeerCandidate], Dict[str, Any]]:
        meta = {"success": False, "error": ""}
        if not ranked:
            meta["error"] = "empty_candidates"
            return ranked, meta

        top_k = max(3, min(int(ai_top_k or 15), len(ranked), 20))
        review_slice = ranked[:top_k]

        payload = []
        for item in review_slice:
            payload.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "rule_score": round(item.total_score, 2),
                    "theme_overlap": item.theme_overlap[:4],
                    "price_position_20d": item.price_position_20d,
                    "pct_chg_20d": item.pct_chg_20d,
                    "pct_chg_today": item.pct_chg_today,
                    "risk_penalty": round(item.risk_penalty, 2),
                }
            )

        prompt = f"""
你是A股短线交易策略评审员。请对“同题材低位补涨候选”做二次评审，只输出JSON。

交易日: {trade_date}
目标股票: {target_symbol} {target_name}
目标题材: {", ".join(target_themes[:8])}

候选列表(JSON):
{json.dumps(payload, ensure_ascii=False)}

请返回严格JSON数组，每个元素包含：
- symbol: 股票代码
- ai_score: 0-100分
- ai_grade: A/B/C
- ai_reason: 不超过40字

评审规则：
1) 题材重合高且位置较低、20日涨幅不过热，可加分；
2) 当日已接近涨停、20日涨幅过高、上影风险高，降分；
3) A表示优先关注，B表示可跟踪，C表示谨慎。
只返回JSON，不要解释文字。
""".strip()

        try:
            client = self._get_ai_client()
            messages = [
                {"role": "system", "content": "你是严格输出JSON的量化评审助手。"},
                {"role": "user", "content": prompt},
            ]
            raw = client.call_api(messages=messages, temperature=0.2, max_tokens=1800)
            parsed = self._parse_ai_json(raw)
            if not parsed:
                meta["error"] = "ai_json_parse_failed"
                return ranked, meta
        except Exception as exc:
            meta["error"] = f"ai_exception:{exc}"
            return ranked, meta

        by_symbol = {}
        for row in parsed:
            symbol = self._normalize_symbol(row.get("symbol"))
            if not symbol:
                continue
            ai_score = self._to_float(row.get("ai_score"))
            grade = str(row.get("ai_grade") or "").strip().upper()
            if grade not in {"A", "B", "C"}:
                grade = ""
            ai_reason = str(row.get("ai_reason") or "").strip()
            by_symbol[symbol] = {
                "ai_score": ai_score,
                "ai_grade": grade,
                "ai_reason": ai_reason[:80],
            }

        if not by_symbol:
            meta["error"] = "ai_empty_result"
            return ranked, meta

        adjusted: List[PeerCandidate] = []
        for item in ranked:
            row = by_symbol.get(item.symbol, {})
            ai_score = row.get("ai_score")
            ai_grade = row.get("ai_grade", "")
            ai_reason = row.get("ai_reason", "")
            item.ai_score = ai_score
            item.ai_grade = ai_grade
            item.ai_reason = ai_reason

            if ai_score is not None:
                # 规则分为主，AI仅做轻量重排，避免模型输出主导整体稳定性
                item.total_score = item.total_score + (ai_score - 50.0) / 10.0
            adjusted.append(item)

        adjusted.sort(key=lambda x: x.total_score, reverse=True)
        meta["success"] = True
        return adjusted, meta

    def _parse_ai_json(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        stripped = str(text).strip()
        fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", stripped, flags=re.S)
        candidate = fenced.group(1) if fenced else stripped
        if not candidate.startswith("["):
            left = candidate.find("[")
            right = candidate.rfind("]")
            if left >= 0 and right > left:
                candidate = candidate[left : right + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            return []
        return []

    def _get_ai_client(self) -> DeepSeekClient:
        if self._ai_client is None:
            self._ai_client = DeepSeekClient()
        return self._ai_client

    def _rank_candidates(
        self,
        target_symbol: str,
        target_ts_code: str,
        target_themes: List[str],
        peer_symbols: List[str],
        trade_date: str,
        top_n: int,
        stock_map: Dict[str, str],
    ) -> List[PeerCandidate]:
        # 控制单次分析量，避免界面等待过久
        peer_symbols = peer_symbols[:220]

        peer_theme_map = self._get_symbol_theme_map(peer_symbols, trade_date)
        day_snapshot = self._get_daily_snapshot(peer_symbols, trade_date)

        out: List[PeerCandidate] = []
        for symbol in peer_symbols:
            themes = peer_theme_map.get(symbol, [])
            overlap = self._calc_theme_overlap(target_themes, themes)
            if not overlap:
                continue

            ts_code = self._to_ts_code(symbol)
            kline = self._get_recent_kline(ts_code, trade_date, days=20)
            if kline is None or kline.empty:
                continue

            low_position_score, price_pos, pct_20d = self._score_low_position(kline)
            if low_position_score <= 0:
                continue

            pct_today = None
            activation_score = 0.0
            risk_penalty = 0.0
            snap = day_snapshot.get(symbol, {})
            if snap:
                pct_today = self._to_float(snap.get("pct_chg"))
                activation_score = self._score_activation(snap)
                risk_penalty = self._score_risk_penalty(snap, kline)

            theme_score = self._score_theme_similarity(target_themes, overlap)
            total = theme_score + low_position_score + activation_score - risk_penalty
            if total <= 0:
                continue

            reason = self._build_reason(overlap, price_pos, pct_20d, pct_today, total)
            out.append(
                PeerCandidate(
                    symbol=symbol,
                    ts_code=ts_code,
                    name=stock_map.get(symbol, ""),
                    themes=themes[:6],
                    theme_overlap=overlap,
                    theme_score=theme_score,
                    low_position_score=low_position_score,
                    activation_score=activation_score,
                    risk_penalty=risk_penalty,
                    total_score=total,
                    price_position_20d=price_pos,
                    pct_chg_20d=pct_20d,
                    pct_chg_today=pct_today,
                    ai_score=None,
                    ai_grade="",
                    ai_reason="",
                    reason=reason,
                )
            )

        out.sort(key=lambda x: x.total_score, reverse=True)
        return out[:max(int(top_n), 1)]

    def _score_theme_similarity(self, target_themes: List[str], overlap: List[str]) -> float:
        hit_ratio = len(overlap) / max(len(target_themes), 1)
        base = min(len(overlap), 3) * 10.0
        return min(45.0, base + hit_ratio * 15.0)

    def _score_low_position(self, df: pd.DataFrame) -> Tuple[float, Optional[float], Optional[float]]:
        close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
        if len(close) < 10:
            return 0.0, None, None

        latest = float(close.iloc[-1])
        low = float(close.min())
        high = float(close.max())
        if high <= 0 or high <= low:
            return 0.0, None, None

        pos = (latest - low) / (high - low)
        pct_20d = (latest / float(close.iloc[0]) - 1.0) * 100.0

        # 低位补涨偏好：位置在0.25~0.65更优，过高/过低都降分
        if pos < 0.15 or pos > 0.85:
            return 0.0, pos, pct_20d

        pos_score = max(0.0, 1.0 - abs(pos - 0.45) / 0.40) * 20.0

        # 避免已经大涨过：20日涨幅越高，补涨性价比越低
        if pct_20d <= 0:
            pct_score = 10.0
        elif pct_20d <= 12:
            pct_score = 10.0 - (pct_20d / 12.0) * 4.0
        elif pct_20d <= 25:
            pct_score = max(0.0, 6.0 - (pct_20d - 12.0) * 0.35)
        else:
            pct_score = 0.0

        return pos_score + pct_score, pos, pct_20d

    def _score_activation(self, snap: Dict[str, Any]) -> float:
        pct = self._to_float(snap.get("pct_chg"))
        amount = self._to_float(snap.get("amount"))
        score = 0.0

        # 温和走强优于极端拉升
        if pct is not None:
            if 1.0 <= pct <= 7.0:
                score += 10.0
            elif 0.0 <= pct < 1.0:
                score += 6.0
            elif 7.0 < pct <= 9.8:
                score += 3.0
            elif pct < -2.5:
                score -= 5.0

        # 仅作轻量参考，防止成交额过小
        if amount is not None:
            if amount >= 300000:
                score += 4.0
            elif amount >= 100000:
                score += 2.0

        return max(0.0, min(score, 15.0))

    def _score_risk_penalty(self, snap: Dict[str, Any], kline: pd.DataFrame) -> float:
        penalty = 0.0
        pct = self._to_float(snap.get("pct_chg"))

        if pct is not None and pct >= 9.5:
            penalty += 6.0  # 当日接近涨停，补涨空间相对下降

        if len(kline) >= 5:
            high = pd.to_numeric(kline["high"], errors="coerce")
            close = pd.to_numeric(kline["close"], errors="coerce")
            open_ = pd.to_numeric(kline["open"], errors="coerce")
            latest_high = self._to_float(high.iloc[-1])
            latest_close = self._to_float(close.iloc[-1])
            latest_open = self._to_float(open_.iloc[-1])
            if latest_high and latest_close and latest_open and latest_high > 0:
                upper = (latest_high - max(latest_close, latest_open)) / latest_high
                if upper > 0.045:
                    penalty += 5.0

        return min(penalty, 12.0)

    def _build_reason(
        self,
        overlap: List[str],
        price_pos: Optional[float],
        pct_20d: Optional[float],
        pct_today: Optional[float],
        total: float,
    ) -> str:
        parts = [f"题材重合: {'/'.join(overlap[:3])}"]
        if price_pos is not None:
            parts.append(f"20日位置: {price_pos * 100:.1f}%")
        if pct_20d is not None:
            parts.append(f"20日涨幅: {pct_20d:.1f}%")
        if pct_today is not None:
            parts.append(f"当日涨跌幅: {pct_today:.1f}%")
        parts.append(f"综合分: {total:.1f}")
        return "；".join(parts)

    def _get_target_themes(self, symbol: str, trade_date: str) -> Tuple[List[str], List[str]]:
        themes: List[str] = []
        source: List[str] = []

        # 1) 同花顺涨停池（lu_desc）
        limit_map = self._get_limit_symbol_theme_map(trade_date, symbols=[symbol])
        from_limit = limit_map.get(symbol, [])
        if from_limit:
            themes.extend(from_limit)
            source.append("limit_list_ths.lu_desc")

        # 2) 开盘啦（theme/reason/lu_desc）
        kpl_map = self._get_kpl_symbol_theme_map(trade_date, symbols=[symbol])
        from_kpl = kpl_map.get(symbol, [])
        if from_kpl:
            themes.extend(from_kpl)
            source.append("kpl_list")

        deduped = self._dedup_tokens(themes)
        return deduped[:10], source

    def _build_peer_pool(self, target_themes: List[str], trade_date: str) -> Tuple[set, List[str]]:
        pool = set()
        source: List[str] = []

        # A. 优先尝试同花顺主题成分（覆盖全市场）
        ths_symbols = self._get_peers_from_ths_member(target_themes)
        if ths_symbols:
            pool.update(ths_symbols)
            source.append("ths_index+ths_member")

        # B. 用当日榜单题材补充（开盘啦 + 同花顺涨停池）
        kpl_map = self._get_kpl_symbol_theme_map(trade_date)
        if kpl_map:
            for symbol, themes in kpl_map.items():
                if self._calc_theme_overlap(target_themes, themes):
                    pool.add(symbol)
            source.append("kpl_list")

        limit_map = self._get_limit_symbol_theme_map(trade_date)
        if limit_map:
            for symbol, themes in limit_map.items():
                if self._calc_theme_overlap(target_themes, themes):
                    pool.add(symbol)
            source.append("limit_list_ths")

        return pool, source

    def _get_peers_from_ths_member(self, target_themes: List[str]) -> set:
        index_df = self._fetch_ths_index_df()
        if index_df is None or index_df.empty:
            return set()

        name_col = self._find_col(index_df, ["name", "ths_name", "index_name"])
        code_col = self._find_col(index_df, ["ts_code", "ths_code", "code"])
        if not name_col or not code_col:
            return set()

        matched_codes: List[str] = []
        for _, row in index_df.iterrows():
            name = str(row.get(name_col) or "").strip()
            if not name:
                continue
            if any(t in name or name in t for t in target_themes):
                code = str(row.get(code_col) or "").strip()
                if code:
                    matched_codes.append(code)

        matched_codes = list(dict.fromkeys(matched_codes))[:8]
        out = set()
        for idx_code in matched_codes:
            members = self._fetch_ths_member(idx_code)
            if members is None or members.empty:
                continue
            for col in ["con_code", "code", "ts_code", "member_code"]:
                if col not in members.columns:
                    continue
                for value in members[col].tolist():
                    symbol = self._normalize_symbol(value)
                    if symbol:
                        out.add(symbol)
        return out

    def _fetch_ths_index_df(self) -> Optional[pd.DataFrame]:
        if not hasattr(self.pro, "ths_index"):
            return None
        candidates = [
            {"exchange": "A"},
            {"exchange": "A", "type": "N"},
            {},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda p=kwargs: self.pro.ths_index(**p),
                    timeout_sec=25,
                    api_name="ths_index",
                )
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return None

    def _fetch_ths_member(self, idx_code: str) -> Optional[pd.DataFrame]:
        if not hasattr(self.pro, "ths_member"):
            return None
        candidates = [
            {"ts_code": idx_code},
            {"ths_code": idx_code},
            {"code": idx_code},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda p=kwargs: self.pro.ths_member(**p),
                    timeout_sec=20,
                    api_name="ths_member",
                )
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return None

    def _get_symbol_theme_map(self, symbols: List[str], trade_date: str) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for symbol, themes in self._get_kpl_symbol_theme_map(trade_date, symbols=symbols).items():
            out.setdefault(symbol, []).extend(themes)
        for symbol, themes in self._get_limit_symbol_theme_map(trade_date, symbols=symbols).items():
            out.setdefault(symbol, []).extend(themes)
        for symbol in list(out.keys()):
            out[symbol] = self._dedup_tokens(out[symbol])
        return out

    def _get_kpl_symbol_theme_map(
        self,
        trade_date: str,
        symbols: Optional[Iterable[str]] = None,
    ) -> Dict[str, List[str]]:
        if not hasattr(self.pro, "kpl_list"):
            return {}

        symbol_set = {self._normalize_symbol(x) for x in symbols} if symbols else None
        out: Dict[str, List[str]] = {}

        for tag in self.KPL_TAGS:
            kwargs = {"trade_date": trade_date, "tag": tag}
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda p=kwargs: self.pro.kpl_list(**p),
                    timeout_sec=25,
                    api_name="kpl_list",
                )
            except Exception:
                continue
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue

            code_col = self._find_col(df, ["ts_code", "code", "symbol", "con_code"])
            if not code_col:
                continue
            theme_col = self._find_col(df, ["theme", "plate_name", "plate", "name"])
            reason_col = self._find_col(df, ["lu_desc", "reason", "原因", "解读"])

            for _, row in df.iterrows():
                symbol = self._normalize_symbol(row.get(code_col))
                if not symbol:
                    continue
                if symbol_set is not None and symbol not in symbol_set:
                    continue

                themes = []
                if theme_col:
                    themes.extend(self._split_theme_tokens(row.get(theme_col)))
                if reason_col:
                    themes.extend(self._split_theme_tokens(row.get(reason_col)))
                if themes:
                    out.setdefault(symbol, []).extend(themes)

        for symbol in list(out.keys()):
            out[symbol] = self._dedup_tokens(out[symbol])
        return out

    def _get_limit_symbol_theme_map(
        self,
        trade_date: str,
        symbols: Optional[Iterable[str]] = None,
    ) -> Dict[str, List[str]]:
        if not hasattr(self.pro, "limit_list_ths"):
            return {}

        symbol_set = {self._normalize_symbol(x) for x in symbols} if symbols else None
        out: Dict[str, List[str]] = {}

        for limit_type in self.LIMIT_TYPES:
            kwargs = {"trade_date": trade_date, "limit_type": limit_type}
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda p=kwargs: self.pro.limit_list_ths(**p),
                    timeout_sec=25,
                    api_name="limit_list_ths",
                )
            except Exception:
                continue
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue

            code_col = self._find_col(df, ["ts_code", "code", "symbol", "con_code"])
            lu_col = self._find_col(df, ["lu_desc", "reason", "原因", "题材"])
            if not code_col or not lu_col:
                continue

            for _, row in df.iterrows():
                symbol = self._normalize_symbol(row.get(code_col))
                if not symbol:
                    continue
                if symbol_set is not None and symbol not in symbol_set:
                    continue
                themes = self._split_theme_tokens(row.get(lu_col))
                if themes:
                    out.setdefault(symbol, []).extend(themes)

        for symbol in list(out.keys()):
            out[symbol] = self._dedup_tokens(out[symbol])
        return out

    def _get_daily_snapshot(self, symbols: List[str], trade_date: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not hasattr(self.pro, "daily"):
            return out
        for symbol in symbols:
            ts_code = self._to_ts_code(symbol)
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda t=ts_code: self.pro.daily(ts_code=t, start_date=trade_date, end_date=trade_date),
                    timeout_sec=15,
                    api_name="daily",
                )
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                out[symbol] = df.iloc[0].to_dict()
        return out

    def _get_recent_kline(self, ts_code: str, trade_date: str, days: int = 20) -> Optional[pd.DataFrame]:
        if not hasattr(self.pro, "daily"):
            return None

        end_dt = datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=max(days * 3, 35))

        try:
            df = call_tushare_with_timeout(
                api_callable=lambda: self.pro.daily(
                    ts_code=ts_code,
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=trade_date,
                ),
                timeout_sec=18,
                api_name="daily",
            )
        except Exception:
            return None

        if not isinstance(df, pd.DataFrame) or df.empty:
            return None

        if "trade_date" in df.columns:
            df = df.sort_values("trade_date")
        return df.tail(days)

    def _get_stock_name_map(self) -> Dict[str, str]:
        if self._stock_basic_cache is None:
            self._stock_basic_cache = self._fetch_stock_basic()

        df = self._stock_basic_cache
        if df is None or df.empty:
            return {}

        code_col = self._find_col(df, ["symbol", "code", "ts_code"])
        name_col = self._find_col(df, ["name"])
        if not code_col or not name_col:
            return {}

        out = {}
        for _, row in df.iterrows():
            symbol = self._normalize_symbol(row.get(code_col))
            if not symbol:
                continue
            out[symbol] = str(row.get(name_col) or "").strip()
        return out

    def _fetch_stock_basic(self) -> pd.DataFrame:
        if not hasattr(self.pro, "stock_basic"):
            return pd.DataFrame()

        candidates = [
            {"exchange": "", "list_status": "L", "fields": "ts_code,symbol,name,market"},
            {"list_status": "L", "fields": "ts_code,symbol,name,market"},
            {"fields": "ts_code,symbol,name,market"},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda p=kwargs: self.pro.stock_basic(**p),
                    timeout_sec=35,
                    api_name="stock_basic",
                )
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        return pd.DataFrame()

    def _calc_theme_overlap(self, target_themes: List[str], candidate_themes: List[str]) -> List[str]:
        overlap = []
        for t in target_themes:
            for c in candidate_themes:
                if t == c or t in c or c in t:
                    overlap.append(t if len(t) <= len(c) else c)
                    break
        return self._dedup_tokens(overlap)

    def _split_theme_tokens(self, value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        text = re.sub(r"[，,、;/；|+]+", "|", text)
        parts = [x.strip() for x in text.split("|") if x and x.strip()]
        cleaned: List[str] = []
        for item in parts:
            if item in {"涨停", "跌停", "连板", "炸板", "首板", "主板"}:
                continue
            if len(item) < 2 or len(item) > 16:
                continue
            cleaned.append(item)
        return cleaned

    def _dedup_tokens(self, items: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for raw in items:
            token = str(raw or "").strip()
            if not token:
                continue
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    def _normalize_trade_date(self, value: Optional[str]) -> str:
        if not value:
            return datetime.now().strftime("%Y%m%d")
        text = str(value).strip().replace("-", "")
        if re.fullmatch(r"\d{8}", text):
            return text
        return datetime.now().strftime("%Y%m%d")

    def _to_ts_code(self, symbol: str) -> str:
        symbol = self._normalize_symbol(symbol)
        if not symbol:
            return ""
        if symbol.startswith(("6", "9")):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def _normalize_symbol(self, value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        m = re.search(r"(\d{6})", text)
        return m.group(1) if m else ""

    def _find_col(self, df: pd.DataFrame, candidates: List[str]) -> str:
        cols = list(df.columns)
        lower_map = {str(c).lower(): c for c in cols}
        for key in candidates:
            if key in cols:
                return key
            low = key.lower()
            if low in lower_map:
                return lower_map[low]
        for key in candidates:
            low = key.lower()
            for c in cols:
                if low in str(c).lower():
                    return c
        return ""

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _is_filtered_market(self, symbol: str) -> bool:
        # 过滤创业板/科创板（与现有用户偏好一致）
        return symbol.startswith("300") or symbol.startswith("688")
