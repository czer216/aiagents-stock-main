"""股票展示组件 - 负责股票信息、图表、分析结果的展示"""
import streamlit as st
import plotly.graph_objects as go
import time
import re


def display_stock_info(stock_info, indicators):
    """显示股票基本信息"""
    st.subheader(f"📊 {stock_info.get('name', 'N/A')} ({stock_info.get('symbol', 'N/A')})")

    # 基本信息卡片
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        current_price = stock_info.get('current_price', 'N/A')
        st.metric("当前价格", f"{current_price}")

    with col2:
        change_percent = stock_info.get('change_percent', 'N/A')
        if isinstance(change_percent, (int, float)):
            st.metric("涨跌幅", f"{change_percent:.2f}%", f"{change_percent:.2f}%")
        else:
            st.metric("涨跌幅", f"{change_percent}")

    with col3:
        pe_ratio = stock_info.get('pe_ratio', 'N/A')
        st.metric("市盈率", f"{pe_ratio}")

    with col4:
        pb_ratio = stock_info.get('pb_ratio', 'N/A')
        st.metric("市净率", f"{pb_ratio}")

    with col5:
        market_cap = stock_info.get('market_cap', 'N/A')
        if isinstance(market_cap, (int, float)):
            market_cap_str = f"{market_cap/1e9:.2f}B" if market_cap > 1e9 else f"{market_cap/1e6:.2f}M"
            st.metric("市值", market_cap_str)
        else:
            st.metric("市值", f"{market_cap}")

    # 概念板块和同花顺历史异动
    concept_boards = stock_info.get('concept_boards', [])
    ths_abnormal_data = stock_info.get('ths_abnormal_data', {})

    def _clean_show_text(text, max_len=140):
        value = str(text or '').replace('**', '').replace('`', '').strip()
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"\[\s*\d+\s*rows?\s*x\s*\d+\s*columns?\s*\]", "", value, flags=re.I).strip()
        if len(value) > max_len:
            value = value[:max_len].rstrip(' ，,;；。') + "..."
        return value

    def _is_meaningful_abnormal_record(rec):
        if not isinstance(rec, dict):
            return False
        date_text = _clean_show_text(rec.get('日期', ''), max_len=20)
        title_text = _clean_show_text(rec.get('标题', rec.get('异动类型', '')), max_len=60)
        reason_text = _clean_show_text(rec.get('原因', '') or rec.get('详情', ''), max_len=200)
        tag_text = _clean_show_text(rec.get('标签', ''), max_len=20)
        placeholder_titles = {"异动", "异动解读"}
        noisy = bool(re.search(r"\d+\s*rows?\s*x\s*\d+\s*columns?", f"{title_text} {reason_text}", flags=re.I))
        if noisy:
            return False
        if date_text in ("", "未知") and title_text in placeholder_titles and not reason_text and not tag_text:
            return False
        return True

    has_abnormal_data = isinstance(ths_abnormal_data, dict) and ths_abnormal_data.get('has_data')
    if has_abnormal_data:
        st.subheader("⚡ 同花顺历史异动（最近7天）")
        summary = ths_abnormal_data.get('summary', {})
        total_records = summary.get('total_records', 0)
        latest_date = _clean_show_text(summary.get('latest_date', '未知'), max_len=20) or "未知"
        st.markdown(f"共 **{total_records}** 条，最近日期 **{latest_date}**")

        raw_records = ths_abnormal_data.get('records', [])
        records = [r for r in raw_records if _is_meaningful_abnormal_record(r)][:10]
        if records:
            for rec in records:
                tag_text = _clean_show_text(rec.get('标签', ''), max_len=18)
                date_text = _clean_show_text(rec.get('日期', '未知'), max_len=20) or "未知"
                title_text = _clean_show_text(
                    rec.get('标题', rec.get('异动类型', '异动解读')), max_len=60
                ) or "异动解读"
                reason_text = _clean_show_text(
                    rec.get('原因', '') or rec.get('详情', ''), max_len=220
                )
                tag_prefix = f"[{tag_text}] " if tag_text else ""
                st.markdown(f"- `{date_text}` {tag_prefix}**{title_text}**")
                if reason_text:
                    st.caption(reason_text)
        else:
            st.info("ℹ️ 最近7天未获取到可用的异动解读数据")

    if concept_boards:
        with st.expander("查看概念板块", expanded=False):
            shown_concepts = concept_boards[:12]
            st.markdown(f"{'、'.join(shown_concepts)}")
            if len(concept_boards) > 12:
                st.caption(f"其余 {len(concept_boards) - 12} 个概念已省略")

    # 技术指标
    if indicators and not isinstance(indicators, dict) or "error" not in indicators:
        st.subheader("📈 关键技术指标")

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            rsi = indicators.get('rsi', 'N/A')
            if isinstance(rsi, (int, float)):
                st.metric("RSI", f"{rsi:.2f}")
            else:
                st.metric("RSI", f"{rsi}")

        with col2:
            ma20 = indicators.get('ma20', 'N/A')
            if isinstance(ma20, (int, float)):
                st.metric("MA20", f"{ma20:.2f}")
            else:
                st.metric("MA20", f"{ma20}")

        with col3:
            volume_ratio = indicators.get('volume_ratio', 'N/A')
            if isinstance(volume_ratio, (int, float)):
                st.metric("量比", f"{volume_ratio:.2f}")
            else:
                st.metric("量比", f"{volume_ratio}")

        with col4:
            macd = indicators.get('macd', 'N/A')
            if isinstance(macd, (int, float)):
                st.metric("MACD", f"{macd:.4f}")
            else:
                st.metric("MACD", f"{macd}")


def display_stock_chart(stock_data, stock_info):
    """显示股票图表"""
    st.subheader("📈 股价走势图")

    # 创建蜡烛图
    fig = go.Figure()

    # 添加蜡烛图
    fig.add_trace(go.Candlestick(
        x=stock_data.index,
        open=stock_data['Open'],
        high=stock_data['High'],
        low=stock_data['Low'],
        close=stock_data['Close'],
        name="K线"
    ))

    # 添加移动平均线
    if 'MA5' in stock_data.columns:
        fig.add_trace(go.Scatter(
            x=stock_data.index,
            y=stock_data['MA5'],
            name="MA5",
            line=dict(color='orange', width=1)
        ))

    if 'MA20' in stock_data.columns:
        fig.add_trace(go.Scatter(
            x=stock_data.index,
            y=stock_data['MA20'],
            name="MA20",
            line=dict(color='blue', width=1)
        ))

    if 'MA60' in stock_data.columns:
        fig.add_trace(go.Scatter(
            x=stock_data.index,
            y=stock_data['MA60'],
            name="MA60",
            line=dict(color='purple', width=1)
        ))

    # 布林带
    if 'BB_upper' in stock_data.columns and 'BB_lower' in stock_data.columns:
        fig.add_trace(go.Scatter(
            x=stock_data.index,
            y=stock_data['BB_upper'],
            name="布林上轨",
            line=dict(color='red', width=1, dash='dash')
        ))
        fig.add_trace(go.Scatter(
            x=stock_data.index,
            y=stock_data['BB_lower'],
            name="布林下轨",
            line=dict(color='green', width=1, dash='dash'),
            fill='tonexty',
            fillcolor='rgba(0,100,80,0.1)'
        ))

    fig.update_layout(
        title=f"{stock_info.get('name', 'N/A')} 股价走势",
        xaxis_title="日期",
        yaxis_title="价格",
        height=500,
        showlegend=True
    )

    chart_key = f"main_stock_chart_{stock_info.get('symbol', 'unknown')}_{int(time.time())}"
    st.plotly_chart(fig, use_container_width=True, config={'responsive': True}, key=chart_key)

    # 成交量图
    if 'Volume' in stock_data.columns:
        fig_volume = go.Figure()
        fig_volume.add_trace(go.Bar(
            x=stock_data.index,
            y=stock_data['Volume'],
            name="成交量",
            marker_color='lightblue'
        ))

        fig_volume.update_layout(
            title="成交量",
            xaxis_title="日期",
            yaxis_title="成交量",
            height=200
        )

        volume_key = f"volume_chart_{stock_info.get('symbol', 'unknown')}_{int(time.time())}"
        st.plotly_chart(fig_volume, use_container_width=True, config={'responsive': True}, key=volume_key)


def display_agents_analysis(agents_results):
    """显示各分析师报告"""
    st.subheader("🤖 AI分析师团队报告")

    tab_names = []
    tab_contents = []

    for agent_key, agent_result in agents_results.items():
        agent_name = agent_result.get('agent_name', '未知分析师')
        tab_names.append(agent_name)
        tab_contents.append(agent_result)

    tabs = st.tabs(tab_names)

    for i, tab in enumerate(tabs):
        with tab:
            agent_result = tab_contents[i]

            st.markdown(f"""
            <div class="agent-card">
                <h4>👨‍💼 {agent_result.get('agent_name', '未知')}</h4>
                <p><strong>职责：</strong>{agent_result.get('agent_role', '未知')}</p>
                <p><strong>关注领域：</strong>{', '.join(agent_result.get('focus_areas', []))}</p>
                <p><strong>分析时间：</strong>{agent_result.get('timestamp', '未知')}</p>
            </div>
            """, unsafe_allow_html=True)

            st.markdown("**📄 分析报告:**")
            st.write(agent_result.get('analysis', '暂无分析'))


def display_team_discussion(discussion_result):
    """显示团队讨论"""
    st.subheader("🤝 分析团队讨论")

    st.markdown("""
    <div class="agent-card">
        <h4>💭 团队综合讨论</h4>
        <p>各位分析师正在就该股票进行深入讨论，整合不同维度的分析观点...</p>
    </div>
    """, unsafe_allow_html=True)

    st.write(discussion_result)


def display_final_decision(final_decision, stock_info, agents_results=None, discussion_result=None):
    """显示最终投资决策"""
    from pdf_generator import display_pdf_export_section

    st.subheader("📋 最终投资决策")

    if isinstance(final_decision, dict) and "decision_text" not in final_decision:
        col1, col2 = st.columns([1, 2])

        with col1:
            rating = final_decision.get('rating', '未知')
            rating_color = {"买入": "🟢", "持有": "🟡", "卖出": "🔴"}.get(rating, "⚪")

            st.markdown(f"""
            <div class="decision-card">
                <h3 style="text-align: center;">{rating_color} {rating}</h3>
                <h4 style="text-align: center;">投资评级</h4>
            </div>
            """, unsafe_allow_html=True)

            confidence = final_decision.get('confidence_level', 'N/A')
            st.metric("信心度", f"{confidence}/10")

            target_price = final_decision.get('target_price', 'N/A')
            st.metric("目标价格", f"{target_price}")

            position_size = final_decision.get('position_size', 'N/A')
            st.metric("建议仓位", f"{position_size}")

        with col2:
            st.markdown("**🎯 操作建议:**")
            st.write(final_decision.get('operation_advice', '暂无建议'))

            st.markdown("**📍 关键位置:**")
            col2_1, col2_2 = st.columns(2)

            with col2_1:
                st.write(f"**进场区间:** {final_decision.get('entry_range', 'N/A')}")
                st.write(f"**止盈位:** {final_decision.get('take_profit', 'N/A')}")

            with col2_2:
                st.write(f"**止损位:** {final_decision.get('stop_loss', 'N/A')}")
                st.write(f"**持有周期:** {final_decision.get('holding_period', 'N/A')}")

        risk_warning = final_decision.get('risk_warning', '')
        if risk_warning:
            st.markdown(f"""
            <div class="warning-card">
                <h4>⚠️ 风险提示</h4>
                <p>{risk_warning}</p>
            </div>
            """, unsafe_allow_html=True)

    else:
        decision_text = final_decision.get('decision_text', str(final_decision))
        st.write(decision_text)

    st.markdown("---")
    if agents_results and discussion_result:
        display_pdf_export_section(stock_info, agents_results, discussion_result, final_decision)
    else:
        st.warning("⚠️ PDF导出功能需要完整的分析数据")
