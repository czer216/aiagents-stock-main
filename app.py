import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import json
from datetime import datetime
import time
import base64
import os
import logging
import re
import uuid
import requests
import config

from infrastructure.pdf.pdf_generator import display_pdf_export_section
from infrastructure.db.database import db
from infrastructure.notification.monitor_service import monitor_service
from infrastructure.notification.notification_service import notification_service
from services.config_manager import config_manager
from infrastructure.db.monitor_db import monitor_db
from infrastructure.db.auth_db import auth_db
from ui.theme import inject_global_styles
from ui.components import render_top_nav, render_usage_guide
from html import escape
from application.auth.auth_service import (
    auth_logged_in as svc_auth_logged_in,
    auth_role as svc_auth_role,
    require_admin as svc_require_admin,
    auth_logout as svc_auth_logout,
    ensure_cookie_session_persisted as svc_ensure_cookie_session_persisted,
    restore_auth_from_cookie_token as svc_restore_auth_from_cookie_token,
    render_bootstrap_admin_page as svc_render_bootstrap_admin_page,
    render_login_page as svc_render_login_page,
)
from application.navigation import activate_nav, is_nav_active, deactivate_nav, reset_to_home
from application.admin import (
    display_history_records as svc_display_history_records,
    render_config_manager,
    render_user_admin_page,
)
from application.analysis import (
    display_batch_analysis_results as svc_display_batch_analysis_results,
    run_batch_analysis_flow,
    run_stock_analysis_flow,
)
from modules.douban_author_strategy.douban_author_ui import display_douban_author_strategy
from modules.smart_monitor.smart_monitor_kline import SmartMonitorKline
from modules.theme_peer.theme_peer_history_db import theme_peer_history_db
from services.data_source_manager import data_source_manager


logger = logging.getLogger(__name__)


def _auth_logged_in() -> bool:
    return svc_auth_logged_in()


def _auth_role() -> str:
    return svc_auth_role()


def _require_admin() -> bool:
    return svc_require_admin()


def _auth_logout():
    return svc_auth_logout()


def _ensure_cookie_session_persisted() -> None:
    return svc_ensure_cookie_session_persisted()


def _restore_auth_from_cookie_token() -> bool:
    return svc_restore_auth_from_cookie_token()


def _display_bootstrap_admin_page():
    return svc_render_bootstrap_admin_page()


def _display_login_page():
    return svc_render_login_page()


def has_any_admin_cached() -> bool:
    try:
        return bool(auth_db.has_any_admin())
    except Exception:
        try:
            st.cache_data.clear()
            return bool(auth_db.has_any_admin())
        except Exception:
            return False


def _render_top_right_account_bar() -> None:
    username = escape(str(st.session_state.get("auth_username", "") or ""))
    role = str(_auth_role() or "user")
    role_text = "管理员" if role == "admin" else "普通用户"
    model_name = escape(str(getattr(config, "DEFAULT_MODEL_NAME", "未配置") or "未配置"))
    api_ok = check_api_key()
    api_class = "app-top-pill-ok" if api_ok else "app-top-pill-bad"
    api_text = "✅ API已连接" if api_ok else "❌ API未配置"

    st.markdown(
        f"""
        <div class="app-top-header-wrap">
            <div class="app-top-header-bar">
                <div class="app-top-header-left">
                    <div class="app-top-header-title">仪表盘</div>
                    <div class="app-top-header-subtitle">欢迎回来！这是您账户的概览。</div>
                </div>
                <div class="app-top-right-bar">
                    <span class="app-top-pill {api_class}">{api_text}</span>
                    <span class="app-top-pill app-top-pill-model">🤖 {model_name}</span>
                    <details class="app-user-dropdown">
                        <summary class="app-user-summary">👤 {username} ({role_text}) <span class="app-user-caret">▾</span></summary>
                        <div class="app-user-dropdown-panel">
                            <div class="app-user-meta">
                                <div class="app-user-name">{username}</div>
                                <div class="app-user-role">{role_text}</div>
                            </div>
                            <a class="app-user-logout" href="?logout=1">⎋ 退出登录</a>
                        </div>
                    </details>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    inject_global_styles()
    st.markdown(
        """
        <style>
        .stApp { background: #eef7f7; }
        .main .block-container { max-width: 1380px; }
        .app-top-header-wrap {
            position: fixed; top: 0; left: 21.6rem; right: 0; z-index: 1000;
            background: #ffffff; border-bottom: 1px solid #e6ecef;
            box-shadow: 0 1px 0 rgba(15, 23, 42, 0.03); height: 78px;
        }
        .app-top-header-bar {
            display: flex; align-items: center; justify-content: space-between; gap: 1rem;
            background: #ffffff; border: none; border-radius: 0; box-shadow: none;
            padding: 0.55rem 1.35rem 0.5rem; min-height: 78px;
        }
        .app-top-header-left { min-width: 0; padding-left: 0.1rem; }
        .app-top-header-title { color: #1f2937; font-size: 2.0rem; font-weight: 700; line-height: 1.08; }
        .app-top-header-subtitle {
            color: #7b8794; font-size: 0.94rem; margin-top: 0.1rem;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .block-container { padding-top: 5.55rem; padding-left: 1.35rem; padding-right: 1.35rem; }
        @media (max-width: 1200px) {
            .app-top-header-wrap { top: 0; left: 0; right: 0; height: auto; }
            .app-top-header-title { font-size: 1.28rem; }
            .app-top-header-subtitle { display: none; }
            .app-top-right-bar { gap: 0.5rem; flex-wrap: wrap; }
            .block-container { padding-top: 5.15rem; padding-left: 0.9rem; padding-right: 0.9rem; }
        }
        @media (max-width: 1100px) {
            [data-testid="stSidebar"] { min-width: 18.8rem !important; max-width: 18.8rem !important; }
            .app-top-header-wrap { left: 18.8rem; }
        }
        .top-nav {
            background: #ffffff; border: 1px solid #dde9ea; border-radius: 18px;
            box-shadow: 0 6px 20px rgba(15, 23, 42, 0.06); padding: 1.8rem 1.5rem; margin-bottom: 1.2rem;
        }
        .nav-title { color: #1f2937; letter-spacing: -0.3px; }
        .nav-subtitle { color: #5f6b7a; }
        header[data-testid="stHeader"], [data-testid="stHeader"], .stDeployButton,
        [data-testid="stAppDeployButton"], [data-testid="stHeaderActionElements"] {
            display: none !important; visibility: hidden !important; opacity: 0 !important;
            pointer-events: none !important; width: 0 !important; height: 0 !important;
            margin: 0 !important; padding: 0 !important; overflow: hidden !important;
            min-height: 0 !important; max-height: 0 !important;
        }
        .app-top-right-bar {
            display: flex; align-items: center; gap: 0.62rem; padding: 0;
            background: transparent; border: none; border-radius: 0; box-shadow: none;
            flex-wrap: nowrap; justify-content: flex-end;
        }
        .app-user-dropdown { position: relative; }
        .app-user-summary {
            list-style: none; cursor: pointer; border: none; border-radius: 999px;
            padding: 0; color: #4b5563; font-size: 0.9rem; font-weight: 600;
            background: transparent; white-space: nowrap;
        }
        .app-user-summary::-webkit-details-marker { display: none; }
        .app-user-caret { color: #9aa0a6; margin-left: 0.22rem; font-size: 0.82rem; }
        .app-user-dropdown-panel {
            position: absolute; right: 0; top: calc(100% + 8px); width: 220px;
            background: #fff; border: 1px solid #e6eaef; border-radius: 12px;
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.15); overflow: hidden; z-index: 1001;
        }
        .app-user-meta { padding: 0.85rem 0.95rem 0.72rem; border-bottom: 1px solid #eef2f7; }
        .app-user-name { color: #2b3137; font-weight: 700; font-size: 1.02rem; line-height: 1.2; }
        .app-user-role { color: #77838f; margin-top: 0.22rem; font-size: 0.9rem; }
        .app-user-logout {
            display: block; text-decoration: none; color: #d92d20; font-weight: 700;
            font-size: 1rem; padding: 0.85rem 0.95rem; background: #fff;
        }
        .app-user-logout:hover { background: #fff1f1; }
        .app-sidebar-brand {
            display: flex; align-items: center; gap: 0.68rem; background: transparent;
            border: none; border-radius: 0; padding: 0.4rem 0.15rem 0.78rem; margin: 0.02rem 0 0.45rem 0;
            box-shadow: none;
        }
        .app-sidebar-brand-icon {
            width: 42px; height: 42px; border-radius: 12px; background: #f1f4f7;
            color: #1f2937; display: inline-flex; align-items: center; justify-content: center;
            font-size: 1rem; font-weight: 700; border: 1px solid #e2e8ee; box-shadow: none;
        }
        .app-sidebar-brand-title {
            color: #111827; font-size: 1.92rem; font-weight: 700;
            line-height: 1.04; letter-spacing: -0.25px;
        }
        .app-sidebar-brand-subtitle { color: #70808f; font-size: 0.8rem; margin-top: 0.06rem; }
        .app-top-pill {
            display: inline-flex; align-items: center; border-radius: 999px;
            padding: 0.22rem 0.58rem; font-size: 0.84rem; font-weight: 600; white-space: nowrap;
        }
        .app-top-pill-ok { background: #e8f8ef; color: #0f9b63; border: 1px solid #cfeedd; }
        .app-top-pill-bad { background: #fdecec; color: #c5221f; border: 1px solid #f7c9c8; }
        .app-top-pill-model {
            background: #edf2ff; color: #315ed8; border: 1px solid #dbe6ff;
            max-width: 420px; overflow: hidden; text-overflow: ellipsis;
        }
        .agent-card {
            border: 1px solid #dfe8ea !important; border-radius: 16px !important;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05) !important;
            background: #ffffff !important; height: 100%; min-height: 164px;
        }
        .decision-card {
            border-radius: 16px !important; border: 1px solid #cfe8df !important;
            background: linear-gradient(135deg, #f5fffa 0%, #ecfbf5 100%) !important;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04) !important; padding: 1rem 1.1rem;
        }
        [data-testid="stSidebar"] {
            background: #f5f7f8; border-right: 1px solid #e4eaec;
            min-width: 21.6rem !important; max-width: 21.6rem !important;
            transform: translateX(0) !important;
        }
        [data-testid="stSidebar"][aria-expanded="false"] {
            min-width: 21.6rem !important; max-width: 21.6rem !important;
            transform: translateX(0) !important; margin-left: 0 !important;
        }
        [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapseButton"],
        button[kind="header"][aria-label*="sidebar"], button[kind="header"][aria-label*="Sidebar"] {
            display: none !important; visibility: hidden !important; pointer-events: none !important;
            width: 0 !important; height: 0 !important; margin: 0 !important; padding: 0 !important; overflow: hidden !important;
        }
        [data-testid="stSidebar"] .stButton > button {
            border-radius: 12px; border: 1px solid transparent; background: transparent;
            text-align: left; padding-left: 0.8rem; color: #2f3b49; font-weight: 500;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            border-color: #d5ebe2; background: #ecf9f3; color: #0f8b63;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not has_any_admin_cached():
        _display_bootstrap_admin_page()
        return

    _restore_auth_from_cookie_token()
    _ensure_cookie_session_persisted()
    if not _auth_logged_in():
        _display_login_page()
        return

    qp_logout = str(st.query_params.get("logout", "") or "").lower()
    if qp_logout in {"1", "true", "yes"}:
        st.query_params.clear()
        _auth_logout()
        return

    _render_top_right_account_bar()

    with st.sidebar:
        st.markdown(
            """
            <div class="app-sidebar-brand">
                <div class="app-sidebar-brand-icon">🐝</div>
                <div>
                    <div class="app-sidebar-brand-title">BeeCode AI</div>
                    <div class="app-sidebar-brand-subtitle">智能交易决策平台</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")

        st.markdown('<p style="color: #aaa; font-size: 0.75rem; margin-bottom: 0.5rem; margin-top: 0.5rem; font-weight: 400;">策略分析</p>', unsafe_allow_html=True)
        if st.button("🐉 智瞰龙虎", width='stretch', key="nav_longhubang"):
            activate_nav("show_longhubang")
        if st.button("🧩 同题材补涨", width='stretch', key="nav_theme_peer"):
            activate_nav("show_theme_peer")
        if st.button("📝 豆瓣交易模式", width='stretch', key="nav_douban_author_strategy"):
            activate_nav("show_douban_author_strategy")
            st.rerun()
        if st.button("🔴📈 同型K线", width='stretch', key="nav_kline_similarity"):
            activate_nav("show_kline_similarity")
            st.rerun()
        if st.button("🔴📈 底部放量套利", width='stretch', key="nav_bottom_volume_arbitrage"):
            activate_nav("show_bottom_volume_arbitrage")
            st.rerun()
        if st.button("🟠📈 强势回踩再启动", width='stretch', key="nav_strong_pullback_restart"):
            activate_nav("show_strong_pullback_restart")
            st.rerun()
        if st.button("🟣📈 突然底部放量", width='stretch', key="nav_bottom_volume_surge"):
            activate_nav("show_bottom_volume_surge")
            st.rerun()

        st.markdown('<p style="color: #aaa; font-size: 0.75rem; margin-top: 1.2rem; margin-bottom: 0.5rem; font-weight: 400;">投资管理</p>', unsafe_allow_html=True)
        if st.button("📊 持仓分析", width='stretch', key="nav_portfolio"):
            activate_nav("show_portfolio")
            st.rerun()
        if st.button("🤖 AI盯盘", width='stretch', key="nav_smart_monitor"):
            activate_nav("show_smart_monitor")
            st.rerun()
        if st.button("📖 历史记录", width='stretch', key="nav_history"):
            activate_nav("show_history")
            st.rerun()

        if _require_admin():
            st.markdown('<p style="color: #aaa; font-size: 0.75rem; margin-top: 1.2rem; margin-bottom: 0.5rem; font-weight: 400;">系统管理</p>', unsafe_allow_html=True)
            if st.button("⚙️ 环境配置", width='stretch', key="nav_config"):
                activate_nav("show_config")
                st.rerun()
            if st.button("👥 用户管理", width='stretch', key="nav_user_admin"):
                activate_nav("show_user_admin")
                st.rerun()

    if is_nav_active('show_history'):
        return display_history_records()

    if is_nav_active('show_longhubang'):
        from ui.longhubang import display_longhubang
        display_longhubang()
        return

    if is_nav_active('show_theme_peer'):
        from ui.theme_peer import display_theme_peer_selector
        display_theme_peer_selector()
        return

    if is_nav_active('show_douban_author_strategy'):
        display_douban_author_strategy()
        return

    if is_nav_active('show_kline_similarity'):
        _display_kline_similarity_page()
        return

    if is_nav_active('show_bottom_volume_arbitrage'):
        _display_bottom_volume_arbitrage_page()
        return

    if is_nav_active('show_strong_pullback_restart'):
        _display_strong_pullback_restart_page()
        return

    if is_nav_active('show_bottom_volume_surge'):
        _display_bottom_volume_surge_page()
        return

    if is_nav_active('show_smart_monitor'):
        from ui.smart_monitor import smart_monitor_ui
        smart_monitor_ui()
        return

    if is_nav_active('show_portfolio'):
        from modules.portfolio.portfolio_ui import display_portfolio_manager
        display_portfolio_manager()
        return

    if is_nav_active('show_config'):
        if not _require_admin():
            deactivate_nav('show_config')
            st.warning("当前账号无权限访问环境配置")
            return
        display_config_manager()
        return

    if is_nav_active('show_user_admin'):
        if not _require_admin():
            deactivate_nav('show_user_admin')
            st.warning("当前账号无权限访问用户管理")
            return
        display_user_admin_page()
        return

    show_example_interface()


def _render_feature_card(icon: str, title: str, desc: str, tip: str) -> None:
    st.markdown(
        f"""
        <div class="agent-card" style="height: 100%; min-height: 156px;">
            <h4 style="margin: 0 0 0.55rem 0; font-size: 1.05rem;">{icon} {title}</h4>
            <p style="margin: 0; color: #5f6368; line-height: 1.55;">{desc}</p>
            <p style="margin: 0.75rem 0 0 0; color: #9aa0a6; font-size: 0.86rem;">{tip}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _bv_fetch_tdx_quote_raw(code: str):
    try:
        base_url = str(config.TDX_CONFIG.get('base_url') or '').strip().rstrip('/')
        if not base_url:
            return None
        resp = requests.get(f"{base_url}/api/quote", params={'code': code}, timeout=8)
        payload = resp.json() if resp is not None else {}
        if (payload or {}).get('code') != 0:
            return None
        data = payload.get('data') or []
        return data[0] if data else None
    except Exception:
        return None


def _bv_fetch_tdx_minute_raw(code: str):
    try:
        base_url = str(config.TDX_CONFIG.get('base_url') or '').strip().rstrip('/')
        if not base_url:
            return []
        resp = requests.get(f"{base_url}/api/minute", params={'code': code}, timeout=8)
        payload = resp.json() if resp is not None else {}
        if (payload or {}).get('code') != 0:
            return []
        data = payload.get('data') or {}
        return data.get('List') or []
    except Exception:
        return []


def _bv_fetch_tdx_trade_raw(code: str):
    try:
        base_url = str(config.TDX_CONFIG.get('base_url') or '').strip().rstrip('/')
        if not base_url:
            return []
        resp = requests.get(f"{base_url}/api/trade", params={'code': code}, timeout=8)
        payload = resp.json() if resp is not None else {}
        if (payload or {}).get('code') != 0:
            return []
        data = payload.get('data') or {}
        return data.get('List') or []
    except Exception:
        return []


def _bv_get_kline_data(code: str, days: int = 60):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - pd.Timedelta(days=max(int(days), 60) + 30)).strftime('%Y%m%d')
    hist = data_source_manager.get_stock_hist_data(symbol=str(code), start_date=start_date, end_date=end_date, adjust='qfq')
    if hist is None or isinstance(hist, dict) or getattr(hist, 'empty', True):
        logger.warning(
            "KLINE_MISS app._bv_get_kline_data code=%s start=%s end=%s hist_type=%s empty=%s",
            str(code),
            start_date,
            end_date,
            type(hist).__name__,
            bool(getattr(hist, 'empty', True)) if hist is not None else True,
        )
        return None
    work = hist.copy().tail(int(days))
    df = pd.DataFrame(
        {
            '日期': pd.to_datetime(work.get('date'), errors='coerce'),
            '开盘': pd.to_numeric(work.get('open'), errors='coerce'),
            '最高': pd.to_numeric(work.get('high'), errors='coerce'),
            '最低': pd.to_numeric(work.get('low'), errors='coerce'),
            '收盘': pd.to_numeric(work.get('close'), errors='coerce'),
            '成交量': pd.to_numeric(work.get('volume'), errors='coerce'),
        }
    )
    df = df.dropna(subset=['日期', '开盘', '最高', '最低', '收盘'])
    if df.empty:
        logger.warning(
            "KLINE_EMPTY_AFTER_CLEAN app._bv_get_kline_data code=%s start=%s end=%s rows_before=%s",
            str(code),
            start_date,
            end_date,
            int(len(work.index)) if hasattr(work, 'index') else 0,
        )
        return None
    if bool((df['日期'].dt.strftime('%Y%m%d') == end_date).any()):
        return df
    raw = _bv_fetch_tdx_quote_raw(code)
    if raw:
        try:
            k = raw.get('K') or {}
            price = float(k.get('Close') or 0) / 1000
            pre_close = float(k.get('Last') or 0) / 1000
            if price > 0 and pre_close > 0:
                open_price = float(k.get('Open') or price * 1000) / 1000
                high_price = float(k.get('High') or price * 1000) / 1000
                low_price = float(k.get('Low') or price * 1000) / 1000
                today_row = pd.DataFrame(
                    {
                        '日期': [pd.to_datetime(end_date, format='%Y%m%d')],
                        '开盘': [open_price],
                        '最高': [max(high_price, price, open_price)],
                        '最低': [min(low_price, price, open_price)],
                        '收盘': [price],
                        '成交量': [float(raw.get('TotalHand') or 0)],
                    }
                )
                mask = df['日期'].dt.strftime('%Y%m%d') == end_date
                if bool(mask.any()):
                    idx = int(df.index[mask][-1])
                    for col in ['开盘', '最高', '最低', '收盘', '成交量']:
                        df.at[idx, col] = today_row.iloc[0][col]
                else:
                    df = pd.concat([df, today_row], ignore_index=True)
                df = df.sort_values('日期').reset_index(drop=True)
        except Exception:
            pass
    return df


def _render_bottom_volume_stock_detail(code: str, name: str):
    raw = _bv_fetch_tdx_quote_raw(code)
    if raw:
        k = raw.get('K') or {}
        price = float(k.get('Close') or 0) / 1000
        pre_close = float(k.get('Last') or 0) / 1000
        chg_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("最新价", f"{price:.3f}")
        m2.metric("涨跌幅", f"{chg_pct:.2f}%")
        m3.metric("成交量(手)", f"{int(raw.get('TotalHand') or 0)}")
        m4.metric("成交额(万元)", f"{float(raw.get('Amount') or 0)/100000:.0f}")

    tabs = st.tabs(["五档行情", "K线图", "分时图", "分时成交"])
    with tabs[0]:
        if not raw:
            st.info("暂无五档行情数据")
        else:
            c1, c2 = st.columns(2)
            buy = raw.get('BuyLevel') or []
            sell = raw.get('SellLevel') or []
            with c1:
                buy_rows = [
                    {'档位': f"买{i + 1}", '价格': round(float((lv or {}).get('Price') or 0) / 1000, 3), '数量(手)': int(float((lv or {}).get('Number') or 0) / 100)}
                    for i, lv in enumerate(buy[:5])
                ]
                st.dataframe(pd.DataFrame(buy_rows), use_container_width=True, height=220)
            with c2:
                sell_rows = [
                    {'档位': f"卖{i + 1}", '价格': round(float((lv or {}).get('Price') or 0) / 1000, 3), '数量(手)': int(float((lv or {}).get('Number') or 0) / 100)}
                    for i, lv in enumerate(sell[:5])
                ]
                st.dataframe(pd.DataFrame(sell_rows), use_container_width=True, height=220)

    with tabs[1]:
        kline_data = _bv_get_kline_data(code=code, days=60)
        if kline_data is None or getattr(kline_data, 'empty', True):
            st.warning("暂无K线数据")
        else:
            fig = SmartMonitorKline().create_kline_with_decisions(
                stock_code=code,
                stock_name=name,
                kline_data=kline_data,
                ai_decisions=[],
                show_volume=True,
                show_ma=True,
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True, config={'responsive': True})

    with tabs[2]:
        rows = _bv_fetch_tdx_minute_raw(code)
        if not rows:
            st.info("暂无分时图数据")
        else:
            df = pd.DataFrame(
                {
                    'time': [str(r.get('Time') or '') for r in rows],
                    'price': [float(r.get('Price') or 0) / 1000 for r in rows],
                    'avg': [float(r.get('AvgPrice') or 0) / 1000 for r in rows],
                }
            )
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df['time'], y=df['price'], mode='lines', name='分时价', line=dict(width=1.6)))
            if df['avg'].sum() > 0:
                fig.add_trace(go.Scatter(x=df['time'], y=df['avg'], mode='lines', name='均价', line=dict(width=1.2, dash='dot')))
            fig.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=10), xaxis_title='时间', yaxis_title='价格')
            st.plotly_chart(fig, use_container_width=True, config={'responsive': True})

    with tabs[3]:
        rows = _bv_fetch_tdx_trade_raw(code)
        if not rows:
            st.info("暂无分时成交数据")
        else:
            df = pd.DataFrame(
                {
                    '时间': [str(r.get('Time') or '') for r in rows],
                    '价格': [float(r.get('Price') or 0) / 1000 for r in rows],
                    '成交量(手)': [float(r.get('Volume') or 0) for r in rows],
                    '买卖方向': [str(r.get('BuyOrSell') or '') for r in rows],
                }
            )
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df['时间'], y=df['成交量(手)'], name='成交量'))
            fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10), xaxis_title='时间', yaxis_title='成交量(手)')
            st.plotly_chart(fig, use_container_width=True, config={'responsive': True})
            st.dataframe(df.tail(80), use_container_width=True, height=240)


def _display_kline_similarity_page() -> None:
    from modules.theme_peer.theme_peer_selector import ThemePeerSelector

    render_top_nav("🔴📈 同型K线", "输入一只股票和时间范围，从主板检索近期同类型K线")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        symbol = st.text_input("目标股票代码", placeholder="例如 600519", key="kline_similarity_symbol_page")
    with col2:
        min_date = datetime.strptime("2026-01-01", "%Y-%m-%d").date()
        default_end = datetime.now().date()
        start_value = st.session_state.get("kline_similarity_start_date", min_date)
        end_value = st.session_state.get("kline_similarity_end_date", default_end)
        date_range = st.date_input(
            "时间范围",
            value=(start_value, end_value),
            min_value=min_date,
            max_value=default_end,
            key="kline_similarity_date_page",
        )
    with col3:
        top_n = st.slider("返回数量", min_value=5, max_value=30, value=15, step=1, key="kline_similarity_topn_page")

    run_col1, run_col2 = st.columns([1, 1])
    with run_col1:
        run = st.button("🚀 开始检索", type="primary", width='stretch', key="kline_similarity_run_page")
    with run_col2:
        clear = st.button("🧹 清空结果", width='stretch', key="kline_similarity_clear_page")

    if clear:
        st.session_state.pop("kline_similarity_result", None)
        st.rerun()

    if run:
        if not str(symbol or "").strip():
            st.warning("请输入目标股票代码")
        elif not isinstance(date_range, tuple) or len(date_range) != 2:
            st.warning("请选择开始和结束日期")
        else:
            start_d, end_d = date_range
            st.session_state["kline_similarity_symbol"] = str(symbol).strip()
            st.session_state["kline_similarity_start_date"] = start_d
            st.session_state["kline_similarity_end_date"] = end_d
            with st.spinner("正在检索主板同类型K线，请稍候..."):
                result = ThemePeerSelector().recommend_kline_similarity(
                    target_symbol=str(symbol).strip(),
                    start_date=str(start_d),
                    end_date=str(end_d),
                    top_n=int(top_n),
                )
                st.session_state["kline_similarity_result"] = result

    result = st.session_state.get("kline_similarity_result")
    if isinstance(result, dict):
        if not result.get("success"):
            st.warning(f"检索失败: {result.get('error', '未知错误')}")
            return
        rows = list(result.get("candidates", []) or [])
        if not rows:
            st.info("未找到候选")
            return

        table_rows = []
        for i, r in enumerate(rows):
            item = dict(r)
            code = str(item.get('symbol') or item.get('code') or '').strip()
            name = str(item.get('name') or '')
            item['股票'] = f"{code} {name}".strip()
            item['_idx'] = i
            table_rows.append(item)

        show_cols = ['股票'] + [
            c for c in [
                'similarity_score',
                'corr_score',
                'euclid_score',
                'path_score',
                'trend_stage',
                'change_pct',
                'latest_change_pct',
                'vol_ratio',
            ] if c in table_rows[0]
        ]
        table_df = pd.DataFrame(table_rows).reindex(columns=show_cols)

        left, right = st.columns([1.15, 1.85])
        with left:
            st.caption("点击表格行查看右侧详情")
            event = st.dataframe(
                table_df,
                use_container_width=True,
                height=520,
                on_select='rerun',
                selection_mode='single-row',
                key='kline_similarity_table_select',
            )

        selected_idx = 0
        selected_rows = (((event or {}).get('selection') or {}).get('rows') or []) if isinstance(event, dict) else []
        if selected_rows:
            selected_idx = int(selected_rows[0])
        selected_idx = max(0, min(selected_idx, len(table_rows) - 1))
        selected_row = table_rows[selected_idx]
        code = str(selected_row.get('symbol') or selected_row.get('code') or '').strip()
        name = str(selected_row.get('name') or '')

        with right:
            st.markdown(f"**{str(selected_row.get('股票') or f'{code} {name}').strip()}**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("相似分", f"{float(selected_row.get('similarity_score') or 0):.4f}")
            c2.metric("相关分", f"{float(selected_row.get('corr_score') or 0):.4f}")
            c3.metric("路径分", f"{float(selected_row.get('path_score') or 0):.4f}")
            c4.metric("20日涨幅", f"{float(selected_row.get('change_pct') or 0):.2f}%")
            _render_bottom_volume_stock_detail(code=code, name=name)


def _display_bottom_volume_surge_page() -> None:
    from modules.theme_peer.theme_peer_selector import ThemePeerSelector

    render_top_nav("🟣📈 突然底部放量", "仅筛选当日突然出现底部放量阳线的主板标的")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        end_date = st.date_input("结束日期", key="bottom_volume_surge_end_page")
    with col2:
        max_candidates = st.number_input("扫描上限(0=全主板)", min_value=0, max_value=10000, value=0, step=100, key="bottom_volume_surge_max_candidates_page")
    with col3:
        top_n = st.slider("返回数量", min_value=5, max_value=50, value=20, step=1, key="bottom_volume_surge_topn_page")

    p1, p2, p3, p4, p5 = st.columns(5)
    with p1:
        surge_min_vol_multiple = st.number_input("放量倍数阈值", min_value=1.2, max_value=5.0, value=2.0, step=0.1, key="bottom_volume_surge_param_vol")
    with p2:
        surge_min_body_pct = st.number_input("最小涨幅%", min_value=1.0, max_value=12.0, value=4.0, step=0.5, key="bottom_volume_surge_param_body")
    with p3:
        bottom_lookback_days = st.number_input("底部参考天数", min_value=20, max_value=180, value=60, step=5, key="bottom_volume_surge_param_lookback")
    with p4:
        recent_days_window = st.selectbox("近几天内触发", options=[1, 3, 5], index=0, key="bottom_volume_surge_param_recent_days")
    with p5:
        hotspot_weight = st.number_input("热点权重", min_value=0.0, max_value=2.0, value=0.8, step=0.1, key="bottom_volume_surge_param_hot_weight")

    run_col1, run_col2 = st.columns([1, 1])
    with run_col1:
        run = st.button("🚀 开始筛选", type="primary", width='stretch', key="bottom_volume_surge_run_page")
    with run_col2:
        clear = st.button("🧹 清空结果", width='stretch', key="bottom_volume_surge_clear_page")

    if clear:
        st.session_state.pop("bottom_volume_surge_result", None)
        st.rerun()

    if run:
        with st.spinner("正在执行突然底部放量筛选，请稍候..."):
            request_params = {
                "end_date": str(end_date),
                "top_n": int(top_n),
                "max_candidates": int(max_candidates),
                "surge_min_vol_multiple": float(surge_min_vol_multiple),
                "surge_min_body_pct": float(surge_min_body_pct),
                "bottom_lookback_days": int(bottom_lookback_days),
                "recent_days_window": int(recent_days_window),
                "hotspot_weight": float(hotspot_weight),
            }
            result = ThemePeerSelector().recommend_bottom_volume_surge(**request_params)
            st.session_state["bottom_volume_surge_result"] = result

    result = st.session_state.get("bottom_volume_surge_result")
    if isinstance(result, dict):
        params = dict(result.get("params") or {})
        rs = str(params.get("recent_start_trade_date") or "")
        re = str(params.get("recent_end_trade_date") or "")
        if rs and re:
            rs_show = f"{rs[:4]}-{rs[4:6]}-{rs[6:8]}" if len(rs) == 8 else rs
            re_show = f"{re[:4]}-{re[4:6]}-{re[6:8]}" if len(re) == 8 else re
            st.caption(f"触发窗口: {rs_show} ~ {re_show}")
        if not result.get("success"):
            st.warning(f"筛选失败: {result.get('error', '未知错误')}")
            return
        rows = list(result.get("candidates", []) or [])
        if not rows:
            st.info("当前无命中标的")
            return

        table_rows = []
        for i, r in enumerate(rows):
            item = dict(r)
            code = str(item.get('symbol') or item.get('code') or '').strip()
            name = str(item.get('name') or '')
            item['股票'] = f"{code} {name}".strip()
            item['_idx'] = i
            table_rows.append(item)

        show_cols = ['股票'] + [c for c in ['signal_score', 'surge_date', 'surge_vol_multiple', 'surge_body_pct', 'bottom_pos'] if c in table_rows[0]]
        table_df = pd.DataFrame(table_rows).reindex(columns=show_cols)

        left, right = st.columns([1.15, 1.85])
        with left:
            st.caption("点击表格行查看右侧详情")
            event = st.dataframe(
                table_df,
                use_container_width=True,
                height=520,
                on_select='rerun',
                selection_mode='single-row',
                key='bottom_volume_surge_table_select',
            )

        selected_idx = 0
        selected_rows = (((event or {}).get('selection') or {}).get('rows') or []) if isinstance(event, dict) else []
        if selected_rows:
            selected_idx = int(selected_rows[0])
        selected_idx = max(0, min(selected_idx, len(table_rows) - 1))
        selected_row = table_rows[selected_idx]
        code = str(selected_row.get('symbol') or selected_row.get('code') or '').strip()
        name = str(selected_row.get('name') or '')

        with right:
            st.markdown(f"**{str(selected_row.get('股票') or f'{code} {name}').strip()}**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("信号分", f"{float(selected_row.get('signal_score') or 0):.4f}")
            c2.metric("放量倍数", f"{float(selected_row.get('surge_vol_multiple') or 0):.3f}")
            c3.metric("当日涨幅", f"{float(selected_row.get('surge_body_pct') or 0):.2f}%")
            c4.metric("底部位", f"{float(selected_row.get('bottom_pos') or 0):.4f}")
            _render_bottom_volume_stock_detail(code=code, name=name)


def _display_strong_pullback_restart_page() -> None:
    from modules.theme_peer.theme_peer_selector import ThemePeerSelector

    render_top_nav("🟠📈 强势回踩再启动", "按强势上攻 + 缩量回踩 + 放量再启动筛选主板标的")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        end_date = st.date_input("结束日期", key="strong_pullback_restart_end_page")
    with col2:
        max_candidates = st.number_input("扫描上限(0=全主板)", min_value=0, max_value=10000, value=0, step=100, key="strong_pullback_restart_max_candidates_page")
    with col3:
        top_n = st.slider("返回数量", min_value=5, max_value=50, value=20, step=1, key="strong_pullback_restart_topn_page")

    p1, p2, p3, p4, p5, p6, p7 = st.columns(7)
    with p1:
        strong_lookback_days = st.number_input("强势观察天数", min_value=20, max_value=180, value=60, step=5, key="strong_pullback_restart_param_lookback")
    with p2:
        strong_min_gain_pct = st.number_input("强势最小涨幅%", min_value=5.0, max_value=40.0, value=15.0, step=0.5, key="strong_pullback_restart_param_gain")
    with p3:
        pullback_max_retrace = st.number_input("回撤上限", min_value=0.1, max_value=0.8, value=0.5, step=0.01, key="strong_pullback_restart_param_retrace")
    with p4:
        pullback_max_vol_ratio = st.number_input("回踩量比上限", min_value=0.3, max_value=1.2, value=0.7, step=0.05, key="strong_pullback_restart_param_pullback_vol")
    with p5:
        restart_min_gain_pct = st.number_input("再启动最小涨幅%", min_value=0.5, max_value=6.0, value=2.0, step=0.1, key="strong_pullback_restart_param_restart_gain")
    with p6:
        restart_min_vol_ratio = st.number_input("再启动放量阈值", min_value=0.8, max_value=3.0, value=1.2, step=0.05, key="strong_pullback_restart_param_restart_vol")
    with p7:
        hotspot_weight = st.number_input("热点权重", min_value=0.0, max_value=2.0, value=0.8, step=0.1, key="strong_pullback_restart_param_hot_weight")

    run_col1, run_col2 = st.columns([1, 1])
    with run_col1:
        run = st.button("🚀 开始筛选", type="primary", width='stretch', key="strong_pullback_restart_run_page")
    with run_col2:
        clear = st.button("🧹 清空结果", width='stretch', key="strong_pullback_restart_clear_page")

    if clear:
        st.session_state.pop("strong_pullback_restart_result", None)
        st.rerun()

    if run:
        with st.spinner("正在执行强势回踩再启动筛选，请稍候..."):
            request_params = {
                "end_date": str(end_date),
                "top_n": int(top_n),
                "max_candidates": int(max_candidates),
                "strong_lookback_days": int(strong_lookback_days),
                "strong_min_gain_pct": float(strong_min_gain_pct),
                "pullback_max_retrace": float(pullback_max_retrace),
                "pullback_max_vol_ratio": float(pullback_max_vol_ratio),
                "restart_min_gain_pct": float(restart_min_gain_pct),
                "restart_min_vol_ratio": float(restart_min_vol_ratio),
                "hotspot_weight": float(hotspot_weight),
            }
            result = ThemePeerSelector().recommend_strong_pullback_restart(**request_params)
            st.session_state["strong_pullback_restart_result"] = result

    result = st.session_state.get("strong_pullback_restart_result")
    if isinstance(result, dict):
        if not result.get("success"):
            st.warning(f"筛选失败: {result.get('error', '未知错误')}")
            return
        rows = list(result.get("candidates", []) or [])
        if not rows:
            st.info("当前无命中标的")
            return

        table_rows = []
        for i, r in enumerate(rows):
            item = dict(r)
            code = str(item.get('symbol') or item.get('code') or '').strip()
            name = str(item.get('name') or '')
            item['股票'] = f"{code} {name}".strip()
            item['_idx'] = i
            table_rows.append(item)

        show_cols = ['股票'] + [c for c in ['signal_score', 'strong_end_date', 'strong_gain_pct', 'pullback_retrace_ratio', 'pullback_vol_ratio', 'restart_date', 'restart_gain_pct', 'restart_vol_ratio'] if c in table_rows[0]]
        table_df = pd.DataFrame(table_rows).reindex(columns=show_cols)

        left, right = st.columns([1.15, 1.85])
        with left:
            st.caption("点击表格行查看右侧详情")
            event = st.dataframe(
                table_df,
                use_container_width=True,
                height=520,
                on_select='rerun',
                selection_mode='single-row',
                key='strong_pullback_restart_table_select',
            )

        selected_idx = 0
        selected_rows = (((event or {}).get('selection') or {}).get('rows') or []) if isinstance(event, dict) else []
        if selected_rows:
            selected_idx = int(selected_rows[0])
        selected_idx = max(0, min(selected_idx, len(table_rows) - 1))
        selected_row = table_rows[selected_idx]
        code = str(selected_row.get('symbol') or selected_row.get('code') or '').strip()
        name = str(selected_row.get('name') or '')

        with right:
            st.markdown(f"**{str(selected_row.get('股票') or f'{code} {name}').strip()}**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("信号分", f"{float(selected_row.get('signal_score') or 0):.4f}")
            c2.metric("强势涨幅", f"{float(selected_row.get('strong_gain_pct') or 0):.2f}%")
            c3.metric("回撤", f"{float(selected_row.get('pullback_retrace_ratio') or 0):.4f}")
            c4.metric("再启动量比", f"{float(selected_row.get('restart_vol_ratio') or 0):.4f}")
            _render_bottom_volume_stock_detail(code=code, name=name)


def _display_bottom_volume_arbitrage_page() -> None:
    from modules.theme_peer.theme_peer_selector import ThemePeerSelector

    render_top_nav("🔴📈 底部放量套利", "按放量阳线 + 回踩不破1/3 + 缩量回调筛选主板标的")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        end_date = st.date_input("结束日期", key="bottom_volume_end_page")
    with col2:
        max_candidates = st.number_input("扫描上限(0=全主板)", min_value=0, max_value=10000, value=0, step=100, key="bottom_volume_max_candidates_page")
    with col3:
        top_n = st.slider("返回数量", min_value=5, max_value=50, value=20, step=1, key="bottom_volume_topn_page")

    p1, p2, p3, p4, p5, p6, p7 = st.columns(7)
    with p1:
        breakout_min_vol_multiple = st.number_input("倍量阈值", min_value=1.2, max_value=4.0, value=1.8, step=0.1, key="bottom_volume_param_vol")
    with p2:
        breakout_min_body_pct = st.number_input("阳线实体%", min_value=1.0, max_value=12.0, value=6.0, step=0.5, key="bottom_volume_param_body")
    with p3:
        pullback_max_retrace = st.number_input("回撤上限", min_value=0.1, max_value=0.9, value=0.6, step=0.01, key="bottom_volume_param_retrace")
    with p4:
        pullback_max_vol_ratio = st.number_input("回踩量比上限", min_value=0.2, max_value=1.0, value=0.5, step=0.05, key="bottom_volume_param_pullback_vol")
    with p5:
        best_vol_ratio = st.number_input("最佳量比", min_value=0.2, max_value=0.6, value=0.333, step=0.01, key="bottom_volume_param_best")
    with p6:
        hotspot_weight = st.number_input("热点权重", min_value=0.0, max_value=2.0, value=0.8, step=0.1, key="bottom_volume_param_hot_weight")
    with p7:
        bottom_lookback_days = st.number_input("底部参考天数", min_value=20, max_value=180, value=60, step=5, key="bottom_volume_param_lookback_days")

    run_col1, run_col2 = st.columns([1, 1])
    with run_col1:
        run = st.button("🚀 开始筛选", type="primary", width='stretch', key="bottom_volume_run_page")
    with run_col2:
        clear = st.button("🧹 清空结果", width='stretch', key="bottom_volume_clear_page")

    if clear:
        st.session_state.pop("bottom_volume_result", None)
        st.rerun()

    if run:
        with st.spinner("正在执行底部放量套利筛选，请稍候..."):
            request_params = {
                "end_date": str(end_date),
                "top_n": int(top_n),
                "max_candidates": int(max_candidates),
                "breakout_min_vol_multiple": float(breakout_min_vol_multiple),
                "breakout_min_body_pct": float(breakout_min_body_pct),
                "pullback_max_retrace": float(pullback_max_retrace),
                "pullback_max_vol_ratio": float(pullback_max_vol_ratio),
                "best_vol_ratio": float(best_vol_ratio),
                "hotspot_weight": float(hotspot_weight),
                "bottom_lookback_days": int(bottom_lookback_days),
            }
            result = ThemePeerSelector().recommend_bottom_volume_arbitrage(**request_params)
            try:
                theme_peer_history_db.save_bottom_volume_fetch(
                    request_id=str(uuid.uuid4()),
                    request_params=request_params,
                    result=result if isinstance(result, dict) else {"success": False, "error": "invalid_result", "candidates": []},
                )
            except Exception as exc:
                st.warning(f"历史记录写入失败: {exc}")
            st.session_state["bottom_volume_result"] = result

    result = st.session_state.get("bottom_volume_result")
    if isinstance(result, dict):
        if not result.get("success"):
            st.warning(f"筛选失败: {result.get('error', '未知错误')}")
            return
        rows = list(result.get("candidates", []) or [])
        if not rows:
            st.info("当前无命中标的")
            return

        table_rows = []
        for i, r in enumerate(rows):
            item = dict(r)
            code = str(item.get('symbol') or item.get('code') or '').strip()
            name = str(item.get('name') or '')
            item['股票'] = f"{code} {name}".strip()
            item['_idx'] = i
            table_rows.append(item)

        show_cols = ['股票'] + [c for c in ['signal_score', 'breakout_date', 'breakout_vol_multiple', 'breakout_body_pct', 'pullback_retrace_ratio', 'pullback_vol_ratio', 'best_ratio_gap'] if c in table_rows[0]]
        table_df = pd.DataFrame(table_rows)
        table_df = table_df.reindex(columns=show_cols)

        left, right = st.columns([1.15, 1.85])
        with left:
            st.caption("点击表格行查看右侧详情")
            event = st.dataframe(
                table_df,
                use_container_width=True,
                height=520,
                on_select='rerun',
                selection_mode='single-row',
                key='bottom_volume_table_select',
            )

        selected_idx = 0
        selected_rows = (((event or {}).get('selection') or {}).get('rows') or []) if isinstance(event, dict) else []
        if selected_rows:
            selected_idx = int(selected_rows[0])
        selected_idx = max(0, min(selected_idx, len(table_rows) - 1))
        selected_row = table_rows[selected_idx]
        code = str(selected_row.get('symbol') or selected_row.get('code') or '').strip()
        name = str(selected_row.get('name') or '')

        with right:
            st.markdown(f"**{str(selected_row.get('股票') or f'{code} {name}').strip()}**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("信号分", f"{float(selected_row.get('signal_score') or 0):.4f}")
            c2.metric("倍量", f"{float(selected_row.get('breakout_vol_multiple') or 0):.3f}")
            c3.metric("回撤", f"{float(selected_row.get('pullback_retrace_ratio') or 0):.4f}")
            c4.metric("回踩量比", f"{float(selected_row.get('pullback_vol_ratio') or 0):.4f}")
            _render_bottom_volume_stock_detail(code=code, name=name)

    st.markdown("---")
    st.markdown("#### 获取历史记录")
    history_limit = st.slider("历史条数", min_value=10, max_value=300, value=80, step=10, key="bottom_volume_history_limit")
    history_rows = theme_peer_history_db.get_bottom_volume_fetch_history(limit=int(history_limit))
    if not history_rows:
        st.info("暂无历史记录")
        return

    history_df = pd.DataFrame(
        [
            {
                "ID": int(h.get("id") or 0),
                "时间": str(h.get("created_at") or ""),
                "结束日期": str(h.get("end_date") or ""),
                "成功": "是" if bool(h.get("success")) else "否",
                "候选数": int(h.get("result_count") or 0),
                "候选池": int(h.get("candidate_universe") or 0),
                "扫描数": int(h.get("scanned") or 0),
                "有效数": int(h.get("valid") or 0),
                "错误": str(h.get("error") or ""),
            }
            for h in history_rows
        ]
    )
    hist_event = st.dataframe(
        history_df,
        use_container_width=True,
        height=320,
        on_select='rerun',
        selection_mode='single-row',
        key='bottom_volume_history_select',
    )

    hist_selected_rows = (((hist_event or {}).get('selection') or {}).get('rows') or []) if isinstance(hist_event, dict) else []
    if hist_selected_rows:
        hist_idx = int(hist_selected_rows[0])
        hist_idx = max(0, min(hist_idx, len(history_rows) - 1))
        selected_hist = history_rows[hist_idx]
        st.caption(
            f"记录ID: {selected_hist.get('id')} | request_id: {selected_hist.get('request_id')} | latest_trade_date: {selected_hist.get('latest_trade_date')}"
        )
        with st.expander("查看该次参数快照", expanded=False):
            st.json(selected_hist.get("params") or {})
        with st.expander("查看该次候选明细", expanded=False):
            st.dataframe(pd.DataFrame(list(selected_hist.get("results") or [])), use_container_width=True, height=280)


def render_home_dashboard() -> None:
    render_top_nav("🏠 AI Stock Analysis 功能总览", "智能选股、AI盯盘、题材挖掘与投资管理的一体化工作台")

    core_cards = [
        ("🤖", "AI盯盘", "按预设策略持续跟踪个股，结合实时行情给出交易动作并支持自动通知。", "左侧入口: 投资管理 > AI盯盘"),
        ("🐉", "智瞰龙虎", "聚焦龙虎榜席位、资金与题材联动，快速识别强势主线与短线机会。", "左侧入口: 策略分析 > 智瞰龙虎"),
        ("🧩", "同题材补涨", "输入核心龙头，自动寻找同题材低位补涨候选，提高选股效率。", "左侧入口: 策略分析 > 同题材补涨"),
        ("📊", "持仓分析", "管理当前持仓、观察盈亏与风险敞口，辅助仓位与节奏决策。", "左侧入口: 投资管理 > 持仓分析"),
    ]

    tool_cards = [
        ("🔴📈", "同型K线", "按目标个股形态检索近期相似走势，辅助复盘与交易计划制定。", "左侧入口: 策略分析 > 同型K线"),
        ("🟢", "底部放量套利", "通过放量突破+回踩结构筛选标的，发现低位启动信号。", "左侧入口: 策略分析 > 底部放量套利"),
        ("🟣", "突然底部放量", "仅捕捉当日突然出现的底部放量阳线信号，快速发现异动启动。", "左侧入口: 策略分析 > 突然底部放量"),
        ("🟠", "强势回踩再启动", "聚焦强势上攻后的缩量回踩与放量再启动，挖掘二次发力机会。", "左侧入口: 策略分析 > 强势回踩再启动"),
        ("📝", "豆瓣交易模式", "持续学习作者交易模式并按策略版本输出候选，适合模式化跟踪。", "左侧入口: 策略分析 > 豆瓣交易模式"),
        ("📖", "历史记录", "集中查看历史分析、监控决策与结果复盘，形成可追溯闭环。", "左侧入口: 投资管理 > 历史记录"),
    ]

    st.markdown("#### 核心能力")
    c1, c2, c3, c4 = st.columns(4)
    for col, card in zip((c1, c2, c3, c4), core_cards):
        with col:
            _render_feature_card(*card)

    st.markdown("#### 策略工具")
    t1, t2, t3, t4 = st.columns(4)
    for col, card in zip((t1, t2, t3, t4), tool_cards):
        with col:
            _render_feature_card(*card)


def check_api_key():
    try:
        return bool(config.DEEPSEEK_API_KEY and config.DEEPSEEK_API_KEY.strip())
    except Exception:
        return False


def show_example_interface():
    return render_home_dashboard()


def display_history_records():
    return svc_display_history_records(
        db=db,
        monitor_db=monitor_db,
        monitor_service=monitor_service,
        activate_nav=activate_nav,
        deactivate_nav=deactivate_nav,
    )


def display_config_manager():
    return render_config_manager(
        require_admin=_require_admin,
        config_manager=config_manager,
        deactivate_nav=deactivate_nav,
    )


def display_user_admin_page():
    render_user_admin_page(
        auth_db=auth_db,
        require_admin=_require_admin,
        deactivate_nav=deactivate_nav,
    )


if __name__ == "__main__":
    main()
