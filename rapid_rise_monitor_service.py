import json
import os
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List

import requests

from longhubang_history import LonghubangHistoryService
from monitor_db import monitor_db
from notification_service import notification_service
from trade_calendar_service import TradeCalendarService


class RapidRiseMonitorService:
    """全市场快速拉升扫描服务（TDX batch-quote）"""

    def __init__(self):
        self.base_url = os.getenv('TDX_BASE_URL', 'http://127.0.0.1:8080').rstrip('/')
        self.enabled = os.getenv('ENABLE_RAPID_RISE_PIPELINE', 'true').lower() == 'true'
        self.interval_sec = int(os.getenv('RAPID_RISE_SCAN_INTERVAL_SEC', '30'))
        self.batch_size = int(os.getenv('RAPID_RISE_BATCH_SIZE', '300'))

        self.threshold_1m = float(os.getenv('RAPID_RISE_THRESHOLD_1M', '1.2'))
        self.threshold_3m = float(os.getenv('RAPID_RISE_THRESHOLD_3M', '2.0'))
        self.threshold_amt_ratio = float(os.getenv('RAPID_RISE_AMOUNT_RATIO_1M20', '1.8'))

        self.max_push_per_minute = int(os.getenv('RAPID_RISE_MAX_PUSH_PER_MINUTE', '30'))
        self.summary_interval_sec = int(os.getenv('RAPID_RISE_SUMMARY_INTERVAL_SEC', '180'))
        self.summary_max_items = int(os.getenv('RAPID_RISE_SUMMARY_MAX_ITEMS', '8'))

        self.running = False
        self.thread = None

        self.symbols: List[str] = []
        self.price_windows: Dict[str, deque] = defaultdict(lambda: deque(maxlen=30))
        self.amount_windows: Dict[str, deque] = defaultdict(lambda: deque(maxlen=30))

        self.push_window = deque(maxlen=300)
        self.pending_summary = defaultdict(list)
        self.last_summary_ts = 0.0

        self.calendar = TradeCalendarService()
        self.history_service = LonghubangHistoryService()

        self.last_status = {
            'last_loop_at': '',
            'last_error': '',
            'last_error_trace': '',
            'last_symbols_load': 0,
            'last_quotes_count': 0,
            'last_event_count': 0,
            'last_skip_reason': '',
        }

    def get_runtime_config(self) -> Dict:
        return {
            'enabled': self.enabled,
            'base_url': self.base_url,
            'interval_sec': self.interval_sec,
            'batch_size': self.batch_size,
            'threshold_1m': self.threshold_1m,
            'threshold_3m': self.threshold_3m,
            'threshold_amt_ratio': self.threshold_amt_ratio,
            'max_push_per_minute': self.max_push_per_minute,
            'summary_interval_sec': self.summary_interval_sec,
            'summary_max_items': self.summary_max_items,
            'running': self.running,
            'symbols_count': len(self.symbols),
            'last_status': self.last_status,
        }

    def update_runtime_config(
        self,
        interval_sec: int,
        batch_size: int,
        threshold_1m: float,
        threshold_3m: float,
        threshold_amt_ratio: float,
        max_push_per_minute: int,
        summary_interval_sec: int,
        summary_max_items: int,
    ):
        self.interval_sec = max(5, int(interval_sec))
        self.batch_size = max(50, int(batch_size))
        self.threshold_1m = float(threshold_1m)
        self.threshold_3m = float(threshold_3m)
        self.threshold_amt_ratio = float(threshold_amt_ratio)
        self.max_push_per_minute = max(1, int(max_push_per_minute))
        self.summary_interval_sec = max(60, int(summary_interval_sec))
        self.summary_max_items = max(1, int(summary_max_items))

    def start(self):
        if not self.enabled or self.running:
            if not self.enabled:
                self.last_status['last_skip_reason'] = 'pipeline_disabled'
            return
        self.running = True
        self.last_status['last_error'] = ''
        self.last_status['last_error_trace'] = ''
        self.last_status['last_skip_reason'] = ''
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)

    def _loop(self):
        while self.running:
            try:
                self.last_status['last_loop_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                if not self._is_trading_session():
                    self.last_status['last_skip_reason'] = 'non_trading_session'
                    time.sleep(min(self.interval_sec, 20))
                    continue

                self.last_status['last_skip_reason'] = ''

                if not self.symbols:
                    self.symbols = self._load_symbols()
                    self.last_status['last_symbols_load'] = len(self.symbols)

                if not self.symbols:
                    self.last_status['last_skip_reason'] = 'empty_symbol_pool'
                    time.sleep(self.interval_sec)
                    continue

                total_quotes = 0
                total_events = 0
                for i in range(0, len(self.symbols), self.batch_size):
                    batch = self.symbols[i:i + self.batch_size]
                    quotes = self._fetch_batch_quotes(batch)
                    total_quotes += len(quotes)
                    total_events += self._process_quotes(quotes)

                self.last_status['last_quotes_count'] = total_quotes
                self.last_status['last_event_count'] = total_events

                self._maybe_push_summary()
                time.sleep(self.interval_sec)
            except Exception as e:
                self.last_status['last_error'] = str(e)
                self.last_status['last_error_trace'] = traceback.format_exc(limit=5)
                time.sleep(max(10, self.interval_sec))

    def _is_trading_session(self) -> bool:
        now = datetime.now()
        date8 = now.strftime('%Y%m%d')
        if not self.calendar.is_trading_day(date8):
            return False
        hm = now.hour * 100 + now.minute
        return (930 <= hm <= 1130) or (1300 <= hm <= 1500)

    def _load_symbols(self) -> List[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/stock-codes?prefix=false", timeout=12)
            payload = resp.json()
            data = payload.get('data', {}) if isinstance(payload, dict) else {}
            codes = data.get('list', []) if isinstance(data, dict) else []
            return [str(c).strip() for c in codes if str(c).strip().isdigit() and len(str(c).strip()) == 6]
        except Exception:
            return []

    def _fetch_batch_quotes(self, codes: List[str]) -> List[Dict]:
        if not codes:
            return []
        try:
            resp = requests.post(
                f"{self.base_url}/api/batch-quote",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"codes": codes}, ensure_ascii=False),
                timeout=20,
            )
            payload = resp.json()
            if isinstance(payload, dict) and payload.get('code') == 0:
                return payload.get('data', []) or []
            return []
        except Exception:
            return []

    def _process_quotes(self, quotes: List[Dict]) -> int:
        now = datetime.now()
        event_time = now.strftime('%Y-%m-%d %H:%M:%S')
        trade_date = now.strftime('%Y%m%d')
        event_count = 0

        for q in quotes:
            code = str(q.get('Code', '')).strip()
            if not code:
                continue

            k = q.get('K', {}) or {}
            close_raw = k.get('Close')
            amount_raw = q.get('Amount')
            name = code

            try:
                close = float(close_raw) / 1000.0
                amount = float(amount_raw) / 1000.0
            except Exception:
                continue

            if close <= 0:
                continue

            self.price_windows[code].append((time.time(), close))
            self.amount_windows[code].append((time.time(), amount))

            rise_1m = self._pct_change(self.price_windows[code], 60)
            rise_3m = self._pct_change(self.price_windows[code], 180)
            rise_5m = self._pct_change(self.price_windows[code], 300)
            amt_ratio = self._amount_ratio_1m20(code)

            if not self._is_trigger(rise_1m, rise_3m, amt_ratio):
                continue

            direction = 'up'
            if monitor_db.has_recent_rapid_rise(code, direction=direction, minutes=8):
                continue

            trigger_level = self._trigger_level(rise_1m, rise_3m)
            bucket = int(time.time() // 120)
            dedupe_key = f"{code}:{direction}:{bucket}:{trigger_level}:v1"

            themes, hist_score, peer_score, peer_examples = self._enrich_theme_stats(code, trade_date)

            event = {
                'trade_date': trade_date,
                'event_time': event_time,
                'symbol': code,
                'name': name,
                'direction': direction,
                'last_price': close,
                'rise_1m': round(rise_1m, 2),
                'rise_3m': round(rise_3m, 2),
                'rise_5m': round(rise_5m, 2),
                'amount_1m': round(self._amount_in_window(code, 60), 2),
                'amount_ratio_1m20': round(amt_ratio, 2),
                'trigger_level': trigger_level,
                'rule_version': 'v1',
                'theme_tokens': '、'.join(themes[:5]),
                'theme_hist_score': round(hist_score, 4),
                'peer_linkage_score': round(peer_score, 4),
                'dedupe_key': dedupe_key,
                'payload': {
                    'quote': {
                        'Code': code,
                        'Close': close_raw,
                        'Amount': amount_raw,
                    },
                    'peer_examples': peer_examples,
                }
            }

            event_id = monitor_db.add_rapid_rise_event(event)
            if not event_id:
                continue

            if self._allow_push_now():
                self._push_event(event, event_id)
            else:
                self._collect_summary(event)
                monitor_db.add_rapid_rise_push_record(event_id, 'default', 'suppressed', 'rate_limited')

            event_count += 1

        return event_count

    def _pct_change(self, window: deque, seconds: int) -> float:
        if not window:
            return 0.0
        now_ts = time.time()
        latest = window[-1][1]
        base = None
        for ts, price in window:
            if now_ts - ts <= seconds:
                base = price
                break
        if not base or base <= 0:
            return 0.0
        return (latest - base) / base * 100

    def _amount_delta_series(self, code: str) -> List[tuple]:
        rows = list(self.amount_windows[code])
        if len(rows) < 2:
            return []
        out = []
        for i in range(1, len(rows)):
            ts, amount = rows[i]
            prev_amount = rows[i - 1][1]
            delta = float(amount) - float(prev_amount)
            # Amount 为当日累计成交额，增量应为非负；异常回退为0
            out.append((ts, delta if delta > 0 else 0.0))
        return out

    def _amount_in_window(self, code: str, seconds: int) -> float:
        deltas = self._amount_delta_series(code)
        if not deltas:
            return 0.0
        now_ts = time.time()
        return sum(v for ts, v in deltas if now_ts - ts <= seconds)

    def _amount_ratio_1m20(self, code: str) -> float:
        deltas = self._amount_delta_series(code)
        if len(deltas) < 5:
            return 0.0

        now_ts = time.time()
        win_1m = [(ts, v) for ts, v in deltas if now_ts - ts <= 60]
        win_20m = [(ts, v) for ts, v in deltas if now_ts - ts <= 1200]
        if not win_1m or len(win_20m) < 5:
            return 0.0

        amt_1m = sum(v for _, v in win_1m)
        amt_20m = sum(v for _, v in win_20m)
        if amt_1m <= 0 or amt_20m <= 0:
            return 0.0

        oldest_ts = min(ts for ts, _ in win_20m)
        covered_sec = max(60.0, min(1200.0, now_ts - oldest_ts))
        avg_per_min_20m = amt_20m / (covered_sec / 60.0)
        if avg_per_min_20m <= 0:
            return 0.0

        return amt_1m / avg_per_min_20m

    def _is_trigger(self, rise_1m: float, rise_3m: float, amt_ratio: float) -> bool:
        return (rise_1m >= self.threshold_1m or rise_3m >= self.threshold_3m)

    def _trigger_level(self, rise_1m: float, rise_3m: float) -> str:
        if rise_1m >= 2.5 or rise_3m >= 4.0:
            return 'L3'
        if rise_1m >= 1.8 or rise_3m >= 3.0:
            return 'L2'
        return 'L1'

    def _enrich_theme_stats(self, code: str, trade_date: str):
        themes: List[str] = []
        hist_score = 0.0
        peer_score = 0.0
        peer_examples: List[Dict] = []

        try:
            peer = self.history_service.query_theme_peer_stocks(code, before_date=trade_date, min_cooccur_count=2, top_n=8)
            if peer.get('success'):
                themes = peer.get('themes', []) or []
                peers = peer.get('peer_stocks', []) or []
                if peers:
                    # 频次与同群表现混合，映射到0-1
                    top_peers = sorted(
                        peers,
                        key=lambda p: (float(p.get('cooccur_count', 0) or 0), float(p.get('avg_pct_chg', 0) or 0)),
                        reverse=True,
                    )
                    peer_examples = top_peers[:3]
                    avg_cooccur = sum(float(p.get('cooccur_count', 0) or 0) for p in top_peers[:5]) / max(1, min(5, len(top_peers)))
                    avg_pct = sum(float(p.get('avg_pct_chg', 0) or 0) for p in top_peers[:5]) / max(1, min(5, len(top_peers)))
                    cooccur_part = min(1.0, avg_cooccur / 6.0)
                    pct_part = max(0.0, min((avg_pct + 2.0) / 8.0, 1.0))
                    peer_score = 0.6 * cooccur_part + 0.4 * pct_part

            if themes:
                stats = self.history_service._get_theme_historical_stats(trade_date, themes)
                vals = []
                for t in themes:
                    key = self.history_service._norm_theme_key(t)
                    item = stats.get(key)
                    if not item:
                        continue
                    wr = float(item.get('win_rate', 0.0) or 0.0)
                    next_pct = float(item.get('avg_next_pct', 0.0) or 0.0)
                    samples = float(item.get('samples', 0) or 0)

                    wr_part = max(0.0, min(wr, 1.0))
                    next_part = max(0.0, min((next_pct + 3.0) / 10.0, 1.0))
                    sample_weight = min(1.0, samples / 30.0)
                    vals.append((0.65 * wr_part + 0.35 * next_part) * (0.5 + 0.5 * sample_weight))

                if vals:
                    hist_score = sum(vals) / len(vals)
        except Exception:
            pass

        return themes, hist_score, peer_score, peer_examples

    def _allow_push_now(self) -> bool:
        now_ts = time.time()
        while self.push_window and now_ts - self.push_window[0] > 60:
            self.push_window.popleft()
        if len(self.push_window) >= self.max_push_per_minute:
            return False
        self.push_window.append(now_ts)
        return True

    def _collect_summary(self, event: Dict):
        themes = str(event.get('theme_tokens', '') or '').split('、')
        key = themes[0].strip() if themes and themes[0].strip() else '未分类'
        self.pending_summary[key].append({
            'symbol': event.get('symbol'),
            'rise_3m': float(event.get('rise_3m', 0.0) or 0.0),
            'trigger_level': event.get('trigger_level', 'L1'),
        })

    def _maybe_push_summary(self):
        now_ts = time.time()
        if not self.pending_summary:
            return
        if self.last_summary_ts and now_ts - self.last_summary_ts < self.summary_interval_sec:
            return

        lines = []
        for theme, items in sorted(self.pending_summary.items(), key=lambda kv: len(kv[1]), reverse=True):
            top_items = sorted(items, key=lambda x: x['rise_3m'], reverse=True)[:3]
            stocks = '、'.join([f"{x['symbol']}({x['rise_3m']:.2f}%)" for x in top_items if x.get('symbol')])
            lines.append(f"{theme}: +{len(items)}只 {stocks}".strip())
            if len(lines) >= self.summary_max_items:
                break

        if not lines:
            return

        title = '全市场快速拉升摘要'
        message = ' | '.join(lines)
        payload = {
            'symbol': 'MARKET',
            'name': '全市场',
            'type': 'rapid_rise_summary',
            'message': message,
            'triggered_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'title': title,
        }
        notification_service.send_notification(payload)
        self.pending_summary.clear()
        self.last_summary_ts = now_ts

    def _push_event(self, event: Dict, event_id: int):
        title = f"全市场快速拉升 {event['symbol']} {event['trigger_level']}"
        peer_examples = ((event.get('payload') or {}).get('peer_examples') or [])[:3]
        peer_text = ""
        if peer_examples:
            peer_items = []
            for p in peer_examples:
                code = str(p.get('code', '')).strip()
                name = str(p.get('name', '')).strip()
                cc = int(float(p.get('cooccur_count', 0) or 0))
                avgp = float(p.get('avg_pct_chg', 0) or 0)
                if code:
                    peer_items.append(f"{code}{name and ('/' + name)}(共现{cc}次,均涨{avgp:.2f}%)")
            if peer_items:
                peer_text = " | 同类票: " + "；".join(peer_items)

        msg = (
            f"{event['symbol']} 价格 {event['last_price']:.2f}，"
            f"1m {event['rise_1m']:.2f}% / 3m {event['rise_3m']:.2f}% / 量比 {event['amount_ratio_1m20']:.2f}，"
            f"题材历史分 {event['theme_hist_score']:.2f}，联动分 {event['peer_linkage_score']:.2f}"
            f"{peer_text}"
        )
        payload = {
            'symbol': event['symbol'],
            'name': event.get('name') or event['symbol'],
            'type': 'rapid_rise',
            'message': msg,
            'triggered_at': event['event_time'],
            'title': title,
        }

        ok = notification_service.send_notification(payload)
        monitor_db.add_rapid_rise_push_record(event_id, 'default', 'sent' if ok else 'failed', '' if ok else 'send_failed')


rapid_rise_monitor_service = RapidRiseMonitorService()
