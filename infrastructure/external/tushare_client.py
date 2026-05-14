"""
Tushare 客户端统一初始化工具
确保所有 pro_api 请求统一走代理地址，同时 token 仍从环境变量读取。
"""

import os
from typing import Optional


DEFAULT_TUSHARE_BASE_URL = "http://8.136.22.187:8010/"


def normalize_tushare_base_url(url: str) -> str:
    """标准化 Tushare 代理地址，确保以 / 结尾。"""
    text = (url or "").strip()
    if not text:
        text = DEFAULT_TUSHARE_BASE_URL
    if not text.endswith("/"):
        text += "/"
    return text


def create_tushare_pro(
    token: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """
    创建 Tushare pro_api 客户端并注入代理地址。

    Args:
        token: Tushare token（为空则从环境变量 TUSHARE_TOKEN 读取）
        base_url: Tushare 代理地址（为空则从环境变量 TUSHARE_BASE_URL 读取）

    Returns:
        tushare.pro_api 实例；若 token 缺失则返回 None
    """
    token_value = (token or os.getenv("TUSHARE_TOKEN", "")).strip()
    if not token_value:
        return None

    proxy_url = normalize_tushare_base_url(
        base_url or os.getenv("TUSHARE_BASE_URL", DEFAULT_TUSHARE_BASE_URL)
    )

    import tushare as ts

    # 保留官方推荐用法，token 不写死在代码中
    ts.set_token(token_value)
    pro = ts.pro_api(token_value)
    pro._DataApi__http_url = proxy_url
    return pro

