import hashlib
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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

    @staticmethod
    def _build_headers() -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.douban.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _get_topic_page_soup(self, topic_url: str) -> BeautifulSoup:
        resp = self.session.get(
            topic_url,
            headers=self._build_headers(),
            cookies=self._cookies_from_env(),
            timeout=self.timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        return BeautifulSoup(resp.text, "html.parser")

    def fetch_topic(self, topic_url: str) -> Dict:
        soup = self._get_topic_page_soup(topic_url)

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
    def _parse_comment_time(comment_time: str) -> Optional[datetime]:
        text = str(comment_time or '').strip()
        if not text:
            return None
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass
        m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?:\s+(\d{1,2}:\d{1,2}(?::\d{1,2})?))?", text)
        if not m:
            return None
        date_part = m.group(1).replace('/', '-').replace('.', '-')
        time_part = m.group(2) or '00:00:00'
        if len(time_part.split(':')) == 2:
            time_part = f"{time_part}:00"
        try:
            return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    @staticmethod
    def _replace_start_query(topic_url: str, start: int) -> str:
        parsed = urlparse(str(topic_url or "").strip())
        query = parse_qs(parsed.query)
        query["start"] = [str(max(0, int(start or 0)))]
        new_query = urlencode(query, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    def fetch_topic_comments(
        self,
        topic_url: str,
        max_pages: int = 3,
        page_size: int = 100,
        start_page: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict]:
        comments: List[Dict] = []
        seen_keys = set()
        pages = max(1, int(max_pages or 1))
        size = max(1, int(page_size or 100))
        first_page = max(1, int(start_page or 1))
        start_dt = self._parse_comment_time(start_time) if start_time else None
        end_dt = self._parse_comment_time(end_time) if end_time else None

        for page_idx in range(pages):
            page_no = first_page + page_idx
            start = (page_no - 1) * size
            page_url = self._replace_start_query(topic_url, start)
            try:
                soup = self._get_topic_page_soup(page_url)
            except Exception as e:
                self.last_errors.append(f"{page_url} -> {str(e)}")
                break

            nodes = soup.select("li.comment-item") or soup.select("div.comment-item") or soup.select(".topic-reply")
            if not nodes:
                if page_idx == 0:
                    self.last_errors.append(f"{page_url} -> 评论节点为空(可能Cookie失效/页面结构变化)")
                break

            page_new = 0
            should_stop_early = False
            for node in nodes:
                comment_id = str(node.get("data-cid") or node.get("id") or "").strip()
                if not comment_id:
                    anchor = node.select_one("a[href*='comment']")
                    if anchor and anchor.get("href"):
                        comment_id = str(anchor.get("href")).split("#")[-1].strip()

                text_el = node.select_one(".reply-content") or node.select_one(".content") or node.select_one("p")
                comment_text = self._normalize_text(text_el.get_text("\n", strip=True) if text_el else "")
                if not comment_text:
                    continue

                user_el = node.select_one("h4 a") or node.select_one(".pubtime") or node.select_one("a")
                comment_user = self._normalize_text(user_el.get_text(strip=True) if user_el else "")

                time_el = node.select_one(".pubtime") or node.select_one(".created_at") or node.select_one("time")
                comment_time = self._normalize_text(time_el.get_text(strip=True) if time_el else "")
                comment_dt = self._parse_comment_time(comment_time)

                if end_dt and comment_dt and comment_dt > end_dt:
                    continue
                if start_dt and comment_dt and comment_dt < start_dt:
                    should_stop_early = True
                    continue

                like_el = node.select_one(".like-count") or node.select_one(".vote-count")
                like_count = 0
                if like_el:
                    m = re.search(r"\d+", like_el.get_text(" ", strip=True))
                    like_count = int(m.group(0)) if m else 0

                key_raw = f"{comment_id}|{comment_time}|{comment_text[:80]}"
                dedup_key = hashlib.md5(key_raw.encode("utf-8")).hexdigest()
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                page_new += 1

                comments.append(
                    {
                        "comment_id": comment_id or dedup_key,
                        "comment_text": comment_text,
                        "comment_time": comment_time,
                        "comment_user": comment_user,
                        "like_count": like_count,
                        "page_idx": page_idx + 1,
                        "post_url": topic_url,
                    }
                )

            if page_new == 0:
                break
            if should_stop_early:
                break

        return comments

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
