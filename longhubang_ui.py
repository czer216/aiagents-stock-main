"""
智瞰龙虎UI界面模块
展示龙虎榜分析结果和推荐股票
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta
import time
import base64

from longhubang_engine import LonghubangEngine
from longhubang_pdf import LonghubangPDFGenerator
import config


def _get_longhubang_engine(model=None) -> LonghubangEngine:
    """会话级复用引擎实例，避免Streamlit重跑时反复初始化。"""
    model_name = str(model or config.DEFAULT_MODEL_NAME or "default")
    resolved_db_path = LonghubangEngine.resolve_db_path()
    key = f"longhubang_engine_instance::{model_name}::{resolved_db_path}"
    if key not in st.session_state:
        st.session_state[key] = LonghubangEngine(model=model_name, db_path=resolved_db_path)
    return st.session_state[key]


def display_longhubang():
    """显示智瞰龙虎主界面"""
    
    st.markdown("""
    <div class="top-nav">
        <h1 class="nav-title">🎯 智瞰龙虎 - AI驱动的龙虎榜分析</h1>
        <p class="nav-subtitle">Multi-Agent Dragon Tiger Analysis | 游资·个股·题材·风险多维分析</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 功能说明
    with st.expander("💡 智瞰龙虎系统介绍", expanded=False):
        st.markdown("""
        ### 🌟 系统特色
        
        **智瞰龙虎**是基于多AI智能体的龙虎榜深度分析系统，通过5位专业分析师的协同工作，
        为您挖掘次日大概率上涨的潜力股票。
        
        ### 🤖 AI分析师团队
        
        1. **🎯 游资行为分析师**
           - 识别活跃游资及其操作风格
           - 分析游资席位的进出特征
           - 研判游资对个股的态度
        
        2. **📈 个股潜力分析师**
           - 从龙虎榜数据挖掘潜力股
           - 识别次日大概率上涨的股票
           - 分析资金动向和技术形态
        
        3. **🔥 题材追踪分析师**
           - 识别当前热点题材和概念
           - 分析题材的炒作周期
           - 预判题材的持续性
        
        4. **⚠️ 风险控制专家**
           - 识别高风险股票和陷阱
           - 分析游资出货信号
           - 提供风险管理建议
        
        5. **👔 首席策略师**
           - 综合所有分析师意见
           - 给出最终推荐股票清单
           - 提供具体操作策略
        
        ### 📊 数据来源
        
        数据来自**StockAPI龙虎榜接口**，包括：
        - 游资上榜交割单历史数据
        - 股票买卖金额和净流入
        - 热门概念和题材
        - 更新时间：交易日下午5点40
        
        ### 🎯 核心功能
        
        - ✅ **潜力股挖掘** - AI识别次日大概率上涨股票
        - ✅ **游资追踪** - 跟踪活跃游资的操作
        - ✅ **题材识别** - 发现热点题材和龙头股
        - ✅ **风险提示** - 识别高风险股票和陷阱
        - ✅ **历史记录** - 存储所有龙虎榜数据
        - ✅ **PDF报告** - 生成专业分析报告
        """)
    
    st.markdown("---")
    
    # 创建标签页
    tab1, tab2, tab3 = st.tabs([
        "📊 龙虎榜分析",
        "📚 历史报告",
        "📈 数据统计"
    ])
    
    with tab1:
        display_analysis_tab()
    
    with tab2:
        display_history_tab()
    
    with tab3:
        display_statistics_tab()


def display_analysis_tab():
    """显示分析标签页"""
    
    # 检查是否触发批量分析（不立即删除标志）
    if st.session_state.get('longhubang_batch_trigger'):
        run_longhubang_batch_analysis()
        return
    
    st.subheader("🔍 龙虎榜综合分析")
    
    # 参数设置
    col1, col2 = st.columns([2, 2])
    
    with col1:
        analysis_mode = st.selectbox(
            "分析模式",
            ["指定日期", "最近N天"],
            help="选择分析特定日期还是最近几天的数据"
        )
    
    with col2:
        if analysis_mode == "指定日期":
            selected_date = st.date_input(
                "选择日期",
                value=datetime.now() - timedelta(days=1),
                help="选择要分析的龙虎榜日期"
            )
        else:
            days = st.number_input(
                "最近天数",
                min_value=1,
                max_value=10,
                value=3,
                help="分析最近N天的龙虎榜数据"
            )
    preview_date = selected_date.strftime('%Y-%m-%d') if analysis_mode == "指定日期" else None
    preview_days = 3 if analysis_mode == "指定日期" else int(days)

    score_pool_label = st.selectbox(
        "评分池来源",
        ["仅龙虎榜", "龙虎榜+同花顺", "龙虎榜+开盘啦"],
        index=2,
        help="仅控制并入参与评分/推荐的股票来源，不改变其他分析链路的数据使用",
    )
    score_pool_source_map = {
        "仅龙虎榜": "lhb_only",
        "龙虎榜+同花顺": "lhb_limit",
        "龙虎榜+开盘啦": "lhb_kpl",
    }
    score_pool_source = score_pool_source_map.get(score_pool_label, "lhb_kpl")
    enable_9d_trend_overlay = st.checkbox(
        "启用9日趋势微调",
        value=True,
        help="默认开启：仅对候选股做小幅修正（单股±2分），不改变主评分框架",
    )
    trend_overlay_scale = st.slider(
        "9日趋势微调系数",
        min_value=0.0,
        max_value=10.0,
        value=1.0,
        step=0.1,
        help="1.0为默认强度；>1增强趋势影响，<1减弱趋势影响（0表示关闭加减分，最高可到10）",
        disabled=not enable_9d_trend_overlay,
    )
    mainboard_limit_up_threshold_pct = st.slider(
        "主板涨停判定阈值(%)",
        min_value=1.0,
        max_value=10.0,
        value=6.0,
        step=0.1,
        help="用于‘当日涨停命中’判定。创业/科创=19.5%，北交所=29.5%，ST=4.8%",
    )
    colq1, colq2 = st.columns(2)
    enable_recommendation_quota = st.checkbox(
        "启用推荐分层配额",
        value=True,
        help="开启后按“涨停/非涨停”分层配额筛选；关闭后按原始候选顺序推荐",
    )
    with colq1:
        recommendation_count = st.slider(
            "推荐数量",
            min_value=3,
            max_value=12,
            value=8,
            step=1,
            help="推荐阶段最终输出的股票数量",
        )
    with colq2:
        recommendation_limit_up_ratio = st.slider(
            "推荐中涨停占比上限",
            min_value=0.0,
            max_value=1.0,
            value=0.4,
            step=0.05,
            help="推荐阶段分层配额参数。基于“近涨停筛选标签”，0.4表示最多40%",
            disabled=not enable_recommendation_quota,
        )

    st.markdown("#### 🧠 主线题材覆盖（可选）")
    if "longhubang_concept_candidates" not in st.session_state:
        st.session_state["longhubang_concept_candidates"] = []
    colm1, colm2 = st.columns([1, 2])
    with colm1:
        load_candidates_btn = st.button(
            "加载题材强度候选",
            help="按程序同口径计算题材强度排序，供主观多选覆盖",
        )
    with colm2:
        enable_manual_mainline_override = st.checkbox(
            "启用主观主线题材覆盖",
            value=False,
            help="默认关闭：使用程序排序；开启后使用你选择的题材（最多3个）替代程序主线",
        )
    if load_candidates_btn:
        with st.spinner("正在加载题材强度候选..."):
            engine_for_preview = _get_longhubang_engine(model=config.DEFAULT_MODEL_NAME)
            preview = engine_for_preview.get_concept_strength_candidates(
                date=preview_date,
                days=preview_days,
                score_pool_source=score_pool_source,
            )
        if preview.get("data_success"):
            st.session_state["longhubang_concept_candidates"] = preview.get("candidates", []) or []
            st.success(f"已加载 {len(st.session_state['longhubang_concept_candidates'])} 个题材候选")
        else:
            st.warning(f"题材候选加载失败：{preview.get('error', '无可用数据')}")

    concept_candidates = st.session_state.get("longhubang_concept_candidates", []) or []
    if concept_candidates:
        df_concepts = pd.DataFrame(concept_candidates)
        st.dataframe(
            df_concepts.head(15),
            column_config={
                "rank": st.column_config.NumberColumn("排序", format="%d", width="small"),
                "concept": st.column_config.TextColumn("题材", width="medium"),
                "strength_100": st.column_config.NumberColumn("强度分", format="%.2f"),
                "count": st.column_config.NumberColumn("出现次数", format="%d"),
                "pct_chg": st.column_config.NumberColumn("涨跌幅", format="%.2f"),
                "source": st.column_config.TextColumn("来源", width="small"),
            },
            hide_index=True,
            width='stretch',
        )
    manual_mainline_options = [str(x.get("concept", "")).strip() for x in concept_candidates if str(x.get("concept", "")).strip()]
    manual_mainline_concepts = st.multiselect(
        "主观最强题材（最多3个）",
        options=manual_mainline_options,
        default=[],
        disabled=not enable_manual_mainline_override or not bool(manual_mainline_options),
        help="建议先加载题材候选，再多选你主观认同的最强题材",
    )
    if len(manual_mainline_concepts) > 3:
        st.warning("最多选择3个题材，已自动截取前3个。")
        manual_mainline_concepts = manual_mainline_concepts[:3]

    st.markdown("#### 🗂 历史数据维护（2026）")
    backfill_col1, backfill_col2 = st.columns([1, 3])
    with backfill_col1:
        trigger_backfill_2026 = st.button(
            "立即补齐2026历史数据",
            help="手动触发：从2026-01-01补齐到目标交易日（增量循环补齐）",
        )
    with backfill_col2:
        st.caption("用于首次建库或接口异常后的补数修复，不影响当次分析参数。")
    if trigger_backfill_2026:
        with st.spinner("正在补齐2026历史数据，请稍候..."):
            engine_for_backfill = _get_longhubang_engine(model=config.DEFAULT_MODEL_NAME)
            backfill_result = engine_for_backfill.backfill_2026_history(
                end_date=preview_date if analysis_mode == "指定日期" else None,
                batch_days=20,
                max_rounds=40,
            )
        st.session_state["longhubang_history_backfill_result"] = backfill_result
        if backfill_result.get("data_success"):
            st.success(
                f"补齐完成：更新交易日 {backfill_result.get('updated_days', 0)}，"
                f"写入股票快照 {backfill_result.get('updated_stocks', 0)}，"
                f"待补 {backfill_result.get('pending_days', 0)}，"
                f"耗时 {backfill_result.get('elapsed_sec', 0)}s"
            )
        else:
            nearest = str(backfill_result.get("nearest_open_day", "") or "").strip()
            msg = f"补齐失败：{backfill_result.get('error', '未知错误')}"
            if nearest:
                msg += f"（最近交易日：{nearest}）"
            st.error(msg)
    last_backfill = st.session_state.get("longhubang_history_backfill_result", {}) or {}
    if last_backfill:
        st.caption(
            f"最近一次补齐：latest={last_backfill.get('latest_trade_date', '-')} | "
            f"updated_days={last_backfill.get('updated_days', 0)} | "
            f"pending={last_backfill.get('pending_days', 0)}"
        )
    
    # 分析按钮
    col1, col2 = st.columns([2, 2])
    
    with col1:
        analyze_button = st.button("🚀 开始分析", type="primary", width='stretch')
    
    with col2:
        if st.button("🔄 清除结果", width='stretch'):
            if 'longhubang_result' in st.session_state:
                del st.session_state.longhubang_result
            st.success("已清除分析结果")
            st.rerun()
    
    st.markdown("---")
    
    # 开始分析
    if analyze_button:
        # 清除之前的结果
        if 'longhubang_result' in st.session_state:
            del st.session_state.longhubang_result
        
        # 准备参数（使用.env中配置的默认模型）
        if analysis_mode == "指定日期":
            date_str = selected_date.strftime('%Y-%m-%d')
            run_longhubang_analysis(
                date=date_str,
                days=3,
                score_pool_source=score_pool_source,
                enable_9d_trend_overlay=enable_9d_trend_overlay,
                trend_overlay_scale=trend_overlay_scale,
                recommendation_count=recommendation_count,
                recommendation_limit_up_ratio=recommendation_limit_up_ratio,
                enable_recommendation_quota=enable_recommendation_quota,
                mainboard_limit_up_threshold_pct=mainboard_limit_up_threshold_pct,
                enable_manual_mainline_override=enable_manual_mainline_override,
                manual_mainline_concepts=manual_mainline_concepts,
            )
        else:
            run_longhubang_analysis(
                days=days,
                score_pool_source=score_pool_source,
                enable_9d_trend_overlay=enable_9d_trend_overlay,
                trend_overlay_scale=trend_overlay_scale,
                recommendation_count=recommendation_count,
                recommendation_limit_up_ratio=recommendation_limit_up_ratio,
                enable_recommendation_quota=enable_recommendation_quota,
                mainboard_limit_up_threshold_pct=mainboard_limit_up_threshold_pct,
                enable_manual_mainline_override=enable_manual_mainline_override,
                manual_mainline_concepts=manual_mainline_concepts,
            )
    
    # 显示分析结果
    if 'longhubang_result' in st.session_state:
        result = st.session_state.longhubang_result
        
        if result.get("success"):
            display_analysis_results(result)
        else:
            nearest = str(result.get("nearest_open_day", "") or "").strip()
            msg = f"❌ 分析失败: {result.get('error', '未知错误')}"
            if nearest:
                msg += f"（最近交易日：{nearest}）"
            st.error(msg)


def run_longhubang_analysis(
    model=None,
    date=None,
    days=3,
    score_pool_source="lhb_kpl",
    enable_9d_trend_overlay=True,
    trend_overlay_scale=1.0,
    recommendation_count=8,
    recommendation_limit_up_ratio=0.4,
    enable_recommendation_quota=True,
    mainboard_limit_up_threshold_pct=6.0,
    enable_manual_mainline_override=False,
    manual_mainline_concepts=None,
):
    """运行龙虎榜分析"""
    import config
    model = model or config.DEFAULT_MODEL_NAME
    
    # 进度显示
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        status_text.text("🚀 初始化分析引擎...")
        progress_bar.progress(5)
        
        engine = _get_longhubang_engine(model=model)
        
        status_text.text("📊 正在获取龙虎榜数据...")
        progress_bar.progress(15)
        
        # 运行分析
        result = engine.run_comprehensive_analysis(
            date=date,
            days=days,
            score_pool_source=score_pool_source,
            enable_9d_trend_overlay=bool(enable_9d_trend_overlay),
            trend_overlay_scale=float(trend_overlay_scale or 1.0),
            recommendation_count=int(recommendation_count or 8),
            recommendation_limit_up_ratio=float(recommendation_limit_up_ratio or 0.4),
            enable_recommendation_quota=bool(enable_recommendation_quota),
            mainboard_limit_up_threshold_pct=float(mainboard_limit_up_threshold_pct or 6.0),
            enable_manual_mainline_override=bool(enable_manual_mainline_override),
            manual_mainline_concepts=list(manual_mainline_concepts or []),
        )
        
        progress_bar.progress(90)
        
        if result.get("success"):
            # 保存结果
            st.session_state.longhubang_result = result
            if result.get("concept_strength_candidates"):
                st.session_state["longhubang_concept_candidates"] = result.get("concept_strength_candidates", [])
            
            progress_bar.progress(100)
            status_text.text("✅ 分析完成！")
            
            time.sleep(1)
            status_text.empty()
            progress_bar.empty()
            
            # 自动刷新显示结果
            st.rerun()
        else:
            nearest = str(result.get("nearest_open_day", "") or "").strip()
            msg = f"❌ 分析失败: {result.get('error', '未知错误')}"
            if nearest:
                msg += f"（最近交易日：{nearest}）"
            st.error(msg)
    
    except Exception as e:
        st.error(f"❌ 分析过程出错: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
    finally:
        progress_bar.empty()
        status_text.empty()


def display_analysis_results(result):
    """显示分析结果"""
    
    st.success("✅ 龙虎榜分析完成！")
    st.info(f"📅 分析时间: {result.get('timestamp', 'N/A')}")
    history_sync = result.get("history_sync", {}) or {}
    if history_sync:
        st.caption(
            f"历史统计同步(2026)：更新交易日 {history_sync.get('updated_days', 0)}，"
            f"待补 {history_sync.get('pending_days', 0)}，"
            f"最新 {history_sync.get('latest_trade_date', '-')}"
        )
    
    # 数据概况
    data_info = result.get('data_info', {})
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("龙虎榜记录", f"{data_info.get('total_records', 0)} 条")
    
    with col2:
        st.metric("涉及股票", f"{data_info.get('total_stocks', 0)} 只")
    
    with col3:
        st.metric("涉及游资", f"{data_info.get('total_youzi', 0)} 个")
    
    with col4:
        recommended = result.get('recommended_stocks', [])
        st.metric("推荐股票", f"{len(recommended)} 只", delta="AI筛选")
    
    # PDF导出功能
    display_pdf_export_section(result)
    
    st.markdown("---")
    
    # 创建子标签页
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏆 AI评分排名",
        "🎯 推荐股票",
        "🤖 AI分析师报告",
        "📊 数据详情",
        "📈 可视化图表"
    ])
    
    with tab1:
        display_scoring_ranking(result)
    
    with tab2:
        display_recommended_stocks(result)
    
    with tab3:
        display_agents_reports(result)
    
    with tab4:
        display_data_details(result)
    
    with tab5:
        display_visualizations(result)


def _format_concept_source_label(concept_source: str) -> str:
    """将后端概念来源标识转换为前端友好文案。"""
    source = str(concept_source or "").strip().lower()
    if source == "dc_member":
        return "东方财富概念成分（dc_member + dc_index）"
    if source == "dc":
        return "东方财富概念成分（dc_concept_cons）"
    if source == "kpl":
        return "开盘啦概念成分（kpl_concept_cons，回退）"
    if source == "kpl_list":
        return "开盘啦热度题材（kpl_list，当日统计）"
    if source == "limit_list_ths":
        return "同花顺涨停池题材（limit_list_ths，当日兜底）"
    if source == "kpl_list+limit_list_ths":
        return "开盘啦+同花顺融合题材（kpl板块骨架 + limit_list_ths.lu_desc增强）"
    if source:
        return source
    return "未知"

def _is_st_theme(theme: str) -> bool:
    text = str(theme or "").strip().upper()
    if not text:
        return False
    if "ST板块" in text or "*ST" in text or " ST " in f" {text} ":
        return True
    return text.startswith("ST")

def _get_kpl_themes_without_st(kpl_summary: dict, limit: int = 0) -> list:
    themes = (kpl_summary or {}).get("top_themes_today", []) or []
    filtered = []
    for item in themes:
        theme = str((item or {}).get("theme", "") or "").strip()
        if not theme or _is_st_theme(theme):
            continue
        filtered.append(
            {
                "theme": theme,
                "count": int((item or {}).get("count", 0) or 0),
            }
        )
    if limit and limit > 0:
        return filtered[:limit]
    return filtered

def _build_kpl_mainline_text(kpl_summary: dict, limit: int = 0) -> str:
    themes = _get_kpl_themes_without_st(kpl_summary, limit=limit)
    if not themes:
        return ""
    return "、".join([f"{x.get('theme', '')}({x.get('count', 0)})" for x in themes if x.get("theme")])


def display_scoring_ranking(result):
    """显示AI智能评分排名"""
    
    st.subheader("🏆 AI智能评分排名")
    
    scoring_df = result.get('scoring_ranking')
    concept_rotation = result.get('concept_rotation', {})
    mainline_dual_track = result.get("mainline_dual_track", {}) or {}
    mainline_info = result.get("mainline_selection_info", {}) or {}
    limit_step_heat = result.get('limit_step_heat', {})
    ths_hot_heat = result.get('ths_hot_heat', {})
    kpl_list_heat = result.get('kpl_list_heat', {})
    
    if scoring_df is None or (hasattr(scoring_df, 'empty') and scoring_df.empty):
        st.warning("暂无评分数据")
        return

    # 强势概念与轮动摘要（优先使用开盘啦热度，过滤ST题材）
    kpl_mainline_shown = False
    if isinstance(kpl_list_heat, dict) and kpl_list_heat.get("data_success"):
        kpl_summary = kpl_list_heat.get("kpl_summary", {}) or {}
        all_theme_text = _build_kpl_mainline_text(kpl_summary, limit=0)
        strongest_themes = _get_kpl_themes_without_st(kpl_summary, limit=1)
        strongest_theme = strongest_themes[0].get("theme", "N/A") if strongest_themes else "N/A"
        strongest_count = strongest_themes[0].get("count", 0) if strongest_themes else 0
        if all_theme_text:
            kpl_mainline_shown = True
            st.markdown("### 🔥 概念主线（Tushare）")
            st.info(
                f"最强题材：**{strongest_theme}**（出现次数：{strongest_count}） | "
                f"今日题材（过滤ST）：**{all_theme_text}** | "
                f"数据源：**开盘啦热度（kpl_list）**"
            )

    if not kpl_mainline_shown and isinstance(concept_rotation, dict) and concept_rotation.get("data_success"):
        strongest = concept_rotation.get("strongest_today", {})
        rotation_summary = concept_rotation.get("rotation_summary", {})
        concept_source = concept_rotation.get("concept_source") or rotation_summary.get("concept_source", "")
        concept_source_label = _format_concept_source_label(concept_source)
        st.markdown("### 🔥 概念主线（Tushare）")
        st.info(
            f"今日最强概念：**{strongest.get('concept', 'N/A')}** "
            f"（关联个股数：{strongest.get('stock_count', strongest.get('limit_up_count', 0))}，"
            f"hot_num：{strongest.get('hot_num_total', 0)}） | "
            f"资金动向：**{rotation_summary.get('capital_flow_signal', 'N/A')}** | "
            f"数据源：**{concept_source_label}**"
        )
    full_market_track = (mainline_dual_track.get("full_market", {}) or {})
    candidate_track = (mainline_dual_track.get("candidate_pool", {}) or {})
    if isinstance(full_market_track, dict) and full_market_track.get("data_success"):
        full_strong = (full_market_track.get("strongest_today", {}) or {}).get("concept", "N/A")
        cand_strong = "N/A"
        cand_cnt = 0
        if isinstance(candidate_track, dict) and candidate_track.get("data_success"):
            cand_strong = (candidate_track.get("strongest_today", {}) or {}).get("concept", "N/A")
            cand_cnt = int((candidate_track.get("strongest_today", {}) or {}).get("stock_count", 0) or 0)
        st.caption(
            f"主线双轨：全市场最强={full_strong} | 候选池最强={cand_strong}（候选命中{cand_cnt}）"
        )
    
    # 连板晋级热度
    if isinstance(limit_step_heat, dict) and limit_step_heat.get("data_success"):
        step_summary = limit_step_heat.get("market_heat_summary", {})
        avg_advance = step_summary.get("avg_advance_count", step_summary.get("avg_promotion_count", 0))
        latest_advance = step_summary.get("latest_advance_count", 0)
        strongest_step_concepts = limit_step_heat.get("strongest_step_concepts", [])[:5]
        strongest_step_concepts_text = "、".join(
            [f"{x.get('concept', '')}({x.get('score', 0)})" for x in strongest_step_concepts if x.get("concept")]
        ) or "暂无"
        st.markdown("### 🚀 连板晋级热度（Tushare）")
        st.info(
            f"市场热度：**{step_summary.get('heat_level', 'N/A')}** | "
            f"平均连板高度：**{step_summary.get('avg_max_step', 0)}** | "
            f"平均晋级个数：**{avg_advance}** | "
            f"最新交易日晋级：**{latest_advance}** | "
            f"梯队最强概念：**{strongest_step_concepts_text}**"
        )

    # 同花顺A股热榜热度
    if isinstance(ths_hot_heat, dict) and ths_hot_heat.get("data_success"):
        hot_summary = ths_hot_heat.get("hot_summary", {})
        st.markdown("### 📱 同花顺A股热榜（Tushare）")
        st.info(
            f"覆盖天数：**{hot_summary.get('days', 0)}** | "
            f"跟踪股票：**{hot_summary.get('tracked_stocks', 0)}** 只"
        )

    if isinstance(kpl_list_heat, dict) and kpl_list_heat.get("data_success"):
        kpl_summary = kpl_list_heat.get("kpl_summary", {})
        top_theme_text = _build_kpl_mainline_text(kpl_summary, limit=5) or "暂无"
        strongest_themes = _get_kpl_themes_without_st(kpl_summary, limit=1)
        strongest_theme = strongest_themes[0].get("theme", "N/A") if strongest_themes else "N/A"
        st.markdown("### 🧭 开盘啦热度（Tushare）")
        st.info(
            f"覆盖天数：**{kpl_summary.get('days', 0)}** | "
            f"评分池命中：**{kpl_summary.get('tracked_stocks', 0)}** 只 | "
            f"全市场样本：**{kpl_summary.get('tracked_stocks_all', 0)}** 只 | "
            f"最热题材（过滤ST）：**{strongest_theme}** | "
            f"今日题材TOP（过滤ST）：**{top_theme_text}**"
        )
    mode = str(mainline_info.get("mode", "auto") or "auto").strip().lower()
    if mode == "manual":
        manual_list = mainline_info.get("effective_concepts", []) or []
        manual_text = "、".join([str(x) for x in manual_list if str(x).strip()]) or "暂无"
        st.caption(f"当前主线来源：用户主观覆盖（手动） | 生效题材：{manual_text}")
    else:
        st.caption("当前主线来源：程序自动排序（默认）")
    
    # 评分说明
    with st.expander("📖 评分维度说明", expanded=False):
        st.markdown("""
        ### 📊 AI智能评分体系 (总分100分)
        
        #### 1️⃣ 买入资金含金量 (0-22分)
        - **顶级游资**（赵老哥、章盟主、92科比等）：每个 +10分
        - **知名游资**（深股通、中信证券等）：每个 +5分
        - **普通游资**：每个 +1.5分
        
        #### 2️⃣ 净买入额评分 (0-18分)
        - 净流入 < 1000万：0-10分（基础分段）
        - 净流入 1000-5000万：10-18分（基础分段）
        - 净流入 5000万-1亿：18-22分（基础分段）
        - 净流入 > 1亿：22-25分（基础分段）
        - 以上分段结果按比例映射至0-18分
        
        #### 3️⃣ 卖出压力评分 (0-14分)
        - 卖出比例 0-10%：20分（基础分段，压力极小）
        - 卖出比例 10-30%：15-20分（基础分段，压力较小）
        - 卖出比例 30-50%：10-15分（基础分段，压力中等）
        - 卖出比例 50-80%：5-10分（基础分段，压力较大）
        - 卖出比例 > 80%：0-5分（基础分段，压力极大）
        - 以上分段结果按比例映射至0-14分
        
        #### 4️⃣ 机构共振评分 (0-15分)
        - **机构+游资共振**：15分 ⭐（最强信号）
        - 仅机构买入：8-12分
        - 仅游资买入：5-10分
        
        #### 5️⃣ 其他加分项 (0-4分)
        - **主力集中度**、**热门概念**、**连续上榜**、**买卖比例**

        #### 6️⃣ 板块轮动评分 (0-15分)
        - 基于 **Tushare `dc_member + dc_index`（失败回退 `dc_concept_cons` / `kpl_concept_cons`）** 的概念成分数据识别“概念主线最强概念”
        - 叠加 **Tushare `limit_step`** 提取“连板梯队最强概念”
        - 个股概念命中当日主线概念，获得更高分

        #### 7️⃣ 连板晋级评分 (0-2分)
        - 基于 **Tushare `limit_step`** 分析连板高度与晋级持续性

        #### 8️⃣ 同花顺热榜评分 (0-10分)
        - 基于 **Tushare `ths_hot`** 的A股热榜排名与在榜天数

        #### ⚠️ 涨跌幅说明
        - **涨跌幅信息仅用于提示展示，不参与评分**
        
        ---
        
        💡 **评分越高，表示该股票受到资金青睐程度越高！**  
        ⚠️ **但仍需结合市场环境、技术面等因素综合判断！**
        """)
    
    st.markdown("---")
    
    # 显示TOP10评分表格
    st.markdown("### 🥇 TOP10 综合评分排名")
    
    # 兼容历史数据与类型统一，避免 Arrow 序列化错误
    if isinstance(scoring_df, list):
        scoring_df = pd.DataFrame(scoring_df)

    kline_9d = result.get("kline_9d_trend", {}) or {}
    overlay_cfg = result.get("kline_9d_overlay_config", {}) or {}
    overlay_scale = float(overlay_cfg.get("scale", 1.0) or 1.0)
    if isinstance(kline_9d, dict) and kline_9d.get("data_success"):
        trend_summary = kline_9d.get("trend_summary", {}) or {}
        stage_dist = trend_summary.get("stage_distribution", {}) or {}
        stage_text = "、".join([f"{k}:{v}" for k, v in list(stage_dist.items())[:4]]) or "暂无"
        st.caption(
            f"9日趋势微调已启用：样本 {trend_summary.get('tracked_stocks', 0)} 只 | "
            f"阶段分布 {stage_text} | 系数 {overlay_scale:.1f} | 仅做修正（单股最高约±10分）"
        )

    numeric_cols = ['排名','综合评分','资金含金量','净买入额','卖出压力','机构共振','加分项','板块轮动','连板晋级','同花顺热榜','硬性扣分','连板高度','顶级游资','买方数','净流入','趋势微调','9日涨幅%','P1增强','机构净额','主力净流','封板质量','游资信号']
    for col in numeric_cols:
        if col in scoring_df.columns:
            scoring_df[col] = pd.to_numeric(scoring_df[col], errors='coerce')

    text_cols = ['股票名称','股票代码','机构参与','主线匹配','热榜最佳','置信度']
    for col in text_cols:
        if col in scoring_df.columns:
            scoring_df[col] = scoring_df[col].astype(str)

    top10_df = scoring_df.head(10).copy()
    if '排名' in top10_df.columns:
        top10_df['排名'] = pd.to_numeric(top10_df['排名'], errors='coerce').fillna(0).astype(int)
    
    # 格式化显示
    st.dataframe(
        top10_df,
        column_config={
            "排名": st.column_config.NumberColumn("排名", format="%d", width="small"),
            "股票名称": st.column_config.TextColumn("股票名称", width="medium"),
            "股票代码": st.column_config.TextColumn("代码", width="small"),
            "综合评分": st.column_config.NumberColumn(
                "综合评分",
                format="%.1f",
                help="总分100分"
            ),
            "资金含金量": st.column_config.ProgressColumn(
                "资金含金量",
                format="%d分",
                min_value=0,
                max_value=22
            ),
            "净买入额": st.column_config.ProgressColumn(
                "净买入额",
                format="%d分",
                min_value=0,
                max_value=18
            ),
            "卖出压力": st.column_config.ProgressColumn(
                "卖出压力",
                format="%d分",
                min_value=0,
                max_value=14
            ),
            "机构共振": st.column_config.ProgressColumn(
                "机构共振",
                format="%d分",
                min_value=0,
                max_value=15
            ),
            "加分项": st.column_config.ProgressColumn(
                "加分项",
                format="%d分",
                min_value=0,
                max_value=4
            ),
            "板块轮动": st.column_config.ProgressColumn(
                "板块轮动",
                format="%.1f分",
                min_value=0,
                max_value=15
            ),
            "连板晋级": st.column_config.ProgressColumn(
                "连板晋级",
                format="%.1f分",
                min_value=0,
                max_value=2
            ),
            "同花顺热榜": st.column_config.ProgressColumn(
                "同花顺热榜",
                format="%.1f分",
                min_value=0,
                max_value=10
            ),
            "硬性扣分": st.column_config.NumberColumn("硬性扣分", format="%.2f"),
            "顶级游资": st.column_config.NumberColumn("顶级游资", format="%d家"),
            "买方数": st.column_config.NumberColumn("买方数", format="%d家"),
            "机构参与": st.column_config.TextColumn("机构参与"),
            "置信度": st.column_config.TextColumn("置信度"),
            "净流入": st.column_config.NumberColumn("净流入(元)", format="%.2f"),
            "主线匹配": st.column_config.TextColumn("主线匹配", width="medium"),
            "连板高度": st.column_config.NumberColumn("连板高度", format="%d板"),
            "热榜最佳": st.column_config.TextColumn("热榜最佳"),
            "9日趋势": st.column_config.TextColumn("9日趋势"),
            "趋势微调": st.column_config.NumberColumn("趋势微调", format="%.2f"),
            "9日涨幅%": st.column_config.NumberColumn("9日涨幅%", format="%.2f"),
        },
        hide_index=True,
        width='stretch'
    )
    
    # 一键批量分析功能
    st.markdown("---")
    
    col_batch1, col_batch2, col_batch3 = st.columns([2, 1, 1])
    with col_batch1:
        st.markdown("#### 🚀 批量深度分析")
        st.caption("对TOP10股票进行完整的AI团队分析，获取投资评级与次日策略建议")
    
    with col_batch2:
        batch_count = st.selectbox(
            "分析数量",
            options=[3, 5, 10],
            index=0,
            help="选择分析前N只股票",
            key="batch_count_selector"
        )
        # 同步更新session_state中的batch_count
        st.session_state.batch_count = batch_count
    
    with col_batch3:
        st.write("")  # 占位
        if st.button("🚀 开始批量分析", type="primary", width='stretch'):
            # 提取股票代码
            stock_codes = top10_df.head(batch_count)['股票代码'].tolist()
            
            # 存储到session_state，触发批量分析
            st.session_state.longhubang_batch_codes = stock_codes
            st.session_state.longhubang_batch_trigger = True
            st.rerun()
    
    st.markdown("---")
    
    # 评分分布图表
    st.markdown("### 📊 评分分布可视化")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 综合评分柱状图
        fig1 = px.bar(
            top10_df,
            x='股票名称',
            y='综合评分',
            title='TOP10 综合评分对比',
            text='综合评分',
            color='综合评分',
            color_continuous_scale='RdYlGn'
        )
        fig1.update_traces(texttemplate='%{text:.1f}分', textposition='outside')
        fig1.update_layout(
            xaxis_tickangle=-45,
            showlegend=False,
            height=400
        )
        st.plotly_chart(fig1, config={'displayModeBar': False}, use_container_width=True)
    
    with col2:
        # 八维评分雷达图（显示批量分析数量的股票）
        if len(top10_df) > 0:
            display_count = min(5, len(top10_df))
            
            fig2 = go.Figure()
            
            # 为每只股票添加雷达图
            colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']
            for i in range(display_count):
                stock = top10_df.iloc[i]
                
                fig2.add_trace(go.Scatterpolar(
                    r=[
                        stock.get('资金含金量', 0) / 22 * 100,
                        stock.get('净买入额', 0) / 18 * 100,
                        stock.get('卖出压力', 0) / 14 * 100,
                        stock.get('机构共振', 0) / 15 * 100,
                        stock.get('加分项', 0) / 4 * 100,
                        stock.get('板块轮动', 0) / 15 * 100,
                        stock.get('连板晋级', 0) / 2 * 100,
                        stock.get('同花顺热榜', 0) / 10 * 100
                    ],
                    theta=['资金含金量', '净买入额', '卖出压力', '机构共振', '加分项', '板块轮动', '连板晋级', '同花顺热榜'],
                    fill='toself',
                    name=f"{stock['股票名称']}",
                    line_color=colors[i % len(colors)],
                    fillcolor=colors[i % len(colors)],
                    opacity=0.6
                ))
            
            fig2.update_layout(
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        range=[0, 100]
                    )
                ),
                showlegend=True,
                title=f"🏆 TOP{display_count} 八维评分对比",
                height=400,
                legend=dict(
                    orientation="h",
                    yanchor="auto",
                    y=-0.2,
                    xanchor="center",
                    x=0.5
                )
            )
            st.plotly_chart(fig2, config={'displayModeBar': False}, use_container_width=True)
    
    st.markdown("---")
    
    # 完整排名表格
    st.markdown("### 📋 完整评分排名")
    
    st.dataframe(
        scoring_df,
        column_config={
            "排名": st.column_config.NumberColumn("排名", format="%d", width="small"),
            "股票名称": st.column_config.TextColumn("股票名称"),
            "股票代码": st.column_config.TextColumn("代码"),
            "综合评分": st.column_config.NumberColumn("综合评分", format="%.1f"),
            "板块轮动": st.column_config.NumberColumn("板块轮动", format="%.1f"),
            "连板晋级": st.column_config.NumberColumn("连板晋级", format="%.1f"),
            "同花顺热榜": st.column_config.NumberColumn("同花顺热榜", format="%.1f"),
            "硬性扣分": st.column_config.NumberColumn("硬性扣分", format="%.2f"),
            "顶级游资": st.column_config.NumberColumn("顶级游资", format="%d家"),
            "买方数": st.column_config.NumberColumn("买方数", format="%d家"),
            "机构参与": st.column_config.TextColumn("机构"),
            "置信度": st.column_config.TextColumn("置信度"),
            "净流入": st.column_config.NumberColumn("净流入(元)", format="%.2f"),
            "主线匹配": st.column_config.TextColumn("主线匹配"),
            "连板高度": st.column_config.NumberColumn("连板高度", format="%d板"),
            "热榜最佳": st.column_config.TextColumn("热榜最佳"),
            "9日趋势": st.column_config.TextColumn("9日趋势"),
            "趋势微调": st.column_config.NumberColumn("趋势微调", format="%.2f"),
            "9日涨幅%": st.column_config.NumberColumn("9日涨幅%", format="%.2f"),
        },
        hide_index=True,
        width='stretch'
    )


def display_recommended_stocks(result):
    """显示推荐股票"""
    
    st.subheader("🎯 AI推荐股票")
    
    recommended = result.get('recommended_stocks', [])
    
    if not recommended:
        st.warning("暂无推荐股票")
        return
    quota_cfg = result.get("recommendation_quota_config", {}) or {}
    rec_count = int(quota_cfg.get("recommendation_count", len(recommended)) or len(recommended))
    limit_ratio = float(quota_cfg.get("limit_up_ratio", 0.4) or 0.4)
    quota_enabled = bool(quota_cfg.get("enabled", True))
    threshold_cfg = result.get("limit_up_threshold_config", {}) or {}
    mainboard_pct = float(threshold_cfg.get("mainboard_pct", 6.0) or 6.0)
    st.caption(
        f"推荐分层配额：{'开启' if quota_enabled else '关闭'} | 总数 {rec_count} | "
        f"近涨停标签占比上限 {limit_ratio * 100:.0f}% | 主板阈值 {mainboard_pct:.1f}%"
    )
    candidate_pool = result.get("recommendation_candidate_pool", {}) or {}
    if candidate_pool:
        pool_total = int(candidate_pool.get("total", 0) or 0)
        pool_source = str(candidate_pool.get("source", "") or "")
        with st.expander("🧩 推荐候选池（调试）", expanded=False):
            st.caption(f"来源：{pool_source or '-'} | 规模：{pool_total} 只")
            pool_all = candidate_pool.get("all", []) or []
            pool_sample = candidate_pool.get("sample", []) or []
            if pool_all:
                st.caption(f"当前展示：全量 {len(pool_all)} 只（样本 {len(pool_sample)} 只）")
                df_pool = pd.DataFrame(pool_all)
                st.dataframe(
                    df_pool,
                    column_config={
                        "code": st.column_config.TextColumn("股票代码"),
                        "name": st.column_config.TextColumn("股票名称"),
                        "net_inflow": st.column_config.NumberColumn("净流入金额", format="%.2f"),
                        "pct_chg": st.column_config.NumberColumn("涨跌幅%", format="%.2f"),
                        "is_today_limit_up": st.column_config.CheckboxColumn("当日涨停"),
                        "is_limit_like_for_quota": st.column_config.CheckboxColumn("近涨停标签"),
                        "limit_quality_score": st.column_config.NumberColumn("封板质量", format="%.3f"),
                        "p1_score": st.column_config.NumberColumn("P1分", format="%.3f"),
                        "peer_rebound_score": st.column_config.NumberColumn("历史补涨分", format="%.2f"),
                        "peer_hist_win_rate": st.column_config.NumberColumn("历史胜率", format="%.3f"),
                        "peer_hist_avg_next_pct": st.column_config.NumberColumn("历史次日均涨%", format="%.3f"),
                        "data_quality_grade": st.column_config.TextColumn("数据质量"),
                        "theme_tokens": st.column_config.TextColumn("题材词"),
                    },
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.info("候选池样本为空")

    history_peer = result.get("history_peer_rebound", {}) or {}
    if history_peer.get("data_success"):
        with st.expander("🧪 历史统计驱动-同类低位补涨候选", expanded=False):
            active = history_peer.get("active_themes", []) or []
            st.caption(f"主线题材：{'、'.join(active[:8]) if active else '暂无'}")
            breakdown = history_peer.get("pool_source_breakdown", {}) or {}
            st.caption(
                f"候选池构成：原池 {int(breakdown.get('base_count', 0) or 0)}，"
                f"扩池 {int(breakdown.get('expanded_count', 0) or 0)}，"
                f"剔除 {int(breakdown.get('dropped_count', 0) or 0)}（无当日daily "
                f"{int(history_peer.get('dropped_no_daily_count', 0) or 0)}）"
            )
            df_peer = pd.DataFrame(history_peer.get("candidates", []) or [])
            if not df_peer.empty:
                st.dataframe(
                    df_peer,
                    column_config={
                        "code": st.column_config.TextColumn("股票代码"),
                        "name": st.column_config.TextColumn("股票名称"),
                        "pct_chg": st.column_config.NumberColumn("涨跌幅%", format="%.2f"),
                        "is_limit_like_for_quota": st.column_config.CheckboxColumn("近涨停标签"),
                        "theme_tokens": st.column_config.TextColumn("命中题材"),
                        "hist_win_rate": st.column_config.NumberColumn("历史胜率", format="%.3f"),
                        "hist_avg_next_pct": st.column_config.NumberColumn("历史次日均涨%", format="%.3f"),
                        "theme_strength_today": st.column_config.NumberColumn("当日题材强度", format="%.3f"),
                        "low_position_score": st.column_config.NumberColumn("低位分", format="%.3f"),
                        "peer_rebound_score": st.column_config.NumberColumn("补涨总分", format="%.2f"),
                        "is_peer_pool_expanded": st.column_config.CheckboxColumn("扩池来源"),
                    },
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.info("暂无历史补涨候选")
    
    st.info(f"💡 基于5位AI分析师的综合分析，系统识别出以下 **{len(recommended)}** 只潜力股票")
    
    # 创建DataFrame
    df_recommended = pd.DataFrame(recommended)
    if "is_today_limit_up" in df_recommended.columns:
        df_recommended["is_today_limit_up"] = df_recommended["is_today_limit_up"].apply(
            lambda x: "是" if bool(x) else "否"
        )
    
    # 显示表格
    st.dataframe(
        df_recommended,
        column_config={
            "rank": st.column_config.NumberColumn("排名", format="%d"),
            "code": st.column_config.TextColumn("股票代码"),
            "name": st.column_config.TextColumn("股票名称"),
            "net_inflow": st.column_config.NumberColumn("净流入金额", format="%.2f"),
            "is_today_limit_up": st.column_config.TextColumn("当日涨停"),
            "confidence": st.column_config.TextColumn("确定性"),
            "hold_period": st.column_config.TextColumn("持有周期"),
            "reason": st.column_config.TextColumn("推荐理由")
        },
        hide_index=True,
        width='stretch'
    )
    
    # 详细推荐理由
    st.markdown("### 📝 详细推荐理由")
    
    for stock in recommended[:5]:  # 只显示前5只
        with st.expander(f"**{stock.get('rank', '-')}. {stock.get('name', '-')} ({stock.get('code', '-')})**"):
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown(f"**推荐理由:** {stock.get('reason', '暂无')}")
                st.markdown(f"**净流入:** {stock.get('net_inflow', 0):,.2f} 元")
            
            with col2:
                st.markdown(f"**确定性:** {stock.get('confidence', '-')}")
                st.markdown(f"**持有周期:** {stock.get('hold_period', '-')}")


def display_agents_reports(result):
    """显示AI分析师报告"""
    
    st.subheader("🤖 AI分析师团队报告")
    
    agents_analysis = result.get('agents_analysis', {})
    
    if not agents_analysis:
        st.warning("暂无分析报告")
        return
    
    # 各分析师报告
    agent_info = {
        'youzi': {'title': '🎯 游资行为分析师', 'icon': '🎯'},
        'stock': {'title': '📈 个股潜力分析师', 'icon': '📈'},
        'theme': {'title': '🔥 题材追踪分析师', 'icon': '🔥'},
        'risk': {'title': '⚠️ 风险控制专家', 'icon': '⚠️'},
        'chief': {'title': '👔 首席策略师综合研判', 'icon': '👔'}
    }
    
    for agent_key, info in agent_info.items():
        agent_data = agents_analysis.get(agent_key, {})
        if agent_data:
            with st.expander(f"{info['icon']} {info['title']}", expanded=(agent_key == 'chief')):
                analysis = agent_data.get('analysis', '暂无分析')
                st.markdown(analysis)
                
                st.markdown(f"*{agent_data.get('agent_role', '')}*")
                st.caption(f"分析时间: {agent_data.get('timestamp', 'N/A')}")


def display_data_details(result):
    """显示数据详情"""
    
    st.subheader("📊 龙虎榜数据详情")
    
    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    
    # TOP游资
    if summary.get('top_youzi'):
        st.markdown("### 🏆 活跃游资 TOP10")
        
        youzi_data = [
            {'排名': idx, '游资名称': name, '净流入金额': amount}
            for idx, (name, amount) in enumerate(list(summary['top_youzi'].items())[:10], 1)
        ]
        df_youzi = pd.DataFrame(youzi_data)
        
        st.dataframe(
            df_youzi,
            column_config={
                "排名": st.column_config.NumberColumn("排名", format="%d"),
                "游资名称": st.column_config.TextColumn("游资名称"),
                "净流入金额": st.column_config.NumberColumn("净流入金额(元)", format="%.2f")
            },
            hide_index=True,
            width='stretch'
        )
    
    # TOP股票
    if summary.get('top_stocks'):
        st.markdown("### 📈 资金净流入 TOP20 股票")
        
        df_stocks = pd.DataFrame(summary['top_stocks'][:20])
        
        st.dataframe(
            df_stocks,
            column_config={
                "code": st.column_config.TextColumn("股票代码"),
                "name": st.column_config.TextColumn("股票名称"),
                "net_inflow": st.column_config.NumberColumn("净流入金额(元)", format="%.2f")
            },
            hide_index=True,
            width='stretch'
        )
    
    # 热门概念
    if summary.get('hot_concepts'):
        st.markdown("### 🔥 热门概念 TOP20")
        
        concepts_data = [
            {'排名': idx, '概念名称': concept, '出现次数': count}
            for idx, (concept, count) in enumerate(list(summary['hot_concepts'].items())[:20], 1)
        ]
        df_concepts = pd.DataFrame(concepts_data)
        
        st.dataframe(
            df_concepts,
            column_config={
                "排名": st.column_config.NumberColumn("排名", format="%d"),
                "概念名称": st.column_config.TextColumn("概念名称"),
                "出现次数": st.column_config.NumberColumn("出现次数", format="%d")
            },
            hide_index=True,
            width='stretch'
        )


def display_visualizations(result):
    """显示可视化图表"""
    
    st.subheader("📈 数据可视化")
    
    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    
    # 资金流向图表
    if summary.get('top_stocks'):
        st.markdown("### 💰 TOP20 股票资金净流入")
        
        stocks = summary['top_stocks'][:20]
        df_chart = pd.DataFrame(stocks)
        
        fig = px.bar(
            df_chart,
            x='name',
            y='net_inflow',
            title='TOP20 股票资金净流入金额',
            labels={'name': '股票名称', 'net_inflow': '净流入金额(元)'}
        )
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, config={'displayModeBar': False}, use_container_width=True)
    
    # 热门概念图表
    if summary.get('hot_concepts'):
        st.markdown("### 🔥 热门概念分布")
        
        concepts = list(summary['hot_concepts'].items())[:15]
        df_concepts = pd.DataFrame(concepts, columns=['概念', '次数'])
        
        fig = px.pie(
            df_concepts,
            values='次数',
            names='概念',
            title='热门概念出现次数分布'
        )
        st.plotly_chart(fig, config={'displayModeBar': False}, use_container_width=True)


def display_pdf_export_section(result):
    """显示PDF导出功能"""
    
    st.markdown("### 📄 导出报告")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.info("💡 点击按钮生成并下载专业分析报告")
    
    with col2:
        if st.button("📥 生成PDF", type="primary", width='stretch'):
            with st.spinner("正在生成PDF报告..."):
                try:
                    generator = LonghubangPDFGenerator()
                    pdf_path = generator.generate_pdf(result)
                    
                    # 读取PDF文件
                    with open(pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                    
                    # 提供下载
                    st.download_button(
                        label="📥 下载PDF报告",
                        data=pdf_bytes,
                        file_name=f"智瞰龙虎报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                        mime="application/pdf",
                        width='stretch'
                    )
                    
                    st.success("✅ PDF报告生成成功！")
                
                except Exception as e:
                    st.error(f"❌ PDF生成失败: {str(e)}")
    
    with col3:
        if st.button("📝 生成Markdown", type="secondary", width='stretch'):
            with st.spinner("正在生成Markdown报告..."):
                try:
                    # 生成Markdown内容
                    markdown_content = generate_markdown_report(result)
                    
                    # 提供下载
                    st.download_button(
                        label="📥 下载Markdown报告",
                        data=markdown_content,
                        file_name=f"智瞰龙虎报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                        mime="text/markdown",
                        width='stretch'
                    )
                    
                    st.success("✅ Markdown报告生成成功！")
                
                except Exception as e:
                    st.error(f"❌ Markdown生成失败: {str(e)}")


def generate_markdown_report(result_data: dict) -> str:
    """生成龙虎榜分析Markdown报告"""
    
    # 获取当前时间
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    
    # 标题页
    markdown_content = f"""# 智瞰龙虎榜分析报告

**AI驱动的龙虎榜多维度分析系统**

---

## 📊 报告概览

- **生成时间**: {current_time}
- **数据记录**: {result_data.get('data_info', {}).get('total_records', 0)} 条
- **涉及股票**: {result_data.get('data_info', {}).get('total_stocks', 0)} 只
- **涉及游资**: {result_data.get('data_info', {}).get('total_youzi', 0)} 个
- **AI分析师**: 5位专业分析师团队
- **分析模型**: DeepSeek AI Multi-Agent System

> ⚠️ 本报告由AI系统基于龙虎榜公开数据自动生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。

---

## 📈 数据概况

本次分析共涵盖 **{result_data.get('data_info', {}).get('total_records', 0)}** 条龙虎榜记录，
涉及 **{result_data.get('data_info', {}).get('total_stocks', 0)}** 只股票和 
**{result_data.get('data_info', {}).get('total_youzi', 0)}** 个游资席位。

"""
    
    # 资金概况
    summary = result_data.get('data_info', {}).get('summary', {})
    markdown_content += f"""
### 💰 资金概况

- **总买入金额**: {summary.get('total_buy_amount', 0):,.2f} 元
- **总卖出金额**: {summary.get('total_sell_amount', 0):,.2f} 元
- **净流入金额**: {summary.get('total_net_inflow', 0):,.2f} 元

"""
    
    # TOP游资
    if summary.get('top_youzi'):
        markdown_content += "### 🏆 活跃游资 TOP10\n\n| 排名 | 游资名称 | 净流入金额(元) |\n|------|----------|---------------|\n"
        for idx, (name, amount) in enumerate(list(summary['top_youzi'].items())[:10], 1):
            markdown_content += f"| {idx} | {name} | {amount:,.2f} |\n"
        markdown_content += "\n"
    
    # TOP股票
    if summary.get('top_stocks'):
        markdown_content += "### 📈 资金净流入 TOP20 股票\n\n| 排名 | 股票代码 | 股票名称 | 净流入金额(元) |\n|------|----------|----------|---------------|\n"
        for idx, stock in enumerate(summary['top_stocks'][:20], 1):
            markdown_content += f"| {idx} | {stock['code']} | {stock['name']} | {stock['net_inflow']:,.2f} |\n"
        markdown_content += "\n"
    
    # 热门概念
    if summary.get('hot_concepts'):
        markdown_content += "### 🔥 热门概念 TOP15\n\n"
        for idx, (concept, count) in enumerate(list(summary['hot_concepts'].items())[:15], 1):
            markdown_content += f"{idx}. {concept} ({count}次)  \n"
        markdown_content += "\n"
    
    # 推荐股票
    recommended = result_data.get('recommended_stocks', [])
    if recommended:
        markdown_content += f"""
## 🎯 AI推荐股票

基于5位AI分析师的综合分析，系统识别出以下 **{len(recommended)}** 只潜力股票，
这些股票在资金流向、游资关注度、题材热度等多个维度表现突出。

### 推荐股票清单

| 排名 | 股票代码 | 股票名称 | 净流入金额 | 确定性 | 持有周期 |
|------|----------|----------|------------|--------|----------|
"""
        for stock in recommended[:10]:
            markdown_content += f"| {stock.get('rank', '-')} | {stock.get('code', '-')} | {stock.get('name', '-')} | {stock.get('net_inflow', 0):,.0f} | {stock.get('confidence', '-')} | {stock.get('hold_period', '-')} |\n"
        
        markdown_content += "\n### 推荐理由详解\n\n"
        for stock in recommended[:5]:  # 只详细展示前5只
            markdown_content += f"**{stock.get('rank', '-')}. {stock.get('name', '-')} ({stock.get('code', '-')})**\n\n"
            markdown_content += f"- 推荐理由: {stock.get('reason', '暂无')}\n"
            markdown_content += f"- 确定性: {stock.get('confidence', '-')}\n"
            markdown_content += f"- 持有周期: {stock.get('hold_period', '-')}\n\n"
    
    # AI分析师报告
    agents_analysis = result_data.get('agents_analysis', {})
    if agents_analysis:
        markdown_content += "## 🤖 AI分析师报告\n\n"
        markdown_content += "本报告由5位AI专业分析师从不同维度进行分析，综合形成投资建议：\n\n"
        markdown_content += "- **游资行为分析师** - 分析游资操作特征和意图\n"
        markdown_content += "- **个股潜力分析师** - 挖掘次日大概率上涨的股票\n"
        markdown_content += "- **题材追踪分析师** - 识别热点题材和轮动机会\n"
        markdown_content += "- **风险控制专家** - 识别高风险股票和市场陷阱\n"
        markdown_content += "- **首席策略师** - 综合研判并给出最终建议\n\n"
        
        agent_titles = {
            'youzi': '游资行为分析师',
            'stock': '个股潜力分析师',
            'theme': '题材追踪分析师',
            'risk': '风险控制专家',
            'chief': '首席策略师综合研判'
        }
        
        for agent_key, agent_title in agent_titles.items():
            agent_data = agents_analysis.get(agent_key, {})
            if agent_data:
                markdown_content += f"### {agent_title}\n\n"
                analysis_text = agent_data.get('analysis', '暂无分析')
                # 处理文本中的换行
                analysis_text = analysis_text.replace('\n', '\n\n')
                markdown_content += f"{analysis_text}\n\n"
    
    markdown_content += """
---

*报告由智瞰龙虎AI系统自动生成*
"""
    
    return markdown_content


def display_history_tab():
    """显示历史报告标签页（增强版）"""
    
    st.subheader("📚 历史分析报告")
    
    try:
        engine = _get_longhubang_engine()
        reports_df = engine.get_historical_reports(limit=50)
        
        if reports_df.empty:
            st.info("暂无历史报告")
            return
        
        st.info(f"💾 共有 {len(reports_df)} 条历史报告")
        
        # 显示报告列表
        st.markdown("### 📋 报告列表")
        
        # 为每条报告创建展开面板
        for idx, row in reports_df.iterrows():
            report_id = row['id']
            analysis_date = row['analysis_date']
            data_date_range = row['data_date_range']
            summary = row['summary']
            
            # 创建展开面板
            with st.expander(
                f"📄 报告 #{report_id} | {analysis_date} | 数据范围: {data_date_range}",
                expanded=False
            ):
                # 获取完整报告详情
                report_detail = engine.get_report_detail(report_id)
                
                if not report_detail:
                    st.warning("无法加载报告详情")
                    continue
                
                # 显示摘要
                st.markdown("#### 📝 报告摘要")
                st.info(summary)
                
                st.markdown("---")
                
                # 显示推荐股票
                recommended_stocks = report_detail.get('recommended_stocks', [])
                if recommended_stocks:
                    st.markdown(f"#### 🎯 推荐股票 ({len(recommended_stocks)}只)")
                    
                    # 创建DataFrame显示
                    df_stocks = pd.DataFrame(recommended_stocks)
                    st.dataframe(
                        df_stocks,
                        column_config={
                            "rank": st.column_config.NumberColumn("排名", format="%d"),
                            "code": st.column_config.TextColumn("代码"),
                            "name": st.column_config.TextColumn("名称"),
                            "net_inflow": st.column_config.NumberColumn("净流入", format="%.2f"),
                            "reason": st.column_config.TextColumn("推荐理由"),
                            "confidence": st.column_config.TextColumn("确定性"),
                            "hold_period": st.column_config.TextColumn("持有周期")
                        },
                        hide_index=True,
                        width='stretch'
                    )
                
                st.markdown("---")
                
                # 尝试解析完整分析内容
                analysis_content_parsed = report_detail.get('analysis_content_parsed')
                
                if analysis_content_parsed and isinstance(analysis_content_parsed, dict):
                    # 显示AI分析师团队报告
                    agents_analysis = analysis_content_parsed.get('agents_analysis', {})
                    
                    if agents_analysis:
                        st.markdown("#### 🤖 AI分析师团队报告")
                        
                        agent_info = {
                            'youzi': {'title': '🎯 游资行为分析师', 'icon': '🎯'},
                            'stock': {'title': '📈 个股潜力分析师', 'icon': '📈'},
                            'theme': {'title': '🔥 题材追踪分析师', 'icon': '🔥'},
                            'risk': {'title': '⚠️ 风险控制专家', 'icon': '⚠️'},
                            'chief': {'title': '👔 首席策略师', 'icon': '👔'}
                        }
                        
                        for agent_key, info in agent_info.items():
                            agent_data = agents_analysis.get(agent_key, {})
                            if agent_data:
                                with st.expander(f"{info['icon']} {info['title']}", expanded=False):
                                    analysis = agent_data.get('analysis', '暂无分析')
                                    st.markdown(analysis)
                                    st.caption(f"分析时间: {agent_data.get('timestamp', 'N/A')}")
                    
                    # 显示AI评分排名
                    scoring_ranking = analysis_content_parsed.get('scoring_ranking', [])
                    if scoring_ranking:
                        st.markdown("---")
                        st.markdown("#### 🏆 AI智能评分排名 (TOP10)")
                        
                        df_scoring = pd.DataFrame(scoring_ranking[:10])
                        # 类型统一，避免Arrow序列化错误
                        numeric_cols = ['排名','综合评分','资金含金量','净买入额','卖出压力','机构共振','加分项','板块轮动','连板晋级','同花顺热榜','连板高度','顶级游资','买方数','净流入','P1增强','机构净额','主力净流','封板质量','游资信号','硬性扣分','趋势微调','9日涨幅%']
                        for col in numeric_cols:
                            if col in df_scoring.columns:
                                df_scoring[col] = pd.to_numeric(df_scoring[col], errors='coerce')
                        text_cols = ['股票名称','股票代码','机构参与','主线匹配','热榜最佳']
                        for col in text_cols:
                            if col in df_scoring.columns:
                                df_scoring[col] = df_scoring[col].astype(str)
                        if '排名' in df_scoring.columns:
                            df_scoring['排名'] = pd.to_numeric(df_scoring['排名'], errors='coerce').fillna(0).astype(int)
                        
                        # 显示完整的评分表格
                        st.dataframe(
                            df_scoring,
                            column_config={
                                "排名": st.column_config.NumberColumn("排名", format="%d"),
                                "股票名称": st.column_config.TextColumn("股票名称", width="medium"),
                                "股票代码": st.column_config.TextColumn("代码", width="small"),
                                "综合评分": st.column_config.NumberColumn(
                                    "综合评分",
                                    format="%.1f",
                                    help="总分100分"
                                ),
                                "资金含金量": st.column_config.ProgressColumn(
                                    "资金含金量",
                                    format="%d分",
                                    min_value=0,
                                    max_value=22
                                ),
                                "净买入额": st.column_config.ProgressColumn(
                                    "净买入额",
                                    format="%d分",
                                    min_value=0,
                                    max_value=18
                                ),
                                "卖出压力": st.column_config.ProgressColumn(
                                    "卖出压力",
                                    format="%d分",
                                    min_value=0,
                                    max_value=14
                                ),
                                "机构共振": st.column_config.ProgressColumn(
                                    "机构共振",
                                    format="%d分",
                                    min_value=0,
                                    max_value=15
                                ),
                                "加分项": st.column_config.ProgressColumn(
                                    "加分项",
                                    format="%d分",
                                    min_value=0,
                                    max_value=4
                                ),
                                "板块轮动": st.column_config.ProgressColumn(
                                    "板块轮动",
                                    format="%.1f分",
                                    min_value=0,
                                    max_value=15
                                ),
                                "连板晋级": st.column_config.ProgressColumn(
                                    "连板晋级",
                                    format="%.1f分",
                                    min_value=0,
                                    max_value=2
                                ),
                                "同花顺热榜": st.column_config.ProgressColumn(
                                    "同花顺热榜",
                                    format="%.1f分",
                                    min_value=0,
                                    max_value=10
                                ),
                                "顶级游资": st.column_config.NumberColumn("顶级游资", format="%d家"),
                                "买方数": st.column_config.NumberColumn("买方数", format="%d家"),
                                "机构参与": st.column_config.TextColumn("机构参与"),
                                "净流入": st.column_config.NumberColumn("净流入(元)", format="%.2f"),
                                "主线匹配": st.column_config.TextColumn("主线匹配", width="medium"),
                                "连板高度": st.column_config.NumberColumn("连板高度", format="%d板"),
                                "热榜最佳": st.column_config.TextColumn("热榜最佳"),
                            },
                            hide_index=True,
                            width='stretch'
                        )
                        
                        # 显示评分说明
                        with st.expander("📖 评分维度说明", expanded=False):
                            st.markdown("""
                            **AI智能评分体系 (总分100分)**
                            
                            - **资金含金量** (0-22分)：顶级游资+10分，知名游资+5分，普通游资+1.5分（映射）
                            - **净买入额** (0-18分)：根据净流入金额大小评分（映射）
                            - **卖出压力** (0-14分)：卖出比例越低得分越高（映射）
                            - **机构共振** (0-15分)：机构+游资共振15分最高
                            - **加分项** (0-4分)：主力集中度、热门概念、连续上榜等
                            - **板块轮动** (0-15分)：基于Tushare `dc_member + dc_index`（失败回退 `dc_concept_cons` / `kpl_concept_cons`） + `limit_step` 的主线/梯队概念匹配评分
                            - **连板晋级** (0-2分)：基于Tushare `limit_step` 的连板高度与晋级热度
                            - **同花顺热榜** (0-10分)：基于Tushare `ths_hot` 的A股热榜热度
                            
                            💡 评分越高，表示该股票受到资金青睐程度越高！
                            """)
                    
                    # 显示数据概况
                    data_info = analysis_content_parsed.get('data_info', {})
                    if data_info:
                        st.markdown("---")
                        st.markdown("#### 📊 数据概况")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("龙虎榜记录", f"{data_info.get('total_records', 0)} 条")
                        with col2:
                            st.metric("涉及股票", f"{data_info.get('total_stocks', 0)} 只")
                        with col3:
                            st.metric("涉及游资", f"{data_info.get('total_youzi', 0)} 个")

                    concept_rotation = analysis_content_parsed.get('concept_rotation', {})
                    kpl_list_heat = analysis_content_parsed.get('kpl_list_heat', {})
                    kpl_mainline_shown = False
                    if kpl_list_heat and kpl_list_heat.get('data_success'):
                        kpl_summary = kpl_list_heat.get("kpl_summary", {}) or {}
                        all_theme_text = _build_kpl_mainline_text(kpl_summary, limit=0)
                        strongest_themes = _get_kpl_themes_without_st(kpl_summary, limit=1)
                        strongest_theme = strongest_themes[0].get("theme", "N/A") if strongest_themes else "N/A"
                        strongest_count = strongest_themes[0].get("count", 0) if strongest_themes else 0
                        if all_theme_text:
                            kpl_mainline_shown = True
                            st.markdown("---")
                            st.markdown("#### 🔥 概念主线")
                            st.info(
                                f"最强题材：**{strongest_theme}**（出现次数：{strongest_count}） | "
                                f"今日题材（过滤ST）：**{all_theme_text}** | "
                                f"数据源：**开盘啦热度（kpl_list）**"
                            )

                    if (not kpl_mainline_shown) and concept_rotation and concept_rotation.get('data_success'):
                        strongest = concept_rotation.get("strongest_today", {})
                        rotation_summary = concept_rotation.get("rotation_summary", {})
                        concept_source = concept_rotation.get("concept_source") or rotation_summary.get("concept_source", "")
                        concept_source_label = _format_concept_source_label(concept_source)
                        st.markdown("---")
                        st.markdown("#### 🔥 概念主线")
                        st.info(
                            f"最强概念：**{strongest.get('concept', 'N/A')}** "
                            f"（关联个股数：{strongest.get('stock_count', strongest.get('limit_up_count', 0))}，"
                            f"hot_num：{strongest.get('hot_num_total', 0)}） | "
                            f"资金动向：**{rotation_summary.get('capital_flow_signal', 'N/A')}** | "
                            f"数据源：**{concept_source_label}**"
                        )

                    limit_step_heat = analysis_content_parsed.get('limit_step_heat', {})
                    if limit_step_heat and limit_step_heat.get('data_success'):
                        step_summary = limit_step_heat.get("market_heat_summary", {})
                        avg_advance = step_summary.get("avg_advance_count", step_summary.get("avg_promotion_count", 0))
                        latest_advance = step_summary.get("latest_advance_count", 0)
                        strongest_step_concepts = limit_step_heat.get("strongest_step_concepts", [])[:5]
                        strongest_step_concepts_text = "、".join(
                            [f"{x.get('concept', '')}({x.get('score', 0)})" for x in strongest_step_concepts if x.get("concept")]
                        ) or "暂无"
                        st.markdown("#### 🚀 连板晋级热度")
                        st.info(
                            f"市场热度：**{step_summary.get('heat_level', 'N/A')}** | "
                            f"平均连板高度：**{step_summary.get('avg_max_step', 0)}** | "
                            f"平均晋级个数：**{avg_advance}** | "
                            f"最新交易日晋级：**{latest_advance}** | "
                            f"梯队最强概念：**{strongest_step_concepts_text}**"
                        )

                    ths_hot_heat = analysis_content_parsed.get('ths_hot_heat', {})
                    if ths_hot_heat and ths_hot_heat.get('data_success'):
                        hot_summary = ths_hot_heat.get("hot_summary", {})
                        st.markdown("#### 📱 同花顺A股热榜")
                        st.info(
                            f"覆盖天数：**{hot_summary.get('days', 0)}** | "
                            f"跟踪股票：**{hot_summary.get('tracked_stocks', 0)}** 只"
                        )

                    if kpl_list_heat and kpl_list_heat.get('data_success'):
                        kpl_summary = kpl_list_heat.get("kpl_summary", {})
                        top_theme_text = _build_kpl_mainline_text(kpl_summary, limit=5) or "暂无"
                        strongest_themes = _get_kpl_themes_without_st(kpl_summary, limit=1)
                        strongest_theme = strongest_themes[0].get("theme", "N/A") if strongest_themes else "N/A"
                        st.markdown("#### 🧭 开盘啦热度")
                        st.info(
                            f"覆盖天数：**{kpl_summary.get('days', 0)}** | "
                            f"评分池命中：**{kpl_summary.get('tracked_stocks', 0)}** 只 | "
                            f"全市场样本：**{kpl_summary.get('tracked_stocks_all', 0)}** 只 | "
                            f"最热题材（过滤ST）：**{strongest_theme}** | "
                            f"今日题材TOP（过滤ST）：**{top_theme_text}**"
                        )
                
                else:
                    # 如果无法解析，显示原始内容
                    st.markdown("#### 📄 原始分析内容")
                    analysis_content = report_detail.get('analysis_content', '')
                    if analysis_content:
                        st.text_area("原始分析内容", value=analysis_content[:2000], height=200, disabled=True)
                        if len(analysis_content) > 2000:
                            st.caption("(内容过长，仅显示前2000字符)")
                
                # 操作按钮
                st.markdown("---")
                col_export1, col_export2, col_export3 = st.columns(3)
                
                with col_export1:
                    if st.button(f"📥 导出为PDF", key=f"export_pdf_{report_id}"):
                        st.info("PDF导出功能开发中...")
                
                with col_export2:
                    # 使用session_state来管理按钮状态，避免需要点击两次的问题
                    load_key = f"load_report_{report_id}"
                    if st.button(f"📋 加载到分析页", key=load_key):
                        # 将历史报告加载到当前分析结果中
                        if analysis_content_parsed:
                            # 重建完整的result结构
                            scoring_data = analysis_content_parsed.get('scoring_ranking', [])
                            if scoring_data:
                                df_scoring = pd.DataFrame(scoring_data)
                                # 类型统一，避免Arrow序列化错误
                                numeric_cols = ['排名','综合评分','资金含金量','净买入额','卖出压力','机构共振','加分项','板块轮动','连板晋级','同花顺热榜','连板高度','顶级游资','买方数','净流入','P1增强','机构净额','主力净流','封板质量','游资信号','硬性扣分','趋势微调','9日涨幅%']
                                for col in numeric_cols:
                                    if col in df_scoring.columns:
                                        df_scoring[col] = pd.to_numeric(df_scoring[col], errors='coerce')
                                text_cols = ['股票名称','股票代码','机构参与','主线匹配','热榜最佳']
                                for col in text_cols:
                                    if col in df_scoring.columns:
                                        df_scoring[col] = df_scoring[col].astype(str)
                                if '排名' in df_scoring.columns:
                                    df_scoring['排名'] = pd.to_numeric(df_scoring['排名'], errors='coerce').fillna(0).astype(int)
                            else:
                                df_scoring = None
                                
                            loaded_result = {
                                "success": True,
                                "timestamp": report_detail.get('analysis_date', ''),
                                "data_info": analysis_content_parsed.get('data_info', {}),
                                "concept_rotation": analysis_content_parsed.get('concept_rotation', {}),
                                "limit_step_heat": analysis_content_parsed.get('limit_step_heat', {}),
                                "ths_hot_heat": analysis_content_parsed.get('ths_hot_heat', {}),
                                "kpl_list_heat": analysis_content_parsed.get('kpl_list_heat', {}),
                                "agents_analysis": analysis_content_parsed.get('agents_analysis', {}),
                                "scoring_ranking": df_scoring,
                                "final_report": analysis_content_parsed.get('final_report', {}),
                                "recommended_stocks": report_detail.get('recommended_stocks', [])
                            }
                            st.session_state.longhubang_result = loaded_result
                            # 使用rerun来立即刷新页面状态
                            st.success('✅ 报告已加载到分析页面，请切换到"龙虎榜分析"标签查看')
                            st.rerun()
                
                with col_export3:
                    # 删除按钮
                    delete_key = f"delete_report_{report_id}"
                    if st.button(f"🗑️ 删除报告", key=delete_key, type="secondary"):
                        # 使用session_state来管理删除确认状态
                        st.session_state[f"confirm_delete_{report_id}"] = True
                        st.rerun()
                
                # 删除确认对话框
                if st.session_state.get(f"confirm_delete_{report_id}", False):
                    st.warning(f"⚠️ 确认删除报告 #{report_id}？此操作不可撤销！")
                    col_confirm1, col_confirm2 = st.columns(2)
                    
                    with col_confirm1:
                        if st.button(f"✅ 确认删除", key=f"confirm_delete_yes_{report_id}", type="primary"):
                            try:
                                # 调用数据库删除方法 - 修复属性名
                                engine.database.delete_analysis_report(report_id)
                                st.success(f"✅ 报告 #{report_id} 已成功删除")
                                # 清除确认状态并刷新页面
                                if f"confirm_delete_{report_id}" in st.session_state:
                                    del st.session_state[f"confirm_delete_{report_id}"]
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ 删除失败: {str(e)}")
                    
                    with col_confirm2:
                        if st.button(f"❌ 取消", key=f"confirm_delete_no_{report_id}"):
                            # 清除确认状态
                            if f"confirm_delete_{report_id}" in st.session_state:
                                del st.session_state[f"confirm_delete_{report_id}"]
                            st.rerun()
        
    except Exception as e:
        st.error(f"❌ 加载历史报告失败: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


def display_statistics_tab():
    """显示数据统计标签页"""
    
    st.subheader("📈 数据统计")
    
    try:
        engine = _get_longhubang_engine()
        stats = engine.get_statistics()
        
        # 基本统计
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("总记录数", f"{stats.get('total_records', 0):,}")
        
        with col2:
            st.metric("股票总数", f"{stats.get('total_stocks', 0):,}")
        
        with col3:
            st.metric("游资总数", f"{stats.get('total_youzi', 0):,}")
        
        with col4:
            st.metric("分析报告", f"{stats.get('total_reports', 0):,}")
        
        # 日期范围
        date_range = stats.get('date_range', {})
        if date_range:
            st.info(f"📅 数据日期范围: {date_range.get('start', 'N/A')} 至 {date_range.get('end', 'N/A')}")
        
        st.markdown("---")
        
        # 活跃游资排名
        st.markdown("### 🏆 历史活跃游资排名 (近30天)")
        
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        top_youzi_df = engine.get_top_youzi(start_date, end_date, limit=20)
        
        if not top_youzi_df.empty:
            st.dataframe(
                top_youzi_df,
                column_config={
                    "youzi_name": st.column_config.TextColumn("游资名称"),
                    "trade_count": st.column_config.NumberColumn("交易次数", format="%d"),
                    "total_net_inflow": st.column_config.NumberColumn("总净流入(元)", format="%.2f")
                },
                hide_index=True,
                width='stretch'
            )
        
        st.markdown("---")
        
        # 热门股票排名
        st.markdown("### 📈 历史热门股票排名 (近30天)")
        
        top_stocks_df = engine.get_top_stocks(start_date, end_date, limit=20)
        
        if not top_stocks_df.empty:
            st.dataframe(
                top_stocks_df,
                column_config={
                    "stock_code": st.column_config.TextColumn("股票代码"),
                    "stock_name": st.column_config.TextColumn("股票名称"),
                    "youzi_count": st.column_config.NumberColumn("游资数量", format="%d"),
                    "total_net_inflow": st.column_config.NumberColumn("总净流入(元)", format="%.2f")
                },
                hide_index=True,
                width='stretch'
            )
        
    except Exception as e:
        st.error(f"❌ 加载统计数据失败: {str(e)}")


def run_longhubang_batch_analysis():
    """执行龙虎榜TOP股票批量分析（遵循统一调用规范）"""
    
    st.markdown("## 🚀 龙虎榜TOP股票批量分析")
    st.markdown("---")
    
    # 检查是否已有分析结果
    if st.session_state.get('longhubang_batch_results'):
        display_longhubang_batch_results(st.session_state.longhubang_batch_results)
        
        # 返回按钮
        col_back, col_clear = st.columns(2)
        with col_back:
            if st.button("🔙 返回龙虎榜分析", width='stretch'):
                # 清除所有批量分析相关状态
                if 'longhubang_batch_trigger' in st.session_state:
                    del st.session_state.longhubang_batch_trigger
                if 'longhubang_batch_codes' in st.session_state:
                    del st.session_state.longhubang_batch_codes
                if 'longhubang_batch_results' in st.session_state:
                    del st.session_state.longhubang_batch_results
                st.rerun()
        
        with col_clear:
            if st.button("🔄 重新分析", width='stretch'):
                # 清除结果，保留触发标志和代码
                if 'longhubang_batch_results' in st.session_state:
                    del st.session_state.longhubang_batch_results
                st.rerun()
        
        return
    
    # 获取股票代码列表
    stock_codes = st.session_state.get('longhubang_batch_codes', [])
    
    if not stock_codes:
        st.error("未找到股票代码列表")
        # 清除触发标志
        if 'longhubang_batch_trigger' in st.session_state:
            del st.session_state.longhubang_batch_trigger
        return
    
    st.info(f"即将分析 {len(stock_codes)} 只股票：{', '.join(stock_codes)}")
    
    # 返回按钮
    if st.button("🔙 取消返回", type="secondary"):
        # 清除所有批量分析相关状态
        if 'longhubang_batch_trigger' in st.session_state:
            del st.session_state.longhubang_batch_trigger
        if 'longhubang_batch_codes' in st.session_state:
            del st.session_state.longhubang_batch_codes
        st.rerun()
    
    st.markdown("---")
    
    # 分析选项
    col1, col2 = st.columns(2)
    
    with col1:
        analysis_mode = st.selectbox(
            "分析模式",
            options=["sequential", "parallel"],
            format_func=lambda x: "顺序分析（稳定）" if x == "sequential" else "并行分析（快速）",
            help="顺序分析较慢但稳定，并行分析更快但消耗更多资源"
        )
    
    with col2:
        if analysis_mode == "parallel":
            max_workers = st.number_input(
                "并行线程数",
                min_value=2,
                max_value=5,
                value=3,
                help="同时分析的股票数量"
            )
        else:
            max_workers = 1
    
    st.markdown("---")
    
    # 开始分析按钮
    col_confirm, col_cancel = st.columns(2)
    
    start_analysis = False
    with col_confirm:
        if st.button("🚀 确认开始分析", type="primary", width='stretch'):
            start_analysis = True
    
    with col_cancel:
        if st.button("❌ 取消", type="secondary", width='stretch'):
            # 清除所有批量分析相关状态
            if 'longhubang_batch_trigger' in st.session_state:
                del st.session_state.longhubang_batch_trigger
            if 'longhubang_batch_codes' in st.session_state:
                del st.session_state.longhubang_batch_codes
            st.rerun()
    
    if start_analysis:
        # 导入统一分析函数（遵循统一规范）
        from app import analyze_single_stock_for_batch
        import concurrent.futures
        import time
        
        st.markdown("---")
        st.info("⏳ 正在执行批量分析，请稍候...")
        
        # 进度显示
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        results = []
        start_time = time.time()
        
        if analysis_mode == "sequential":
            # 顺序分析
            for i, code in enumerate(stock_codes):
                status_text.text(f"正在分析 {code} ({i+1}/{len(stock_codes)})")
                progress_bar.progress((i + 1) / len(stock_codes))
                
                try:
                    # 调用统一分析函数
                    result = analyze_single_stock_for_batch(
                        symbol=code,
                        period="1y",
                        enabled_analysts_config={
                            'technical': True,
                            'fundamental': True,
                            'fund_flow': True,
                            'risk': True,
                            'sentiment': False,
                            'news': False
                        },
                        selected_model=config.DEFAULT_MODEL_NAME
                    )
                    
                    results.append({
                        "code": code,
                        "result": result
                    })
                    
                except Exception as e:
                    results.append({
                        "code": code,
                        "result": {"success": False, "error": str(e)}
                    })
        
        else:
            # 并行分析
            status_text.text(f"并行分析 {len(stock_codes)} 只股票...")
            
            def analyze_one(code):
                try:
                    result = analyze_single_stock_for_batch(
                        symbol=code,
                        period="1y",
                        enabled_analysts_config={
                            'technical': True,
                            'fundamental': True,
                            'fund_flow': True,
                            'risk': True,
                            'sentiment': False,
                            'news': False
                        },
                        selected_model=config.DEFAULT_MODEL_NAME
                    )
                    return {"code": code, "result": result}
                except Exception as e:
                    return {"code": code, "result": {"success": False, "error": str(e)}}
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(analyze_one, code): code for code in stock_codes}
                
                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    completed += 1
                    progress_bar.progress(completed / len(stock_codes))
                    status_text.text(f"已完成 {completed}/{len(stock_codes)}")
                    results.append(future.result())
        
        # 清除进度
        progress_bar.empty()
        status_text.empty()
        
        # 计算统计
        elapsed_time = time.time() - start_time
        success_count = sum(1 for r in results if r.get("result", {}).get("success"))
        failed_count = len(results) - success_count
        
        st.success(f"✅ 批量分析完成！成功 {success_count} 只，失败 {failed_count} 只，耗时 {elapsed_time:.1f}秒")
        
        # 保存结果到session_state
        st.session_state.longhubang_batch_results = {
            "results": results,
            "total": len(results),
            "success": success_count,
            "failed": failed_count,
            "elapsed_time": elapsed_time
        }
        
        time.sleep(0.5)
        st.rerun()


def display_longhubang_batch_results(batch_results: dict):
    """显示龙虎榜批量分析结果"""
    
    st.markdown("### 📊 批量分析结果")
    
    results = batch_results.get("results", [])
    total = batch_results.get("total", 0)
    success = batch_results.get("success", 0)
    failed = batch_results.get("failed", 0)
    elapsed_time = batch_results.get("elapsed_time", 0)
    
    # 统计信息
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总计", total)
    with col2:
        st.metric("成功", success)
    with col3:
        st.metric("失败", failed)
    with col4:
        st.metric("耗时", f"{elapsed_time:.1f}秒")
    
    st.markdown("---")
    
    # 失败的股票
    failed_results = [r for r in results if not r.get("result", {}).get("success")]
    if failed_results:
        with st.expander(f"❌ 失败股票 ({len(failed_results)}只)", expanded=False):
            for item in failed_results:
                code = item.get("code", "")
                error = item.get("result", {}).get("error", "未知错误")
                st.error(f"**{code}**: {error}")
    
    # 成功的股票
    success_results = [r for r in results if r.get("result", {}).get("success")]
    
    if not success_results:
        st.warning("⚠️ 没有成功分析的股票")
        return
    
    st.markdown("### 🎯 分析结果详情")
    
    # 显示每只股票的分析结果（使用统一字段名）
    for item in success_results:
        code = item.get("code", "")
        result = item.get("result", {})
        final_decision = result.get("final_decision", {})
        stock_info = result.get("stock_info", {})
        
        # 使用统一字段名
        rating = final_decision.get("rating", "未知")
        confidence = final_decision.get("confidence_level", "N/A")
        entry_range = final_decision.get("entry_range", "N/A")
        take_profit = final_decision.get("take_profit", "N/A")
        stop_loss = final_decision.get("stop_loss", "N/A")
        target_price = final_decision.get("target_price", "N/A")
        advice = final_decision.get("advice", "")
        
        # 评级颜色
        if "强烈买入" in rating or "买入" in rating:
            rating_color = "🟢"
        elif "卖出" in rating:
            rating_color = "🔴"
        else:
            rating_color = "🟡"
        
        with st.expander(f"{rating_color} {code} {stock_info.get('name', '')} - {rating} (信心度: {confidence})", expanded=False):
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.markdown("**基本信息**")
                st.write(f"当前价: {stock_info.get('current_price', 'N/A')}")
                st.write(f"目标价: {target_price}")
            
            with col2:
                st.markdown("**进出场位置**")
                st.write(f"进场区间: {entry_range}")
                st.write(f"止盈位: {take_profit}")
            
            with col3:
                st.markdown("**风控**")
                st.write(f"止损位: {stop_loss}")
                st.write(f"评级: {rating}")
            
            if advice:
                st.markdown("**投资建议**")
                st.info(advice)
            
            # 添加到监测按钮
            if st.button(f"➕ 加入监测", key=f"add_monitor_{code}"):
                add_to_monitor_from_longhubang(code, stock_info.get('name', ''), final_decision)


def add_to_monitor_from_longhubang(code: str, name: str, final_decision: dict):
    """从龙虎榜分析结果添加到监测列表"""
    try:
        from monitor_db import monitor_db
        import re
        
        # 提取数据（使用统一字段名和解析逻辑）
        rating = final_decision.get("rating", "持有")
        entry_range = final_decision.get("entry_range", "")
        take_profit_str = final_decision.get("take_profit", "")
        stop_loss_str = final_decision.get("stop_loss", "")
        
        # 解析进场区间
        entry_min, entry_max = None, None
        if entry_range and isinstance(entry_range, str) and "-" in entry_range:
            try:
                parts = entry_range.split("-")
                entry_min = float(parts[0].strip())
                entry_max = float(parts[1].strip())
            except:
                pass
        
        # 解析止盈止损
        take_profit, stop_loss = None, None
        if take_profit_str:
            try:
                numbers = re.findall(r'\d+\.?\d*', str(take_profit_str))
                if numbers:
                    take_profit = float(numbers[0])
            except:
                pass
        
        if stop_loss_str:
            try:
                numbers = re.findall(r'\d+\.?\d*', str(stop_loss_str))
                if numbers:
                    stop_loss = float(numbers[0])
            except:
                pass
        
        # 验证必需参数
        if not all([entry_min, entry_max, take_profit, stop_loss]):
            st.error("❌ 分析结果缺少完整的进场区间和止盈止损信息")
            return
        
        # 添加到监测
        monitor_db.add_monitored_stock(
            symbol=code,
            name=name,
            rating=rating,
            entry_range={"min": entry_min, "max": entry_max},
            take_profit=take_profit,
            stop_loss=stop_loss,
            check_interval=60,
            notification_enabled=True
        )
        
        st.success(f"✅ {code} 已成功加入监测列表！")
        
    except Exception as e:
        st.error(f"❌ 添加监测失败: {str(e)}")


# 测试函数
if __name__ == "__main__":
    st.set_page_config(
        page_title="智瞰龙虎",
        page_icon="🎯",
        layout="wide"
    )
    
    display_longhubang()
