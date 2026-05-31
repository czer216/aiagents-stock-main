import json
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import config

from ui.components import render_top_nav
from modules.douban_author_strategy.douban_author_db import douban_author_db
from modules.douban_author_strategy.douban_author_engine import douban_author_engine
from modules.douban_author_strategy.douban_author_scheduler import get_douban_author_scheduler
from modules.smart_monitor.smart_monitor_kline import SmartMonitorKline
from services.data_source_manager import data_source_manager


def _parse_urls(text: str) -> List[str]:
    return [x.strip() for x in str(text or "").splitlines() if x.strip()]


def _extract_candidate_rows(payload: dict) -> List[dict]:
    candidates = []
    if isinstance(payload, dict):
        candidates = payload.get('candidates') or []
    rows = []
    for c in candidates if isinstance(candidates, list) else []:
        code = str(c.get('code') or '')
        name = str(c.get('name') or '')
        action = str(c.get('action') or c.get('entry_idea') or '观察')
        reason = str(c.get('reason') or c.get('why_consistent_with_author') or c.get('trigger_logic') or '')
        matched_rule = str(c.get('matched_rule') or c.get('which_past_lesson_applied') or '')
        theme = str(c.get('theme') or '')
        rows.append(
            {
                '股票代码': code,
                '股票名称': name,
                '题材': theme,
                '操作建议': action,
                '推荐理由': reason,
                '匹配规则': matched_rule,
                '实时涨跌幅%': c.get('rt_change_pct'),
                '实时价': c.get('rt_price'),
                '数据源': c.get('rt_source'),
                '_candidate': c,
            }
        )
    return rows


@st.cache_data(ttl=300)
def _get_candidate_kline_data(code: str, days: int = 60):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - pd.Timedelta(days=max(int(days), 60) + 30)).strftime('%Y%m%d')
    hist = data_source_manager.get_stock_hist_data(
        symbol=str(code),
        start_date=start_date,
        end_date=end_date,
        adjust='qfq',
    )
    if hist is None or isinstance(hist, dict) or getattr(hist, 'empty', True):
        return None
    work = hist.copy().tail(int(days))
    df = pd.DataFrame({
        '日期': pd.to_datetime(work.get('date'), errors='coerce'),
        '开盘': pd.to_numeric(work.get('open'), errors='coerce'),
        '最高': pd.to_numeric(work.get('high'), errors='coerce'),
        '最低': pd.to_numeric(work.get('low'), errors='coerce'),
        '收盘': pd.to_numeric(work.get('close'), errors='coerce'),
        '成交量': pd.to_numeric(work.get('volume'), errors='coerce'),
    })
    df = df.dropna(subset=['日期', '开盘', '最高', '最低', '收盘'])
    if df.empty:
        return None
    return df


@st.cache_data(ttl=15)
def _fetch_tdx_quote_raw(code: str) -> Optional[Dict]:
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


@st.cache_data(ttl=15)
def _fetch_tdx_minute_raw(code: str) -> List[Dict]:
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


@st.cache_data(ttl=15)
def _fetch_tdx_trade_raw(code: str) -> List[Dict]:
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


def _render_tdx_quote_panel(candidate: Dict):
    code = str(candidate.get('code') or '').strip()
    raw = _fetch_tdx_quote_raw(code)
    if not raw:
        st.info("暂无五档行情数据")
        return

    k = raw.get('K') or {}
    price = float(k.get('Close') or 0) / 1000
    pre_close = float(k.get('Last') or 0) / 1000
    chg_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("最新价", f"{price:.3f}")
    m2.metric("涨跌幅", f"{chg_pct:.2f}%")
    m3.metric("成交量(手)", f"{int(raw.get('TotalHand') or 0)}")
    m4.metric("成交额(万元)", f"{float(raw.get('Amount') or 0)/100000:.0f}")

    c1, c2 = st.columns(2)
    buy = raw.get('BuyLevel') or []
    sell = raw.get('SellLevel') or []
    with c1:
        st.markdown("**买五档**")
        buy_rows = [
            {
                '档位': f"买{i + 1}",
                '价格': round(float((lv or {}).get('Price') or 0) / 1000, 3),
                '数量(手)': int(float((lv or {}).get('Number') or 0) / 100),
            }
            for i, lv in enumerate(buy[:5])
        ]
        st.dataframe(pd.DataFrame(buy_rows), use_container_width=True, height=210)
    with c2:
        st.markdown("**卖五档**")
        sell_rows = [
            {
                '档位': f"卖{i + 1}",
                '价格': round(float((lv or {}).get('Price') or 0) / 1000, 3),
                '数量(手)': int(float((lv or {}).get('Number') or 0) / 100),
            }
            for i, lv in enumerate(sell[:5])
        ]
        st.dataframe(pd.DataFrame(sell_rows), use_container_width=True, height=210)


def _render_tdx_minute_panel(candidate: Dict, panel_key: str):
    code = str(candidate.get('code') or '').strip()
    rows = _fetch_tdx_minute_raw(code)
    if not rows:
        st.info("暂无分时图数据")
        return

    df = pd.DataFrame(
        {
            'time': [str(r.get('Time') or '') for r in rows],
            'price': [float(r.get('Price') or 0) / 1000 for r in rows],
            'avg': [float(r.get('AvgPrice') or 0) / 1000 for r in rows],
            'volume': [float(r.get('Volume') or 0) for r in rows],
        }
    )
    if df.empty:
        st.info("暂无分时图数据")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['time'], y=df['price'], mode='lines', name='分时价', line=dict(width=1.6)))
    if df['avg'].sum() > 0:
        fig.add_trace(go.Scatter(x=df['time'], y=df['avg'], mode='lines', name='均价', line=dict(width=1.2, dash='dot')))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), xaxis_title='时间', yaxis_title='价格')
    st.plotly_chart(fig, use_container_width=True, config={'responsive': True}, key=f"minute_chart_{panel_key}_{code}")


def _render_tdx_trade_panel(candidate: Dict, panel_key: str):
    code = str(candidate.get('code') or '').strip()
    rows = _fetch_tdx_trade_raw(code)
    if not rows:
        st.info("暂无分时成交数据")
        return

    df = pd.DataFrame(
        {
            '时间': [str(r.get('Time') or '') for r in rows],
            '价格': [float(r.get('Price') or 0) / 1000 for r in rows],
            '成交量(手)': [float(r.get('Volume') or 0) for r in rows],
            '买卖方向': [str(r.get('BuyOrSell') or '') for r in rows],
        }
    )
    if df.empty:
        st.info("暂无分时成交数据")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(x=df['时间'], y=df['成交量(手)'], name='成交量'))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10), xaxis_title='时间', yaxis_title='成交量(手)')
    st.plotly_chart(fig, use_container_width=True, config={'responsive': True}, key=f"trade_chart_{panel_key}_{code}")
    st.dataframe(df.tail(80), use_container_width=True, height=240)


def _render_candidate_analysis_panel(candidate: Dict):
    st.markdown("**交易分析**")
    st.caption(f"操作建议: {str(candidate.get('action') or '未提供')}")
    st.caption(f"题材: {str(candidate.get('theme') or '未提供')}")
    st.caption(f"匹配规则: {str(candidate.get('matched_rule') or '未提供')}")
    st.caption(f"触发逻辑: {str(candidate.get('trigger_logic') or '未提供')}")
    st.caption(f"入场思路: {str(candidate.get('entry_idea') or '未提供')}")
    st.caption(f"退出思路: {str(candidate.get('exit_idea') or '未提供')}")
    st.caption(f"主要风险: {str(candidate.get('risk') or '未提供')}")
    conf = candidate.get('confidence')
    st.caption(f"置信度: {conf if conf is not None else '未提供'}")
    if candidate.get('rt_price') is not None:
        st.caption(f"实时价: {candidate.get('rt_price')}")
    if candidate.get('rt_change_pct') is not None:
        st.caption(f"实时涨跌幅: {candidate.get('rt_change_pct')}%")
    if candidate.get('rt_update_time'):
        st.caption(f"行情时间: {candidate.get('rt_update_time')}")
    if candidate.get('rt_source'):
        st.caption(f"数据源: {candidate.get('rt_source')}")
    if candidate.get('rerank_score') is not None:
        st.caption(f"盘中重排分: {candidate.get('rerank_score')}")
    if candidate.get('intraday_score') is not None:
        st.caption(f"分时结构分: {candidate.get('intraday_score')}")
    if candidate.get('next_day_prior_score') is not None:
        st.caption(f"次日倾向分: {candidate.get('next_day_prior_score')}")
    feat = candidate.get('intraday_features') or {}
    if feat:
        st.caption(
            f"拉升节奏:{feat.get('pull_rhythm_score')} | 量价配合:{feat.get('volume_price_score')} | 回落承接:{feat.get('pullback_support_score')}"
        )
    risk_flags = candidate.get('risk_flags') or {}
    if risk_flags.get('missing_intraday_data'):
        st.warning('分时数据不足，已回退到基础排序')
    st.markdown("**推荐理由**")
    st.write(str(candidate.get('reason') or '未提供'))


def _render_candidate_kline_panel(candidate: Dict, panel_key: str):
    code = str(candidate.get('code') or '').strip()
    name = str(candidate.get('name') or '').strip()
    if not code:
        st.caption("候选字段不完整: 缺少股票代码")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        refresh = st.button("刷新该票K线", key=f"refresh_kline_{panel_key}_{code}", width='stretch')
        if refresh:
            _get_candidate_kline_data.clear()
        kline_data = _get_candidate_kline_data(code=code, days=60)
        tabs = st.tabs(["五档行情", "K线图", "分时图", "分时成交"])
        with tabs[0]:
            _render_tdx_quote_panel(candidate)
        with tabs[1]:
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
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={'responsive': True},
                    key=f"kline_chart_{panel_key}_{code}",
                )
        with tabs[2]:
            _render_tdx_minute_panel(candidate, panel_key=panel_key)
        with tabs[3]:
            _render_tdx_trade_panel(candidate, panel_key=panel_key)
    with c2:
        _render_candidate_analysis_panel(candidate)


def display_douban_author_strategy():
    render_top_nav("📝 豆瓣交易模式", "持续学习作者交易模式，并按模式版本生成候选", show_title=False)

    with st.expander("💡 功能说明", expanded=False):
        st.markdown("""
- 输入作者+豆瓣链接，持续学习该作者交易模式
- 同一作者会累积多个模式版本
- 选择作者和模式版本，按龙虎榜池+近10日K线生成候选股
- 人工审核后进入可操作清单
        """)

    st.subheader("1) 学习入口（作者+链接）")
    col1, col2 = st.columns(2)
    with col1:
        author_id = st.text_input("作者标识", value=st.session_state.get("douban_author_id", "douban_author_1"))
        author_name = st.text_input("作者名称", value=st.session_state.get("douban_author_name", "豆瓣作者"))
    with col2:
        daily_time = st.text_input("每日执行时间(HH:MM)", value=st.session_state.get("douban_daily_time", "08:40"))
        topic_urls_text = st.text_area("帖子URL列表(每行一个)", value=st.session_state.get("douban_topic_urls", ""), height=120)
        st.caption("提示：完整URL一行一个，并确认 DOUBAN_COOKIE 有效。")

    cc1, cc2, cc3, cc4 = st.columns(4)
    with cc1:
        enable_comment_analysis = st.checkbox(
            "学习后自动抓取评论并总结",
            value=bool(st.session_state.get("douban_enable_comment_analysis", True)),
            key="douban_enable_comment_analysis_checkbox",
        )
    with cc2:
        comment_start_page = st.number_input(
            "评论起始页",
            min_value=1,
            max_value=2000,
            value=int(st.session_state.get("douban_comment_start_page", 1)),
            step=1,
            key="douban_comment_start_page_input",
        )
    with cc3:
        comment_max_pages = st.number_input(
            "评论抓取页数",
            min_value=1,
            max_value=500,
            value=int(st.session_state.get("douban_comment_max_pages", 30)),
            step=1,
            key="douban_comment_max_pages_input",
        )
    with cc4:
        st.caption("实际抓取区间")
        st.write(f"第 {int(comment_start_page)} 页 ~ 第 {int(comment_start_page) + int(comment_max_pages) - 1} 页")
    topic_urls = _parse_urls(topic_urls_text)

    st.session_state["douban_author_id"] = author_id
    st.session_state["douban_author_name"] = author_name
    st.session_state["douban_daily_time"] = daily_time
    st.session_state["douban_topic_urls"] = topic_urls_text
    st.session_state["douban_enable_comment_analysis"] = bool(enable_comment_analysis)
    st.session_state["douban_comment_start_page"] = int(comment_start_page)
    st.session_state["douban_comment_max_pages"] = int(comment_max_pages)

    scheduler = get_douban_author_scheduler()
    scheduler.configure(
        author_id=author_id,
        author_name=author_name,
        topic_urls=topic_urls,
        daily_time=daily_time,
        enable_comment_analysis=bool(enable_comment_analysis),
        comment_max_pages=int(comment_max_pages),
        comment_start_page=int(comment_start_page),
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("🧠 立即学习", key="douban_run_now", type="primary", width='stretch'):
            status_ph = st.empty()
            progress_box = st.empty()
            progress_lines = []

            def _progress(msg: str):
                progress_lines.append(f"- {msg}")
                progress_box.markdown("\n".join(progress_lines[-8:]))

            with st.spinner("执行中..."):
                status_ph.info("任务已启动")
                result = scheduler.run_now(
                    progress_cb=_progress,
                    max_seconds=300,
                )

            if result.get("success"):
                status_ph.success(
                    f"学习成功: pattern_v={result.get('pattern_version')} report_id={result.get('report_id')} "
                    f"| 新增帖子={result.get('inserted_posts', 0)}"
                )
            else:
                status_ph.error(f"执行失败: {result.get('error')}")
    with c2:
        if st.button("▶️ 启动定时", key="douban_start_schedule", width='stretch'):
            scheduler.start()
            st.success("定时任务已启动")
    with c3:
        if st.button("⏹️ 停止定时", key="douban_stop_schedule", width='stretch'):
            scheduler.stop()
            st.info("定时任务已停止")
    with c4:
        st.caption(f"状态: {'运行中' if scheduler.running else '已停止'}")

    st.markdown("---")
    st.subheader("1.5) 评论抓取与情绪总结")
    if st.button("💬 抓取评论并总结", key="douban_comment_run_now", width='stretch'):
        with st.spinner("评论分析执行中..."):
            result = douban_author_engine.analyze_topic_comments(
                author_id=author_id,
                topic_urls=topic_urls,
                max_pages=int(comment_max_pages),
                start_page=int(comment_start_page),
            )
        if result.get('success'):
            st.success(f"评论分析完成: topic={result.get('topic_count')} | 评论总数={result.get('total_comments')}")
            errs = result.get('errors') or []
            if errs:
                for er in errs[:5]:
                    st.warning(f"抓取异常: {er.get('post_url')} | {er.get('error')}")
        else:
            st.error(f"评论分析失败: {result.get('error')}")

    recent_comment_summaries = douban_author_db.list_comment_summaries(author_id=author_id, limit=10)
    if recent_comment_summaries:
        with st.expander("查看最近评论情绪汇总", expanded=False):
            for sm in recent_comment_summaries:
                summary_payload = {}
                try:
                    summary_payload = json.loads(sm.get('summary_json') or '{}')
                except Exception:
                    summary_payload = {}
                st.markdown(f"**{sm.get('post_url')}**")
                st.caption(
                    f"评论数: {int(sm.get('comment_count') or 0)} | 情绪: {sm.get('sentiment_label') or '中性'} "
                    f"({float(sm.get('sentiment_score') or 50):.1f})"
                )
                if sm.get('comment_count') == 0 and summary_payload.get('summary_text') == '暂无评论数据':
                    st.warning('该URL本次未抓到评论，可能是Cookie失效、页面结构变化或页码区间无数据。')
                st.write(str(summary_payload.get('summary_text') or ''))
                discussion_overview = str(summary_payload.get('discussion_overview') or '').strip()
                topic_list = summary_payload.get('top_discussion_topics') or []
                if discussion_overview:
                    st.info(f"讨论总览: {discussion_overview}")
                if topic_list:
                    st.caption("高频讨论话题: " + "、".join([str(x) for x in topic_list[:8]]))
                viewpoint = summary_payload.get('viewpoint_summary') or {}
                rep_comments = summary_payload.get('representative_comments') or {}

                k1, k2, k3 = st.columns(3)
                with k1:
                    st.caption("看多关键词")
                    st.write("、".join([str(x) for x in (viewpoint.get('bullish') or [])]) or "-")
                with k2:
                    st.caption("看空关键词")
                    st.write("、".join([str(x) for x in (viewpoint.get('bearish') or [])]) or "-")
                with k3:
                    st.caption("观望关键词")
                    st.write("、".join([str(x) for x in (viewpoint.get('neutral') or [])]) or "-")

                for label, key in [("代表性看多评论", "bullish"), ("代表性看空评论", "bearish"), ("代表性观望评论", "neutral")]:
                    rows = rep_comments.get(key) or []
                    if not rows:
                        continue
                    st.markdown(f"**{label}**")
                    for row in rows[:3]:
                        user = str(row.get('comment_user') or '匿名')
                        ctime = str(row.get('comment_time') or '')
                        likes = int(row.get('like_count') or 0)
                        text = str(row.get('comment_text') or '').strip()
                        st.markdown(f"- [{user}] {ctime} 👍{likes}\\n  {text}")

    st.markdown("---")
    authors = douban_author_db.list_authors(limit=200)
    if not authors:
        st.info("暂无作者模式，请先执行学习")
        selected_author_id = author_id
        selected_author_name = author_name
        pattern_version = 0
        patterns = []
    else:
        author_options = {f"{a.get('author_name')} ({a.get('author_id')})": a for a in authors}
        default_label = next(iter(author_options.keys()))
        selected_label = st.selectbox("选择作者", options=list(author_options.keys()), index=0)
        selected_author = author_options.get(selected_label) or {}
        selected_author_id = str(selected_author.get('author_id') or author_id)
        selected_author_name = str(selected_author.get('author_name') or author_name)

        patterns = douban_author_db.list_author_patterns(author_id=selected_author_id, limit=30)
        if patterns:
            version_options = {f"v{int(p.get('pattern_version') or 0)} | conf={float(p.get('confidence') or 0):.1f}": p for p in patterns}
            ver_label = st.selectbox("选择模式版本", options=list(version_options.keys()), index=0)
            selected_pattern = version_options.get(ver_label) or {}
            pattern_version = int(selected_pattern.get('pattern_version') or 0)
            st.caption(f"摘要: {str(selected_pattern.get('summary') or '')}")
            with st.expander("查看模式JSON", expanded=False):
                try:
                    st.json(json.loads(selected_pattern.get('pattern_json') or '{}'))
                except Exception:
                    st.json({'raw': selected_pattern.get('pattern_json')})
        else:
            pattern_version = 0
            st.info("该作者暂无模式版本，请先学习")

    st.markdown("---")
    st.subheader("2.5) 作者反馈融合到最新交易库")
    feedback_default = (
        "模型形态符合主升中断板低吸；但实盘会考虑筹码图形和历史股性等主观因素。"
        "像远东这类历史股性偏趋势、少隔日反包的票，可能会入选但通常不参与。"
    )
    feedback_text = st.text_area(
        "作者反馈文本",
        value=st.session_state.get("douban_author_feedback_text", feedback_default),
        height=90,
        key="douban_author_feedback_text_area",
    )
    st.session_state["douban_author_feedback_text"] = feedback_text

    if st.button("🧩 融合反馈到最新交易库", key="merge_feedback_pattern_btn", width='stretch'):
        merge_result = douban_author_engine.merge_author_feedback_to_latest_pattern(
            author_id=selected_author_id,
            author_name=selected_author_name,
            feedback_text=feedback_text,
            reviewed_by='admin',
        )
        if merge_result.get('success'):
            st.success(
                f"已融合成功：v{merge_result.get('previous_version')} -> v{merge_result.get('new_version')}"
            )
            st.rerun()
        else:
            st.error(f"融合失败: {merge_result.get('error', '未知错误')}")

    st.markdown("---")
    st.subheader("3) 按选中作者模式推荐选股")
    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        pool_lookback_days = st.number_input(
            "龙虎榜回看天数",
            min_value=1,
            max_value=90,
            value=int(st.session_state.get("douban_pool_lookback_days", 20)),
            step=1,
            key="recommend_pool_lookback_days",
        )
    with rc2:
        pool_limit = st.number_input(
            "候选池上限",
            min_value=20,
            max_value=500,
            value=int(st.session_state.get("douban_pool_limit", 120)),
            step=10,
            key="recommend_pool_limit",
        )
    with rc3:
        candidate_limit = st.number_input(
            "推荐候选数量",
            min_value=1,
            max_value=20,
            value=int(st.session_state.get("douban_candidate_limit", 3)),
            step=1,
            key="recommend_candidate_limit",
        )

    st.session_state["douban_pool_lookback_days"] = int(pool_lookback_days)
    st.session_state["douban_pool_limit"] = int(pool_limit)
    st.session_state["douban_candidate_limit"] = int(candidate_limit)

    r1, r2, r3 = st.columns([1, 1, 2])
    with r1:
        run_recommend = st.button("🎯 按模式生成候选", key="recommend_by_pattern", type="primary", width='stretch', disabled=(pattern_version <= 0))
    with r2:
        run_intraday = st.button("⚡ 盘中实时推荐(TDX)", key="recommend_intraday_by_pattern", width='stretch', disabled=(pattern_version <= 0))
    with r3:
        st.caption(f"当前: {selected_author_name} | v{pattern_version if pattern_version > 0 else '-'}")

    if run_recommend and pattern_version > 0:
        status_ph = st.empty()
        progress_box = st.empty()
        progress_lines = []

        def _progress(msg: str):
            progress_lines.append(f"- {msg}")
            progress_box.markdown("\n".join(progress_lines[-8:]))

        with st.spinner("生成中..."):
            status_ph.info("任务已启动")
            result = douban_author_engine.recommend_by_pattern(
                author_id=selected_author_id,
                author_name=selected_author_name,
                pattern_version=int(pattern_version),
                pool_lookback_days=int(pool_lookback_days),
                pool_limit=int(pool_limit),
                candidate_limit=int(candidate_limit),
                progress_cb=_progress,
            )
        if result.get('success'):
            status_ph.success(f"已生成草稿 candidate_id={result.get('candidate_id')} | pool={result.get('pool_size')}只")
            st.rerun()
        else:
            status_ph.error(f"生成失败: {result.get('error')}")

    if run_intraday and pattern_version > 0:
        status_ph = st.empty()
        progress_box = st.empty()
        progress_lines = []
        started_at = datetime.now()

        def _progress_rt(msg: str):
            progress_lines.append(f"- {msg}")
            progress_box.markdown("\n".join(progress_lines[-10:]))

        with st.spinner("盘中推荐生成中..."):
            status_ph.info("任务已启动")
            result = douban_author_engine.recommend_intraday_by_pattern(
                author_id=selected_author_id,
                author_name=selected_author_name,
                pattern_version=int(pattern_version),
                pool_lookback_days=int(pool_lookback_days),
                pool_limit=int(pool_limit),
                candidate_limit=int(candidate_limit),
                progress_cb=_progress_rt,
                enforce_tdx=True,
            )

        duration = (datetime.now() - started_at).total_seconds()
        progress_text = " | ".join([x.lstrip('- ').strip() for x in progress_lines[-8:] if x.strip()])

        if result.get('success'):
            status_ph.success(
                f"盘中草稿已生成 candidate_id={result.get('candidate_id')} | 入选={result.get('selected_count')} | "
                f"跳过={result.get('skipped_count')}"
            )
            douban_author_db.save_scheduler_log(
                task_type="intraday_recommend_tdx",
                status="success",
                message=(
                    f"candidate_id={result.get('candidate_id')} | selected={result.get('selected_count')} | "
                    f"skipped={result.get('skipped_count')} | failed={result.get('failed_count')}"
                    + (f" | progress={progress_text}" if progress_text else "")
                ),
                retry_count=0,
                duration=duration,
                related_post_id=None,
            )
            st.rerun()
        else:
            err = str(result.get('error') or '未知错误')
            status_ph.error(f"盘中生成失败: {err}")
            douban_author_db.save_scheduler_log(
                task_type="intraday_recommend_tdx",
                status="failed",
                message=(f"error={err}" + (f" | progress={progress_text}" if progress_text else "")),
                retry_count=0,
                duration=duration,
                related_post_id=None,
            )

    st.markdown("---")
    st.subheader("运行日志")
    logs = douban_author_db.get_scheduler_logs(limit=30)
    if not logs:
        st.info("暂无运行日志")
    else:
        for row in logs:
            status = str(row.get('status') or '')
            icon = "✅" if status == "success" else ("❌" if status == "failed" else "ℹ️")
            st.markdown(
                f"{icon} **{row.get('executed_at', '')}** | {row.get('task_type', '')} | {status} | 重试:{row.get('retry_count', 0)} | 耗时:{round(float(row.get('duration') or 0),2)}s"
            )
            msg = str(row.get('message') or '').strip()
            if msg:
                st.caption(msg)

    st.markdown("---")
    st.subheader("候选股草稿")
    drafts = douban_author_db.list_candidates(status='draft', limit=20, author_id=selected_author_id if selected_author_id else None)
    if not drafts:
        st.info("暂无草稿")
    else:
        for row in drafts:
            cid = int(row.get('id'))
            payload = {}
            try:
                payload = json.loads(row.get('candidate_json') or '{}')
            except Exception:
                payload = {"raw": row.get('candidate_json')}
            with st.expander(f"草稿 #{cid} | pattern_v={row.get('pattern_version', 0)} | batch={row.get('batch_type') or 'offline'}", expanded=False):
                rows = _extract_candidate_rows(payload if isinstance(payload, dict) else {})
                if rows:
                    display_rows = [{k: v for k, v in r.items() if k != '_candidate'} for r in rows]
                    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, height=260)
                    for i, r in enumerate(rows, 1):
                        code = str(r.get('股票代码') or '')
                        name = str(r.get('股票名称') or '')
                        with st.expander(f"📈 {code} {name} | 点击展开查看K线与分析", expanded=False):
                            _render_candidate_kline_panel(r.get('_candidate') or {}, panel_key=f"draft_{cid}_{i}")
                else:
                    st.info("该草稿暂无结构化候选，已保留原始结果")
                with st.expander("查看原始JSON", expanded=False):
                    st.json(payload)
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ 批准", key=f"approve_{cid}", width='stretch'):
                        douban_author_db.update_candidate_status(cid, 'approved', reviewed_by='admin', review_comment='manual approved')
                        douban_author_db.save_candidate_outcome(
                            candidate_id=cid,
                            author_id=selected_author_id,
                            horizon='T1',
                            ret_pct=0,
                            max_drawdown=0,
                            hit_trigger=False,
                            outcome_label='neutral',
                        )
                        st.success("已批准")
                        st.rerun()
                with cc2:
                    if st.button("❌ 驳回", key=f"reject_{cid}", width='stretch'):
                        douban_author_db.update_candidate_status(cid, 'rejected', reviewed_by='admin', review_comment='manual rejected')
                        douban_author_db.save_learning_memory(
                            author_id=selected_author_id,
                            report_id=0,
                            memory_type='lesson',
                            memory_json={'lesson': '人工驳回样本', 'summary': '本次候选被人工驳回，后续应降低同类信号优先级'},
                            quality_score=0,
                        )
                        st.warning("已驳回")
                        st.rerun()

    st.markdown("---")
    st.subheader("可操作清单(已批准)")
    approved = douban_author_db.list_candidates(status='approved', limit=20, author_id=selected_author_id if selected_author_id else None)
    if not approved:
        st.info("暂无已批准候选")
    else:
        for row in approved:
            cid = int(row.get('id'))
            payload = {}
            try:
                payload = json.loads(row.get('candidate_json') or '{}')
            except Exception:
                payload = {"raw": row.get('candidate_json')}
            with st.expander(f"候选 #{cid} | pattern_v={row.get('pattern_version', 0)} | batch={row.get('batch_type') or 'offline'} | reviewed_at={row.get('reviewed_at')}", expanded=False):
                rows = _extract_candidate_rows(payload if isinstance(payload, dict) else {})
                if rows:
                    display_rows = [{k: v for k, v in r.items() if k != '_candidate'} for r in rows]
                    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, height=260)
                    for i, r in enumerate(rows, 1):
                        code = str(r.get('股票代码') or '')
                        name = str(r.get('股票名称') or '')
                        with st.expander(f"📈 {code} {name} | 点击展开查看K线与分析", expanded=False):
                            _render_candidate_kline_panel(r.get('_candidate') or {}, panel_key=f"approved_{cid}_{i}")
                else:
                    st.info("该候选暂无结构化候选，已保留原始结果")
                with st.expander("查看原始JSON", expanded=False):
                    st.json(payload)
