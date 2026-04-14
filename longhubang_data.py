"""
智瞰龙虎数据采集模块
使用StockAPI获取龙虎榜数据
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import warnings
import re
from typing import Any, Dict, List, Optional

from data_source_manager import data_source_manager
from tushare_proxy_rate_limit import call_tushare_with_timeout

warnings.filterwarnings('ignore')


class LonghubangDataFetcher:
    """龙虎榜数据获取类"""
    
    def __init__(self, api_key=None):
        """
        初始化数据获取器
        
        Args:
            api_key: StockAPI的API密钥（可选，普通请求每日免费1000次）
        """
        print("[智瞰龙虎] 龙虎榜数据获取器初始化...")
        # self.base_url = "https://api-lhb.zhongdu.net"
        self.base_url = "http://lhb-api.ws4.cn/v1"
       # self.base_url = "https://www.stockapi.com.cn/v1"
        self.api_key = api_key
        self.max_retries = 3  # 最大重试次数
        self.retry_delay = 2  # 重试延迟（秒）
        self.request_delay = 0.025  # 请求间隔（秒），40次/秒 = 0.025秒/次
    
    def _safe_request(self, url, params=None):
        """
        安全的HTTP请求，包含重试机制
        
        Args:
            url: 请求URL
            params: 请求参数
            
        Returns:
            dict: 响应数据
        """
        for attempt in range(self.max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                
                # 添加请求延迟，遵守40次/秒的限制
                time.sleep(self.request_delay)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 20000:
                        return data
                    else:
                        print(f"    API返回错误: {data.get('msg', '未知错误')}")
                        return None
                else:
                    print(f"    HTTP错误: {response.status_code}")
                    
            except Exception as e:
                if attempt < self.max_retries - 1:
                    print(f"    请求失败，{self.retry_delay}秒后重试... (尝试 {attempt + 1}/{self.max_retries})")
                    time.sleep(self.retry_delay)
                else:
                    print(f"    请求失败，已达最大重试次数: {e}")
                    return None
        
        return None

    def _safe_float(self, value: Any) -> float:
        if value is None:
            return 0.0
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return 0.0
        m = re.search(r"[-+]?\d*\.?\d+", text)
        if not m:
            return 0.0
        try:
            return float(m.group())
        except Exception:
            return 0.0

    def _normalize_stock_code(self, value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if "." in text:
            text = text.split(".", 1)[0]
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if m:
            return m.group(1)
        return text if len(text) == 6 and text.isdigit() else ""

    def _normalize_trade_date(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        text = text.replace("/", "-").replace(".", "-")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        return ""

    def _to_trade_date_compact(self, date: str) -> str:
        text = str(date or "").strip().replace("-", "").replace("/", "").replace(".", "")
        return text if re.fullmatch(r"\d{8}", text) else ""

    def _parse_date_safe(self, value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    def _date_to_key(self, value: Any) -> str:
        norm = self._normalize_trade_date(value)
        if norm:
            return norm.replace("-", "")
        text = str(value or "").strip().replace("-", "").replace("/", "").replace(".", "")
        return text if re.fullmatch(r"\d{8}", text) else ""

    def _build_recent_trade_dates(self, days: int = 3, end_date: Optional[str] = None) -> List[str]:
        target_days = max(int(days or 0), 1)
        end_dt = self._parse_date_safe(end_date) if end_date else None
        if end_dt is None:
            end_dt = datetime.now()
        out: List[str] = []
        cur = end_dt
        # 仅按工作日近似交易日，最多回看60个自然日防止异常死循环
        while len(out) < target_days and len(out) < 60:
            if cur.weekday() < 5:
                out.append(cur.strftime("%Y-%m-%d"))
            cur -= timedelta(days=1)
        return out

    def _find_col(self, df: pd.DataFrame, keywords: List[str]) -> str:
        for col in df.columns:
            name = str(col).lower()
            if any(k.lower() in name for k in keywords):
                return col
        return ""

    def _extract_top_list_records(self, df: pd.DataFrame, requested_date: str) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []

        code_col = self._find_col(df, ["ts_code", "stock_code", "代码", "证券代码", "code", "symbol"])
        if not code_col:
            return []
        name_col = self._find_col(df, ["name", "股票名称", "证券简称"])
        buy_col = self._find_col(df, ["l_buy", "buy", "买入"])
        sell_col = self._find_col(df, ["l_sell", "sell", "卖出"])
        net_col = self._find_col(df, ["net_amount", "net", "净额", "净买入"])
        reason_col = self._find_col(df, ["reason", "上榜原因"])
        date_col = self._find_col(df, ["trade_date", "date", "日期"])

        # Tushare top_list 金额字段常见单位为“万元”，这里做启发式自动换算到“元”
        sample_values: List[float] = []
        for col in [buy_col, sell_col, net_col]:
            if not col or col not in df.columns:
                continue
            for v in df[col].head(20).tolist():
                x = abs(self._safe_float(v))
                if x > 0:
                    sample_values.append(x)
        amount_scale = 10000.0 if sample_values and max(sample_values) < 1e7 else 1.0

        records: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            code = self._normalize_stock_code(row.get(code_col))
            if not code:
                continue
            buy_raw = self._safe_float(row.get(buy_col)) if buy_col else 0.0
            sell_raw = self._safe_float(row.get(sell_col)) if sell_col else 0.0
            net_raw = self._safe_float(row.get(net_col)) if net_col else (buy_raw - sell_raw)
            buy_amt = buy_raw * amount_scale
            sell_amt = sell_raw * amount_scale
            net_amt = net_raw * amount_scale
            trade_date = self._normalize_trade_date(row.get(date_col)) if date_col else ""
            if not trade_date:
                trade_date = requested_date
            reason = str(row.get(reason_col) or "").strip() if reason_col else "Tushare龙虎榜日榜"

            # 转换成现有龙虎榜链路可直接消费的统一字段
            records.append(
                {
                    "rq": trade_date,
                    "gpdm": code,
                    "gpmc": str(row.get(name_col) or "").strip() if name_col else "",
                    "yzmc": "Tushare日榜明细",
                    "yyb": "Tushare top_list",
                    "sblx": reason or "Tushare龙虎榜日榜",
                    "mrje": buy_amt,
                    "mcje": sell_amt,
                    "jlrje": net_amt,
                    "gl": "",
                    "data_source": "tushare.top_list",
                }
            )
        return records

    def _get_tushare_top_list_data(self, date: str) -> Optional[Dict[str, Any]]:
        if not data_source_manager.tushare_available or data_source_manager.tushare_api is None:
            return None
        pro = data_source_manager.tushare_api
        if not hasattr(pro, "top_list"):
            return None

        trade_date = self._to_trade_date_compact(date)
        if not trade_date:
            return None

        candidates = [
            {"trade_date": trade_date},
            {"date": trade_date},
            {"trade_date": trade_date, "limit": 5000},
            {"date": trade_date, "limit": 5000},
        ]
        for kwargs in candidates:
            try:
                df = call_tushare_with_timeout(
                    api_callable=lambda: pro.top_list(**kwargs),
                    timeout_sec=60,
                    api_name="top_list",
                )
            except TypeError:
                continue
            except TimeoutError:
                continue
            except Exception:
                continue

            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            records = self._extract_top_list_records(df, requested_date=date)
            if not records:
                continue
            return {
                "code": 20000,
                "msg": "success(fallback:tushare.top_list)",
                "data": records,
                "source": "tushare.top_list",
            }
        return None

    def prioritize_latest_stock_records(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        同一股票跨交易日重复时，仅保留最近交易日的榜单记录；
        同一最近交易日内的多席位记录全部保留。
        """
        if not data_list:
            return []

        latest_date_by_code: Dict[str, str] = {}
        for record in data_list:
            code = self._normalize_stock_code(record.get("gpdm") or record.get("股票代码") or record.get("ts_code"))
            if not code:
                continue
            date_key = self._date_to_key(record.get("rq") or record.get("日期") or record.get("trade_date"))
            if not date_key:
                continue
            old = latest_date_by_code.get(code, "")
            if not old or date_key > old:
                latest_date_by_code[code] = date_key

        if not latest_date_by_code:
            return data_list

        filtered: List[Dict[str, Any]] = []
        for record in data_list:
            code = self._normalize_stock_code(record.get("gpdm") or record.get("股票代码") or record.get("ts_code"))
            if not code:
                continue
            date_key = self._date_to_key(record.get("rq") or record.get("日期") or record.get("trade_date"))
            if not date_key:
                continue
            if latest_date_by_code.get(code, "") == date_key:
                filtered.append(record)
        return filtered
    
    def get_longhubang_data(self, date):
        """
        获取指定日期的龙虎榜数据
        
        Args:
            date: 日期，格式为 YYYY-MM-DD，如 "2023-03-21"
            
        Returns:
            dict: 龙虎榜数据
        """
        print(f"[智瞰龙虎] 获取 {date} 的龙虎榜数据...")
        
        # url = f"{self.base_url}"
        url = f"{self.base_url}/youzi/all"
        params = {'date': date}
        
        result = self._safe_request(url, params)
        
        if result and result.get('data'):
            print(f"    ✓ 成功获取 {len(result['data'])} 条龙虎榜记录")
            return result

        print(f"    ✗ StockAPI未获取到数据，尝试Tushare top_list兜底...")
        fallback = self._get_tushare_top_list_data(date)
        if fallback and fallback.get("data"):
            print(f"    ✓ Tushare兜底成功，获取 {len(fallback['data'])} 条龙虎榜日榜记录")
            return fallback

        print(f"    ✗ 未获取到数据（StockAPI + Tushare兜底均失败）")
        return None
    
    def get_longhubang_data_range(self, start_date, end_date):
        """
        获取日期范围内的龙虎榜数据
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD
            end_date: 结束日期，格式为 YYYY-MM-DD
            
        Returns:
            list: 龙虎榜数据列表
        """
        print(f"[智瞰龙虎] 获取 {start_date} 至 {end_date} 的龙虎榜数据...")
        
        all_data = []
        
        # 转换日期
        current_date = datetime.strptime(start_date, '%Y-%m-%d')
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
        
        while current_date <= end_date_obj:
            date_str = current_date.strftime('%Y-%m-%d')
            
            # 跳过周末
            if current_date.weekday() < 5:  # 0-4表示周一到周五
                result = self.get_longhubang_data(date_str)
                if result and result.get('data'):
                    all_data.extend(result['data'])
            
            # 下一天
            current_date += timedelta(days=1)
        
        print(f"[智瞰龙虎] ✓ 共获取 {len(all_data)} 条记录")
        return all_data
    
    def get_recent_days_data(self, days=5, end_date: Optional[str] = None):
        """
        获取最近N个交易日的龙虎榜数据
        
        Args:
            days: 天数（默认5天）
            end_date: 结束日期（可选，格式 YYYY-MM-DD）
            
        Returns:
            list: 龙虎榜数据列表
        """
        all_data: List[Dict[str, Any]] = []
        trade_dates = self._build_recent_trade_dates(days=max(int(days or 0), 1), end_date=end_date)
        if not trade_dates:
            return all_data

        print(f"[智瞰龙虎] 获取近{len(trade_dates)}个交易日龙虎榜数据（截止 {trade_dates[0]}）...")
        for date_str in trade_dates:
            result = self.get_longhubang_data(date_str)
            if result and result.get("data"):
                all_data.extend(result["data"])
        print(f"[智瞰龙虎] ✓ 近N日共获取 {len(all_data)} 条记录")
        return all_data
    
    def parse_to_dataframe(self, data_list):
        """
        将龙虎榜数据转换为DataFrame
        
        Args:
            data_list: 龙虎榜数据列表
            
        Returns:
            pd.DataFrame: 数据框
        """
        if not data_list:
            return pd.DataFrame()
        
        df = pd.DataFrame(data_list)
        
        # 重命名列
        column_mapping = {
            'yzmc': '游资名称',
            'yyb': '营业部',
            'sblx': '榜单类型',
            'gpdm': '股票代码',
            'gpmc': '股票名称',
            'mrje': '买入金额',
            'mcje': '卖出金额',
            'jlrje': '净流入金额',
            'rq': '日期',
            'gl': '概念'
        }
        
        df = df.rename(columns=column_mapping)
        
        # 转换数据类型
        numeric_columns = ['买入金额', '卖出金额', '净流入金额']
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # 排序
        if '净流入金额' in df.columns:
            df = df.sort_values('净流入金额', ascending=False)
        
        return df
    
    def analyze_data_summary(self, data_list):
        """
        分析龙虎榜数据，生成摘要统计
        
        Args:
            data_list: 龙虎榜数据列表
            
        Returns:
            dict: 统计摘要
        """
        if not data_list:
            return {}
        
        df = self.parse_to_dataframe(data_list)
        
        summary = {
            'total_records': len(df),
            'total_stocks': df['股票代码'].nunique() if '股票代码' in df.columns else 0,
            'total_youzi': df['游资名称'].nunique() if '游资名称' in df.columns else 0,
            'total_buy_amount': df['买入金额'].sum() if '买入金额' in df.columns else 0,
            'total_sell_amount': df['卖出金额'].sum() if '卖出金额' in df.columns else 0,
            'total_net_inflow': df['净流入金额'].sum() if '净流入金额' in df.columns else 0,
        }
        
        # Top游资排名
        if '游资名称' in df.columns and '净流入金额' in df.columns:
            top_youzi = df.groupby('游资名称')['净流入金额'].sum().sort_values(ascending=False)
            summary['top_youzi'] = top_youzi.head(10).to_dict()
        
        # Top股票排名
        if '股票代码' in df.columns and '净流入金额' in df.columns:
            top_stocks = df.groupby(['股票代码', '股票名称'])['净流入金额'].sum().sort_values(ascending=False)
            summary['top_stocks'] = [
                {'code': code, 'name': name, 'net_inflow': amount}
                for (code, name), amount in top_stocks.head(20).items()
            ]
        
        # 热门概念统计
        if '概念' in df.columns:
            all_concepts = []
            for concepts in df['概念'].dropna():
                all_concepts.extend(self._split_concepts(concepts))
            
            from collections import Counter
            concept_counter = Counter(all_concepts)
            summary['hot_concepts'] = dict(concept_counter.most_common(20))
        
        return summary
    
    def format_data_for_ai(self, data_list, summary=None):
        """
        将龙虎榜数据格式化为适合AI分析的文本格式
        
        Args:
            data_list: 龙虎榜数据列表
            summary: 统计摘要（可选）
            
        Returns:
            str: 格式化的文本
        """
        if not data_list:
            return "暂无龙虎榜数据"
        
        df = self.parse_to_dataframe(data_list)
        
        if summary is None:
            summary = self.analyze_data_summary(data_list)
        
        text_parts = []
        
        # 总体概况
        text_parts.append(f"""
【龙虎榜总体概况】
数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
记录总数: {summary.get('total_records', 0)}
涉及股票: {summary.get('total_stocks', 0)} 只
涉及游资: {summary.get('total_youzi', 0)} 个
总买入金额: {summary.get('total_buy_amount', 0):,.2f} 元
总卖出金额: {summary.get('total_sell_amount', 0):,.2f} 元
净流入金额: {summary.get('total_net_inflow', 0):,.2f} 元
""")
        
        # Top游资
        if summary.get('top_youzi'):
            text_parts.append("\n【活跃游资 TOP10】")
            for idx, (name, amount) in enumerate(summary['top_youzi'].items(), 1):
                text_parts.append(f"{idx}. {name}: {amount:,.2f} 元")
        
        # Top股票
        if summary.get('top_stocks'):
            text_parts.append("\n【资金净流入 TOP20股票】")
            for idx, stock in enumerate(summary['top_stocks'], 1):
                text_parts.append(
                    f"{idx}. {stock['name']}({stock['code']}): {stock['net_inflow']:,.2f} 元"
                )
        
        # 热门概念
        if summary.get('hot_concepts'):
            text_parts.append("\n【热门概念 TOP20】")
            for idx, (concept, count) in enumerate(list(summary['hot_concepts'].items())[:20], 1):
                text_parts.append(f"{idx}. {concept}: {count} 次")
        
        # 详细交易记录（前50条）
        text_parts.append("\n【详细交易记录 TOP50】")
        for idx, row in df.head(50).iterrows():
            text_parts.append(
                f"{row.get('游资名称', 'N/A')} | "
                f"{row.get('股票名称', 'N/A')}({row.get('股票代码', 'N/A')}) | "
                f"买入:{row.get('买入金额', 0):,.0f} "
                f"卖出:{row.get('卖出金额', 0):,.0f} "
                f"净流入:{row.get('净流入金额', 0):,.0f} | "
                f"日期:{row.get('日期', 'N/A')}"
            )
        
        return "\n".join(text_parts)

    def _split_concepts(self, raw):
        text = str(raw or "").strip()
        if not text:
            return []
        parts = re.split(r"[,，;；/|、\s]+", text)
        noise = {"概念", "题材", "板块", "-", "--", "N/A", "None", "nan"}
        out = []
        for part in parts:
            concept = str(part or "").strip()
            if not concept:
                continue
            if concept in noise or len(concept) < 2 or concept.isdigit():
                continue
            out.append(concept)
        return out


# 测试函数
if __name__ == "__main__":
    print("=" * 60)
    print("测试智瞰龙虎数据采集模块")
    print("=" * 60)
    
    fetcher = LonghubangDataFetcher()
    
    # 测试获取单日数据
    date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    result = fetcher.get_longhubang_data(date)
    
    if result and result.get('data'):
        # 分析数据
        summary = fetcher.analyze_data_summary(result['data'])
        
        print("\n" + "=" * 60)
        print("数据采集成功！")
        print("=" * 60)
        
        # 格式化输出
        formatted_text = fetcher.format_data_for_ai(result['data'], summary)
        print(formatted_text[:2000])  # 显示前2000字符
        print(f"\n... (总长度: {len(formatted_text)} 字符)")
    else:
        print("\n数据采集失败")
