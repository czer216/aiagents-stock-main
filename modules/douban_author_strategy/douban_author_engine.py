import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from infrastructure.data.stock_data import StockDataFetcher
from modules.douban_author_strategy.douban_author_data import DoubanAuthorDataFetcher
from modules.douban_author_strategy.douban_author_db import douban_author_db
from modules.douban_author_strategy.douban_author_agents import DoubanAuthorAgents
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

        def _score(x: Dict):
            return (
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
