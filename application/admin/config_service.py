import os
import time

import streamlit as st


def display_config_manager(*, require_admin, config_manager, deactivate_nav):
    st.subheader("⚙️ 环境配置管理")

    is_admin = require_admin()
    if not is_admin:
        st.warning("当前为普通用户，只能查看配置，不能保存或重置。")

    st.markdown(
        """
    <div class="agent-card">
        <p>在这里可以配置系统的环境变量，包括API密钥、数据源配置、量化交易配置等。</p>
        <p><strong>注意：</strong>配置修改后需要重启应用才能生效。</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    config_info = config_manager.get_config_info()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📝 基本配置", "📊 数据源配置", "🤖 量化交易配置", "📢 通知配置", "📝 豆瓣配置"])

    if "temp_config" not in st.session_state:
        st.session_state.temp_config = {key: info["value"] for key, info in config_info.items()}
    else:
        for key, info in config_info.items():
            st.session_state.temp_config.setdefault(key, info["value"])

    with tab1:
        st.markdown("### DeepSeek API配置")
        st.markdown("DeepSeek是系统的核心AI引擎，必须配置才能使用分析功能。")
        st.markdown("DeepSeek:https://api.deepseek.com/v1")
        st.markdown("硅基流动:https://api.siliconflow.cn/v1")
        st.markdown("火山引擎:https://ark.cn-beijing.volces.com/api/v3")
        st.markdown("阿里:https://dashscope.aliyuncs.com/compatible-mode/v1")

        api_key_info = config_info["DEEPSEEK_API_KEY"]
        current_api_key = st.session_state.temp_config.get("DEEPSEEK_API_KEY", "")

        new_api_key = st.text_input(
            f"🔑 {api_key_info['description']} {'*' if api_key_info['required'] else ''}",
            value=current_api_key,
            type="password",
            help="从 https://platform.deepseek.com 获取API密钥",
            key="input_deepseek_api_key",
        )
        st.session_state.temp_config["DEEPSEEK_API_KEY"] = new_api_key

        if new_api_key:
            masked_key = new_api_key[:8] + "*" * (len(new_api_key) - 12) + new_api_key[-4:] if len(new_api_key) > 12 else "***"
            st.success(f"✅ API密钥已设置: {masked_key}")
        else:
            st.warning("⚠️ 未设置API密钥，系统无法使用AI分析功能")

        st.markdown("---")

        base_url_info = config_info["DEEPSEEK_BASE_URL"]
        current_base_url = st.session_state.temp_config.get("DEEPSEEK_BASE_URL", "")

        new_base_url = st.text_input(
            f"🌐 {base_url_info['description']}",
            value=current_base_url,
            help="一般无需修改，保持默认即可",
            key="input_deepseek_base_url",
        )
        st.session_state.temp_config["DEEPSEEK_BASE_URL"] = new_base_url

        st.markdown("---")

        model_name_info = config_info["DEFAULT_MODEL_NAME"]
        current_model_name = st.session_state.temp_config.get("DEFAULT_MODEL_NAME", "deepseek-chat")

        new_model_name = st.text_input(
            f"🤖 {model_name_info['description']}",
            value=current_model_name,
            help="输入OpenAI兼容的模型名称，修改后重启生效",
            key="input_default_model_name",
        )
        st.session_state.temp_config["DEFAULT_MODEL_NAME"] = new_model_name

        if new_model_name:
            st.success(f"✅ 当前模型: **{new_model_name}**")
        else:
            st.warning("⚠️ 未设置模型名称，将使用默认值 deepseek-chat")

        st.markdown(
            """
        **常用模型名称参考：**
        - `deepseek-chat` — DeepSeek Chat（默认）
        - `deepseek-reasoner` — DeepSeek Reasoner（推理增强）
        - `qwen-plus` — 通义千问 Plus
        - `qwen-turbo` — 通义千问 Turbo
        - `gpt-4o` — OpenAI GPT-4o
        - `gpt-4o-mini` — OpenAI GPT-4o Mini

        > 💡 使用非 DeepSeek 模型时，请同时修改上方的 API地址 和 API密钥
        """
        )

        st.info("💡 如何获取DeepSeek API密钥？\n\n1. 访问 https://platform.deepseek.com\n2. 注册/登录账号\n3. 进入API密钥管理页面\n4. 创建新的API密钥\n5. 复制密钥并粘贴到上方输入框")

    with tab2:
        st.markdown("### Tushare数据接口（可选）")
        st.markdown("Tushare提供更丰富的A股财务数据，配置后可以获取更详细的财务分析。")

        tushare_info = config_info["TUSHARE_TOKEN"]
        current_tushare = st.session_state.temp_config.get("TUSHARE_TOKEN", "")

        new_tushare = st.text_input(
            f"🎫 {tushare_info['description']}",
            value=current_tushare,
            type="password",
            help="从 https://tushare.pro 获取Token",
            key="input_tushare_token",
        )
        st.session_state.temp_config["TUSHARE_TOKEN"] = new_tushare

        if new_tushare:
            st.success("✅ Tushare Token已设置")
        else:
            st.info("ℹ️ 未设置Tushare Token，系统将使用其他数据源")

        tushare_base_url_info = config_info.get("TUSHARE_BASE_URL", {"description": "Tushare代理地址", "value": "http://8.136.22.187:8010/"})
        current_tushare_base_url = st.session_state.temp_config.get("TUSHARE_BASE_URL", "http://8.136.22.187:8010/")

        new_tushare_base_url = st.text_input(
            f"🌐 {tushare_base_url_info['description']}",
            value=current_tushare_base_url,
            help="默认使用代理地址，通常无需修改",
            key="input_tushare_base_url",
        )
        st.session_state.temp_config["TUSHARE_BASE_URL"] = new_tushare_base_url

        st.info("💡 如何获取Tushare Token？\n\n1. 访问 https://tushare.pro\n2. 注册账号\n3. 进入个人中心\n4. 获取Token\n5. 复制并粘贴到上方输入框")

        st.markdown("---")
        st.markdown("### TDX本地数据源（可选）")
        st.markdown("启用后，实时监测和AI盯盘模块可优先使用本地TDX接口。")

        current_tdx_enabled = st.session_state.temp_config.get("TDX_ENABLED", "false") == "true"
        new_tdx_enabled = st.checkbox("启用TDX数据源", value=current_tdx_enabled, help="开启后优先使用TDX接口获取行情", key="input_tdx_enabled")
        st.session_state.temp_config["TDX_ENABLED"] = "true" if new_tdx_enabled else "false"

        tdx_base_url_info = config_info.get("TDX_BASE_URL", {"description": "TDX数据源地址", "value": "http://192.168.1.222:8181"})
        current_tdx_base_url = st.session_state.temp_config.get("TDX_BASE_URL", "http://192.168.1.222:8181")
        new_tdx_base_url = st.text_input(
            f"🔗 {tdx_base_url_info['description']}",
            value=current_tdx_base_url,
            disabled=not new_tdx_enabled,
            placeholder="http://localhost:8080",
            key="input_tdx_base_url",
        )
        st.session_state.temp_config["TDX_BASE_URL"] = new_tdx_base_url

        if new_tdx_enabled and new_tdx_base_url:
            st.success(f"✅ TDX已启用: {new_tdx_base_url}")
        elif new_tdx_enabled:
            st.warning("⚠️ 请填写TDX数据源地址")
        else:
            st.info("ℹ️ TDX数据源未启用")

    with tab3:
        st.markdown("### MiniQMT量化交易配置（可选）")
        st.markdown("配置后可以使用量化交易功能，自动执行交易策略。")

        current_enabled = st.session_state.temp_config.get("MINIQMT_ENABLED", "false") == "true"
        new_enabled = st.checkbox("启用MiniQMT量化交易", value=current_enabled, help="开启后可以使用量化交易功能", key="input_miniqmt_enabled")
        st.session_state.temp_config["MINIQMT_ENABLED"] = "true" if new_enabled else "false"

        col1, col2 = st.columns(2)
        with col1:
            account_id_info = config_info["MINIQMT_ACCOUNT_ID"]
            current_account_id = st.session_state.temp_config.get("MINIQMT_ACCOUNT_ID", "")
            new_account_id = st.text_input(f"🆔 {account_id_info['description']}", value=current_account_id, disabled=not new_enabled, key="input_miniqmt_account_id")
            st.session_state.temp_config["MINIQMT_ACCOUNT_ID"] = new_account_id

            host_info = config_info["MINIQMT_HOST"]
            current_host = st.session_state.temp_config.get("MINIQMT_HOST", "")
            new_host = st.text_input(f"🖥️ {host_info['description']}", value=current_host, disabled=not new_enabled, key="input_miniqmt_host")
            st.session_state.temp_config["MINIQMT_HOST"] = new_host

        with col2:
            port_info = config_info["MINIQMT_PORT"]
            current_port = st.session_state.temp_config.get("MINIQMT_PORT", "")
            new_port = st.text_input(f"🔌 {port_info['description']}", value=current_port, disabled=not new_enabled, key="input_miniqmt_port")
            st.session_state.temp_config["MINIQMT_PORT"] = new_port

        if new_enabled:
            st.success("✅ MiniQMT已启用")
        else:
            st.info("ℹ️ MiniQMT未启用")

        st.warning("⚠️ 警告：量化交易涉及真实资金操作，请谨慎配置和使用！")

    with tab4:
        st.markdown("### 通知配置")
        st.markdown("配置邮件和Webhook通知，用于实时监测和智策定时分析的提醒。")

        col_email, col_webhook = st.columns(2)

        with col_email:
            st.markdown("#### 📧 邮件通知")
            current_email_enabled = st.session_state.temp_config.get("EMAIL_ENABLED", "false") == "true"
            new_email_enabled = st.checkbox("启用邮件通知", value=current_email_enabled, help="开启后可以接收邮件提醒", key="input_email_enabled")
            st.session_state.temp_config["EMAIL_ENABLED"] = "true" if new_email_enabled else "false"

            current_smtp_server = st.session_state.temp_config.get("SMTP_SERVER", "")
            new_smtp_server = st.text_input("📮 SMTP服务器地址", value=current_smtp_server, disabled=not new_email_enabled, placeholder="smtp.qq.com", key="input_smtp_server")
            st.session_state.temp_config["SMTP_SERVER"] = new_smtp_server

            current_smtp_port = st.session_state.temp_config.get("SMTP_PORT", "587")
            new_smtp_port = st.text_input("🔌 SMTP端口", value=current_smtp_port, disabled=not new_email_enabled, placeholder="587 (TLS) 或 465 (SSL)", key="input_smtp_port")
            st.session_state.temp_config["SMTP_PORT"] = new_smtp_port

            current_email_from = st.session_state.temp_config.get("EMAIL_FROM", "")
            new_email_from = st.text_input("📤 发件人邮箱", value=current_email_from, disabled=not new_email_enabled, placeholder="your-email@qq.com", key="input_email_from")
            st.session_state.temp_config["EMAIL_FROM"] = new_email_from

            current_email_password = st.session_state.temp_config.get("EMAIL_PASSWORD", "")
            new_email_password = st.text_input("🔐 邮箱授权码", value=current_email_password, type="password", disabled=not new_email_enabled, help="不是邮箱登录密码，而是SMTP授权码", key="input_email_password")
            st.session_state.temp_config["EMAIL_PASSWORD"] = new_email_password

            current_email_to = st.session_state.temp_config.get("EMAIL_TO", "")
            new_email_to = st.text_input("📥 收件人邮箱", value=current_email_to, disabled=not new_email_enabled, placeholder="receiver@qq.com", key="input_email_to")
            st.session_state.temp_config["EMAIL_TO"] = new_email_to

            if new_email_enabled and all([new_smtp_server, new_email_from, new_email_password, new_email_to]):
                st.success("✅ 邮件配置完整")
            elif new_email_enabled:
                st.warning("⚠️ 邮件配置不完整")
            else:
                st.info("ℹ️ 邮件通知未启用")

            st.caption("💡 QQ邮箱授权码获取：设置 → 账户 → POP3/IMAP/SMTP → 生成授权码")

        with col_webhook:
            st.markdown("#### 📱 Webhook通知")
            current_webhook_enabled = st.session_state.temp_config.get("WEBHOOK_ENABLED", "false") == "true"
            new_webhook_enabled = st.checkbox("启用Webhook通知", value=current_webhook_enabled, help="开启后可以发送到钉钉或飞书群", key="input_webhook_enabled")
            st.session_state.temp_config["WEBHOOK_ENABLED"] = "true" if new_webhook_enabled else "false"

            current_webhook_type = st.session_state.temp_config.get("WEBHOOK_TYPE", "dingtalk")
            new_webhook_type = st.selectbox("📲 Webhook类型", options=["dingtalk", "feishu"], index=0 if current_webhook_type == "dingtalk" else 1, disabled=not new_webhook_enabled, key="input_webhook_type")
            st.session_state.temp_config["WEBHOOK_TYPE"] = new_webhook_type

            current_webhook_url = st.session_state.temp_config.get("WEBHOOK_URL", "")
            new_webhook_url = st.text_input("🔗 Webhook地址", value=current_webhook_url, disabled=not new_webhook_enabled, placeholder="https://oapi.dingtalk.com/robot/send?access_token=...", key="input_webhook_url")
            st.session_state.temp_config["WEBHOOK_URL"] = new_webhook_url

            current_webhook_keyword = st.session_state.temp_config.get("WEBHOOK_KEYWORD", "aiagents通知")
            new_webhook_keyword = st.text_input("🔑 自定义关键词（钉钉安全验证）", value=current_webhook_keyword, disabled=not new_webhook_enabled or new_webhook_type != "dingtalk", placeholder="aiagents通知", help="钉钉机器人安全设置中的自定义关键词，飞书不需要此设置", key="input_webhook_keyword")
            st.session_state.temp_config["WEBHOOK_KEYWORD"] = new_webhook_keyword

            if new_webhook_enabled and new_webhook_url:
                if st.button("🧪 测试Webhook连通", width="stretch", key="test_webhook_btn"):
                    with st.spinner("正在发送测试消息..."):
                        temp_env_backup = {}
                        for key in ["WEBHOOK_ENABLED", "WEBHOOK_TYPE", "WEBHOOK_URL", "WEBHOOK_KEYWORD"]:
                            temp_env_backup[key] = os.getenv(key)
                            os.environ[key] = st.session_state.temp_config.get(key, "")

                        try:
                            from notification_service import NotificationService

                            temp_notification_service = NotificationService()
                            success, message = temp_notification_service.send_test_webhook()
                            if success:
                                st.success(f"✅ {message}")
                            else:
                                st.error(f"❌ {message}")
                        except Exception as e:
                            st.error(f"❌ 测试失败: {str(e)}")
                        finally:
                            for key, value in temp_env_backup.items():
                                if value is not None:
                                    os.environ[key] = value
                                elif key in os.environ:
                                    del os.environ[key]

            if new_webhook_enabled and new_webhook_url:
                st.success(f"✅ Webhook配置完整 ({new_webhook_type})")
            elif new_webhook_enabled:
                st.warning("⚠️ 请配置Webhook URL")
            else:
                st.info("ℹ️ Webhook通知未启用")

    with tab5:
        st.markdown("### 豆瓣抓取配置（可选）")
        st.markdown("用于豆瓣交易模式模块抓取作者帖子，建议使用账号专用Cookie并定期更换。")

        douban_info = config_info.get("DOUBAN_COOKIE", {"description": "豆瓣Cookie"})
        current_douban_cookie = st.session_state.temp_config.get("DOUBAN_COOKIE", "")

        new_douban_cookie = st.text_area(
            f"🍪 {douban_info['description']}",
            value=current_douban_cookie,
            height=180,
            help="直接粘贴浏览器Cookie字符串；仅保存在本地.env，请勿外泄",
            key="input_douban_cookie",
        )
        st.session_state.temp_config["DOUBAN_COOKIE"] = new_douban_cookie.strip()

        if new_douban_cookie.strip():
            st.success("✅ DOUBAN_COOKIE 已配置")
        else:
            st.info("ℹ️ 未配置DOUBAN_COOKIE，豆瓣抓取将不可用")

        st.caption("安全建议：使用最小权限账号，泄露后立刻在豆瓣退出所有设备并更改密码。")


    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("💾 保存配置", type="primary", width="stretch", disabled=not is_admin):
            if not is_admin:
                st.error("权限不足：仅管理员可保存配置")
                return
            is_valid, message = config_manager.validate_config(st.session_state.temp_config)
            if is_valid:
                if config_manager.write_env(st.session_state.temp_config):
                    st.success("✅ 配置已保存到 .env 文件")
                    st.info("ℹ️ 请重启应用使配置生效")
                    try:
                        config_manager.reload_config()
                        st.success("✅ 配置已重新加载")
                    except Exception as e:
                        st.warning(f"⚠️ 配置重新加载失败: {e}")
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error("❌ 保存配置失败")
            else:
                st.error(f"❌ 配置验证失败: {message}")

    with col2:
        if st.button("🔄 重置", width="stretch", disabled=not is_admin):
            if not is_admin:
                st.error("权限不足：仅管理员可重置配置")
                return
            st.session_state.temp_config = {key: info["value"] for key, info in config_info.items()}
            st.success("✅ 已重置为当前配置")
            st.rerun()

    with col3:
        if st.button("⬅️ 返回", width="stretch"):
            deactivate_nav("show_config")
            if "temp_config" in st.session_state:
                del st.session_state.temp_config
            st.rerun()

    st.markdown("---")
    with st.expander("📄 查看当前 .env 文件内容"):
        current_config = config_manager.read_env()
        st.code(
            f'''# AI股票分析系统环境配置
# 由系统自动生成和管理

# ========== DeepSeek API配置 ==========
DEEPSEEK_API_KEY="{current_config.get('DEEPSEEK_API_KEY', '')}"
DEEPSEEK_BASE_URL="{current_config.get('DEEPSEEK_BASE_URL', '')}"
DEFAULT_MODEL_NAME="{current_config.get('DEFAULT_MODEL_NAME', 'deepseek-chat')}"

# ========== 数据源配置（可选）==========
TUSHARE_TOKEN="{current_config.get('TUSHARE_TOKEN', '')}"
TUSHARE_BASE_URL="{current_config.get('TUSHARE_BASE_URL', 'http://8.136.22.187:8010/')}"
TDX_ENABLED="{current_config.get('TDX_ENABLED', 'false')}"
TDX_BASE_URL="{current_config.get('TDX_BASE_URL', 'http://192.168.1.222:8181')}"

# ========== MiniQMT量化交易配置（可选）==========
MINIQMT_ENABLED="{current_config.get('MINIQMT_ENABLED', 'false')}"
MINIQMT_ACCOUNT_ID="{current_config.get('MINIQMT_ACCOUNT_ID', '')}"
MINIQMT_HOST="{current_config.get('MINIQMT_HOST', '127.0.0.1')}"
MINIQMT_PORT="{current_config.get('MINIQMT_PORT', '58610')}"

# ========== 邮件通知配置（可选）==========
EMAIL_ENABLED="{current_config.get('EMAIL_ENABLED', 'false')}"
SMTP_SERVER="{current_config.get('SMTP_SERVER', '')}"
SMTP_PORT="{current_config.get('SMTP_PORT', '587')}"
EMAIL_FROM="{current_config.get('EMAIL_FROM', '')}"
EMAIL_PASSWORD="{current_config.get('EMAIL_PASSWORD', '')}"
EMAIL_TO="{current_config.get('EMAIL_TO', '')}"

# ========== Webhook通知配置（可选）==========
WEBHOOK_ENABLED="{current_config.get('WEBHOOK_ENABLED', 'false')}"
WEBHOOK_TYPE="{current_config.get('WEBHOOK_TYPE', 'dingtalk')}"
WEBHOOK_URL="{current_config.get('WEBHOOK_URL', '')}"
WEBHOOK_KEYWORD="{current_config.get('WEBHOOK_KEYWORD', 'aiagents通知')}"

# ========== 豆瓣抓取配置（可选）==========
DOUBAN_COOKIE="{current_config.get('DOUBAN_COOKIE', '')}"
''',
            language="bash",
        )
