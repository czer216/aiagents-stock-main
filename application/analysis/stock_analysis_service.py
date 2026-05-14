import concurrent.futures
import threading
import time

import streamlit as st



def run_batch_analysis_flow(stock_list, period, batch_mode, *, analyze_single_stock_for_batch, config):
    enabled_analysts_config = {
        "technical": st.session_state.get("enable_technical", True),
        "fundamental": st.session_state.get("enable_fundamental", True),
        "fund_flow": st.session_state.get("enable_fund_flow", True),
        "risk": st.session_state.get("enable_risk", True),
        "sentiment": st.session_state.get("enable_sentiment", False),
        "news": st.session_state.get("enable_news", False),
    }
    selected_model = st.session_state.get("selected_model", config.DEFAULT_MODEL_NAME)

    st.subheader(f"📊 批量分析进行中 ({batch_mode})")

    progress_bar = st.progress(0)
    status_text = st.empty()

    results = []
    total = len(stock_list)

    if batch_mode == "多线程并行":
        status_text.text(f"🚀 使用多线程并行分析 {total} 只股票...")

        lock = threading.Lock()
        completed = [0]
        progress_status = [{}]

        def analyze_with_progress(symbol):
            try:
                result = analyze_single_stock_for_batch(symbol, period, enabled_analysts_config, selected_model)
                with lock:
                    completed[0] += 1
                    progress_status[0][symbol] = result
                return result
            except Exception as e:
                with lock:
                    completed[0] += 1
                    error_result = {"symbol": symbol, "error": str(e), "success": False}
                    progress_status[0][symbol] = error_result
                return error_result

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_symbol = {executor.submit(analyze_with_progress, symbol): symbol for symbol in stock_list}

            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result(timeout=300)
                    results.append(result)

                    progress = len(results) / total
                    progress_bar.progress(progress)

                    if result["success"]:
                        status_text.text(f"✅ [{len(results)}/{total}] {symbol} 分析完成")
                    else:
                        status_text.text(f"❌ [{len(results)}/{total}] {symbol} 分析失败: {result.get('error', '未知错误')}")

                except concurrent.futures.TimeoutError:
                    results.append({"symbol": symbol, "error": "分析超时（5分钟）", "success": False})
                    progress_bar.progress(len(results) / total)
                    status_text.text(f"⏱️ [{len(results)}/{total}] {symbol} 分析超时")
                except Exception as e:
                    results.append({"symbol": symbol, "error": str(e), "success": False})
                    progress_bar.progress(len(results) / total)
                    status_text.text(f"❌ [{len(results)}/{total}] {symbol} 出现错误")

    else:
        status_text.text(f"📝 按顺序分析 {total} 只股票...")

        for i, symbol in enumerate(stock_list, 1):
            status_text.text(f"🔍 [{i}/{total}] 正在分析 {symbol}...")

            try:
                result = analyze_single_stock_for_batch(symbol, period, enabled_analysts_config, selected_model)
            except Exception as e:
                result = {"symbol": symbol, "error": str(e), "success": False}

            results.append(result)

            progress = i / total
            progress_bar.progress(progress)

            if result["success"]:
                status_text.text(f"✅ [{i}/{total}] {symbol} 分析完成")
            else:
                status_text.text(f"❌ [{i}/{total}] {symbol} 分析失败: {result.get('error', '未知错误')}")

    progress_bar.progress(1.0)

    success_count = sum(1 for r in results if r["success"])
    failed_count = total - success_count
    saved_count = sum(1 for r in results if r.get("saved_to_db", False))

    if success_count > 0:
        status_text.success(f"✅ 批量分析完成！成功 {success_count} 只，失败 {failed_count} 只，已保存 {saved_count} 只到历史记录")

        save_failed = [r["symbol"] for r in results if r.get("success") and not r.get("saved_to_db", False)]
        if save_failed:
            st.warning(f"⚠️ 以下股票分析成功但保存失败: {', '.join(save_failed)}")
    else:
        status_text.error("❌ 批量分析完成，但所有股票都分析失败")

    st.session_state.batch_analysis_results = results
    st.session_state.batch_analysis_mode = batch_mode

    time.sleep(1)
    progress_bar.empty()
    st.rerun()



def display_comparison_table(results):
    import pandas as pd

    st.subheader("📋 股票对比表格")

    comparison_data = []
    for result in results:
        stock_info = result["stock_info"]
        indicators = result.get("indicators", {})
        final_decision = result["final_decision"]

        if isinstance(final_decision, dict):
            rating = final_decision.get("rating", "N/A")
            confidence = final_decision.get("confidence_level", "N/A")
            target_price = final_decision.get("target_price", "N/A")
        else:
            rating = "N/A"
            confidence = "N/A"
            target_price = "N/A"

        if isinstance(confidence, (int, float)):
            confidence = str(confidence)

        row = {
            "股票代码": stock_info.get("symbol", "N/A"),
            "股票名称": stock_info.get("name", "N/A"),
            "当前价格": stock_info.get("current_price", "N/A"),
            "涨跌幅(%)": stock_info.get("change_percent", "N/A"),
            "市盈率": stock_info.get("pe_ratio", "N/A"),
            "市净率": stock_info.get("pb_ratio", "N/A"),
            "RSI": indicators.get("rsi", "N/A"),
            "MACD": indicators.get("macd", "N/A"),
            "投资评级": rating,
            "信心度": confidence,
            "目标价格": target_price,
        }
        comparison_data.append(row)

    df = pd.DataFrame(comparison_data)

    st.dataframe(df, width="stretch", height=400)

    st.caption("💡 投资评级说明：强烈买入 > 买入 > 持有 > 卖出 > 强烈卖出")

    st.markdown("---")
    st.subheader("🔍 快速筛选")

    col1, col2 = st.columns(2)
    with col1:
        rating_filter = st.multiselect("按评级筛选", options=df["投资评级"].unique().tolist(), default=df["投资评级"].unique().tolist())

    with col2:
        sort_by = st.selectbox("排序方式", ["默认", "涨跌幅降序", "涨跌幅升序", "信心度降序", "RSI降序"])

    filtered_df = df[df["投资评级"].isin(rating_filter)]

    if sort_by == "涨跌幅降序":
        filtered_df = filtered_df.sort_values("涨跌幅(%)", ascending=False)
    elif sort_by == "涨跌幅升序":
        filtered_df = filtered_df.sort_values("涨跌幅(%)", ascending=True)
    elif sort_by == "信心度降序":
        filtered_df = filtered_df.sort_values("信心度", ascending=False)
    elif sort_by == "RSI降序":
        filtered_df = filtered_df.sort_values("RSI", ascending=False)

    if not filtered_df.empty:
        st.dataframe(filtered_df, width="stretch")
    else:
        st.info("没有符合条件的股票")



def display_detailed_cards(
    results,
    period,
    *,
    get_stock_data,
    display_stock_info,
    display_stock_chart,
    display_agents_analysis,
    display_team_discussion,
    display_final_decision,
):
    st.subheader("📇 详细分析卡片")

    stock_options = [f"{r['stock_info']['symbol']} - {r['stock_info']['name']}" for r in results]
    selected_stock = st.selectbox("选择股票", options=stock_options)

    selected_index = stock_options.index(selected_stock)
    result = results[selected_index]

    stock_info = result["stock_info"]
    indicators = result["indicators"]
    agents_results = result["agents_results"]
    discussion_result = result["discussion_result"]
    final_decision = result["final_decision"]

    try:
        stock_info_current, stock_data, _ = get_stock_data(stock_info["symbol"], period)

        display_stock_info(stock_info, indicators)

        if stock_data is not None:
            display_stock_chart(stock_data, stock_info)

        display_agents_analysis(agents_results)
        display_team_discussion(discussion_result)
        display_final_decision(final_decision, stock_info, agents_results, discussion_result)

    except Exception as e:
        st.error(f"显示详细信息时出错: {str(e)}")



def display_batch_analysis_results(
    results,
    period,
    *,
    get_stock_data,
    display_stock_info,
    display_stock_chart,
    display_agents_analysis,
    display_team_discussion,
    display_final_decision,
):
    st.subheader("📊 批量分析结果对比")

    total = len(results)
    success_results = [r for r in results if r["success"]]
    failed_results = [r for r in results if not r["success"]]
    saved_count = sum(1 for r in results if r.get("saved_to_db", False))

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总数", total)
    with col2:
        st.metric("成功", len(success_results), delta=None, delta_color="normal")
    with col3:
        st.metric("失败", len(failed_results), delta=None, delta_color="inverse")
    with col4:
        st.metric("已保存", saved_count, delta=None, delta_color="normal")

    if saved_count > 0:
        st.info(f"💾 已有 {saved_count} 只股票的分析结果保存到历史记录，可在侧边栏点击「📖 历史记录」查看")

    st.markdown("---")

    if failed_results:
        with st.expander(f"❌ 查看失败的 {len(failed_results)} 只股票", expanded=False):
            for result in failed_results:
                st.error(f"**{result['symbol']}**: {result.get('error', '未知错误')}")

    save_failed_results = [r for r in success_results if not r.get("saved_to_db", False)]
    if save_failed_results:
        with st.expander(f"⚠️ 查看分析成功但保存失败的 {len(save_failed_results)} 只股票", expanded=False):
            for result in save_failed_results:
                db_error = result.get("db_error", "未知错误")
                st.warning(f"**{result['symbol']} - {result['stock_info'].get('name', 'N/A')}**: {db_error}")

    if not success_results:
        st.warning("⚠️ 没有成功分析的股票")
        return

    view_mode = st.radio(
        "显示模式",
        ["对比表格", "详细卡片"],
        horizontal=True,
        help="对比表格：横向对比多只股票；详细卡片：逐个查看详细分析",
    )

    if view_mode == "对比表格":
        display_comparison_table(success_results)
    else:
        display_detailed_cards(
            success_results,
            period,
            get_stock_data=get_stock_data,
            display_stock_info=display_stock_info,
            display_stock_chart=display_stock_chart,
            display_agents_analysis=display_agents_analysis,
            display_team_discussion=display_team_discussion,
            display_final_decision=display_final_decision,
        )



def run_stock_analysis_flow(
    symbol,
    period,
    *,
    get_stock_data,
    infer_query_trade_date,
    display_stock_info,
    display_stock_chart,
    display_agents_analysis,
    display_team_discussion,
    display_final_decision,
    db,
    config,
    StockDataFetcher,
    StockAnalysisAgents,
):
    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        status_text.text("📈 正在获取股票数据...")
        progress_bar.progress(10)

        stock_info, stock_data, indicators = get_stock_data(symbol, period)

        if "error" in stock_info:
            st.error(f"❌ {stock_info['error']}")
            return

        if stock_data is None:
            st.error("❌ 无法获取股票历史数据")
            return

        stock_info = dict(stock_info)

        fetcher = StockDataFetcher()
        if fetcher._is_chinese_stock(symbol):
            status_text.text("🧩 正在获取概念板块和同花顺历史异动...")
            try:
                from ths_feature_data import THSFeatureDataFetcher

                ths_fetcher = THSFeatureDataFetcher()
                query_trade_date = infer_query_trade_date(stock_data)
                ths_context_data = ths_fetcher.get_context_data(symbol, query_date=query_trade_date)
                stock_info["ths_context_data"] = ths_context_data

                concept_data = ths_context_data.get("concept_data", {})
                abnormal_data = ths_context_data.get("abnormal_data", {})

                if concept_data.get("has_data"):
                    concepts = concept_data.get("concepts", [])
                    stock_info["concept_boards"] = concepts
                    st.info(f"✅ 成功获取概念板块：{min(len(concepts), 12)}个（展示前12个）")
                else:
                    st.info("ℹ️ 未获取到概念板块数据")

                if abnormal_data.get("has_data"):
                    stock_info["ths_abnormal_data"] = abnormal_data
                    total_records = abnormal_data.get("summary", {}).get("total_records", 0)
                    st.info(f"✅ 成功获取最近7天同花顺历史异动：{total_records}条")
                else:
                    st.info("ℹ️ 最近7天未获取到同花顺历史异动数据")
            except Exception as e:
                stock_info["ths_context_error"] = str(e)
                st.warning(f"⚠️ 获取概念板块/历史异动时出错: {str(e)}")
        else:
            st.info("ℹ️ 港美股暂不支持概念板块和同花顺历史异动")
        progress_bar.progress(15)

        display_stock_info(stock_info, indicators)
        progress_bar.progress(20)

        display_stock_chart(stock_data, stock_info)
        progress_bar.progress(30)

        status_text.text("📊 正在获取财务数据...")
        financial_data = fetcher.get_financial_data(symbol)
        progress_bar.progress(35)

        enable_fundamental = st.session_state.get("enable_fundamental", True)
        quarterly_data = None
        if enable_fundamental and fetcher._is_chinese_stock(symbol):
            status_text.text("📊 正在获取季报数据（akshare数据源）...")
            try:
                from quarterly_report_data import QuarterlyReportDataFetcher

                quarterly_fetcher = QuarterlyReportDataFetcher()
                quarterly_data = quarterly_fetcher.get_quarterly_reports(symbol)
                if quarterly_data and quarterly_data.get("data_success"):
                    income_count = quarterly_data.get("income_statement", {}).get("periods", 0) if quarterly_data.get("income_statement") else 0
                    balance_count = quarterly_data.get("balance_sheet", {}).get("periods", 0) if quarterly_data.get("balance_sheet") else 0
                    cash_flow_count = quarterly_data.get("cash_flow", {}).get("periods", 0) if quarterly_data.get("cash_flow") else 0
                    st.info(f"✅ 成功获取季报数据：利润表{income_count}期，资产负债表{balance_count}期，现金流量表{cash_flow_count}期")
                else:
                    st.warning("⚠️ 未能获取季报数据，将基于基本财务数据分析")
            except Exception as e:
                st.warning(f"⚠️ 获取季报数据时出错: {str(e)}")
                quarterly_data = None
        elif enable_fundamental and not fetcher._is_chinese_stock(symbol):
            st.info("ℹ️ 美股暂不支持季报数据")
        progress_bar.progress(37)

        enable_fund_flow = st.session_state.get("enable_fund_flow", True)
        enable_sentiment = st.session_state.get("enable_sentiment", False)
        enable_news = st.session_state.get("enable_news", False)

        fund_flow_data = None
        if enable_fund_flow and fetcher._is_chinese_stock(symbol):
            status_text.text("💰 正在获取资金流向数据（akshare数据源）...")
            try:
                from fund_flow_akshare import FundFlowAkshareDataFetcher

                fund_flow_fetcher = FundFlowAkshareDataFetcher()
                fund_flow_data = fund_flow_fetcher.get_fund_flow_data(symbol)
                if fund_flow_data and fund_flow_data.get("data_success"):
                    days = fund_flow_data.get("fund_flow_data", {}).get("days", 0) if fund_flow_data.get("fund_flow_data") else 0
                    st.info(f"✅ 成功获取 {days} 个交易日的资金流向数据")
                else:
                    st.warning("⚠️ 未能获取资金流向数据，将基于技术指标进行资金面分析")
            except Exception as e:
                st.warning(f"⚠️ 获取资金流向数据时出错: {str(e)}")
                fund_flow_data = None
        elif enable_fund_flow and not fetcher._is_chinese_stock(symbol):
            st.info("ℹ️ 美股暂不支持资金流向数据")
        progress_bar.progress(40)

        sentiment_data = None
        if enable_sentiment and fetcher._is_chinese_stock(symbol):
            status_text.text("📊 正在获取市场情绪数据（ARBR等指标）...")
            try:
                from market_sentiment_data import MarketSentimentDataFetcher

                sentiment_fetcher = MarketSentimentDataFetcher()
                sentiment_data = sentiment_fetcher.get_market_sentiment_data(symbol, stock_data)
                if sentiment_data and sentiment_data.get("data_success"):
                    st.info("✅ 成功获取市场情绪数据（ARBR、换手率、涨跌停等）")
                else:
                    st.warning("⚠️ 未能获取完整的市场情绪数据，将基于基本信息进行分析")
            except Exception as e:
                st.warning(f"⚠️ 获取市场情绪数据时出错: {str(e)}")
                sentiment_data = None
        elif enable_sentiment and not fetcher._is_chinese_stock(symbol):
            st.info("ℹ️ 美股暂不支持市场情绪数据（ARBR等指标）")
        progress_bar.progress(45)

        news_data = None
        if enable_news and fetcher._is_chinese_stock(symbol):
            status_text.text("📰 正在获取新闻数据...")
            try:
                from qstock_news_data import QStockNewsDataFetcher

                news_fetcher = QStockNewsDataFetcher()
                news_data = news_fetcher.get_stock_news(symbol)
                if news_data and news_data.get("data_success"):
                    news_count = news_data.get("news_data", {}).get("count", 0) if news_data.get("news_data") else 0
                    st.info(f"✅ 成功从东方财富获取个股 {news_count} 条新闻")
                else:
                    st.warning("⚠️ 未能获取新闻数据，将基于基本信息进行分析")
            except Exception as e:
                st.warning(f"⚠️ 获取新闻数据时出错: {str(e)}")
                news_data = None
        elif enable_news and not fetcher._is_chinese_stock(symbol):
            st.info("ℹ️ 美股暂不支持新闻数据")
        progress_bar.progress(45)

        enable_risk = st.session_state.get("enable_risk", True)
        risk_data = None
        if enable_risk and fetcher._is_chinese_stock(symbol):
            status_text.text("⚠️ 正在获取风险数据（限售解禁、大股东减持、重要事件）...")
            try:
                risk_data = fetcher.get_risk_data(symbol)
                if risk_data and risk_data.get("data_success"):
                    risk_types = []
                    if risk_data.get("lifting_ban") and risk_data["lifting_ban"].get("has_data"):
                        risk_types.append("限售解禁")
                    if risk_data.get("shareholder_reduction") and risk_data["shareholder_reduction"].get("has_data"):
                        risk_types.append("大股东减持")
                    if risk_data.get("important_events") and risk_data["important_events"].get("has_data"):
                        risk_types.append("重要事件")

                    if risk_types:
                        st.info(f"✅ 成功获取风险数据：{', '.join(risk_types)}")
                    else:
                        st.info("ℹ️ 暂无风险相关数据")
                else:
                    st.info("ℹ️ 暂无风险相关数据，将基于基本信息进行风险分析")
            except Exception as e:
                st.warning(f"⚠️ 获取风险数据时出错: {str(e)}")
                risk_data = None
        elif enable_risk and not fetcher._is_chinese_stock(symbol):
            st.info("ℹ️ 美股暂不支持风险数据（限售解禁、大股东减持等）")
        progress_bar.progress(50)

        status_text.text("🤖 正在初始化AI分析系统...")
        selected_model = st.session_state.get("selected_model", config.DEFAULT_MODEL_NAME)
        agents = StockAnalysisAgents(model=selected_model)
        progress_bar.progress(55)

        enable_technical = st.session_state.get("enable_technical", True)
        enable_fundamental = st.session_state.get("enable_fundamental", True)
        enable_risk = st.session_state.get("enable_risk", True)

        enabled_analysts = {
            "technical": enable_technical,
            "fundamental": enable_fundamental,
            "fund_flow": enable_fund_flow,
            "risk": enable_risk,
            "sentiment": enable_sentiment,
            "news": enable_news,
        }

        status_text.text("🔍 AI分析师团队正在分析,请耐心等待几分钟...")
        agents_results = agents.run_multi_agent_analysis(
            stock_info,
            stock_data,
            indicators,
            financial_data,
            fund_flow_data,
            sentiment_data,
            news_data,
            quarterly_data,
            risk_data,
            enabled_analysts=enabled_analysts,
        )
        progress_bar.progress(75)

        display_agents_analysis(agents_results)

        status_text.text("🤝 分析团队正在讨论...")
        discussion_result = agents.conduct_team_discussion(agents_results, stock_info)
        progress_bar.progress(88)

        display_team_discussion(discussion_result)

        status_text.text("📋 正在制定最终投资决策...")
        final_decision = agents.make_final_decision(discussion_result, stock_info, indicators)
        progress_bar.progress(100)

        display_final_decision(final_decision, stock_info, agents_results, discussion_result)

        st.session_state.analysis_completed = True
        st.session_state.stock_info = stock_info
        st.session_state.agents_results = agents_results
        st.session_state.discussion_result = discussion_result
        st.session_state.final_decision = final_decision
        st.session_state.just_completed = True

        try:
            db.save_analysis(
                symbol=stock_info.get("symbol", ""),
                stock_name=stock_info.get("name", ""),
                period=period,
                stock_info=stock_info,
                agents_results=agents_results,
                discussion_result=discussion_result,
                final_decision=final_decision,
            )
            st.success("✅ 分析记录已保存到数据库")
        except Exception as e:
            st.warning(f"⚠️ 保存到数据库时出现错误: {str(e)}")

        status_text.text("✅ 分析完成！")
        time.sleep(1)
        status_text.empty()
        progress_bar.empty()

    except Exception as e:
        st.error(f"❌ 分析过程中出现错误: {str(e)}")
        progress_bar.empty()
        status_text.empty()
