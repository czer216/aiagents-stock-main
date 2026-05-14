from datetime import datetime
import time

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

import config
from infrastructure.db.auth_db import auth_db
from ui.components import render_top_nav


AUTH_SESSION_KEYS = [
    "auth_logged_in",
    "auth_user_id",
    "auth_username",
    "auth_role",
    "auth_login_at",
    "auth_session_token",
    "auth_cookie_needs_sync",
    "auth_cookie_restore_retry",
]

AUTH_LOGOUT_SKIP_RESTORE_KEY = "auth_skip_cookie_restore_once"


def get_cookie_store() -> EncryptedCookieManager:
    return EncryptedCookieManager(prefix="stockapp_auth", password=str(config.DEEPSEEK_API_KEY or "stockapp-fallback-secret"))


def cookies_ready_or_none(max_wait_sec: float = 1.5, step_sec: float = 0.1):
    try:
        cookies = get_cookie_store()
    except Exception:
        return None

    deadline = time.time() + max(0.0, float(max_wait_sec or 0.0))
    while time.time() <= deadline:
        try:
            if cookies.ready():
                return cookies
        except Exception:
            return None
        time.sleep(max(0.01, float(step_sec or 0.1)))
    return None


def auth_logged_in() -> bool:
    return bool(st.session_state.get("auth_logged_in", False))


def auth_role() -> str:
    return str(st.session_state.get("auth_role", "") or "")


def require_admin() -> bool:
    return auth_role() == "admin"


def auth_set_user(user: dict):
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_user_id"] = int(user.get("id") or 0)
    st.session_state["auth_username"] = str(user.get("username") or "")
    st.session_state["auth_role"] = str(user.get("role") or "user")
    st.session_state["auth_login_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def auth_logout():
    # 避免退出后本轮立刻走cookie恢复并触发反复重跑
    st.session_state[AUTH_LOGOUT_SKIP_RESTORE_KEY] = True

    token = str(st.session_state.get("auth_session_token", "") or "")
    if token:
        try:
            auth_db.revoke_session(token)
        except Exception:
            pass

    cookies = cookies_ready_or_none(max_wait_sec=0.3, step_sec=0.05)
    if cookies is not None:
        try:
            if cookies.get("session_token"):
                del cookies["session_token"]
                cookies.save()
        except Exception:
            pass

    for key in AUTH_SESSION_KEYS:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()


def ensure_cookie_session_persisted() -> None:
    token = str(st.session_state.get("auth_session_token", "") or "").strip()
    if not token:
        return
    if not bool(st.session_state.get("auth_cookie_needs_sync", False)):
        return

    cookies = cookies_ready_or_none(max_wait_sec=2.0)
    if cookies is None:
        return

    try:
        current = str(cookies.get("session_token", "") or "").strip()
        if current != token:
            cookies["session_token"] = token
            cookies.save()
        st.session_state["auth_cookie_needs_sync"] = False
    except Exception:
        pass


def restore_auth_from_cookie_token() -> bool:
    if bool(st.session_state.pop(AUTH_LOGOUT_SKIP_RESTORE_KEY, False)):
        st.session_state.pop("auth_cookie_restore_retry", None)
        return False

    if auth_logged_in():
        st.session_state.pop("auth_cookie_restore_retry", None)
        return True

    cookies = cookies_ready_or_none(max_wait_sec=2.0)
    if cookies is None:
        retried = bool(st.session_state.get("auth_cookie_restore_retry", False))
        if not retried:
            st.session_state["auth_cookie_restore_retry"] = True
            st.info("正在恢复登录会话，请稍候...")
            time.sleep(0.2)
            st.rerun()
        st.session_state.pop("auth_cookie_restore_retry", None)
        return False

    st.session_state.pop("auth_cookie_restore_retry", None)
    token = str(cookies.get("session_token", "") or "")
    if not token:
        return False

    user = auth_db.get_user_by_session(token)
    if not user:
        try:
            del cookies["session_token"]
            cookies.save()
        except Exception:
            pass
        return False

    auth_set_user(user)
    st.session_state["auth_session_token"] = token
    return True


def render_bootstrap_admin_page():
    # 注入初始化页面专属样式
    st.markdown(
        """
        <style>
        .main {
            background: linear-gradient(135deg, #e8f5f3 0%, #d4ebe8 50%, #e0f2f1 100%) !important;
            background-attachment: fixed;
        }
        .stApp {
            background: transparent !important;
        }
        .block-container {
            padding-top: 3rem;
            max-width: 480px;
            margin: 0 auto;
        }
        .bootstrap-header {
            text-align: center;
            margin-bottom: 2rem;
        }
        .bootstrap-logo {
            width: 80px;
            height: 80px;
            margin: 0 auto 1rem;
            background: white;
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2.5rem;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }
        .bootstrap-title {
            font-size: 2.2rem;
            font-weight: 700;
            color: #00897b;
            margin-bottom: 0.5rem;
        }
        .bootstrap-subtitle {
            font-size: 1rem;
            color: #546e7a;
            font-weight: 400;
        }
        .bootstrap-card {
            background: white;
            padding: 2.5rem 2rem;
            border-radius: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
        }
        .bootstrap-card h3 {
            text-align: center;
            font-size: 1.5rem;
            font-weight: 600;
            color: #263238;
            margin-bottom: 0.5rem;
        }
        .bootstrap-card p {
            text-align: center;
            color: #78909c;
            font-size: 0.9rem;
            margin-bottom: 2rem;
        }
        .stTextInput > label {
            font-weight: 500;
            color: #37474f;
            font-size: 0.95rem;
        }
        .stTextInput > div > div > input {
            border-radius: 12px;
            border: 1.5px solid #e0e0e0;
            padding: 0.85rem 1rem;
            font-size: 0.95rem;
            transition: all 0.3s ease;
        }
        .stTextInput > div > div > input:focus {
            border-color: #00897b;
            box-shadow: 0 0 0 3px rgba(0, 137, 123, 0.1);
        }
        .bootstrap-card .stButton > button {
            width: 100%;
            background: linear-gradient(135deg, #00897b 0%, #00695c 100%);
            color: white;
            border: none;
            border-radius: 12px;
            padding: 0.85rem 1.5rem;
            font-weight: 600;
            font-size: 1rem;
            margin-top: 1.5rem;
            transition: all 0.3s ease;
            box-shadow: 0 4px 12px rgba(0, 137, 123, 0.3);
        }
        .bootstrap-card .stButton > button:hover {
            background: linear-gradient(135deg, #00796b 0%, #004d40 100%);
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 137, 123, 0.4);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 初始化页面布局
    st.markdown(
        """
        <div class="bootstrap-header">
            <div class="bootstrap-logo">🔐</div>
            <div class="bootstrap-title">AI Stock Analysis</div>
            <div class="bootstrap-subtitle">智能股票分析系统</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="bootstrap-card">', unsafe_allow_html=True)
    st.markdown("<h3>初始化管理员</h3>", unsafe_allow_html=True)
    st.markdown("<p>首次启动请先创建管理员账号</p>", unsafe_allow_html=True)

    with st.form("bootstrap_admin_form"):
        username = st.text_input("管理员用户名", key="bootstrap_admin_username", placeholder="请输入管理员用户名")
        password = st.text_input("管理员密码", type="password", key="bootstrap_admin_password", placeholder="至少6位密码")
        confirm = st.text_input("确认密码", type="password", key="bootstrap_admin_confirm", placeholder="再次输入密码")
        submitted = st.form_submit_button("🚀 创建管理员", type="primary")

    st.markdown('</div>', unsafe_allow_html=True)

    if not submitted:
        return

    if not username.strip():
        st.error("❌ 请输入管理员用户名")
        return
    if len(password) < 6:
        st.error("❌ 密码至少6位")
        return
    if password != confirm:
        st.error("❌ 两次密码不一致")
        return

    try:
        auth_db.create_user(username=username.strip(), password=password, role="admin")
        st.success("✅ 管理员创建成功，请登录")
        time.sleep(0.8)
        st.rerun()
    except Exception as e:
        st.error(f"❌ 创建管理员失败: {str(e)}")


def render_login_page():
    # 注入登录页面专属样式
    st.markdown(
        """
        <style>
        .main {
            background: linear-gradient(135deg, #e8f5f3 0%, #d4ebe8 50%, #e0f2f1 100%) !important;
            background-attachment: fixed;
        }
        .stApp {
            background: transparent !important;
        }
        .block-container {
            padding-top: 2rem;
            max-width: 1100px;
            margin: 0 auto;
            background: transparent;
        }
        .login-shell {
            max-width: 520px;
            margin: 0 auto;
        }
        .login-header {
            text-align: center;
            margin-bottom: 1.4rem;
        }
        .login-logo {
            width: 70px;
            height: 70px;
            margin: 0 auto 1.5rem;
            background: white;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2rem;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        }
        .login-title {
            font-size: 2.5rem;
            font-weight: 700;
            color: #00897b;
            margin-bottom: 0.8rem;
            letter-spacing: -0.5px;
        }
        .login-subtitle {
            font-size: 1.05rem;
            color: #546e7a;
            font-weight: 400;
        }
        div[data-testid="stForm"] {
            background: rgba(255, 255, 255, 0.92);
            padding: 2rem 1.5rem 1.4rem;
            border-radius: 18px;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08);
            border: 1px solid rgba(0, 137, 123, 0.14);
            backdrop-filter: blur(2px);
            max-width: 520px;
            margin: 0 auto;
        }
        .stTextInput > label {
            font-weight: 500;
            color: #202124;
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
        }
        .stTextInput > div > div > input {
            border-radius: 10px;
            border: 1.5px solid #dadce0;
            padding: 0.9rem 1rem;
            font-size: 0.95rem;
            transition: all 0.2s ease;
            background: #fafafa;
        }
        .stTextInput > div > div > input:focus {
            border-color: #00897b;
            background: white;
            box-shadow: 0 0 0 3px rgba(0, 137, 123, 0.08);
        }
        div[data-testid="stForm"] .stButton > button {
            width: 100%;
            background: #00bfa5;
            color: white;
            border: none;
            border-radius: 10px;
            padding: 0.95rem 1.5rem;
            font-weight: 600;
            font-size: 1.05rem;
            margin-top: 1.5rem;
            transition: all 0.25s ease;
            box-shadow: 0 3px 10px rgba(0, 191, 165, 0.25);
        }
        div[data-testid="stForm"] .stButton > button:hover {
            background: #00a896;
            transform: translateY(-1px);
            box-shadow: 0 5px 15px rgba(0, 191, 165, 0.35);
        }
        div[data-testid="stForm"] .stButton > button:active {
            transform: translateY(0px);
        }
        /* 隐藏Streamlit默认元素 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .stDeployButton {display: none;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="login-shell">', unsafe_allow_html=True)

    # 登录页面布局
    st.markdown(
        """
        <div class="login-header">
            <div class="login-logo">🐝</div>
            <div class="login-title">AI Stock Analysis</div>
            <div class="login-subtitle">智能股票分析系统</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        username = st.text_input("用户名", key="login_username", placeholder="请输入用户名")
        password = st.text_input("密码", type="password", key="login_password", placeholder="请输入密码")
        submitted = st.form_submit_button("🔓 登录", type="primary")

    st.markdown('</div>', unsafe_allow_html=True)

    if not submitted:
        return

    user = auth_db.authenticate_user(username=username.strip(), password=password)
    if not user:
        st.error("❌ 用户名或密码错误")
        return

    auth_set_user(user)
    try:
        token = auth_db.create_session(int(user.get("id") or 0), days=7)
        st.session_state["auth_session_token"] = token
        st.session_state["auth_cookie_needs_sync"] = True
        cookies = cookies_ready_or_none()
        if cookies is not None:
            cookies["session_token"] = token
            cookies.save()
            st.session_state["auth_cookie_needs_sync"] = False
    except Exception:
        pass
    st.success(f"✅ 登录成功，欢迎 {user.get('username', '')}")
    time.sleep(0.6)
    st.rerun()
