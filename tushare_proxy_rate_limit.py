"""
Tushare代理接口限流保护
默认1分钟内请求1000次后，下一次请求前sleep 100ms（0.1秒）。
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from collections import deque
from typing import Optional


_LOCK = threading.Lock()
_REQUEST_TIMESTAMPS = deque()


def _safe_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


_LIMIT_COUNT = max(_safe_int(os.getenv("TUSHARE_PROXY_LIMIT_COUNT", "1000"), 1000), 1)
_WINDOW_SECONDS = max(_safe_int(os.getenv("TUSHARE_PROXY_LIMIT_WINDOW_SEC", "60"), 60), 1)
_SLEEP_SECONDS = max(_safe_float(os.getenv("TUSHARE_PROXY_LIMIT_SLEEP", "0.1"), 0.1), 0.01)
_MAX_INFLIGHT = max(_safe_int(os.getenv("TUSHARE_PROXY_MAX_INFLIGHT", "2"), 2), 1)
_RATE_RETRY = max(_safe_int(os.getenv("TUSHARE_PROXY_RATE_RETRY", "3"), 3), 0)
_RATE_RETRY_BACKOFF = max(_safe_float(os.getenv("TUSHARE_PROXY_RATE_RETRY_BACKOFF", "0.8"), 0.8), 0.1)
_INFLIGHT_SEM = threading.Semaphore(_MAX_INFLIGHT)


def throttle_tushare_proxy_request(api_name: str = "", logger: Optional[object] = None) -> None:
    """
    全局节流（滑动时间窗）：
    - 在 _WINDOW_SECONDS 秒内最多 _LIMIT_COUNT 次请求
    - 超出后在下一次请求前 sleep _SLEEP_SECONDS 秒
    """
    while True:
        sleep_for = 0.0
        with _LOCK:
            now = time.time()
            cutoff = now - _WINDOW_SECONDS
            while _REQUEST_TIMESTAMPS and _REQUEST_TIMESTAMPS[0] < cutoff:
                _REQUEST_TIMESTAMPS.popleft()

            if len(_REQUEST_TIMESTAMPS) < _LIMIT_COUNT:
                _REQUEST_TIMESTAMPS.append(now)
                return
            oldest = _REQUEST_TIMESTAMPS[0] if _REQUEST_TIMESTAMPS else now
            # 精准等待到窗口释放（避免固定0.1s高频碰撞）
            sleep_for = max((oldest + _WINDOW_SECONDS) - now + 0.01, _SLEEP_SECONDS)

        if sleep_for > 0:
            msg = (
                f"[Tushare代理限流] {_WINDOW_SECONDS}s内请求已达{_LIMIT_COUNT}次，"
                f"请求 {api_name or 'unknown'} 前暂停{round(sleep_for, 3)}s"
            )
            if logger and hasattr(logger, "warning"):
                logger.warning(msg)
            else:
                print(msg, flush=True)
            time.sleep(sleep_for)


def call_tushare_with_timeout(
    api_callable,
    timeout_sec: int = 60,
    api_name: str = "",
    logger: Optional[object] = None,
):
    """
    对单次Tushare请求执行：
    1) 限流节流
    2) 单次调用超时控制（默认60秒）
    """
    attempt = 0
    while True:
        throttle_tushare_proxy_request(api_name=api_name, logger=logger)
        executor = ThreadPoolExecutor(max_workers=1)
        _INFLIGHT_SEM.acquire()
        future = executor.submit(api_callable)
        try:
            return future.result(timeout=max(int(timeout_sec), 1))
        except FuturesTimeoutError as exc:
            future.cancel()
            msg = f"[Tushare单次超时] {api_name or 'unknown'} 超时（>{timeout_sec}s）"
            if logger and hasattr(logger, "warning"):
                logger.warning(msg)
            else:
                print(msg, flush=True)
            raise TimeoutError(msg) from exc
        except Exception as exc:
            text = str(exc).lower()
            is_rate_limited = (
                "429" in text
                or "rate" in text
                or "limit" in text
                or "too many requests" in text
            )
            if is_rate_limited and attempt < _RATE_RETRY:
                wait_sec = _RATE_RETRY_BACKOFF * (attempt + 1)
                msg = (
                    f"[Tushare限流重试] {api_name or 'unknown'} "
                    f"第{attempt + 1}次重试，等待{round(wait_sec, 2)}s"
                )
                if logger and hasattr(logger, "warning"):
                    logger.warning(msg)
                else:
                    print(msg, flush=True)
                time.sleep(wait_sec)
                attempt += 1
                continue
            raise
        finally:
            _INFLIGHT_SEM.release()
            executor.shutdown(wait=False, cancel_futures=True)
