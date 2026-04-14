"""
季报数据获取模块
使用akshare获取个股最近8期季度财务报告
"""

import pandas as pd
import sys
import io
import warnings
from datetime import datetime
import re
import akshare as ak

warnings.filterwarnings('ignore')

# 设置标准输出编码为UTF-8（仅在命令行环境，避免streamlit冲突）
def _setup_stdout_encoding():
    """仅在命令行环境设置标准输出编码"""
    if sys.platform == 'win32' and not hasattr(sys.stdout, '_original_stream'):
        try:
            # 检测是否在streamlit环境中
            import streamlit
            # 在streamlit中不修改stdout
            return
        except ImportError:
            # 不在streamlit环境，可以安全修改
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='ignore')
            except:
                pass

_setup_stdout_encoding()


class QuarterlyReportDataFetcher:
    """季报数据获取类（使用akshare数据源）"""
    
    def __init__(self):
        self.periods = 8  # 获取最近8期季报
        self.available = True
        print("✓ 季报数据获取器初始化成功（akshare数据源）")
    
    def get_quarterly_reports(self, symbol):
        """
        获取股票的季报数据
        
        Args:
            symbol: 股票代码（6位数字）
            
        Returns:
            dict: 包含季报数据的字典
        """
        data = {
            "symbol": symbol,
            "income_statement": None,      # 利润表
            "balance_sheet": None,         # 资产负债表
            "cash_flow": None,             # 现金流量表
            "financial_indicators": None,   # 财务指标
            "data_success": False,
            "source": "akshare"
        }
        
        # 只支持中国股票
        if not self._is_chinese_stock(symbol):
            data["error"] = "季报数据仅支持中国A股股票"
            return data
        
        try:
            print(f"📊 正在获取 {symbol} 的季报数据...")
            
            # 获取利润表
            income_data = self._get_income_statement(symbol)
            if income_data:
                data["income_statement"] = income_data
                print(f"   ✓ 成功获取 {len(income_data.get('data', []))} 期利润表数据")
            
            # 获取资产负债表
            balance_data = self._get_balance_sheet(symbol)
            if balance_data:
                data["balance_sheet"] = balance_data
                print(f"   ✓ 成功获取 {len(balance_data.get('data', []))} 期资产负债表数据")
            
            # 获取现金流量表
            cash_flow_data = self._get_cash_flow(symbol)
            if cash_flow_data:
                data["cash_flow"] = cash_flow_data
                print(f"   ✓ 成功获取 {len(cash_flow_data.get('data', []))} 期现金流量表数据")
            
            # 获取财务指标
            indicators_data = self._get_financial_indicators(symbol)
            if indicators_data:
                data["financial_indicators"] = indicators_data
                print(f"   ✓ 成功获取 {len(indicators_data.get('data', []))} 期财务指标数据")
            
            # 如果至少有一个成功，则标记为成功
            if income_data or balance_data or cash_flow_data or indicators_data:
                data["data_success"] = True
                print("✅ 季报数据获取完成")
            else:
                print("⚠️ 未能获取到季报数据")
                
        except Exception as e:
            print(f"❌ 获取季报数据失败: {e}")
            data["error"] = str(e)
        
        return data
    
    def _is_chinese_stock(self, symbol):
        """判断是否为中国股票"""
        return symbol.isdigit() and len(symbol) == 6
    
    def _get_income_statement(self, symbol):
        """获取利润表数据"""
        try:
            # stock_financial_report_sina - 新浪财经季度利润表
            df = ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
            
            if df is None or df.empty:
                print(f"   未找到利润表数据")
                return None
            
            # 按报告期降序，获取最近8期
            df = self._sort_by_report_period(df).head(self.periods)
            
            # 转换为字典列表
            data_list = []
            for idx, row in df.iterrows():
                item = {}
                for col in df.columns:
                    value = row.get(col)
                    if value is None or (isinstance(value, float) and pd.isna(value)):
                        continue
                    try:
                        item[col] = str(value)
                    except:
                        item[col] = "N/A"
                if item:
                    data_list.append(item)
            
            return {
                "data": data_list,
                "periods": len(data_list),
                "columns": df.columns.tolist(),
                "query_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"   获取利润表异常: {e}")
            return None
    
    def _get_balance_sheet(self, symbol):
        """获取资产负债表数据"""
        try:
            # stock_financial_report_sina - 新浪财经季度资产负债表
            df = ak.stock_financial_report_sina(stock=symbol, symbol="资产负债表")
            
            if df is None or df.empty:
                print(f"   未找到资产负债表数据")
                return None
            
            # 按报告期降序，获取最近8期
            df = self._sort_by_report_period(df).head(self.periods)
            
            # 转换为字典列表
            data_list = []
            for idx, row in df.iterrows():
                item = {}
                for col in df.columns:
                    value = row.get(col)
                    if value is None or (isinstance(value, float) and pd.isna(value)):
                        continue
                    try:
                        item[col] = str(value)
                    except:
                        item[col] = "N/A"
                if item:
                    data_list.append(item)
            
            return {
                "data": data_list,
                "periods": len(data_list),
                "columns": df.columns.tolist(),
                "query_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"   获取资产负债表异常: {e}")
            return None
    
    def _get_cash_flow(self, symbol):
        """获取现金流量表数据"""
        try:
            # stock_financial_report_sina - 新浪财经季度现金流量表
            df = ak.stock_financial_report_sina(stock=symbol, symbol="现金流量表")
            
            if df is None or df.empty:
                print(f"   未找到现金流量表数据")
                return None
            
            # 按报告期降序，获取最近8期
            df = self._sort_by_report_period(df).head(self.periods)
            
            # 转换为字典列表
            data_list = []
            for idx, row in df.iterrows():
                item = {}
                for col in df.columns:
                    value = row.get(col)
                    if value is None or (isinstance(value, float) and pd.isna(value)):
                        continue
                    try:
                        item[col] = str(value)
                    except:
                        item[col] = "N/A"
                if item:
                    data_list.append(item)
            
            return {
                "data": data_list,
                "periods": len(data_list),
                "columns": df.columns.tolist(),
                "query_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"   获取现金流量表异常: {e}")
            return None
    
    def _get_financial_indicators(self, symbol):
        """获取财务指标数据"""
        try:
            # 使用stock_financial_abstract替代已失效的stock_financial_analysis_indicator
            df = ak.stock_financial_abstract(symbol=symbol)
            
            if df is None or df.empty:
                print(f"   未找到财务指标数据")
                return None
            
            # 获取最近8期
            df = df.head(self.periods * 2)  # 取更多数据以确保有足够的季度数据
            
            # 提取关键财务指标
            key_indicators = [
                '净资产收益率(ROE)', '总资产报酬率(ROA)', '销售净利率', '销售毛利率',
                '资产负债率', '流动比率', '速动比率', '应收账款周转率', '存货周转率',
                '总资产周转率', '基本每股收益', '每股净资产', '每股现金流'
            ]
            
            # 筛选出包含关键指标的行
            indicator_rows = df[df['指标'].isin(key_indicators)]
            
            if indicator_rows.empty:
                print(f"   未找到关键财务指标数据")
                return None
            
            # 获取日期列（排除'选项'和'指标'列）
            date_columns = [col for col in df.columns if col not in ['选项', '指标']]
            date_columns = self._sort_date_labels(date_columns)[:self.periods]
            
            # 转换为字典列表，每个字典代表一个时期的财务指标
            data_list = []
            for date_col in date_columns:  # 只取最近的periods期
                item = {'报告期': date_col}
                for _, row in indicator_rows.iterrows():
                    indicator_name = row['指标']
                    value = row.get(date_col)
                    if value is not None and not (isinstance(value, float) and pd.isna(value)):
                        try:
                            # 尝试转换为字符串
                            item[indicator_name] = str(value)
                        except:
                            item[indicator_name] = "N/A"
                    else:
                        item[indicator_name] = "N/A"
                data_list.append(item)
            
            return {
                "data": data_list,
                "periods": len(data_list),
                "columns": ['报告期'] + key_indicators,
                "query_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"   获取财务指标异常: {e}")
            return None

    def _sort_by_report_period(self, df):
        """按报告期列降序排序"""
        if df is None or df.empty:
            return df
        
        report_col = self._find_report_column(df.columns)
        if not report_col:
            return df
        
        sorted_df = df.copy()
        sorted_df['_report_dt'] = sorted_df[report_col].apply(self._parse_report_date)
        
        # 有效日期优先，按最新到最旧
        sorted_df = sorted_df.sort_values(
            by=['_report_dt'],
            ascending=False,
            na_position='last'
        )
        sorted_df = sorted_df.drop(columns=['_report_dt'])
        return sorted_df
    
    def _find_report_column(self, columns):
        """寻找报告期相关列"""
        candidates = ['报告期', '报告日期', '报表日期', '截止日期', '日期', '时间']
        for col in columns:
            name = str(col)
            if any(k in name for k in candidates):
                return col
        return ""
    
    def _sort_date_labels(self, labels):
        """排序日期列标签（最新在前）"""
        return sorted(
            labels,
            key=lambda x: self._parse_report_date(x) or datetime.min,
            reverse=True
        )
    
    def _parse_report_date(self, value):
        """解析报告期/日期文本为datetime"""
        if value is None:
            return None
        
        text = str(value).strip()
        if not text or text.lower() == 'nan':
            return None
        
        # 常规日期
        try:
            dt = pd.to_datetime(text, errors='coerce')
            if pd.notna(dt):
                return dt.to_pydatetime()
        except Exception:
            pass
        
        # 兼容 "2024年三季报" / "2024Q3" / "2024-3Q"
        quarter_map = {'一': 3, '二': 6, '三': 9, '四': 12}
        zh_quarter = re.search(r'(\d{4})年([一二三四])季', text)
        if zh_quarter:
            year = int(zh_quarter.group(1))
            month = quarter_map.get(zh_quarter.group(2), 12)
            return datetime(year, month, 1)
        
        en_quarter = re.search(r'(\d{4}).*Q([1-4])', text, flags=re.IGNORECASE)
        if en_quarter:
            year = int(en_quarter.group(1))
            month = int(en_quarter.group(2)) * 3
            return datetime(year, month, 1)
        
        # 回退：提取 YYYY-MM-DD / YYYY/MM/DD
        date_match = re.search(r'(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})', text)
        if date_match:
            try:
                dt = pd.to_datetime(date_match.group(1), errors='coerce')
                if pd.notna(dt):
                    return dt.to_pydatetime()
            except Exception:
                pass
        
        # 仅年份时按该年末处理
        year_match = re.search(r'(\d{4})', text)
        if year_match:
            year = int(year_match.group(1))
            return datetime(year, 12, 31)
        
        return None
    
    def format_quarterly_reports_for_ai(self, data):
        """
        将季报数据格式化为适合AI阅读的文本
        """
        if not data or not data.get("data_success"):
            return "未能获取季报数据"
        
        text_parts = []
        text_parts.append(f"""
【季度财务报告数据 - akshare数据源】
股票代码：{data.get('symbol', 'N/A')}
数据期数：最近{self.periods}期季报

""")
        
        # 利润表数据
        if data.get("income_statement"):
            income_data = data["income_statement"]
            text_parts.append(f"""
═══════════════════════════════════════
📊 利润表（最近{income_data.get('periods', 0)}期）
═══════════════════════════════════════
""")
            
            # 提取关键指标
            key_fields = ['报告期', '营业总收入', '营业收入', '营业总成本', '营业利润', 
                         '利润总额', '净利润', '归属于母公司所有者的净利润', 
                         '基本每股收益', '稀释每股收益']
            
            for idx, item in enumerate(income_data.get('data', []), 1):
                text_parts.append(f"\n第 {idx} 期:")
                for field in key_fields:
                    if field in item:
                        text_parts.append(f"  {field}: {item[field]}")
                
                # 显示其他重要字段（如果有）
                other_fields = ['销售费用', '管理费用', '财务费用', '研发费用']
                for field in other_fields:
                    if field in item:
                        text_parts.append(f"  {field}: {item[field]}")
        
        # 资产负债表数据
        if data.get("balance_sheet"):
            balance_data = data["balance_sheet"]
            text_parts.append(f"""

═══════════════════════════════════════
📊 资产负债表（最近{balance_data.get('periods', 0)}期）
═══════════════════════════════════════
""")
            
            # 提取关键指标
            key_fields = ['报告期', '资产总计', '流动资产合计', '非流动资产合计',
                         '负债合计', '流动负债合计', '非流动负债合计',
                         '所有者权益合计', '归属于母公司股东权益合计']
            
            for idx, item in enumerate(balance_data.get('data', []), 1):
                text_parts.append(f"\n第 {idx} 期:")
                for field in key_fields:
                    if field in item:
                        text_parts.append(f"  {field}: {item[field]}")
        
        # 现金流量表数据
        if data.get("cash_flow"):
            cash_flow_data = data["cash_flow"]
            text_parts.append(f"""

═══════════════════════════════════════
📊 现金流量表（最近{cash_flow_data.get('periods', 0)}期）
═══════════════════════════════════════
""")
            
            # 提取关键指标
            key_fields = ['报告期', '经营活动产生的现金流量净额', 
                         '投资活动产生的现金流量净额', '筹资活动产生的现金流量净额',
                         '现金及现金等价物净增加额', '期末现金及现金等价物余额']
            
            for idx, item in enumerate(cash_flow_data.get('data', []), 1):
                text_parts.append(f"\n第 {idx} 期:")
                for field in key_fields:
                    if field in item:
                        text_parts.append(f"  {field}: {item[field]}")
        
        # 财务指标数据
        if data.get("financial_indicators"):
            indicators_data = data["financial_indicators"]
            text_parts.append(f"""

═══════════════════════════════════════
📊 关键财务指标（最近{indicators_data.get('periods', 0)}期）
═══════════════════════════════════════
""")
            
            # 提取关键指标
            key_fields = ['报告期', '净资产收益率', '总资产净利率', '销售净利率',
                         '销售毛利率', '资产负债率', '流动比率', '速动比率',
                         '应收账款周转率', '存货周转率', '总资产周转率',
                         '每股收益', '每股净资产', '每股经营现金流']
            
            for idx, item in enumerate(indicators_data.get('data', []), 1):
                text_parts.append(f"\n第 {idx} 期:")
                for field in key_fields:
                    if field in item:
                        text_parts.append(f"  {field}: {item[field]}")
        
        return "\n".join(text_parts)


# 测试函数
if __name__ == "__main__":
    print("测试季报数据获取（akshare数据源）...")
    print("="*60)
    
    fetcher = QuarterlyReportDataFetcher()
    
    if not fetcher.available:
        print("❌ 季报数据获取器不可用")
        sys.exit(1)
    
    # 测试股票
    test_symbols = ["000001", "600519"]  # 平安银行、贵州茅台
    
    for symbol in test_symbols:
        print(f"\n{'='*60}")
        print(f"正在测试股票: {symbol}")
        print(f"{'='*60}\n")
        
        data = fetcher.get_quarterly_reports(symbol)
        
        if data.get("data_success"):
            print("\n" + "="*60)
            print("季报数据获取成功！")
            print("="*60)
            
            formatted_text = fetcher.format_quarterly_reports_for_ai(data)
            print(formatted_text)
        else:
            print(f"\n获取失败: {data.get('error', '未知错误')}")
        
        print("\n")
