"""
统一交易日历服务（Tushare trade_cal）
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from services.data_source_manager import data_source_manager
from infrastructure.external.tushare_proxy_rate_limit import call_tushare_with_timeout


class TradeCalendarError(ValueError):
    def __init__(self, message: str, code: str = "TRADE_CAL_ERROR", nearest_open_day: str = ""):
        super().__init__(message)
        self.code = code
        self.nearest_open_day = nearest_open_day


class TradeCalendarService:
    """基于 Tushare trade_cal 的交易日服务（进程内缓存）。"""

    _OPEN_DAY_CACHE: Dict[int, Set[str]] = {}

    def _to_date8(self, value: Any) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "").replace(".", "")
        return text if re.fullmatch(r"\d{8}", text) else ""

    def _fmt_dash(self, date8: str) -> str:
        if not date8 or len(date8) != 8:
            return ""
        return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"

    def _ensure_year_loaded(self, year: int) -> None:
        if year in self._OPEN_DAY_CACHE:
            return
        pro = data_source_manager.tushare_api
        if not data_source_manager.tushare_available or pro is None or not hasattr(pro, "trade_cal"):
            raise TradeCalendarError("交易日服务不可用（trade_cal不可用）", code="TRADE_CAL_UNAVAILABLE")
        start_date = f"{year}0101"
        end_date = f"{year}1231"
        kwargs_list = [
            {"exchange": "SSE", "start_date": start_date, "end_date": end_date},
            {"exchange": "SZSE", "start_date": start_date, "end_date": end_date},
            {"start_date": start_date, "end_date": end_date},
        ]
        df: Optional[pd.DataFrame] = None
        for kwargs in kwargs_list:
            try:
                data = call_tushare_with_timeout(
                    api_callable=lambda kw=kwargs: pro.trade_cal(**kw),
                    timeout_sec=60,
                    api_name="trade_cal",
                )
            except Exception:
                continue
            if isinstance(data, pd.DataFrame) and not data.empty:
                df = data
                break
        if df is None or df.empty:
            raise TradeCalendarError("交易日服务不可用（trade_cal无数据）", code="TRADE_CAL_UNAVAILABLE")

        cal_col = self._find_col(df, ["cal_date", "trade_date", "date", "日期"])
        open_col = self._find_col(df, ["is_open", "open", "交易", "开市"])
        if not cal_col or not open_col:
            raise TradeCalendarError("交易日服务不可用（trade_cal字段缺失）", code="TRADE_CAL_UNAVAILABLE")

        open_days: Set[str] = set()
        for _, row in df.iterrows():
            d = self._to_date8(row.get(cal_col))
            if not d:
                continue
            is_open_raw = str(row.get(open_col) or "").strip()
            is_open = is_open_raw in {"1", "True", "true", "Y", "y", "开市"}
            if is_open:
                open_days.add(d)
        if not open_days:
            raise TradeCalendarError("交易日服务不可用（trade_cal开市日为空）", code="TRADE_CAL_UNAVAILABLE")
        self._OPEN_DAY_CACHE[year] = open_days

    def _find_col(self, df: pd.DataFrame, keys: List[str]) -> str:
        for col in df.columns:
            c = str(col).lower()
            if any(k.lower() in c for k in keys):
                return col
        return ""

    def is_trading_day(self, date_value: Any) -> bool:
        date8 = self._to_date8(date_value)
        if not date8:
            return False
        year = int(date8[:4])
        self._ensure_year_loaded(year)
        return date8 in self._OPEN_DAY_CACHE.get(year, set())

    def nearest_open_day(self, date_value: Any) -> str:
        date8 = self._to_date8(date_value)
        if not date8:
            return ""
        year = int(date8[:4])
        years = [year, year - 1]
        days: List[str] = []
        for y in years:
            self._ensure_year_loaded(y)
            days.extend(sorted(list(self._OPEN_DAY_CACHE.get(y, set()))))
        days = [d for d in days if d <= date8]
        return days[-1] if days else ""

    def normalize_or_raise(self, date_value: Any) -> str:
        date8 = self._to_date8(date_value)
        if not date8:
            raise TradeCalendarError("日期格式无效，请使用YYYY-MM-DD", code="INVALID_DATE")
        if not self.is_trading_day(date8):
            nearest = self.nearest_open_day(date8)
            nearest_dash = self._fmt_dash(nearest) if nearest else ""
            raise TradeCalendarError(
                f"所选日期非交易日，请改为最近交易日 {nearest_dash or 'N/A'}",
                code="NON_TRADING_DAY",
                nearest_open_day=nearest_dash,
            )
        return date8

    def recent_open_days(self, end_date: Optional[Any], n: int) -> List[str]:
        target_n = max(int(n or 0), 1)
        if end_date:
            end8 = self._to_date8(end_date)
        else:
            end8 = datetime.now().strftime("%Y%m%d")
        if not end8:
            end8 = datetime.now().strftime("%Y%m%d")
        year = int(end8[:4])
        years = [year, year - 1, year - 2]
        days: List[str] = []
        for y in years:
            self._ensure_year_loaded(y)
            days.extend(self._OPEN_DAY_CACHE.get(y, set()))
        days = sorted([d for d in set(days) if d <= end8], reverse=True)
        return days[:target_n]

    def open_days_between(self, start_date: Any, end_date: Any) -> List[str]:
        start8 = self._to_date8(start_date)
        end8 = self._to_date8(end_date)
        if not start8 or not end8 or start8 > end8:
            return []
        start_y = int(start8[:4])
        end_y = int(end8[:4])
        out: List[str] = []
        for y in range(start_y, end_y + 1):
            self._ensure_year_loaded(y)
            out.extend(self._OPEN_DAY_CACHE.get(y, set()))
        return sorted([d for d in set(out) if start8 <= d <= end8])
