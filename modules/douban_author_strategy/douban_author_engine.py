import time
import json
import logging
import statistics
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from infrastructure.data.stock_data import StockDataFetcher
from modules.douban_author_strategy.douban_author_data import DoubanAuthorDataFetcher
from modules.douban_author_strategy.douban_author_db import douban_author_db
from modules.douban_author_strategy.douban_author_agents import DoubanAuthorAgents
from modules.douban_author_strategy.douban_author_comment_analyzer import comment_analyzer
from modules.smart_monitor.smart_monitor_data import SmartMonitorDataFetcher
from infrastructure.data.market_sentiment_data import MarketSentimentDataFetcher
import config


class DoubanAuthorStrategyEngine:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.fetcher = DoubanAuthorDataFetcher()
        self.db = douban_author_db
        self.agents = DoubanAuthorAgents()
        self.stock_fetcher = StockDataFetcher()
        self.rt_fetcher = SmartMonitorDataFetcher()
        self.sentiment_fetcher = MarketSentimentDataFetcher()

    def merge_author_feedback_to_latest_pattern(
        self,
        author_id: str,
        author_name: str,
        feedback_text: str,
        reviewed_by: str = 'admin',
    ) -> Dict:
        aid = str(author_id or '').strip()
        if not aid:
            return {"success": False, "error": "author_id不能为空"}

        latest = self.db.get_latest_author_pattern(author_id=aid)
        if not latest:
            return {"success": False, "error": "该作者暂无模式版本，无法融合反馈"}

        try:
            pattern_json = json.loads(latest.get('pattern_json') or '{}')
        except Exception:
            pattern_json = {}
        if not isinstance(pattern_json, dict):
            pattern_json = {}

        feedback = str(feedback_text or '').strip()
        if not feedback:
            feedback = (
                "模型形态符合主升中断板低吸；实盘需结合筹码图形和历史股性主观过滤；"
                "对偏趋势且历史少隔日反包标的，入选但通常不参与（示例：远东）。"
            )

        pattern_json['subjective_filters'] = {
            'enabled': True,
            'notes': feedback,
            'focus': ['筹码图形', '历史股性'],
        }
        pattern_json['stock_character_constraints'] = {
            'trend_style_skip_rebound_weak': True,
            'notes': '偏趋势、历史少隔日反包的标的，通常不参与隔日反包型执行',
        }
        pattern_json['chip_structure_preference'] = {
            'require_readable_chip_structure': True,
            'notes': '入场前优先确认筹码结构是否支持次日反包/承接',
        }
        pattern_json['pass_but_skip_examples'] = [
            {
                'symbol_or_name': '远东',
                'reason': '历史股性偏趋势，少隔日反包，可入选但通常不参与',
            }
        ]

        base_source_post_ids = []
        try:
            base_source_post_ids = json.loads(latest.get('source_post_ids') or '[]')
        except Exception:
            base_source_post_ids = []
        if not isinstance(base_source_post_ids, list):
            base_source_post_ids = []

        confidence = float((pattern_json or {}).get('confidence') or latest.get('confidence') or 0)
        row_id = self.db.save_author_pattern(
            author_id=aid,
            pattern_json=pattern_json,
            source_post_ids=base_source_post_ids,
            confidence=confidence,
            as_of_date=datetime.now().strftime('%Y-%m-%d'),
        )
        newest = self.db.get_latest_author_pattern(author_id=aid) or {}
        new_version = int(newest.get('pattern_version') or 0)

        lesson_payload = {
            'type': 'author_feedback_merge',
            'author_name': str(author_name or aid),
            'reviewed_by': str(reviewed_by or 'admin'),
            'feedback': feedback,
            'merged_pattern_version': new_version,
            'merged_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self.db.save_learning_memory(
            author_id=aid,
            report_id=None,
            memory_type='lesson',
            memory_json=lesson_payload,
            quality_score=0.8,
        )

        return {
            'success': True,
            'author_id': aid,
            'author_name': str(author_name or aid),
            'pattern_row_id': int(row_id),
            'previous_version': int(latest.get('pattern_version') or 0),
            'new_version': new_version,
            'merged_feedback': feedback,
        }

    def _fetch_realtime_with_retry(self, code: str, retries: int = 3, retry_delay: float = 0.6) -> Dict:
        last_quote = {}
        tdx_fetcher = getattr(self.rt_fetcher, 'tdx_fetcher', None)

        if not tdx_fetcher:
            self.logger.warning(f"TDX fetcher未启用，无法获取实时行情: {code}")
            return {}

        for i in range(max(1, int(retries))):
            try:
                quote = tdx_fetcher.get_realtime_quote(code) or {}
                source = str(quote.get('data_source') or '').lower()
                price = float(quote.get('current_price') or 0)
                if quote and source == 'tdx' and price > 0:
                    return quote
                last_quote = quote
            except Exception:
                last_quote = {}
            if i < retries - 1:
                time.sleep(retry_delay)
        return last_quote

    @staticmethod
    def _safe_float(v, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return float(default)

    def _get_intraday_raw(self, code: str) -> Dict:
        tdx_fetcher = getattr(self.rt_fetcher, 'tdx_fetcher', None)
        if not tdx_fetcher:
            return {'minute': [], 'trade': [], 'quote': {}}
        minute = tdx_fetcher.get_minute_data(code, limit=240) if hasattr(tdx_fetcher, 'get_minute_data') else []
        trade = tdx_fetcher.get_trade_data(code, limit=400) if hasattr(tdx_fetcher, 'get_trade_data') else []
        quote = tdx_fetcher.get_realtime_quote(code) or {}
        return {'minute': minute or [], 'trade': trade or [], 'quote': quote or {}}

    def _calc_pull_rhythm_score(self, minute_rows: List[Dict]) -> float:
        if not minute_rows or len(minute_rows) < 20:
            return 50.0
        prices = [self._safe_float(x.get('Price')) for x in minute_rows if self._safe_float(x.get('Price')) > 0]
        if len(prices) < 20:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        positive_ratio = sum(1 for d in deltas if d > 0) / max(1, len(deltas))
        acceleration = sum(1 for i in range(1, len(deltas)) if deltas[i] > deltas[i - 1] > 0)
        accel_ratio = acceleration / max(1, len(deltas) - 1)
        hi = max(prices)
        lo = min(prices)
        swing_pct = ((hi - lo) / lo * 100) if lo > 0 else 0
        overheat_penalty = min(20.0, max(0.0, swing_pct - 6.0) * 2.5)
        score = 40 + positive_ratio * 35 + accel_ratio * 20 - overheat_penalty
        return max(0.0, min(100.0, score))

    def _calc_volume_price_score(self, minute_rows: List[Dict], trade_rows: List[Dict]) -> float:
        if not minute_rows or len(minute_rows) < 20:
            return 50.0
        prices = [self._safe_float(x.get('Price')) for x in minute_rows]
        vols = [self._safe_float(x.get('Volume')) for x in minute_rows]
        valid = [(p, v) for p, v in zip(prices, vols) if p > 0 and v >= 0]
        if len(valid) < 20:
            return 50.0
        prices = [x[0] for x in valid]
        vols = [x[1] for x in valid]
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        up_vol = sum(vols[i] for i, d in enumerate(deltas, start=1) if d > 0)
        down_vol = sum(vols[i] for i, d in enumerate(deltas, start=1) if d <= 0)
        updown_ratio = up_vol / max(1.0, down_vol)
        vol_mean = statistics.mean(vols) if vols else 0.0
        vol_last = statistics.mean(vols[-20:]) if len(vols) >= 20 else vol_mean
        vol_boost = vol_last / max(1.0, vol_mean)
        buy_ratio = 0.5
        if trade_rows:
            buy = sum(self._safe_float(x.get('Volume')) for x in trade_rows if str(x.get('BuyOrSell') or '').lower().startswith('b'))
            sell = sum(self._safe_float(x.get('Volume')) for x in trade_rows if str(x.get('BuyOrSell') or '').lower().startswith('s'))
            if buy + sell > 0:
                buy_ratio = buy / (buy + sell)
        score = 35 + min(30.0, updown_ratio * 12) + min(20.0, max(0.0, vol_boost - 0.8) * 20) + buy_ratio * 20
        return max(0.0, min(100.0, score))

    def _calc_pullback_support_score(self, minute_rows: List[Dict], trade_rows: List[Dict], quote: Dict) -> float:
        if not minute_rows or len(minute_rows) < 20:
            return 50.0
        prices = [self._safe_float(x.get('Price')) for x in minute_rows if self._safe_float(x.get('Price')) > 0]
        avg_prices = [self._safe_float(x.get('AvgPrice')) for x in minute_rows if self._safe_float(x.get('AvgPrice')) > 0]
        if len(prices) < 20:
            return 50.0
        peak = max(prices)
        last = prices[-1]
        retrace_pct = ((peak - last) / peak * 100) if peak > 0 else 0
        vwap_hold = 0.5
        if avg_prices:
            avg_last = avg_prices[-1]
            if avg_last > 0:
                vwap_hold = 1.0 if last >= avg_last else max(0.0, 1 - (avg_last - last) / avg_last)
        rebound = 0.0
        if len(prices) > 8:
            tail_low = min(prices[-8:])
            rebound = ((last - tail_low) / tail_low * 100) if tail_low > 0 else 0
        buy_ratio = 0.5
        if trade_rows:
            tail = trade_rows[-120:]
            buy = sum(self._safe_float(x.get('Volume')) for x in tail if str(x.get('BuyOrSell') or '').lower().startswith('b'))
            sell = sum(self._safe_float(x.get('Volume')) for x in tail if str(x.get('BuyOrSell') or '').lower().startswith('s'))
            if buy + sell > 0:
                buy_ratio = buy / (buy + sell)
        score = 45 + (1 - min(1.0, retrace_pct / 4.0)) * 25 + vwap_hold * 15 + min(15.0, rebound * 5) + (buy_ratio - 0.5) * 20
        return max(0.0, min(100.0, score))

    def _load_next_day_prior_map(self, codes: List[str]) -> Dict[str, float]:
        try:
            from modules.longhubang.longhubang_p1_signals import AdvancedP1SignalFetcher
            fetcher = AdvancedP1SignalFetcher()
            payload = fetcher.get_signal_data(days=3, stock_codes=codes, next_day_focus=True)
            stock_map = (payload or {}).get('stock_signal_map') or {}
            out = {}
            for code in codes:
                raw = self._safe_float((stock_map.get(code) or {}).get('score'))
                out[code] = max(0.0, min(100.0, raw / 1.2 * 100))
            return out
        except Exception:
            return {}

    def _calc_rerank_score(self, confidence: float, intraday_score: float, next_day_prior_score: float, overheat: bool) -> float:
        penalty = 12.0 if overheat else 0.0
        score = 0.60 * confidence + 0.25 * intraday_score + 0.15 * next_day_prior_score - penalty
        return max(0.0, min(100.0, score))

    @staticmethod
    def _kline_last_10(df) -> List[Dict]:
        if df is None or isinstance(df, dict) or not hasattr(df, 'tail'):
            return []
        try:
            work = df.tail(10).reset_index()
            out = []
            for _, row in work.iterrows():
                dt = row.get('Date')
                date_str = pd.to_datetime(dt, errors='coerce').strftime('%Y-%m-%d') if dt is not None else ''
                out.append(
                    {
                        'date': date_str,
                        'open': float(row.get('Open') or 0),
                        'high': float(row.get('High') or 0),
                        'low': float(row.get('Low') or 0),
                        'close': float(row.get('Close') or 0),
                        'volume': float(row.get('Volume') or 0),
                    }
                )
            return out
        except Exception:
            return []

    @staticmethod
    def _next_trade_date(date_str: str) -> str:
        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
        d = dt + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.strftime('%Y-%m-%d')

    def _build_market_context(self, benchmark_symbol: str = '000001') -> Dict:
        try:
            raw = self.sentiment_fetcher.get_market_sentiment_data(symbol=benchmark_symbol)
        except Exception:
            raw = {}

        market_idx = (raw or {}).get('market_index') or {}
        limit_ud = (raw or {}).get('limit_up_down') or {}
        fear_greed = (raw or {}).get('fear_greed_index') or {}

        context = {
            'benchmark_symbol': benchmark_symbol,
            'snapshot_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'market_index': {
                'name': market_idx.get('index_name') or '上证指数',
                'change_percent': market_idx.get('change_percent'),
                'sentiment_score': market_idx.get('sentiment_score'),
                'sentiment_interpretation': market_idx.get('sentiment_interpretation'),
                'up_count': market_idx.get('up_count'),
                'down_count': market_idx.get('down_count'),
                'flat_count': market_idx.get('flat_count'),
            },
            'limit_up_down': {
                'limit_up_count': limit_ud.get('limit_up_count'),
                'limit_down_count': limit_ud.get('limit_down_count'),
                'limit_ratio': limit_ud.get('limit_ratio'),
                'interpretation': limit_ud.get('interpretation'),
            },
            'fear_greed': {
                'score': fear_greed.get('score'),
                'level': fear_greed.get('level'),
                'interpretation': fear_greed.get('interpretation'),
            },
        }
        return context

    def _build_pool_with_kline(self, end_date: str, lookback_days: int, pool_limit: int) -> List[Dict]:
        pool = self.db.list_longhubang_stock_pool(end_date=end_date, lookback_days=lookback_days, limit=pool_limit)
        out = []
        for item in pool:
            code = str(item.get('code') or '').strip()
            if not code:
                continue
            hist = self.stock_fetcher.get_stock_data(code, period='3mo')
            kline_10d = self._kline_last_10(hist)
            if not kline_10d:
                continue
            out.append(
                {
                    'code': code,
                    'name': item.get('name') or '',
                    'source': item.get('source') or 'longhubang_top',
                    'score': float(item.get('score') or 0),
                    'theme': str(item.get('theme') or '').strip(),
                    'kline_10d': kline_10d,
                }
            )
        return out

    def learn_author_pattern(
        self,
        author_id: str,
        author_name: str,
        topic_urls: List[str],
        lookback_days: int = 60,
        progress_cb=None,
    ) -> Dict:
        inserted_post_ids: List[int] = []
        self.db.ensure_author(author_id=author_id, author_name=author_name)

        if callable(progress_cb):
            progress_cb("开始抓取帖子内容")
        posts = self.fetcher.fetch_topics(topic_urls)
        if not posts:
            err = "未抓取到有效帖子"
            if getattr(self.fetcher, "last_errors", None):
                err = err + " | " + " ; ".join(self.fetcher.last_errors[:3])
            return {"success": False, "error": err}

        if callable(progress_cb):
            progress_cb(f"抓取完成，获取到 {len(posts)} 篇，开始入库去重")
        for p in posts:
            post_id = self.db.upsert_post(author_id=author_id, post=p)
            if post_id:
                inserted_post_ids.append(post_id)

        latest_pattern_row = self.db.get_latest_author_pattern(author_id=author_id)
        new_posts = self.db.list_new_posts_for_author(author_id=author_id, limit=30)

        if latest_pattern_row and not new_posts:
            self.db.mark_posts_processed([int(x) for x in inserted_post_ids if x])
            try:
                latest_pattern_json = json.loads(latest_pattern_row.get('pattern_json') or '{}')
            except Exception:
                latest_pattern_json = {}
            return {
                "success": True,
                "author_id": author_id,
                "author_name": author_name,
                "report_id": None,
                "pattern_row_id": int(latest_pattern_row.get('id') or 0),
                "pattern_version": int(latest_pattern_row.get('pattern_version') or 0),
                "inserted_posts": 0,
                "pattern": latest_pattern_json,
                "message": "no_new_posts_skip_update",
                "run_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }

        input_posts: List[Dict] = []
        input_post_ids: List[int] = []
        pattern_json: Dict = {}

        if latest_pattern_row:
            if callable(progress_cb):
                progress_cb(f"检测到新增帖子 {len(new_posts)} 篇，开始增量更新模式")
            try:
                previous_pattern_json = json.loads(latest_pattern_row.get('pattern_json') or '{}')
            except Exception:
                previous_pattern_json = {}
            input_posts = list(new_posts)
            input_post_ids = [int(x.get("id")) for x in input_posts if x.get("id")]

            t1 = time.time()
            pattern_json = self.agents.update_pattern(
                author_name=author_name,
                previous_pattern_json=previous_pattern_json,
                new_posts=input_posts,
                lookback_days=lookback_days,
            )
            pattern_elapsed = time.time() - t1

            if not isinstance(pattern_json, dict) or pattern_json.get('error_reason') == 'json_parse_failed':
                if callable(progress_cb):
                    progress_cb("增量解析失败，回退到最近30篇全量重算")
                history_posts = self.db.get_posts_for_lookback(author_id=author_id, limit=30)
                if not history_posts:
                    return {"success": False, "error": "增量失败且无可回退历史帖子"}
                input_posts = history_posts
                input_post_ids = [int(x.get("id")) for x in input_posts if x.get("id")]
                t2 = time.time()
                pattern_json = self.agents.extract_pattern(author_name=author_name, posts=input_posts, lookback_days=lookback_days)
                pattern_elapsed = time.time() - t2
        else:
            history_posts = self.db.get_posts_for_lookback(author_id=author_id, limit=30)
            if not history_posts:
                return {"success": False, "error": "历史帖子为空"}
            input_posts = history_posts
            input_post_ids = [int(x.get("id")) for x in input_posts if x.get("id")]
            if callable(progress_cb):
                progress_cb(f"首次学习，样本 {len(history_posts)} 篇，开始提炼交易模式")
            t1 = time.time()
            pattern_json = self.agents.extract_pattern(author_name=author_name, posts=input_posts, lookback_days=lookback_days)
            pattern_elapsed = time.time() - t1

        report_id = self.db.save_pattern_report(
            author_id=author_id,
            lookback_days=lookback_days,
            input_post_ids=input_post_ids,
            pattern_json=pattern_json,
            model_used=str(config.DEFAULT_MODEL_NAME),
            analysis_time=pattern_elapsed,
        )

        confidence = float((pattern_json or {}).get('confidence') or 0)
        pattern_row_id = self.db.save_author_pattern(
            author_id=author_id,
            pattern_json=pattern_json,
            source_post_ids=input_post_ids,
            confidence=confidence,
            as_of_date=datetime.now().strftime('%Y-%m-%d'),
        )
        latest_pattern = self.db.get_latest_author_pattern(author_id=author_id) or {}
        pattern_version = int(latest_pattern.get('pattern_version') or 0)

        self.db.save_learning_memory(
            author_id=author_id,
            report_id=report_id,
            memory_type='pattern',
            memory_json=pattern_json,
            quality_score=confidence / 100.0,
        )

        if new_posts:
            self.db.mark_posts_processed([int(x.get('id')) for x in new_posts if x.get('id')])

        if callable(progress_cb):
            progress_cb(f"模式学习完成，已生成版本 v{pattern_version}")

        return {
            "success": True,
            "author_id": author_id,
            "author_name": author_name,
            "report_id": report_id,
            "pattern_row_id": pattern_row_id,
            "pattern_version": pattern_version,
            "inserted_posts": len(inserted_post_ids),
            "pattern": pattern_json,
            "run_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def recommend_by_pattern(
        self,
        author_id: str,
        author_name: str,
        pattern_version: int,
        pool_lookback_days: int = 20,
        pool_limit: int = 120,
        candidate_limit: int = 3,
        progress_cb=None,
    ) -> Dict:
        pattern_row = self.db.get_author_pattern(author_id=author_id, pattern_version=int(pattern_version))
        if not pattern_row:
            return {"success": False, "error": f"未找到作者模式版本: v{int(pattern_version)}"}

        try:
            pattern_json = json.loads(pattern_row.get('pattern_json') or '{}')
        except Exception:
            pattern_json = {}

        success_memories = self.db.list_top_memories(author_id=author_id, memory_type='pattern', limit=5)
        failure_lessons = self.db.list_top_memories(author_id=author_id, memory_type='lesson', limit=5)
        feedback_metrics = self.db.aggregate_author_feedback(author_id=author_id, lookback_days=90)
        analysis_date = datetime.now().strftime('%Y-%m-%d')
        target_trade_date = self._next_trade_date(analysis_date)
        market_context = self._build_market_context(benchmark_symbol='000001')
        stock_pool = self._build_pool_with_kline(
            end_date=analysis_date,
            lookback_days=pool_lookback_days,
            pool_limit=pool_limit,
        )
        if not stock_pool:
            return {'success': False, 'error': '龙虎榜股票池为空或近10日K线不可用'}

        if callable(progress_cb):
            progress_cb(f"已加载龙虎榜股票池: {len(stock_pool)} 只，按{analysis_date}收盘数据生成{target_trade_date}交易计划")
        candidate_json = self.agents.generate_candidates(
            author_name=author_name,
            pattern_json=pattern_json,
            historical_success_patterns=[json.loads(x.get('memory_json') or '{}') if isinstance(x.get('memory_json'), str) else x.get('memory_json') for x in success_memories],
            historical_failure_lessons=[json.loads(x.get('memory_json') or '{}') if isinstance(x.get('memory_json'), str) else x.get('memory_json') for x in failure_lessons],
            author_feedback_metrics=feedback_metrics,
            allowed_stock_pool=stock_pool,
            candidate_limit=int(candidate_limit or 3),
            analysis_date=analysis_date,
            target_trade_date=target_trade_date,
            market_context=market_context,
            intraday_mode=False,
        )
        if isinstance(candidate_json, dict):
            candidate_json['market_context'] = market_context

        candidate_id = self.db.save_candidate(
            author_id=author_id,
            report_id=None,
            post_id=None,
            candidate_json=candidate_json,
            status='draft',
            pattern_version=int(pattern_version),
            batch_type='offline',
            batch_id=datetime.now().strftime('offline_%Y%m%d_%H%M%S'),
        )

        self.db.save_learning_memory(
            author_id=author_id,
            report_id=None,
            memory_type='candidate',
            memory_json=candidate_json,
            quality_score=float(feedback_metrics.get('win_rate') or 0) / 100.0,
        )

        if callable(progress_cb):
            progress_cb("候选生成完成，已写入草稿")
        return {
            "success": True,
            "author_id": author_id,
            "author_name": author_name,
            "pattern_version": int(pattern_version),
            "candidate_id": candidate_id,
            "candidates": candidate_json,
            "pool_size": len(stock_pool),
            "market_context": market_context,
            "pool_lookback_days": int(pool_lookback_days),
            "analysis_date": analysis_date,
            "target_trade_date": target_trade_date,
            "run_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def recommend_intraday_by_pattern(
        self,
        author_id: str,
        author_name: str,
        pattern_version: int,
        pool_lookback_days: int = 20,
        pool_limit: int = 120,
        candidate_limit: int = 3,
        progress_cb=None,
        enforce_tdx: bool = True,
    ) -> Dict:
        base_result = self.recommend_by_pattern(
            author_id=author_id,
            author_name=author_name,
            pattern_version=int(pattern_version),
            pool_lookback_days=int(pool_lookback_days),
            pool_limit=int(pool_limit),
            candidate_limit=max(int(candidate_limit or 3), 6),
            progress_cb=progress_cb,
        )
        if not base_result.get('success'):
            return base_result

        candidate_payload = base_result.get('candidates') or {}
        base_candidates = list((candidate_payload or {}).get('candidates') or [])
        generation_mode = 'base_then_filter'

        if not base_candidates:
            generation_mode = 'rebuild_on_empty_base'
            pattern_row = self.db.get_author_pattern(author_id=author_id, pattern_version=int(pattern_version))
            try:
                pattern_json = json.loads((pattern_row or {}).get('pattern_json') or '{}')
            except Exception:
                pattern_json = {}
            if not pattern_json:
                return {"success": False, "error": "作者模式为空，无法进行盘中实时推荐"}

            realtime_pool = self._build_pool_with_kline(
                end_date=datetime.now().strftime('%Y-%m-%d'),
                lookback_days=pool_lookback_days,
                pool_limit=pool_limit,
            )
            if not realtime_pool:
                return {"success": False, "error": "实时龙虎榜股票池为空，无法重建盘中推荐"}

            enriched_pool = []
            if callable(progress_cb):
                progress_cb(f"基础候选为空，开始基于龙虎榜+TDX实时数据重建候选: {len(realtime_pool)} 只")
            for item in realtime_pool:
                code = str(item.get('code') or '').strip()
                if not code:
                    continue
                quote = self._fetch_realtime_with_retry(code, retries=3, retry_delay=0.6)
                source = str(quote.get('data_source') or '').lower()
                rt_price = float(quote.get('current_price') or 0)
                if callable(progress_cb):
                    progress_cb(f"重建候选行情 {code}: source={source or 'none'} price={rt_price:.3f}")
                if enforce_tdx and source != 'tdx':
                    continue
                if rt_price <= 0:
                    continue
                enriched_item = dict(item)
                enriched_item['rt_price'] = rt_price
                enriched_item['rt_change_pct'] = float(quote.get('change_pct') or 0)
                enriched_item['rt_update_time'] = str(quote.get('update_time') or '')
                enriched_item['rt_source'] = source or 'unknown'
                enriched_pool.append(enriched_item)

            if not enriched_pool:
                return {"success": False, "error": "TDX实时可用标的不足，无法重建盘中推荐"}

            rebuilt_candidate_json = self.agents.generate_candidates(
                author_name=author_name,
                pattern_json=pattern_json,
                historical_success_patterns=[json.loads(x.get('memory_json') or '{}') if isinstance(x.get('memory_json'), str) else x.get('memory_json') for x in self.db.list_top_memories(author_id=author_id, memory_type='pattern', limit=5)],
                historical_failure_lessons=[json.loads(x.get('memory_json') or '{}') if isinstance(x.get('memory_json'), str) else x.get('memory_json') for x in self.db.list_top_memories(author_id=author_id, memory_type='lesson', limit=5)],
                author_feedback_metrics=self.db.aggregate_author_feedback(author_id=author_id, lookback_days=90),
                allowed_stock_pool=enriched_pool,
                candidate_limit=int(candidate_limit or 3),
                analysis_date=datetime.now().strftime('%Y-%m-%d'),
                target_trade_date=self._next_trade_date(datetime.now().strftime('%Y-%m-%d')),
                market_context=self._build_market_context(benchmark_symbol='000001'),
                intraday_mode=True,
            )
            base_candidates = list((rebuilt_candidate_json or {}).get('candidates') or [])
            candidate_payload = dict(rebuilt_candidate_json or {})
            candidate_payload['candidates'] = base_candidates
            candidate_payload['summary'] = str(candidate_payload.get('summary') or '') + f" | 重建候选数量:{len(base_candidates)}"

        if callable(progress_cb):
            progress_cb(f"开始查询实时行情(TDX): {len(base_candidates)} 只")

        enriched = []
        skipped = 0
        failed = 0
        for c in base_candidates:
            code = str((c or {}).get('code') or '').strip()
            if not code:
                skipped += 1
                continue
            quote = self._fetch_realtime_with_retry(code, retries=3, retry_delay=0.6)
            source = str(quote.get('data_source') or '').lower()
            rt_price = float(quote.get('current_price') or 0)
            if callable(progress_cb):
                progress_cb(f"盘中行情 {code}: source={source or 'none'} price={rt_price:.3f}")
            if enforce_tdx and source != 'tdx':
                skipped += 1
                continue
            if not quote or rt_price <= 0:
                failed += 1
                continue
            item = dict(c)
            item['rt_price'] = rt_price
            item['rt_change_pct'] = float(quote.get('change_pct') or 0)
            if float(item.get('rt_change_pct') or 0) >= 7.0:
                item['action'] = '回避'
                item['entry_idea'] = '盘中涨幅已大，避免追高，等待回踩或次日分歧再评估'
                item['reason'] = str(item.get('reason') or '') + ' | 盘中涨幅过大，低吸条件不成立'
                item['confidence'] = min(float(item.get('confidence') or 0), 35)
            item['rt_update_time'] = str(quote.get('update_time') or '')
            item['rt_source'] = source or 'unknown'
            enriched.append(item)

        if callable(progress_cb):
            progress_cb(f"实时行情统计: 总候选={len(base_candidates)} 入选={len(enriched)} 跳过={skipped} 失败={failed}")

        if not enriched:
            return {"success": False, "error": "TDX实时行情不可用，本次未生成盘中推荐"}

        codes = [str(x.get('code') or '').strip() for x in enriched if str(x.get('code') or '').strip()]
        prior_map = self._load_next_day_prior_map(codes)
        for item in enriched:
            code = str(item.get('code') or '').strip()
            intraday_raw = self._get_intraday_raw(code)
            minute_rows = intraday_raw.get('minute') or []
            trade_rows = intraday_raw.get('trade') or []
            quote_raw = intraday_raw.get('quote') or {}
            pull_rhythm_score = self._calc_pull_rhythm_score(minute_rows)
            volume_price_score = self._calc_volume_price_score(minute_rows, trade_rows)
            pullback_support_score = self._calc_pullback_support_score(minute_rows, trade_rows, quote_raw)
            intraday_score = max(0.0, min(100.0, 0.38 * pull_rhythm_score + 0.34 * volume_price_score + 0.28 * pullback_support_score))
            confidence = self._safe_float(item.get('confidence'))
            next_day_prior_score = self._safe_float(prior_map.get(code), 50.0)
            overheat = self._safe_float(item.get('rt_change_pct')) >= 7.0
            missing_intraday_data = (len(minute_rows) < 20)
            if missing_intraday_data:
                intraday_score = confidence
            rerank_score = self._calc_rerank_score(
                confidence=confidence,
                intraday_score=intraday_score,
                next_day_prior_score=next_day_prior_score,
                overheat=overheat,
            )
            item['intraday_feature_version'] = 'tdx_intraday_v1'
            item['intraday_score'] = round(intraday_score, 2)
            item['next_day_prior_score'] = round(next_day_prior_score, 2)
            item['rerank_score'] = round(rerank_score, 2)
            item['intraday_features'] = {
                'pull_rhythm_score': round(pull_rhythm_score, 2),
                'volume_price_score': round(volume_price_score, 2),
                'pullback_support_score': round(pullback_support_score, 2),
                'minute_points': len(minute_rows),
                'trade_points': len(trade_rows),
            }
            item['risk_flags'] = {
                'overheat': bool(overheat),
                'missing_intraday_data': bool(missing_intraday_data),
            }

        def _score(x: Dict):
            return (
                float(x.get('rerank_score') or x.get('confidence') or 0),
                float(x.get('confidence') or 0),
                -abs(float(x.get('rt_change_pct') or 0)),
            )

        enriched.sort(key=_score, reverse=True)
        limited = enriched[: max(1, int(candidate_limit or 3))]

        intraday_payload = dict(candidate_payload)
        intraday_payload['candidates'] = limited
        intraday_payload['summary'] = f"盘中实时筛选完成: 入选{len(limited)}只, 跳过{skipped}只, 失败{failed}只"
        intraday_payload['generation_mode'] = generation_mode
        intraday_payload['base_count'] = len(base_candidates)
        intraday_payload['filtered_count'] = len(enriched)
        intraday_payload['skipped_count'] = int(skipped)
        intraday_payload['failed_count'] = int(failed)
        intraday_payload['rerank_enabled'] = True
        intraday_payload['rerank_formula_version'] = 'tdx_intraday_v1'
        intraday_payload['feature_coverage_stats'] = {
            'with_minute': int(sum(1 for x in enriched if not (x.get('risk_flags') or {}).get('missing_intraday_data'))),
            'with_prior': int(sum(1 for x in enriched if self._safe_float(x.get('next_day_prior_score')) > 0)),
            'total': int(len(enriched)),
        }

        batch_id = datetime.now().strftime('intraday_%Y%m%d_%H%M%S')
        candidate_id = self.db.save_candidate(
            author_id=author_id,
            report_id=None,
            post_id=None,
            candidate_json=intraday_payload,
            status='draft',
            pattern_version=int(pattern_version),
            batch_type='intraday_tdx',
            batch_id=batch_id,
        )

        if callable(progress_cb):
            progress_cb(f"盘中实时推荐完成，入选 {len(limited)} 只")

        return {
            "success": True,
            "author_id": author_id,
            "author_name": author_name,
            "pattern_version": int(pattern_version),
            "candidate_id": int(candidate_id),
            "candidates": intraday_payload,
            "selected_count": len(limited),
            "base_count": len(base_candidates),
            "filtered_count": len(enriched),
            "rebuilt_count": len(base_candidates) if generation_mode == 'rebuild_on_empty_base' else 0,
            "skipped_count": int(skipped),
            "failed_count": int(failed),
            "generation_mode": generation_mode,
            "batch_type": 'intraday_tdx',
            "batch_id": batch_id,
            "run_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def analyze_topic_comments(
        self,
        author_id: str,
        topic_urls: List[str],
        max_pages: int = 3,
        start_page: int = 1,
        progress_cb=None,
    ) -> Dict:
        aid = str(author_id or '').strip()
        urls = [str(u or '').strip() for u in (topic_urls or []) if str(u or '').strip()]
        if not aid:
            return {'success': False, 'error': 'author_id不能为空'}
        if not urls:
            return {'success': False, 'error': 'topic_urls为空'}

        summaries = []
        errors = []
        total_comments = 0
        for url in urls:
            if callable(progress_cb):
                progress_cb(f"抓取评论: {url}")
            self.fetcher.last_errors = []
            comments = self.fetcher.fetch_topic_comments(
                url,
                max_pages=max_pages,
                start_page=start_page,
            )
            fetch_error = " ; ".join(self.fetcher.last_errors[:3]) if getattr(self.fetcher, 'last_errors', None) else ""
            post_row = None
            posts = self.db.get_posts_for_lookback(author_id=aid, limit=200)
            for p in posts:
                if str(p.get('post_url') or '').strip() == url:
                    post_row = p
                    break
            post_id = int((post_row or {}).get('id') or 0) if post_row else None

            for c in comments:
                self.db.upsert_post_comment(
                    author_id=aid,
                    post_id=post_id,
                    post_url=url,
                    comment=c,
                )
            analysis = comment_analyzer.analyze_comments(comments)
            self.db.replace_post_comment_summary(
                author_id=aid,
                post_id=post_id,
                post_url=url,
                comment_count=int(analysis.get('comment_count') or 0),
                sentiment_score=float(analysis.get('sentiment_score') or 50),
                sentiment_label=str(analysis.get('sentiment_label') or '中性'),
                operation_bias=analysis.get('operation_bias') or {},
                summary_payload=analysis,
            )
            total_comments += int(analysis.get('comment_count') or 0)
            if fetch_error:
                errors.append({'post_url': url, 'error': fetch_error})
            summaries.append(
                {
                    'post_url': url,
                    'comment_count': int(analysis.get('comment_count') or 0),
                    'sentiment_label': str(analysis.get('sentiment_label') or '中性'),
                    'sentiment_score': float(analysis.get('sentiment_score') or 50),
                    'summary_text': str(analysis.get('summary_text') or ''),
                    'operation_bias': analysis.get('operation_bias') or {},
                    'fetch_error': fetch_error,
                }
            )

        return {
            'success': True,
            'author_id': aid,
            'topic_count': len(urls),
            'total_comments': int(total_comments),
            'summaries': summaries,
            'errors': errors,
            'run_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def run_once(
        self,
        author_id: str,
        author_name: str,
        topic_urls: List[str],
        lookback_days: int = 60,
        pool_lookback_days: int = 20,
        pool_limit: int = 120,
        progress_cb=None,
    ) -> Dict:
        learn_result = self.learn_author_pattern(
            author_id=author_id,
            author_name=author_name,
            topic_urls=topic_urls,
            lookback_days=lookback_days,
            progress_cb=progress_cb,
        )
        if not learn_result.get('success'):
            return learn_result

        pattern_version = int(learn_result.get('pattern_version') or 0)
        rec_result = self.recommend_by_pattern(
            author_id=author_id,
            author_name=author_name,
            pattern_version=pattern_version,
            pool_lookback_days=pool_lookback_days,
            pool_limit=pool_limit,
            progress_cb=progress_cb,
        )
        if not rec_result.get('success'):
            return rec_result

        return {
            "success": True,
            "inserted_posts": int(learn_result.get('inserted_posts') or 0),
            "report_id": int(learn_result.get('report_id') or 0),
            "candidate_id": int(rec_result.get('candidate_id') or 0),
            "pattern_version": pattern_version,
            "pattern": learn_result.get('pattern') or {},
            "candidates": rec_result.get('candidates') or {},
            "pool_size": int(rec_result.get('pool_size') or 0),
            "pool_lookback_days": int(pool_lookback_days),
            "run_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }


douban_author_engine = DoubanAuthorStrategyEngine()
