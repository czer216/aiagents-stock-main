"""
数据获取基础封装
提供统一的 HTTP 请求、错误处理、重试机制
"""
import requests
import logging
import time
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseDataFetcher(ABC):
    """数据获取器基类"""

    def __init__(self, timeout: int = 10, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def safe_request(
        self,
        url: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        fallback: Optional[Any] = None,
    ) -> Dict:
        """
        安全的 HTTP 请求，带重试和降级

        Args:
            url: 请求 URL
            method: 请求方法
            params: URL 参数
            data: 请求体数据
            headers: 请求头
            fallback: 失败时的降级返回值

        Returns:
            响应数据字典
        """
        for attempt in range(self.max_retries):
            try:
                if method.upper() == "GET":
                    response = self.session.get(
                        url, params=params, headers=headers, timeout=self.timeout
                    )
                elif method.upper() == "POST":
                    response = self.session.post(
                        url, params=params, json=data, headers=headers, timeout=self.timeout
                    )
                else:
                    raise ValueError(f"不支持的请求方法: {method}")

                response.raise_for_status()
                result = response.json()
                logger.info(f"✅ 请求成功: {url}")
                return result

            except requests.exceptions.Timeout:
                logger.warning(f"⏱️ 请求超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
                if attempt < self.max_retries - 1:
                    time.sleep(1 * (attempt + 1))  # 指数退避
                continue

            except requests.exceptions.RequestException as e:
                logger.error(f"❌ 请求失败 (尝试 {attempt + 1}/{self.max_retries}): {url}, 错误: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1 * (attempt + 1))
                continue

            except Exception as e:
                logger.error(f"❌ 未知错误: {e}")
                break

        # 所有重试都失败
        logger.error(f"❌ 请求最终失败: {url}")
        if fallback is not None:
            return fallback
        return {"error": "请求失败", "url": url}

    def batch_fetch(
        self,
        urls: List[str],
        method: str = "GET",
        max_workers: int = 5,
        fallback: Optional[Any] = None,
    ) -> List[Dict]:
        """
        批量获取数据（并发）

        Args:
            urls: URL 列表
            method: 请求方法
            max_workers: 最大并发数
            fallback: 失败时的降级返回值

        Returns:
            结果列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {
                executor.submit(self.safe_request, url, method, fallback=fallback): url
                for url in urls
            }

            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"❌ 批量请求失败: {url}, 错误: {e}")
                    if fallback is not None:
                        results.append(fallback)

        return results

    @abstractmethod
    def fetch_data(self, **kwargs) -> Dict:
        """子类实现具体的数据获取逻辑"""
        pass
