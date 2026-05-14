from typing import Iterable

import streamlit as st


NAV_FLAGS = [
    "show_history",
    "show_config",
    "show_main_force",
    "show_longhubang",
    "show_portfolio",
    "show_theme_peer",
    "show_kline_similarity",
    "show_bottom_volume_arbitrage",
    "show_smart_monitor",
    "show_user_admin",
    "show_douban_author_strategy",
]


def clear_nav_flags(exclude: Iterable[str] | None = None) -> None:
    keep = set(exclude or [])
    for key in NAV_FLAGS:
        if key in keep:
            continue
        if key in st.session_state:
            del st.session_state[key]


def activate_nav(flag: str) -> None:
    clear_nav_flags(exclude=[flag])
    st.session_state[flag] = True


def is_nav_active(flag: str) -> bool:
    return bool(st.session_state.get(flag, False))


def deactivate_nav(flag: str) -> None:
    if flag in st.session_state:
        del st.session_state[flag]


def reset_to_home() -> None:
    clear_nav_flags()
