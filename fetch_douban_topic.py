#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from bs4 import BeautifulSoup

URL = "https://www.douban.com/topic/487231175/?_spm_id=MTY5MDMwOTUy&_dtcc=1"

def parse_cookie_str(cookie_str: str) -> dict:
    cookies = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def main():
    cookie_str = os.getenv("DOUBAN_COOKIE", "").strip()
    if not cookie_str:
        raise SystemExit("请先设置环境变量 DOUBAN_COOKIE")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.douban.com/",
    }

    resp = requests.get(
        URL,
        headers=headers,
        cookies=parse_cookie_str(cookie_str),
        timeout=20,
    )
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding

    soup = BeautifulSoup(resp.text, "html.parser")

    title = soup.select_one("h1")
    content = (
        soup.select_one(".topic-content")
        or soup.select_one(".rich-content")
        or soup.select_one(".topic-doc")
    )

    print("标题：", title.get_text(strip=True) if title else "未找到")
    if content:
        text = content.get_text("\n", strip=True)
        print("\n正文预览：\n")
        print(text[:2000])
    else:
        print("未定位到正文节点，可能页面结构变化或未登录成功。")
        with open("douban_page_debug.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print("已保存 HTML 到 douban_page_debug.html")

if __name__ == "__main__":
    main()
