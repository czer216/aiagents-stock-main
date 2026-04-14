"""
Tushare代理接口限流保护
默认1分钟内请求50次后，下一次请求前sleep 20秒。
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


_LIMIT_COUNT = max(_safe_int(os.getenv("TUSHARE_PROXY_LIMIT_COUNT", "50"), 50), 1)
_WINDOW_SECONDS = max(_safe_int(os.getenv("TUSHARE_PROXY_LIMIT_WINDOW_SEC", "60"), 60), 1)
_SLEEP_SECONDS = max(_safe_int(os.getenv("TUSHARE_PROXY_LIMIT_SLEEP", "20"), 20), 1)


def throttle_tushare_proxy_request(api_name: str = "", logger: Optional[object] = None) -> None:
    """
    全局节流（滑动时间窗）：
    - 在 _WINDOW_SECONDS 秒内最多 _LIMIT_COUNT 次请求
    - 超出后在下一次请求前 sleep _SLEEP_SECONDS 秒
    """
    while True:
        need_sleep = False
        with _LOCK:
            now = time.time()
            cutoff = now - _WINDOW_SECONDS
            while _REQUEST_TIMESTAMPS and _REQUEST_TIMESTAMPS[0] < cutoff:
                _REQUEST_TIMESTAMPS.popleft()

            if len(_REQUEST_TIMESTAMPS) < _LIMIT_COUNT:
                _REQUEST_TIMESTAMPS.append(now)
                return
            need_sleep = True

        if need_sleep:
            msg = (
                f"[Tushare代理限流] {_WINDOW_SECONDS}s内请求已达{_LIMIT_COUNT}次，"
                f"请求 {api_name or 'unknown'} 前暂停{_SLEEP_SECONDS}s"
            )
            if logger and hasattr(logger, "warning"):
                logger.warning(msg)
            else:
                print(msg)
            time.sleep(_SLEEP_SECONDS)


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
    throttle_tushare_proxy_request(api_name=api_name, logger=logger)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(api_callable)
    try:
        return future.result(timeout=max(int(timeout_sec), 1))
    except FuturesTimeoutError as exc:
        future.cancel()
        msg = f"[Tushare单次超时] {api_name or 'unknown'} 超时（>{timeout_sec}s）"
        if logger and hasattr(logger, "warning"):
            logger.warning(msg)
        else:
            print(msg)
        raise TimeoutError(msg) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
