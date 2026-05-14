import streamlit as st


def render_top_nav(title: str, subtitle: str = "", show_title: bool = True) -> None:
    """渲染顶部导航栏

    Args:
        title: 标题文本
        subtitle: 副标题文本
        show_title: 是否显示标题，默认True。设为False可隐藏大标题
    """
    if not show_title:
        return

    subtitle_html = f'<p class="nav-subtitle">{subtitle}</p>' if str(subtitle or "").strip() else ""
    st.markdown(
        f"""
    <div class="top-nav">
        <h1 class="nav-title">{title}</h1>
        {subtitle_html}
    </div>
    """,
        unsafe_allow_html=True,
    )



def render_usage_guide() -> None:
    st.subheader("💡 使用说明")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
        ### 🚀 如何使用
        1. **输入股票代码**：支持A股(如000001)、港股(如00700)和美股(如AAPL)
        2. **点击开始分析**：系统将启动AI分析师团队
        3. **查看分析报告**：多位专业分析师将从不同角度分析
        4. **获得投资建议**：获得最终的投资评级和操作建议

        ### 📊 分析维度
        - **技术面**：趋势、指标、支撑阻力
        - **基本面**：财务、估值、行业分析
        - **资金面**：资金流向、主力行为
        - **风险管理**：风险识别与控制
        - **市场情绪**：情绪指标、热点分析
        """
        )

    with col2:
        st.markdown(
            """
        ### 📈 示例股票代码

        **A股热门**
        - 000001 (平安银行)
        - 600036 (招商银行)
        - 600519 (贵州茅台)

        **港股热门**
        - 00700 或 700 (腾讯控股)
        - 09988 或 9988 (阿里巴巴-SW)
        - 01810 或 1810 (小米集团-W)

        **美股热门**
        - AAPL (苹果)
        - MSFT (微软)
        - NVDA (英伟达)
        """
        )

    st.info("💡 提示：首次运行需要配置DeepSeek API Key，请在.env中设置DEEPSEEK_API_KEY")

    st.markdown("---")
    st.markdown(
        """
    ### 🌏 市场支持说明
    - **A股**：完整支持（技术分析、财务数据、资金流向、市场情绪、新闻数据qstock）
    - **港股**：部分支持（技术分析、21项财务指标）⭐️
    - **美股**：完整支持（技术分析、财务数据）

    ### 📊 港股支持的财务指标
    盈利能力（6项）、营运能力（3项）、偿债能力（2项）、市场表现（4项）、分红指标（3项）、股本结构（3项）
    """
    )
