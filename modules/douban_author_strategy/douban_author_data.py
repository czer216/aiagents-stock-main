import hashlib
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup


class DoubanAuthorDataFetcher:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.session = requests.Session()
        self.last_errors: List[str] = []

    @staticmethod
    def parse_cookie_str(cookie_str: str) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        for part in str(cookie_str or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    def _cookies_from_env(self) -> Dict[str, str]:
        cookie_str = str(os.getenv("DOUBAN_COOKIE", "") or "").strip()
        if not cookie_str:
            raise RuntimeError("缺少 DOUBAN_COOKIE 环境变量")
        return self.parse_cookie_str(cookie_str)

    def fetch_topic(self, topic_url: str) -> Dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.douban.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        cookies = self._cookies_from_env()
        resp = self.session.get(topic_url, headers=headers, cookies=cookies, timeout=self.timeout, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.select_one("h1")
        content_el = (
            soup.select_one(".topic-content")
            or soup.select_one(".rich-content")
            or soup.select_one(".topic-doc")
            or soup.select_one("#link-report")
            or soup.select_one(".note")
            or soup.select_one(".article")
            or soup.select_one(".topic-richtext")
        )

        title = title_el.get_text(strip=True) if title_el else ""
        content = content_el.get_text("\n", strip=True) if content_el else ""
        content = self._normalize_text(content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else ""

        return {
            "post_url": topic_url,
            "post_title": title,
            "post_published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "clean_content": content,
            "content_hash": content_hash,
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        t = str(text or "")
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    def fetch_topics(self, topic_urls: List[str]) -> List[Dict]:
        items: List[Dict] = []
        self.last_errors = []
        for url in topic_urls:
            try:
                data = self.fetch_topic(url)
                if data.get("clean_content"):
                    items.append(data)
                else:
                    self.last_errors.append(f"{url} -> 页面解析为空(可能Cookie失效或页面结构变化)")
            except Exception as e:
                self.last_errors.append(f"{url} -> {str(e)}")
                continue
        return items
