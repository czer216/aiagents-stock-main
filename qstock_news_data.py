"""
新闻数据获取模块
使用akshare获取股票的最新新闻信息（替代qstock）
"""

import pandas as pd
import sys
import io
import warnings
from datetime import datetime, timedelta
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


class QStockNewsDataFetcher:
    """新闻数据获取类（使用akshare作为数据源）"""
    
    def __init__(self):
        self.max_items = 30  # 最多获取的新闻数量
        self.lookback_days = 30  # 仅保留最近N天新闻
        self.xueqiu_top_n = 10  # 雪球热度数据条数
        self.available = True
        print("✓ 新闻数据获取器初始化成功（akshare数据源）")
    
    def get_stock_news(self, symbol):
        """
        获取股票的新闻数据
        
        Args:
            symbol: 股票代码（6位数字）
            
        Returns:
            dict: 包含新闻数据的字典
        """
        data = {
            "symbol": symbol,
            "news_data": None,
            "data_success": False,
            "source": "qstock"
        }
        
        if not self.available:
            data["error"] = "qstock库未安装或不可用"
            return data
        
        # 只支持中国股票
        if not self._is_chinese_stock(symbol):
            data["error"] = "新闻数据仅支持中国A股股票"
            return data
        
        try:
            # 获取新闻数据
            print(f"📰 正在使用qstock获取 {symbol} 的最新新闻...")
            news_data = self._get_news_data(symbol)
            
            if news_data:
                data["news_data"] = news_data
                print(f"   ✓ 成功获取 {len(news_data.get('items', []))} 条新闻")
                data["data_success"] = True
                print("✅ 新闻数据获取完成")
            else:
                print("⚠️ 未能获取到新闻数据")
                
        except Exception as e:
            print(f"❌ 获取新闻数据失败: {e}")
            data["error"] = str(e)
        
        return data
    
    def _is_chinese_stock(self, symbol):
        """判断是否为中国股票"""
        return symbol.isdigit() and len(symbol) == 6
    
    def _get_news_data(self, symbol):
        """获取新闻数据（使用akshare）"""
        try:
            print(f"   使用 akshare 获取新闻...")
            
            news_items = []
            
            # 方法1: 尝试获取个股新闻（东方财富）
            try:
                # stock_news_em(symbol="600519") - 东方财富个股新闻
                df = ak.stock_news_em(symbol=symbol)
                
                if df is not None and not df.empty:
                    print(f"   ✓ 从东方财富获取到 {len(df)} 条新闻")
                    news_items.extend(self._collect_items_from_df(df, source='东方财富'))
            
            except Exception as e:
                print(f"   ⚠ 从东方财富获取失败: {e}")
            
            # 方法2: 如果没有获取到，尝试获取新浪财经新闻
            if not news_items:
                try:
                    # stock_zh_a_spot_em() - 获取股票信息，包含代码和名称
                    df_info = ak.stock_zh_a_spot_em()
                    
                    # 查找股票名称
                    stock_name = None
                    if df_info is not None and not df_info.empty:
                        match = df_info[df_info['代码'] == symbol]
                        if not match.empty:
                            stock_name = match.iloc[0]['名称']
                            print(f"   找到股票名称: {stock_name}")
                    
                    # 使用股票名称搜索新闻
                    if stock_name:
                        # stock_news_sina - 新浪财经新闻
                        try:
                            df = ak.stock_news_sina(symbol=stock_name)
                            if df is not None and not df.empty:
                                print(f"   ✓ 从新浪财经获取到 {len(df)} 条新闻")
                                news_items.extend(self._collect_items_from_df(df, source='新浪财经'))
                        except:
                            pass
                
                except Exception as e:
                    print(f"   ⚠ 从新浪财经获取失败: {e}")
            
            # 方法3: 尝试获取财联社电报
            if not news_items or len(news_items) < 5:
                try:
                    # stock_news_cls() - 财联社电报
                    df = ak.stock_news_cls()
                    
                    if df is not None and not df.empty:
                        # 筛选包含股票代码或名称的新闻
                        df_filtered = df[
                            df['内容'].str.contains(symbol, na=False) |
                            df['标题'].str.contains(symbol, na=False)
                        ]
                        
                        if not df_filtered.empty:
                            print(f"   ✓ 从财联社获取到 {len(df_filtered)} 条相关新闻")
                            news_items.extend(self._collect_items_from_df(df_filtered, source='财联社'))
                
                except Exception as e:
                    print(f"   ⚠ 从财联社获取失败: {e}")
            
            # 方法4: 获取雪球热度前10条（作为额外数据支持）
            xq_items = self._get_xueqiu_hot_items(symbol, top_n=self.xueqiu_top_n)
            if xq_items:
                print(f"   ✓ 从雪球获取到 {len(xq_items)} 条热度数据")
                news_items.extend(xq_items)
            else:
                print("   ⚠ 未获取到雪球热度数据")
            
            if not news_items:
                print(f"   未找到股票 {symbol} 的新闻")
                return None
            
            # 时间过滤 + 排序 + 去重（默认最近90天）
            news_items = self._post_process_news_items(news_items)
            if not news_items:
                print(f"   最近{self.lookback_days}天未找到有效新闻")
                return None

            # 优先保留雪球热度前10条，再补充其他来源
            xq_items = [item for item in news_items if item.get('source') == '雪球'][:self.xueqiu_top_n]
            other_items = [item for item in news_items if item.get('source') != '雪球']
            final_items = self._deduplicate_items(xq_items + other_items)
            
            # 限制数量
            news_items = final_items[:self.max_items]
            dated_items = [item for item in news_items if item.get('date')]
            if dated_items:
                date_range = f"{dated_items[-1].get('date', '')} ~ {dated_items[0].get('date', '')}"
            else:
                date_range = f"最近{self.lookback_days}天（未解析到明确发布时间）"
            
            return {
                "items": news_items,
                "count": len(news_items),
                "query_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "date_range": date_range
            }
            
        except Exception as e:
            print(f"   获取新闻数据异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _get_xueqiu_hot_items(self, symbol, top_n=10):
        """获取雪球热度前N条（尽量兼容不同akshare版本）"""
        items = []
        xq_symbol = self._to_xueqiu_symbol(symbol)
        
        candidate_calls = [
            ("stock_hot_tweet_xq", {"symbol": xq_symbol}),
            ("stock_hot_tweet_xq", {"symbol": symbol}),
            ("stock_hot_deal_xq", {"symbol": xq_symbol}),
            ("stock_hot_deal_xq", {"symbol": symbol}),
        ]
        
        for func_name, kwargs in candidate_calls:
            func = getattr(ak, func_name, None)
            if func is None:
                continue
            try:
                df = func(**kwargs)
            except TypeError:
                # 部分版本可能是单参数形式
                try:
                    df = func(kwargs.get("symbol"))
                except Exception:
                    continue
            except Exception:
                continue
            
            if df is None or getattr(df, "empty", True):
                continue
            
            normalized = self._collect_xueqiu_items_from_df(df, symbol, top_n=top_n)
            if normalized:
                items.extend(normalized)
        
        if not items:
            return []
        
        # 去重并按热度降序
        items = self._deduplicate_items(items)
        items.sort(key=lambda x: self._safe_number(x.get("heat_score", 0)), reverse=True)
        return items[:top_n]

    def _collect_xueqiu_items_from_df(self, df, symbol, top_n=10):
        """将雪球DataFrame标准化为新闻条目"""
        rows = []
        if df is None or df.empty:
            return rows
        
        for _, row in df.iterrows():
            row_dict = {}
            for col in df.columns:
                value = row.get(col)
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                row_dict[str(col)] = str(value)
            
            if not row_dict:
                continue
            
            title = self._pick_by_keywords(
                row_dict,
                ["标题", "title", "内容", "text", "摘要", "话题", "讨论", "帖子", "tweet"]
            )
            content = self._pick_by_keywords(
                row_dict,
                ["内容", "text", "正文", "摘要", "评论", "说明", "话题", "讨论"]
            )
            author = self._pick_by_keywords(
                row_dict,
                ["用户", "作者", "昵称", "username", "user", "name"]
            )
            
            if not title:
                title = f"{symbol} 雪球热度讨论"
            if not content:
                content = title
            if author:
                content = f"作者: {author} | {content}"
            
            heat_score = self._calc_heat_score(row_dict)
            
            item = {
                "source": "雪球",
                "title": title,
                "content": content,
                "heat_score": heat_score,
            }
            
            # 透传主要字段，便于调试
            for k, v in row_dict.items():
                if k not in item:
                    item[k] = v
            
            publish_dt = self._extract_publish_datetime(row_dict)
            if publish_dt is not None:
                item['date'] = publish_dt.strftime('%Y-%m-%d')
                item['time'] = publish_dt.strftime('%H:%M:%S')
                item['_published_at'] = publish_dt.strftime('%Y-%m-%d %H:%M:%S')
            
            rows.append(item)
        
        rows.sort(key=lambda x: self._safe_number(x.get("heat_score", 0)), reverse=True)
        return rows[:top_n]
    
    def _collect_items_from_df(self, df, source):
        """从DataFrame提取新闻条目"""
        items = []
        if df is None or df.empty:
            return items
        
        for _, row in df.iterrows():
            item = {'source': source}
            for col in df.columns:
                value = row.get(col)
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                try:
                    item[col] = str(value)
                except Exception:
                    item[col] = "无法解析"
            
            if len(item) > 1:
                items.append(self._normalize_news_item(item))
        
        return items
    
    def _normalize_news_item(self, item):
        """标准化新闻字段（title/content/date/time/url）"""
        normalized = dict(item)
        
        title = self._pick_first_value(item, ['title', '标题', '新闻标题'])
        content = self._pick_first_value(item, ['content', '内容', '摘要', '正文', '新闻内容'])
        url = self._pick_first_value(item, ['url', '链接', '网址', '新闻链接'])
        
        if title:
            normalized['title'] = title
        if content:
            normalized['content'] = content
        if url:
            normalized['url'] = url
        
        publish_dt = self._extract_publish_datetime(item)
        if publish_dt is not None:
            normalized['date'] = publish_dt.strftime('%Y-%m-%d')
            normalized['time'] = publish_dt.strftime('%H:%M:%S')
            normalized['_published_at'] = publish_dt.strftime('%Y-%m-%d %H:%M:%S')
        
        return normalized
    
    def _extract_publish_datetime(self, item):
        """从新闻字段中提取发布时间"""
        # 优先候选字段
        candidate_keys = [
            '发布时间', '时间', '日期', 'publish_time', 'pub_time',
            'datetime', 'date', 'time', '发布时间戳', '更新时间'
        ]
        for key in candidate_keys:
            if key in item:
                dt = self._parse_datetime(item.get(key))
                if dt is not None:
                    return dt
        
        # 兜底：扫描包含日期/时间关键词的字段
        for key, value in item.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ['date', 'time', '时间', '日期', '发布', '更新']):
                dt = self._parse_datetime(value)
                if dt is not None:
                    return dt
        
        return None
    
    def _parse_datetime(self, value):
        """解析日期时间"""
        if value is None:
            return None
        
        text = str(value).strip()
        if not text or text.lower() == 'nan':
            return None
        
        # Unix时间戳（秒/毫秒）
        if re.fullmatch(r'\d{10,13}', text):
            try:
                ts = int(text)
                if len(text) == 13:
                    ts = ts / 1000
                return datetime.fromtimestamp(ts)
            except Exception:
                pass
        
        try:
            parsed = pd.to_datetime(text, errors='coerce')
            if pd.notna(parsed):
                return parsed.to_pydatetime()
        except Exception:
            pass
        
        # 回退：仅日期格式
        date_match = re.search(r'(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})', text)
        if date_match:
            try:
                parsed = pd.to_datetime(date_match.group(1), errors='coerce')
                if pd.notna(parsed):
                    return parsed.to_pydatetime()
            except Exception:
                pass

        # 回退：YYYYMMDD
        compact_match = re.search(r'(?<!\d)(\d{8})(?!\d)', text)
        if compact_match:
            raw = compact_match.group(1)
            formatted = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
            try:
                parsed = pd.to_datetime(formatted, errors='coerce')
                if pd.notna(parsed):
                    return parsed.to_pydatetime()
            except Exception:
                pass
        
        return None
    
    def _post_process_news_items(self, news_items):
        """新闻去重、时间过滤和排序"""
        if not news_items:
            return []
        
        cutoff = datetime.now() - timedelta(days=self.lookback_days)
        dedup = []
        seen = set()
        
        for item in news_items:
            title = str(item.get('title', '')).strip()
            published_at = str(item.get('_published_at', '')).strip()
            unique_key = (title, published_at, item.get('source', ''))
            if unique_key in seen:
                continue
            seen.add(unique_key)
            dedup.append(item)

        # 二次尝试时间解析：避免部分来源仅有 date/time 字段却未写入 _published_at
        for item in dedup:
            if item.get('_published_at'):
                continue
            dt = self._extract_publish_datetime(item)
            if dt is not None:
                item['_published_at'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                item['date'] = item.get('date') or dt.strftime('%Y-%m-%d')
                item['time'] = item.get('time') or dt.strftime('%H:%M:%S')
        
        # 仅把“可解析到时间”的新闻视为有效时效新闻；雪球热度可作为无时间补充
        dated = [item for item in dedup if self._parse_datetime(item.get('_published_at'))]
        if dated:
            filtered = []
            for item in dedup:
                published_at = item.get('_published_at')
                if not published_at:
                    # 没有时间戳时，仅保留雪球热度数据
                    if item.get('source') == '雪球' and item.get('heat_score') is not None:
                        filtered.append(item)
                    continue
                dt = self._parse_datetime(published_at)
                if dt and dt >= cutoff:
                    filtered.append(item)
            dedup = filtered
        else:
            # 没有任何可解析发布时间时，不保留普通新闻，避免混入历史陈旧内容
            dedup = [
                item for item in dedup
                if item.get('source') == '雪球' and item.get('heat_score') is not None
            ]
        
        # 按发布时间降序
        dedup.sort(
            key=lambda x: self._parse_datetime(x.get('_published_at')) or datetime.min,
            reverse=True
        )
        
        # 清理内部字段
        for item in dedup:
            if '_published_at' in item:
                del item['_published_at']
        
        return dedup

    def _calc_heat_score(self, row_dict):
        """计算雪球热度分（兼容不同字段名）"""
        score = 0.0
        weights = [
            (["热度", "heat", "人气"], 1.0),
            (["评论", "reply", "comment"], 2.0),
            (["转发", "retweet", "repost"], 2.5),
            (["点赞", "like", "赞"], 1.5),
            (["关注", "follower", "粉丝"], 0.8),
            (["浏览", "阅读", "view"], 0.2),
        ]
        
        for keys, weight in weights:
            value_text = self._pick_by_keywords(row_dict, keys)
            if not value_text:
                continue
            score += self._safe_number(value_text) * weight
        
        # 防止全空导致0，给一个基础分
        if score <= 0:
            score = 1.0
        return round(score, 2)

    def _pick_by_keywords(self, data, keywords):
        """从字典中按关键字匹配字段值"""
        if not isinstance(data, dict):
            return ""
        for key, value in data.items():
            key_text = str(key).lower()
            if any(k.lower() in key_text for k in keywords):
                text = str(value).strip()
                if text and text.lower() != 'nan':
                    return text
        return ""

    def _safe_number(self, value):
        """安全解析数值"""
        if value is None:
            return 0.0
        text = str(value).strip()
        if not text:
            return 0.0
        text = text.replace(',', '')
        
        # 中文单位
        multiplier = 1.0
        if text.endswith('万'):
            multiplier = 10000.0
            text = text[:-1]
        elif text.endswith('亿'):
            multiplier = 100000000.0
            text = text[:-1]
        
        match = re.search(r'[-+]?\d*\.?\d+', text)
        if not match:
            return 0.0
        try:
            return float(match.group()) * multiplier
        except Exception:
            return 0.0

    def _to_xueqiu_symbol(self, symbol):
        """将A股代码转换为雪球常见代码格式"""
        if symbol.startswith('6'):
            return f"SH{symbol}"
        return f"SZ{symbol}"

    def _deduplicate_items(self, items):
        """按标题+来源+日期去重，保持顺序"""
        dedup = []
        seen = set()
        for item in items:
            key = (
                str(item.get('title', '')).strip(),
                str(item.get('source', '')).strip(),
                str(item.get('date', '')).strip(),
                str(item.get('time', '')).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup
    
    def _pick_first_value(self, data, keys):
        """按候选键顺序取第一个非空值"""
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() != 'nan':
                return text
        return ""
    
    def format_news_for_ai(self, data):
        """
        将新闻数据格式化为适合AI阅读的文本
        """
        if not data or not data.get("data_success"):
            return "未能获取新闻数据"
        
        text_parts = []
        
        # 新闻数据
        if data.get("news_data"):
            news_data = data["news_data"]
            text_parts.append(f"""
【最新新闻 - akshare数据源】
查询时间：{news_data.get('query_time', 'N/A')}
时间范围：{news_data.get('date_range', 'N/A')}
新闻数量：{news_data.get('count', 0)}条

""")
            
            for idx, item in enumerate(news_data.get('items', []), 1):
                text_parts.append(f"新闻 {idx}:")
                
                # 优先显示的字段
                priority_fields = ['title', 'date', 'time', 'source', 'content', 'url']
                
                # 先显示优先字段
                for field in priority_fields:
                    if field in item:
                        value = item[field]
                        # 限制content长度
                        if field == 'content' and len(str(value)) > 500:
                            value = str(value)[:500] + "..."
                        text_parts.append(f"  {field}: {value}")
                
                # 再显示其他字段
                for key, value in item.items():
                    if key not in priority_fields and key != 'source':
                        # 跳过过长的字段
                        if len(str(value)) > 300:
                            value = str(value)[:300] + "..."
                        text_parts.append(f"  {key}: {value}")
                
                text_parts.append("")  # 空行分隔
        
        return "\n".join(text_parts)


# 测试函数
if __name__ == "__main__":
    print("测试新闻数据获取（akshare数据源）...")
    print("="*60)
    
    fetcher = QStockNewsDataFetcher()
    
    if not fetcher.available:
        print("❌ 新闻数据获取器不可用")
        sys.exit(1)
    
    # 测试股票
    test_symbols = ["000001", "600519"]  # 平安银行、贵州茅台
    
    for symbol in test_symbols:
        print(f"\n{'='*60}")
        print(f"正在测试股票: {symbol}")
        print(f"{'='*60}\n")
        
        data = fetcher.get_stock_news(symbol)
        
        if data.get("data_success"):
            print("\n" + "="*60)
            print("新闻数据获取成功！")
            print("="*60)
            
            formatted_text = fetcher.format_news_for_ai(data)
            print(formatted_text)
        else:
            print(f"\n获取失败: {data.get('error', '未知错误')}")
        
        print("\n")
