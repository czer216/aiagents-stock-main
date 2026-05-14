import re
import time

import streamlit as st


def display_add_to_monitor_dialog(record, *, monitor_db, monitor_service, activate_nav, deactivate_nav):
    st.markdown("---")
    st.subheader("➕ 加入监测")

    final_decision = record["final_decision"]

    if isinstance(final_decision, dict):
        entry_range_str = final_decision.get("entry_range", "N/A")
        entry_min = 0.0
        entry_max = 0.0

        if entry_range_str and entry_range_str != "N/A":
            try:
                clean_str = str(entry_range_str).replace("¥", "").replace("元", "").replace("$", "")
                numbers = re.findall(r"\d+\.?\d*", clean_str)
                if len(numbers) >= 2:
                    entry_min = float(numbers[0])
                    entry_max = float(numbers[1])
            except Exception:
                try:
                    clean_str = str(entry_range_str).replace("¥", "").replace("元", "").replace("$", "")
                    for sep in ["-", "~", "至", "到"]:
                        if sep in clean_str:
                            parts = clean_str.split(sep)
                            if len(parts) == 2:
                                entry_min = float(parts[0].strip())
                                entry_max = float(parts[1].strip())
                                break
                except Exception:
                    pass

        take_profit_str = final_decision.get("take_profit", "N/A")
        stop_loss_str = final_decision.get("stop_loss", "N/A")

        take_profit = 0.0
        stop_loss = 0.0

        if take_profit_str and take_profit_str != "N/A":
            try:
                clean_str = str(take_profit_str).replace("¥", "").replace("元", "").replace("$", "").strip()
                numbers = re.findall(r"\d+\.?\d*", clean_str)
                if numbers:
                    take_profit = float(numbers[0])
            except Exception:
                pass

        if stop_loss_str and stop_loss_str != "N/A":
            try:
                clean_str = str(stop_loss_str).replace("¥", "").replace("元", "").replace("$", "").strip()
                numbers = re.findall(r"\d+\.?\d*", clean_str)
                if numbers:
                    stop_loss = float(numbers[0])
            except Exception:
                pass

        rating = final_decision.get("rating", "买入")

        existing_stocks = monitor_db.get_monitored_stocks()
        is_duplicate = any(stock["symbol"] == record["symbol"] for stock in existing_stocks)

        if is_duplicate:
            st.warning(f"⚠️ {record['symbol']} 已经在监测列表中。继续添加将创建重复监测项。")

        st.info(f"""
        **从分析结果中提取的数据：**
        - 进场区间: {entry_min} - {entry_max}
        - 止盈位: {take_profit if take_profit > 0 else '未设置'}
        - 止损位: {stop_loss if stop_loss > 0 else '未设置'}
        - 投资评级: {rating}
        """)

        with st.form(key=f"monitor_form_{record['id']}"):
            st.markdown("**请确认或修改监测参数：**")

            col1, col2 = st.columns([1, 1])

            with col1:
                st.subheader("🎯 关键位置")
                new_entry_min = st.number_input("进场区间最低价", value=float(entry_min), step=0.01, format="%.2f")
                new_entry_max = st.number_input("进场区间最高价", value=float(entry_max), step=0.01, format="%.2f")
                new_take_profit = st.number_input("止盈价位", value=float(take_profit), step=0.01, format="%.2f")
                new_stop_loss = st.number_input("止损价位", value=float(stop_loss), step=0.01, format="%.2f")

            with col2:
                st.subheader("⚙️ 监测设置")
                check_interval = st.slider("监测间隔(分钟)", 5, 120, 30)
                notification_enabled = st.checkbox("启用通知", value=True)
                new_rating = st.selectbox(
                    "投资评级",
                    ["买入", "持有", "卖出"],
                    index=["买入", "持有", "卖出"].index(rating) if rating in ["买入", "持有", "卖出"] else 0,
                )

            col_a, col_b, col_c = st.columns(3)

            with col_a:
                submit = st.form_submit_button("✅ 确认加入监测", type="primary", width="stretch")

            with col_b:
                cancel = st.form_submit_button("❌ 取消", width="stretch")

            if submit:
                if new_entry_min > 0 and new_entry_max > 0 and new_entry_max > new_entry_min:
                    try:
                        entry_range = {"min": new_entry_min, "max": new_entry_max}

                        stock_id = monitor_db.add_monitored_stock(
                            symbol=record["symbol"],
                            name=record["stock_name"],
                            rating=new_rating,
                            entry_range=entry_range,
                            take_profit=new_take_profit if new_take_profit > 0 else None,
                            stop_loss=new_stop_loss if new_stop_loss > 0 else None,
                            check_interval=check_interval,
                            notification_enabled=notification_enabled,
                        )

                        st.success(f"✅ 已成功将 {record['symbol']} 加入监测列表！")
                        st.balloons()

                        monitor_service.manual_update_stock(stock_id)

                        if "add_to_monitor_id" in st.session_state:
                            del st.session_state.add_to_monitor_id
                        if "viewing_record_id" in st.session_state:
                            del st.session_state.viewing_record_id
                        deactivate_nav("show_history")
                        activate_nav("show_history")

                        time.sleep(1.5)
                        st.rerun()

                    except Exception as e:
                        st.error(f"❌ 加入监测失败: {str(e)}")
                else:
                    st.error("❌ 请输入有效的进场区间（最低价应小于最高价，且都大于0）")

            if cancel:
                if "add_to_monitor_id" in st.session_state:
                    del st.session_state.add_to_monitor_id
                st.rerun()
    else:
        st.warning("⚠️ 无法从分析结果中提取关键数据")
        if st.button("❌ 取消"):
            if "add_to_monitor_id" in st.session_state:
                del st.session_state.add_to_monitor_id
            st.rerun()


def display_record_detail(record_id, *, db, monitor_db, monitor_service, activate_nav, deactivate_nav):
    st.markdown("---")
    st.subheader("📋 详细分析记录")

    record = db.get_record_by_id(record_id)
    if not record:
        st.error("❌ 记录不存在")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("股票代码", record["symbol"])
    with col2:
        st.metric("股票名称", record["stock_name"])
    with col3:
        st.metric("分析时间", record["analysis_date"])

    st.subheader("📊 股票基本信息")
    stock_info = record["stock_info"]
    if stock_info:
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("当前价格", f"{stock_info.get('current_price', 'N/A')}")
        with col2:
            change_percent = stock_info.get("change_percent", "N/A")
            if isinstance(change_percent, (int, float)):
                st.metric("涨跌幅", f"{change_percent:.2f}%", f"{change_percent:.2f}%")
            else:
                st.metric("涨跌幅", f"{change_percent}")
        with col3:
            st.metric("市盈率", f"{stock_info.get('pe_ratio', 'N/A')}")
        with col4:
            st.metric("市净率", f"{stock_info.get('pb_ratio', 'N/A')}")
        with col5:
            market_cap = stock_info.get("market_cap", "N/A")
            if isinstance(market_cap, (int, float)):
                market_cap_str = f"{market_cap/1e9:.2f}B" if market_cap > 1e9 else f"{market_cap/1e6:.2f}M"
                st.metric("市值", market_cap_str)
            else:
                st.metric("市值", f"{market_cap}")

    st.subheader("🤖 AI分析师团队报告")
    agents_results = record["agents_results"]
    if agents_results:
        tab_names = []
        tab_contents = []
        for agent_key, agent_result in agents_results.items():
            tab_names.append(agent_result.get("agent_name", "未知分析师"))
            tab_contents.append(agent_result)

        tabs = st.tabs(tab_names)
        for i, tab in enumerate(tabs):
            with tab:
                agent_result = tab_contents[i]
                st.markdown(
                    f"""
                <div class="agent-card">
                    <h4>👨‍💼 {agent_result.get('agent_name', '未知')}</h4>
                    <p><strong>职责：</strong>{agent_result.get('agent_role', '未知')}</p>
                    <p><strong>关注领域：</strong>{', '.join(agent_result.get('focus_areas', []))}</p>
                </div>
                """,
                    unsafe_allow_html=True,
                )
                st.markdown("**📄 分析报告:**")
                st.write(agent_result.get("analysis", "暂无分析"))

    st.subheader("🤝 分析团队讨论")
    discussion_result = record["discussion_result"]
    if discussion_result:
        st.markdown(
            """
        <div class="agent-card">
            <h4>💭 团队综合讨论</h4>
        </div>
        """,
            unsafe_allow_html=True,
        )
        st.write(discussion_result)

    st.subheader("📋 最终投资决策")
    final_decision = record["final_decision"]
    if final_decision:
        if isinstance(final_decision, dict) and "decision_text" not in final_decision:
            col1, col2 = st.columns([1, 2])
            with col1:
                rating = final_decision.get("rating", "未知")
                rating_color = {"买入": "🟢", "持有": "🟡", "卖出": "🔴"}.get(rating, "⚪")
                st.markdown(
                    f"""
                <div class="decision-card">
                    <h3 style="text-align: center;">{rating_color} {rating}</h3>
                    <h4 style="text-align: center;">投资评级</h4>
                </div>
                """,
                    unsafe_allow_html=True,
                )
                st.metric("信心度", f"{final_decision.get('confidence_level', 'N/A')}/10")
                st.metric("目标价格", f"{final_decision.get('target_price', 'N/A')}")
                st.metric("建议仓位", f"{final_decision.get('position_size', 'N/A')}")
            with col2:
                st.markdown("**🎯 操作建议:**")
                st.write(final_decision.get("operation_advice", "暂无建议"))
                st.markdown("**📍 关键位置:**")
                col2_1, col2_2 = st.columns(2)
                with col2_1:
                    st.write(f"**进场区间:** {final_decision.get('entry_range', 'N/A')}")
                    st.write(f"**止盈位:** {final_decision.get('take_profit', 'N/A')}")
                with col2_2:
                    st.write(f"**止损位:** {final_decision.get('stop_loss', 'N/A')}")
                    st.write(f"**持有周期:** {final_decision.get('holding_period', 'N/A')}")
        else:
            st.write(final_decision.get("decision_text", str(final_decision)))

    st.markdown("---")
    st.subheader("🎯 操作")

    if "add_to_monitor_id" in st.session_state and st.session_state.add_to_monitor_id == record_id:
        display_add_to_monitor_dialog(
            record,
            monitor_db=monitor_db,
            monitor_service=monitor_service,
            activate_nav=activate_nav,
            deactivate_nav=deactivate_nav,
        )
    else:
        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("➕ 加入监测", type="primary", width="stretch"):
                st.session_state.add_to_monitor_id = record_id
                st.rerun()

    st.markdown("---")
    if st.button("⬅️ 返回历史记录列表"):
        if "viewing_record_id" in st.session_state:
            del st.session_state.viewing_record_id
        if "add_to_monitor_id" in st.session_state:
            del st.session_state.add_to_monitor_id
        st.rerun()


def display_history_records(*, db, monitor_db, monitor_service, activate_nav, deactivate_nav):
    st.subheader("📚 历史分析记录")

    records = db.get_all_records()
    if not records:
        st.info("📭 暂无历史分析记录")
        return

    st.write(f"📊 共找到 {len(records)} 条分析记录")

    col1, col2 = st.columns([3, 1])
    with col1:
        search_term = st.text_input("🔍 搜索股票代码或名称", placeholder="输入股票代码或名称进行搜索")
    with col2:
        st.write("")
        st.write("")
        if st.button("🔄 刷新列表"):
            st.rerun()

    filtered_records = records
    if search_term:
        filtered_records = [
            record
            for record in records
            if search_term.lower() in record["symbol"].lower() or search_term.lower() in record["stock_name"].lower()
        ]

    if not filtered_records:
        st.warning("🔍 未找到匹配的记录")
        return

    for record in filtered_records:
        rating = record.get("rating", "未知")
        rating_color = {
            "买入": "🟢",
            "持有": "🟡",
            "卖出": "🔴",
            "强烈买入": "🟢",
            "强烈卖出": "🔴",
        }.get(rating, "⚪")

        with st.expander(f"{rating_color} {record['stock_name']} ({record['symbol']}) - {record['analysis_date']}"):
            col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
            with col1:
                st.write(f"**股票代码:** {record['symbol']}")
                st.write(f"**股票名称:** {record['stock_name']}")
            with col2:
                st.write(f"**分析时间:** {record['analysis_date']}")
                st.write(f"**数据周期:** {record['period']}")
                st.write(f"**投资评级:** **{rating}**")
            with col3:
                if st.button("👀 查看详情", key=f"view_{record['id']}"):
                    st.session_state.viewing_record_id = record["id"]
            with col4:
                if st.button("➕ 监测", key=f"add_monitor_{record['id']}"):
                    st.session_state.add_to_monitor_id = record["id"]
                    st.session_state.viewing_record_id = record["id"]

            col5, _, _, _ = st.columns(4)
            with col5:
                if st.button("🗑️ 删除", key=f"delete_{record['id']}"):
                    if db.delete_record(record["id"]):
                        st.success("✅ 记录已删除")
                        st.rerun()
                    else:
                        st.error("❌ 删除失败")

    if "viewing_record_id" in st.session_state:
        display_record_detail(
            st.session_state.viewing_record_id,
            db=db,
            monitor_db=monitor_db,
            monitor_service=monitor_service,
            activate_nav=activate_nav,
            deactivate_nav=deactivate_nav,
        )
