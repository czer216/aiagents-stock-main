"""
智能盯盘 - UI界面
集成到主程序的智能盯盘功能界面
"""

import streamlit as st
import pandas as pd
import json
from datetime import datetime
import logging
import os
from typing import Dict, List, Optional
from dotenv import load_dotenv
import plotly.graph_objects as go
import requests
import re

import config

from .smart_monitor_engine import SmartMonitorEngine
from .smart_monitor_db import SmartMonitorDB
from modules.douban_author_strategy.douban_author_db import douban_author_db
from services.config_manager import config_manager  # 使用主程序的配置管理器
from ui.components import render_top_nav
from infrastructure.notification.rapid_rise_monitor_service import rapid_rise_monitor_service
from infrastructure.db.monitor_db import monitor_db


# 加载环境变量
load_dotenv()


def _parse_tag_list(raw: str) -> List[str]:
    if not raw:
        return []
    parts = [x.strip() for x in str(raw).replace('，', ',').split(',')]
    out: List[str] = []
    seen = set()
    for p in parts:
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _normalize_schedule_times(raw: str) -> List[str]:
    if not raw:
        return []
    parts = [x.strip() for x in str(raw).replace('，', ',').split(',')]
    out: List[str] = []
    seen = set()
    for p in parts:
        if not re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', p):
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    out.sort()
    return out


def smart_monitor_ui():
    """AI盯盘主界面"""
    
    render_top_nav("🤖 AI盯盘 - AI决策交易系统", "参照AlphaArena项目，基于DeepSeek AI的A股自动化交易系统")
    
    # 使用说明
    with st.expander("📖 快速使用指南", expanded=False):
        st.markdown("""
        ### 🚀 快速开始
        
        **第一步：环境配置**
        1. 点击左侧菜单"⚙️ 环境配置"
        2. 填写 DeepSeek API Key（必需）
        3. 配置 miniQMT 账户（可选，用于实盘交易）
        4. 配置通知方式（可选，邮件/Webhook）
        
        **第二步：开始使用**
        - **实时分析**：输入股票代码，AI即时分析并给出交易建议
        - **监控任务**：添加股票到监控列表，定时自动分析
        - **持仓管理**：查看和管理当前持仓（已持仓股票可直接监控）
        
        ---
        
        ### 💡 核心功能
        
        | 功能 | 说明 |
        |------|------|
        | 📊 **实时分析** | 输入股票代码，AI分析市场数据并给出买入/卖出/持有建议 |
        | 🎯 **监控任务** | 定时自动分析目标股票，可设置自动交易 |
        | 📈 **持仓管理** | 记录持仓成本，实时显示盈亏，AI决策考虑持仓情况 |
        | 📜 **历史记录** | 查看所有AI决策历史、交易记录和通知记录 |
        | ⚙️ **系统设置** | 配置API、交易方式（实盘/模拟）、通知等 |
        
        ---
        
        ### 🎯 AI决策逻辑
        
        **买入信号**（至少满足3个）：
        1. ✅ 趋势向上：价格 > MA5 > MA20 > MA60（多头排列）
        2. ✅ 量价配合：成交量 > 5日均量的120%（放量上涨）
        3. ✅ MACD金叉：MACD > 0 且DIF上穿DEA
        4. ✅ RSI健康：RSI在50-70区间（不超买不超卖）
        5. ✅ 突破关键位：突破前期高点或重要阻力位
        6. ✅ 布林带位置：价格接近布林中轨上方，有上行空间
        
        **卖出信号**（满足任一立即卖出）：
        1. 🔴 止损触发：亏损 ≥ -5%（明天开盘立即卖出）
        2. 🟢 止盈触发：盈利 ≥ +10%（锁定收益）
        3. 🔴 趋势转弱：跌破MA20/MA60，MACD死叉
        4. 🔴 放量下跌：成交量放大但价格下跌
        5. 🔴 技术破位：跌破重要支撑位
        
        ---
        
        ### ⚠️ A股T+1规则
        
        **关键限制**：
        - 今天买入的股票，**今天不能卖出**
        - 必须等到下一个交易日才能卖出
        - 系统会自动检查并遵守T+1规则
        
        **建议**：
        - **宁可错过，不可做错** - 买入前务必确认趋势
        - 单只股票仓位 ≤ 30%（T+1风险较大）
        - 止损位：-5%（明天开盘立即执行）
        - 止盈位：+8-15%（分批止盈）
        
        ---
        
        ### 🔧 使用技巧
        
        **新手建议**：
        1. 先使用"模拟交易"模式测试
        2. 小仓位试水（建议5-10%）
        3. 严格执行止损，不要心存侥幸
        4. 关注交易时段（9:30-11:30, 13:00-15:00）
        
        **高级功能**：
        - 在"监控任务"中勾选"已持仓"，填入成本价
        - AI会考虑当前盈亏情况给出更准确的建议
        - 可设置多个监控任务，同时盯盘多只股票
        
        ---
        
        ### 📞 常见问题
        
        **Q: 提示"DeepSeek API调用失败"？**
        - 检查API Key是否正确
        - 确认API账户余额充足
        - 检查网络连接
        
        **Q: 数据显示为0或获取失败？**
        - 可能是非交易时间
        - AKShare接口可能暂时不可用
        - 尝试更换股票代码测试
        
        **Q: 想实盘交易如何操作？**
        1. 下载并安装 [miniQMT](https://www.xtp-mini.com/)
        2. 启动miniQMT客户端并登录
        3. 在"系统设置"中填写账户ID
        4. 取消勾选"使用模拟交易"
        
        ---
        
        ### ⚠️ 风险提示
        
        1. **股市有风险，投资需谨慎**
        2. AI决策仅供参考，不构成投资建议
        3. 建议先使用模拟交易充分测试
        4. 严格控制仓位，不要满仓操作
        5. 不要投入超过承受能力的资金
        
        ---
        
        **🎉 祝您交易顺利！如有问题，请查看详细文档或联系技术支持。**
        """)
    
    st.markdown("---")
    
    # 初始化组件（自动从配置读取）
    if 'engine' not in st.session_state:
        try:
            # SmartMonitorEngine会自动从config_manager读取配置
            st.session_state.engine = SmartMonitorEngine()
            st.session_state.db = SmartMonitorDB()
        except Exception as e:
            st.error(f"初始化失败: {e}")
            st.error("请先在'环境配置'中完成基础配置")
            return
    
    # 创建标签页
    tabs = st.tabs([
        "📊 实时分析",
        "🎯 监控任务",
        "📈 持仓管理",
        "📜 历史记录",
        "⚡ 主板快拉联动",
        "⚙️ 系统设置"
    ])
    
    # 标签页1: 实时分析
    with tabs[0]:
        render_realtime_analysis()
    
    # 标签页2: 监控任务
    with tabs[1]:
        render_monitor_tasks()
    
    # 标签页3: 持仓管理
    with tabs[2]:
        render_position_management()
    
    # 标签页4: 历史记录
    with tabs[3]:
        render_history()
    
    # 标签页5: 主板快拉联动
    with tabs[4]:
        render_mainboard_rapid_rise_linkage()

    # 标签页6: 系统设置
    with tabs[5]:
        render_settings()


def render_realtime_analysis():
    """实时分析界面"""
    
    st.header("📊 实时分析")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        stock_code = st.text_input(
            "输入股票代码",
            placeholder="例如: 600519",
            help="输入6位股票代码"
        )
    
    with col2:
        auto_trade = st.checkbox("自动交易", value=False, 
                                help="开启后AI会自动执行交易决策")
    
    if st.button("🔍 开始分析", type="primary"):
        if not stock_code:
            st.error("请输入股票代码")
            return
        
        if len(stock_code) != 6 or not stock_code.isdigit():
            st.error("股票代码格式错误，请输入6位数字")
            return
        
        # 显示进度
        with st.spinner('正在分析...'):
            engine = st.session_state.engine
            result = engine.analyze_stock(
                stock_code=stock_code,
                auto_trade=auto_trade,
                notify=True
            )
        
        if result['success']:
            # 显示分析结果
            display_analysis_result(result)
        else:
            st.error(f"分析失败: {result.get('error')}")


def display_analysis_result(result: dict):
    """显示分析结果"""
    
    stock_code = result['stock_code']
    stock_name = result['stock_name']
    decision = result['decision']
    market_data = result['market_data']
    session_info = result['session_info']
    
    st.success(f"✅ 分析完成: {stock_code} {stock_name}")
    
    # 交易时段信息
    st.info(f"⏰ 当前时段: {session_info['session']} - {session_info['recommendation']}")
    
    # AI决策
    st.markdown("### 🤖 AI决策")

    if result.get('ai_new_request'):
        st.info("🧠 本轮命中计划时间，已发起AI请求")
    elif result.get('ai_reused'):
        st.info("♻️ 本轮复用当日最近AI决策")
    elif result.get('skipped') and result.get('skip_reason') == 'waiting_for_schedule_time':
        st.warning("⏳ 未到计划时间且当日无可复用决策")
    
    col1, col2, col3, col4 = st.columns(4)
    
    # 决策动作
    action = decision['action']
    action_emoji = {"BUY": "📈", "SELL": "📉", "HOLD": "⏸️"}
    action_color = {"BUY": "green", "SELL": "red", "HOLD": "gray"}
    
    col1.metric("决策", action, delta=None)
    col2.metric("信心度", f"{decision['confidence']}%")
    col3.metric("风险等级", decision.get('risk_level', 'N/A'))
    col4.metric("建议仓位", f"{decision.get('position_size_pct', 0)}%")
    
    # 决策理由
    st.markdown("**决策理由:**")
    st.text_area("决策理由", decision['reasoning'], height=150, disabled=True, label_visibility="hidden")
    
    # 市场数据
    st.markdown("### 📊 市场数据")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("当前价", f"¥{market_data.get('current_price', 0):.2f}")
    col2.metric("涨跌幅", f"{market_data.get('change_pct', 0):+.2f}%")
    col3.metric("成交量", f"{market_data.get('volume', 0):,.0f}手")
    col4.metric("换手率", f"{market_data.get('turnover_rate', 0):.2f}%")
    
    # 技术指标
    st.markdown("### 📈 技术指标")
    
    tech_col1, tech_col2, tech_col3 = st.columns(3)
    
    with tech_col1:
        st.markdown("**均线系统**")
        st.write(f"MA5: ¥{market_data.get('ma5', 0):.2f}")
        st.write(f"MA20: ¥{market_data.get('ma20', 0):.2f}")
        st.write(f"MA60: ¥{market_data.get('ma60', 0):.2f}")
        st.write(f"趋势: {market_data.get('trend', 'N/A')}")
    
    with tech_col2:
        st.markdown("**动量指标**")
        st.write(f"MACD: {market_data.get('macd', 0):.4f}")
        st.write(f"DIF: {market_data.get('macd_dif', 0):.4f}")
        st.write(f"DEA: {market_data.get('macd_dea', 0):.4f}")
    
    with tech_col3:
        st.markdown("**摆动指标**")
        st.write(f"RSI(6): {market_data.get('rsi6', 0):.2f}")
        st.write(f"RSI(12): {market_data.get('rsi12', 0):.2f}")
        st.write(f"RSI(24): {market_data.get('rsi24', 0):.2f}")
    
    # 主力资金（已禁用 - 接口不稳定）
    # if 'main_force' in market_data:
    #     st.markdown("### 💰 主力资金")
    #     mf = market_data['main_force']
    #     
    #     mf_col1, mf_col2, mf_col3 = st.columns(3)
    #     mf_col1.metric("主力净额", f"{mf['main_net']:,.2f}万", 
    #                   delta=f"{mf['main_net_pct']:+.2f}%")
    #     mf_col2.metric("超大单", f"{mf['super_net']:,.2f}万")
    #     mf_col3.metric("大单", f"{mf['big_net']:,.2f}万")
    #     
    #     st.info(f"主力动向: {mf['trend']}")
    
    # 执行结果（如果有）
    if result.get('execution_result'):
        exec_result = result['execution_result']
        st.markdown("### ⚡ 执行结果")
        
        if exec_result.get('success'):
            st.success(f"✅ {exec_result.get('message', '执行成功')}")
        else:
            st.error(f"❌ {exec_result.get('error', '执行失败')}")


def render_monitor_tasks():
    """监控任务界面"""
    
    st.header("🎯 监控任务管理")
    
    db = st.session_state.db
    engine = st.session_state.engine

    if "smart_monitor_daily_strong_themes" not in st.session_state:
        st.session_state["smart_monitor_daily_strong_themes"] = []

    with st.expander("🔥 当天强力题材（全局）", expanded=False):
        existing_themes = st.session_state.get("smart_monitor_daily_strong_themes", []) or []
        themes_text = st.text_input(
            "题材列表（逗号分隔）",
            value=", ".join(existing_themes),
            key="sm_daily_themes_text",
            placeholder="例如: 机器人, AI算力, 低空经济",
        )
        parsed_themes = _parse_tag_list(themes_text)
        selected_themes = st.multiselect(
            "当天强力题材（可多选）",
            options=parsed_themes,
            default=parsed_themes,
            key="sm_daily_themes_multi",
        )
        st.session_state["smart_monitor_daily_strong_themes"] = selected_themes
    
    # 添加新任务
    with st.expander("➕ 添加新监控任务", expanded=True):
        # 改回使用form，确保值正确提交
        with st.form("add_monitor_task_form", clear_on_submit=False):
            col1, col2 = st.columns(2)
            
            with col1:
                task_name = st.text_input("任务名称", placeholder="例如: 茅台盯盘")
                stock_code = st.text_input("股票代码", placeholder="例如: 600519")
                check_interval = st.slider("检查间隔(秒)", 60, 3600, 300)
                stock_concepts_input = st.text_input(
                    "该股题材概念（逗号分隔）",
                    value="",
                    placeholder="例如: 白酒, 消费升级, 高股息",
                    help="可填写多个题材，AI决策会作为参考",
                )
                
                # 持仓信息
                st.markdown("---")
                st.markdown("**📊 持仓信息**")
                has_position = st.checkbox("已持仓该股票", value=False,
                                          help="勾选后可填写持仓成本和数量，AI会考虑持仓情况")
                
                # 注意：在form内部，复选框的变化要到提交后才能看到
                # 所以持仓输入框始终显示，用户可以选择填写或不填写
                position_cost = st.number_input("持仓成本(元)", min_value=0.01, value=10.0, step=0.01,
                                               help="如果已持仓，填写买入时的成本价格（未持仓可忽略）")
                position_quantity = st.number_input("持仓数量(股)", min_value=100, value=100, step=100,
                                                   help="如果已持仓，填写持有的股票数量（未持仓可忽略）")
            
            with col2:
                auto_trade = st.checkbox("自动交易", value=False,
                                        help="AI决策后自动执行交易")
                trading_hours_only = st.checkbox(
                    "仅交易时段监控",
                    value=True,
                    help="开启后，只在交易日的交易时段（9:30-11:30, 13:00-15:00）进行AI分析"
                )
                position_size = st.slider("仓位百分比(%)", 5, 50, 20,
                                         help="新建仓位时使用的资金比例")

                st.markdown("---")
                st.markdown("**⏰ AI触发计划（可选）**")
                ai_schedule_enabled = st.checkbox(
                    "启用定时AI请求",
                    value=False,
                    help="开启后仅在指定时间点请求AI，其余轮次复用当日最近决策",
                )
                ai_daily_max_calls = st.number_input(
                    "每天最多AI请求次数",
                    min_value=1,
                    max_value=20,
                    value=3,
                    step=1,
                )
                ai_schedule_times_input = st.text_input(
                    "AI触发时间点（逗号分隔）",
                    value="09:40,11:30,14:35",
                    placeholder="例如: 09:40,11:30,14:35",
                    help="仅支持HH:MM格式，如09:40",
                )

                st.markdown("---")
                st.markdown("**🧠 作者模式（可选）**")
                use_author_pattern = st.checkbox("启用作者交易模式", value=False)

                authors = douban_author_db.list_authors(limit=200)
                author_options = {f"{a.get('author_name')} ({a.get('author_id')})": a for a in (authors or [])}
                author_labels = list(author_options.keys()) if author_options else ["暂无可用作者"]
                selected_author_label = st.selectbox(
                    "选择作者",
                    options=author_labels,
                    key="sm_douban_author",
                    disabled=(not author_options),
                )
                selected_author = author_options.get(selected_author_label) or {}

                selected_pattern_version = 0
                ver_options = {}
                patterns = []
                if selected_author:
                    patterns = douban_author_db.list_author_patterns(author_id=str(selected_author.get('author_id') or ''), limit=30)
                    ver_options = {
                        f"v{int(p.get('pattern_version') or 0)} | conf={float(p.get('confidence') or 0):.1f}": p
                        for p in patterns
                    }

                ver_labels = list(ver_options.keys()) if ver_options else ["暂无模式版本"]
                ver_label = st.selectbox(
                    "选择模式版本",
                    options=ver_labels,
                    key="sm_douban_pattern_ver",
                    disabled=(not ver_options),
                )
                selected_pattern_version = int((ver_options.get(ver_label) or {}).get('pattern_version') or 0)

                if use_author_pattern and not author_options:
                    st.warning("暂无可用作者模式，请先在豆瓣交易模式中学习作者")
                if use_author_pattern and author_options and not patterns:
                    st.warning("该作者暂无模式版本")
            
            # 添加任务按钮（表单提交按钮）
            submitted = st.form_submit_button("➕ 添加任务", type="primary", width='stretch')
        
        if submitted:
            # 验证必填项（form中直接使用局部变量）
            if not task_name or not stock_code:
                st.error("❌ 请填写必填项：任务名称和股票代码")
            else:
                
                try:
                    # 检查是否已存在该股票的监控任务
                    existing_tasks = db.get_monitor_tasks(enabled_only=False)
                    existing_task = next((t for t in existing_tasks if t['stock_code'] == stock_code), None)
                    
                    if existing_task:
                        st.error(f"❌ 股票代码 {stock_code} 已存在监控任务！")
                        st.warning(f"任务名称: {existing_task['task_name']}")
                        st.info("💡 请在下方任务列表中找到该任务，点击启动或删除后重新添加")
                    else:
                        # 创建任务（初始状态为禁用，需要用户手动启动）
                        selected_author_id = ''
                        selected_author_name = ''
                        selected_pattern_version_value = 0
                        if use_author_pattern:
                            selected_author_id = str((selected_author or {}).get('author_id') or '').strip()
                            selected_author_name = str((selected_author or {}).get('author_name') or '').strip()
                            selected_pattern_version_value = int(selected_pattern_version or 0)
                            if not selected_author_id or selected_pattern_version_value <= 0:
                                st.error("❌ 已启用作者模式，但作者或版本未选择完整")
                                st.stop()

                        normalized_schedule_times = _normalize_schedule_times(ai_schedule_times_input)
                        if ai_schedule_enabled and not normalized_schedule_times:
                            st.error("❌ 已启用定时AI请求，但未提供有效时间（格式需为HH:MM）")
                            st.stop()

                        task_data = {
                            'task_name': task_name,
                            'stock_code': stock_code,
                            'enabled': 0,  # 关键修改：初始状态为禁用，不自动启动
                            'check_interval': check_interval,
                            'auto_trade': 1 if auto_trade else 0,
                            'trading_hours_only': 1 if trading_hours_only else 0,
                            'position_size_pct': position_size,
                            'has_position': 1 if has_position else 0,
                            'position_cost': position_cost if has_position else 0,
                            'position_quantity': position_quantity if has_position else 0,
                            'position_date': datetime.now().strftime('%Y-%m-%d') if has_position else None,
                            'douban_author_id': selected_author_id or None,
                            'douban_author_name': selected_author_name or None,
                            'douban_pattern_version': selected_pattern_version_value if selected_pattern_version_value > 0 else None,
                            'stock_concepts': json.dumps(_parse_tag_list(stock_concepts_input), ensure_ascii=False),
                            'ai_schedule_enabled': 1 if ai_schedule_enabled else 0,
                            'ai_daily_max_calls': int(ai_daily_max_calls),
                            'ai_schedule_times': json.dumps(normalized_schedule_times, ensure_ascii=False),
                        }
                        
                        task_id = db.add_monitor_task(task_data)
                        
                        st.success(f"✅ 任务创建成功! ID: {task_id}")
                        if has_position:
                            st.info(f"📊 已记录持仓: {position_quantity}股 @ {position_cost:.2f}元")
                        st.info("💡 任务已创建但未启动，请在下方任务列表中点击'▶️ 启动'按钮开始监控")
                        
                        st.rerun()
                except Exception as e:
                    error_msg = str(e)
                    if "UNIQUE constraint failed" in error_msg:
                        st.error(f"❌ 股票代码 {stock_code} 已存在监控任务！")
                        st.info("💡 请在下方任务列表中找到该任务")
                    else:
                        st.error(f"创建失败: {error_msg}")
    
    # 显示任务列表
    st.markdown("### 📋 监控任务列表")
    
    tasks = db.get_monitor_tasks(enabled_only=False)
    
    if not tasks:
        st.info("暂无监控任务，点击上方'添加新监控任务'创建")
        return
    
    for task in tasks:
        with st.container():
            # 获取实时价格计算盈亏
            has_position = task.get('has_position', 0)
            position_cost = task.get('position_cost', 0)
            position_quantity = task.get('position_quantity', 0)
            
            # 尝试获取当前价格
            current_price = 0
            profit_loss = 0
            profit_loss_pct = 0
            
            if has_position and position_cost > 0 and position_quantity > 0:
                try:
                    # 获取实时行情
                    from smart_monitor_data import SmartMonitorDataFetcher
                    data_fetcher = SmartMonitorDataFetcher()
                    quote = data_fetcher.get_realtime_quote(task['stock_code'], retry=1)
                    if quote:
                        current_price = quote.get('current_price', 0)
                        if current_price > 0:
                            # 计算盈亏
                            cost_total = position_cost * position_quantity
                            current_total = current_price * position_quantity
                            profit_loss = current_total - cost_total
                            profit_loss_pct = (profit_loss / cost_total) * 100
                except Exception as e:
                    pass
            
            col1, col2, col3, col4, col5, col6 = st.columns([2, 2, 1.5, 1, 1, 1])
            
            with col1:
                st.write(f"**{task['task_name']}**")
                st.caption(f"{task['stock_code']} - 间隔{task['check_interval']}秒")
            
            with col2:
                status = "✅ 已启用" if task['enabled'] else "⏸️ 已禁用"
                auto_trade_status = "🤖 自动交易" if task['auto_trade'] else "👀 仅监控"
                trading_mode = "🕒 仅交易时段" if task.get('trading_hours_only', 1) else "🌐 全时段"
                st.write(status)
                st.caption(f"{auto_trade_status} | {trading_mode}")
                
                # 显示持仓状态
                if has_position:
                    st.caption(f"📊 持仓: {position_quantity}股 @ {position_cost:.2f}元")
                pattern_author_id = str(task.get('douban_author_id') or '').strip()
                pattern_author_name = str(task.get('douban_author_name') or '').strip()
                pattern_ver = int(task.get('douban_pattern_version') or 0)
                raw_concepts = task.get('stock_concepts')
                concept_list: List[str] = []
                if isinstance(raw_concepts, str) and raw_concepts.strip():
                    try:
                        parsed = json.loads(raw_concepts)
                        if isinstance(parsed, list):
                            concept_list = [str(x).strip() for x in parsed if str(x).strip()]
                    except Exception:
                        concept_list = _parse_tag_list(raw_concepts)
                if pattern_author_id and pattern_ver > 0:
                    st.caption(f"🧠 模式: {pattern_author_name or pattern_author_id} v{pattern_ver}")
                else:
                    st.caption("🧠 模式: 默认模式")
                if concept_list:
                    st.caption(f"🏷️ 题材: {' / '.join(concept_list)}")

                schedule_enabled = task.get('ai_schedule_enabled', 0) == 1
                schedule_times_text = ''
                raw_schedule_times = task.get('ai_schedule_times')
                parsed_schedule_times: List[str] = []
                if isinstance(raw_schedule_times, str) and raw_schedule_times.strip():
                    try:
                        parsed = json.loads(raw_schedule_times)
                        if isinstance(parsed, list):
                            parsed_schedule_times = [str(x).strip() for x in parsed if str(x).strip()]
                    except Exception:
                        parsed_schedule_times = _normalize_schedule_times(raw_schedule_times)
                if parsed_schedule_times:
                    schedule_times_text = ' / '.join(parsed_schedule_times)
                if schedule_enabled:
                    st.caption(f"🕘 AI计划: {int(task.get('ai_daily_max_calls') or 3)}次 | {schedule_times_text or '-'}")
                else:
                    st.caption("🕘 AI计划: 关闭")
            
            with col3:
                is_running = task['stock_code'] in engine.monitoring_threads
                if is_running:
                    st.success("▶️ 运行中")
                else:
                    st.info("⏸️ 未运行")
                
                # 显示盈亏
                if has_position and current_price > 0:
                    if profit_loss > 0:
                        st.success(f"💰 +{profit_loss:.2f}元 ({profit_loss_pct:+.2f}%)")
                    elif profit_loss < 0:
                        st.error(f"📉 {profit_loss:.2f}元 ({profit_loss_pct:+.2f}%)")
                    else:
                        st.info("持平")
            
            with col4:
                if is_running:
                    if st.button("⏹️ 停止", key=f"stop_{task['id']}"):
                        engine.stop_monitor(task['stock_code'])
                        # 停止时更新数据库状态为禁用
                        db.update_monitor_task(task['stock_code'], {'enabled': 0})
                        st.success("已停止")
                        st.rerun()
                else:
                    # 启动按钮始终可点击（只要任务未运行）
                    if st.button("▶️ 启动", key=f"start_{task['id']}"):
                        # 启动监控
                        raw_concepts = task.get('stock_concepts')
                        start_concepts: List[str] = []
                        if isinstance(raw_concepts, str) and raw_concepts.strip():
                            try:
                                parsed = json.loads(raw_concepts)
                                if isinstance(parsed, list):
                                    start_concepts = [str(x).strip() for x in parsed if str(x).strip()]
                            except Exception:
                                start_concepts = _parse_tag_list(raw_concepts)

                        raw_schedule_times = task.get('ai_schedule_times')
                        start_schedule_times: List[str] = []
                        if isinstance(raw_schedule_times, str) and raw_schedule_times.strip():
                            try:
                                parsed_schedule = json.loads(raw_schedule_times)
                                if isinstance(parsed_schedule, list):
                                    start_schedule_times = [str(x).strip() for x in parsed_schedule if str(x).strip()]
                            except Exception:
                                start_schedule_times = _normalize_schedule_times(raw_schedule_times)

                        engine.start_monitor(
                            stock_code=task['stock_code'],
                            check_interval=task['check_interval'],
                            auto_trade=task['auto_trade'] == 1,
                            notify=True,
                            has_position=has_position == 1,
                            position_cost=position_cost,
                            position_quantity=position_quantity,
                            trading_hours_only=task.get('trading_hours_only', 1) == 1,
                            douban_author_id=str(task.get('douban_author_id') or ''),
                            douban_author_name=str(task.get('douban_author_name') or ''),
                            douban_pattern_version=int(task.get('douban_pattern_version') or 0),
                            stock_concepts=start_concepts,
                            daily_strong_themes=st.session_state.get('smart_monitor_daily_strong_themes', []) or [],
                            ai_schedule_enabled=task.get('ai_schedule_enabled', 0) == 1,
                            ai_schedule_times=start_schedule_times,
                            ai_daily_max_calls=int(task.get('ai_daily_max_calls') or 3),
                        )

                        # 启动时更新数据库状态为启用
                        db.update_monitor_task(task['stock_code'], {'enabled': 1})
                        st.success("已启动")
                        st.rerun()
            
            with col5:
                if st.button("🗑️ 删除", key=f"del_{task['id']}"):
                    # 如果正在运行，先停止
                    if task['stock_code'] in engine.monitoring_threads:
                        engine.stop_monitor(task['stock_code'])

                    db.delete_monitor_task(task['id'])
                    st.success("已删除")
                    st.rerun()

            with col6:
                if st.button("✏️ 编辑", key=f"edit_{task['id']}"):
                    st.session_state[f"editing_task_{task['id']}"] = not st.session_state.get(f"editing_task_{task['id']}", False)
                    st.rerun()

            if st.session_state.get(f"editing_task_{task['id']}", False):
                with st.form(f"edit_task_form_{task['id']}", clear_on_submit=False):
                    st.markdown("### ✏️ 编辑任务")
                    e1, e2 = st.columns(2)
                    with e1:
                        edit_interval = st.slider(
                            "检查间隔(秒)",
                            60,
                            3600,
                            int(task.get('check_interval') or 300),
                            key=f"edit_interval_{task['id']}",
                        )
                        raw_edit_concepts = task.get('stock_concepts')
                        edit_concepts_default = ''
                        if isinstance(raw_edit_concepts, str) and raw_edit_concepts.strip():
                            try:
                                parsed = json.loads(raw_edit_concepts)
                                if isinstance(parsed, list):
                                    edit_concepts_default = ', '.join([str(x).strip() for x in parsed if str(x).strip()])
                                else:
                                    edit_concepts_default = raw_edit_concepts
                            except Exception:
                                edit_concepts_default = raw_edit_concepts
                        edit_stock_concepts_input = st.text_input(
                            "该股题材概念（逗号分隔）",
                            value=edit_concepts_default,
                            key=f"edit_concepts_{task['id']}",
                        )
                        edit_has_position = st.checkbox(
                            "已持仓该股票",
                            value=bool(task.get('has_position', 0) == 1),
                            key=f"edit_has_pos_{task['id']}",
                        )
                        edit_position_cost = st.number_input(
                            "持仓成本(元)",
                            min_value=0.01,
                            value=float(task.get('position_cost') or 10.0),
                            step=0.01,
                            key=f"edit_pos_cost_{task['id']}",
                        )
                        edit_position_qty = st.number_input(
                            "持仓数量(股)",
                            min_value=100,
                            value=int(task.get('position_quantity') or 100),
                            step=100,
                            key=f"edit_pos_qty_{task['id']}",
                        )
                    with e2:
                        edit_use_pattern = st.checkbox(
                            "启用作者交易模式",
                            value=bool(str(task.get('douban_author_id') or '').strip() and int(task.get('douban_pattern_version') or 0) > 0),
                            key=f"edit_use_pattern_{task['id']}",
                        )

                        edit_schedule_enabled = st.checkbox(
                            "启用定时AI请求",
                            value=bool(task.get('ai_schedule_enabled', 0) == 1),
                            key=f"edit_schedule_enabled_{task['id']}",
                        )
                        edit_daily_max_calls = st.number_input(
                            "每天最多AI请求次数",
                            min_value=1,
                            max_value=20,
                            value=int(task.get('ai_daily_max_calls') or 3),
                            step=1,
                            key=f"edit_schedule_max_{task['id']}",
                        )

                        raw_edit_schedule = task.get('ai_schedule_times')
                        edit_schedule_default = ''
                        if isinstance(raw_edit_schedule, str) and raw_edit_schedule.strip():
                            try:
                                parsed_schedule = json.loads(raw_edit_schedule)
                                if isinstance(parsed_schedule, list):
                                    edit_schedule_default = ','.join([str(x).strip() for x in parsed_schedule if str(x).strip()])
                                else:
                                    edit_schedule_default = raw_edit_schedule
                            except Exception:
                                edit_schedule_default = raw_edit_schedule

                        edit_schedule_times_input = st.text_input(
                            "AI触发时间点（逗号分隔）",
                            value=edit_schedule_default,
                            key=f"edit_schedule_times_{task['id']}",
                        )

                        authors = douban_author_db.list_authors(limit=200)
                        author_options = {f"{a.get('author_name')} ({a.get('author_id')})": a for a in (authors or [])}
                        author_labels = list(author_options.keys()) if author_options else ["暂无可用作者"]

                        default_author_idx = 0
                        current_author_id = str(task.get('douban_author_id') or '').strip()
                        if current_author_id and author_options:
                            for i, lb in enumerate(author_labels):
                                a = author_options.get(lb) or {}
                                if str(a.get('author_id') or '').strip() == current_author_id:
                                    default_author_idx = i
                                    break

                        edit_author_label = st.selectbox(
                            "选择作者",
                            options=author_labels,
                            index=default_author_idx,
                            key=f"edit_author_{task['id']}",
                            disabled=(not author_options),
                        )
                        edit_author = author_options.get(edit_author_label) or {}

                        ver_options = {}
                        if edit_author:
                            patterns = douban_author_db.list_author_patterns(
                                author_id=str(edit_author.get('author_id') or ''),
                                limit=30,
                            )
                            ver_options = {
                                f"v{int(p.get('pattern_version') or 0)} | conf={float(p.get('confidence') or 0):.1f}": p
                                for p in patterns
                            }
                        ver_labels = list(ver_options.keys()) if ver_options else ["暂无模式版本"]

                        default_ver_idx = 0
                        current_ver = int(task.get('douban_pattern_version') or 0)
                        if current_ver > 0 and ver_options:
                            for i, lb in enumerate(ver_labels):
                                p = ver_options.get(lb) or {}
                                if int(p.get('pattern_version') or 0) == current_ver:
                                    default_ver_idx = i
                                    break

                        edit_ver_label = st.selectbox(
                            "选择模式版本",
                            options=ver_labels,
                            index=default_ver_idx,
                            key=f"edit_ver_{task['id']}",
                            disabled=(not ver_options),
                        )
                        edit_pattern_version = int((ver_options.get(edit_ver_label) or {}).get('pattern_version') or 0)

                    s1, s2 = st.columns(2)
                    with s1:
                        save_edit = st.form_submit_button("💾 保存修改", type="primary", width='stretch')
                    with s2:
                        cancel_edit = st.form_submit_button("取消", width='stretch')

                if cancel_edit:
                    st.session_state[f"editing_task_{task['id']}"] = False
                    st.rerun()

                if save_edit:
                    normalized_edit_schedule_times = _normalize_schedule_times(edit_schedule_times_input)
                    if edit_schedule_enabled and not normalized_edit_schedule_times:
                        st.error("❌ 已启用定时AI请求，但未提供有效时间（格式需为HH:MM）")
                        st.stop()

                    edit_data = {
                        'check_interval': int(edit_interval),
                        'has_position': 1 if edit_has_position else 0,
                        'position_cost': float(edit_position_cost) if edit_has_position else 0,
                        'position_quantity': int(edit_position_qty) if edit_has_position else 0,
                        'position_date': datetime.now().strftime('%Y-%m-%d') if edit_has_position else None,
                        'stock_concepts': json.dumps(_parse_tag_list(edit_stock_concepts_input), ensure_ascii=False),
                        'ai_schedule_enabled': 1 if edit_schedule_enabled else 0,
                        'ai_daily_max_calls': int(edit_daily_max_calls),
                        'ai_schedule_times': json.dumps(normalized_edit_schedule_times, ensure_ascii=False),
                    }

                    if edit_use_pattern:
                        edit_author_id = str((edit_author or {}).get('author_id') or '').strip()
                        edit_author_name = str((edit_author or {}).get('author_name') or '').strip()
                        if not edit_author_id or edit_pattern_version <= 0:
                            st.error("❌ 已启用作者模式，但作者或版本未选择完整")
                            st.stop()
                        edit_data.update({
                            'douban_author_id': edit_author_id,
                            'douban_author_name': edit_author_name,
                            'douban_pattern_version': int(edit_pattern_version),
                        })
                    else:
                        edit_data.update({
                            'douban_author_id': None,
                            'douban_author_name': None,
                            'douban_pattern_version': None,
                        })

                    was_running = task['stock_code'] in engine.monitoring_threads
                    if was_running:
                        engine.stop_monitor(task['stock_code'])

                    db.update_monitor_task(task['stock_code'], edit_data)

                    updated_task = next(
                        (x for x in db.get_monitor_tasks(enabled_only=False) if x.get('stock_code') == task['stock_code']),
                        None,
                    )
                    if was_running and updated_task:
                        updated_raw_concepts = updated_task.get('stock_concepts')
                        updated_concepts: List[str] = []
                        if isinstance(updated_raw_concepts, str) and updated_raw_concepts.strip():
                            try:
                                parsed = json.loads(updated_raw_concepts)
                                if isinstance(parsed, list):
                                    updated_concepts = [str(x).strip() for x in parsed if str(x).strip()]
                            except Exception:
                                updated_concepts = _parse_tag_list(updated_raw_concepts)

                        updated_raw_schedule_times = updated_task.get('ai_schedule_times')
                        updated_schedule_times: List[str] = []
                        if isinstance(updated_raw_schedule_times, str) and updated_raw_schedule_times.strip():
                            try:
                                parsed_schedule = json.loads(updated_raw_schedule_times)
                                if isinstance(parsed_schedule, list):
                                    updated_schedule_times = [str(x).strip() for x in parsed_schedule if str(x).strip()]
                            except Exception:
                                updated_schedule_times = _normalize_schedule_times(updated_raw_schedule_times)

                        engine.start_monitor(
                            stock_code=updated_task['stock_code'],
                            check_interval=int(updated_task.get('check_interval') or 300),
                            auto_trade=updated_task.get('auto_trade') == 1,
                            notify=True,
                            has_position=updated_task.get('has_position') == 1,
                            position_cost=float(updated_task.get('position_cost') or 0),
                            position_quantity=int(updated_task.get('position_quantity') or 0),
                            trading_hours_only=updated_task.get('trading_hours_only', 1) == 1,
                            douban_author_id=str(updated_task.get('douban_author_id') or ''),
                            douban_author_name=str(updated_task.get('douban_author_name') or ''),
                            douban_pattern_version=int(updated_task.get('douban_pattern_version') or 0),
                            stock_concepts=updated_concepts,
                            daily_strong_themes=st.session_state.get('smart_monitor_daily_strong_themes', []) or [],
                            ai_schedule_enabled=updated_task.get('ai_schedule_enabled', 0) == 1,
                            ai_schedule_times=updated_schedule_times,
                            ai_daily_max_calls=int(updated_task.get('ai_daily_max_calls') or 3),
                        )


                    st.success("✅ 任务已更新")
                    st.session_state[f"editing_task_{task['id']}"] = False
                    st.rerun()
            
            # K线图和AI决策详情（可展开）
            with st.expander(f"📊 K线图 & AI决策 - {task['task_name']}", expanded=False):
                _render_task_kline_and_decisions(task, db, engine)
            
            st.markdown("---")


def render_mainboard_rapid_rise_linkage():
    """主板快速拉升联动界面"""

    st.header("⚡ 主板快拉联动")

    cfg = rapid_rise_monitor_service.get_runtime_config()
    status = cfg.get('last_status') or {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("运行状态", "运行中" if cfg.get('running') else "未运行")
    c2.metric("扫描股票数", int(cfg.get('symbols_count') or 0))
    c3.metric("最近批次行情", int(status.get('last_quotes_count') or 0))
    c4.metric("最近触发数", int(status.get('last_event_count') or 0))

    st.caption(
        f"最近轮询: {status.get('last_loop_at') or '-'} | "
        f"跳过原因: {status.get('last_skip_reason') or '-'} | "
        f"最近错误: {status.get('last_error') or '-'}"
    )

    b1, b2, b3 = st.columns([1, 1, 2])
    with b1:
        if st.button("▶️ 启动快拉", key="sm_rr_start", width='stretch'):
            rapid_rise_monitor_service.start()
            st.success("已启动")
            st.rerun()
    with b2:
        if st.button("⏹️ 停止快拉", key="sm_rr_stop", width='stretch'):
            rapid_rise_monitor_service.stop()
            st.success("已停止")
            st.rerun()
    with b3:
        if st.button("🔄 刷新状态", key="sm_rr_refresh", width='stretch'):
            st.rerun()

    st.markdown("### ⚙️ 扫描参数")
    with st.form("sm_rr_config_form", clear_on_submit=False):
        p1, p2, p3 = st.columns(3)
        with p1:
            interval_sec = st.number_input("扫描间隔(秒)", min_value=5, max_value=600, value=int(cfg.get('interval_sec') or 30), step=5)
            batch_size = st.number_input("批量大小", min_value=50, max_value=2000, value=int(cfg.get('batch_size') or 300), step=50)
            mainboard_only = st.checkbox("仅主板", value=bool(cfg.get('mainboard_only', True)))
        with p2:
            threshold_1m = st.number_input("1分钟涨幅阈值(%)", min_value=0.1, max_value=10.0, value=float(cfg.get('threshold_1m') or 1.2), step=0.1)
            threshold_3m = st.number_input("3分钟涨幅阈值(%)", min_value=0.1, max_value=20.0, value=float(cfg.get('threshold_3m') or 2.0), step=0.1)
            threshold_amt_ratio = st.number_input("1m/20m量比阈值", min_value=0.1, max_value=20.0, value=float(cfg.get('threshold_amt_ratio') or 1.8), step=0.1)
        with p3:
            max_push_per_minute = st.number_input("每分钟最大推送数", min_value=1, max_value=200, value=int(cfg.get('max_push_per_minute') or 30), step=1)
            summary_interval_sec = st.number_input("摘要推送间隔(秒)", min_value=60, max_value=3600, value=int(cfg.get('summary_interval_sec') or 180), step=30)
            summary_max_items = st.number_input("摘要最大题材数", min_value=1, max_value=50, value=int(cfg.get('summary_max_items') or 8), step=1)
            exclude_st = st.checkbox("排除ST（预留）", value=bool(cfg.get('exclude_st', True)))

        apply_cfg = st.form_submit_button("💾 应用参数", type="primary", width='stretch')

    if apply_cfg:
        rapid_rise_monitor_service.update_runtime_config(
            interval_sec=int(interval_sec),
            batch_size=int(batch_size),
            threshold_1m=float(threshold_1m),
            threshold_3m=float(threshold_3m),
            threshold_amt_ratio=float(threshold_amt_ratio),
            max_push_per_minute=int(max_push_per_minute),
            summary_interval_sec=int(summary_interval_sec),
            summary_max_items=int(summary_max_items),
            mainboard_only=bool(mainboard_only),
            exclude_st=bool(exclude_st),
        )
        st.success("参数已更新")
        st.rerun()

    st.markdown("### 📡 最近快速拉升事件")
    events = monitor_db.get_recent_rapid_rise_events(limit=50)
    if not events:
        st.info("暂无快速拉升事件")
        return

    view_rows = []
    for e in events:
        view_rows.append({
            "时间": e.get('event_time'),
            "代码": e.get('symbol'),
            "名称": e.get('name'),
            "级别": e.get('trigger_level'),
            "1m涨幅%": e.get('rise_1m'),
            "3m涨幅%": e.get('rise_3m'),
            "量比": e.get('amount_ratio_1m20'),
            "题材": e.get('theme_tokens') or '-',
            "题材分": e.get('theme_hist_score'),
            "联动分": e.get('peer_linkage_score'),
        })

    st.dataframe(pd.DataFrame(view_rows), hide_index=True, width='stretch')

    st.markdown("### 🔗 联动明细")
    event_options = [f"{e.get('event_time')} | {e.get('symbol')} | {e.get('trigger_level')}" for e in events]
    selected_event_label = st.selectbox("选择事件", options=event_options, key="sm_rr_event_selector")
    selected_event = events[event_options.index(selected_event_label)] if selected_event_label else None

    if selected_event:
        payload = {}
        raw_payload = selected_event.get('payload_json')
        if isinstance(raw_payload, str) and raw_payload.strip():
            try:
                payload = json.loads(raw_payload)
            except Exception:
                payload = {}

        peer_examples = (payload.get('peer_examples') or []) if isinstance(payload, dict) else []
        if peer_examples:
            peer_rows = []
            for p in peer_examples:
                peer_rows.append({
                    "代码": str(p.get('code') or ''),
                    "名称": str(p.get('name') or ''),
                    "共现次数": int(float(p.get('cooccur_count') or 0)),
                    "历史均涨%": float(p.get('avg_pct_chg') or 0),
                    "题材": ' / '.join([str(x) for x in (p.get('themes') or [])[:5]]) if isinstance(p.get('themes'), list) else '-',
                })
            st.dataframe(pd.DataFrame(peer_rows), hide_index=True, width='stretch')
        else:
            st.info("该事件暂无同概念样本")


def render_position_management():
    """持仓管理界面"""
    
    st.header("📈 持仓管理")
    
    engine = st.session_state.engine
    qmt = engine.qmt
    
    # 获取账户信息
    account_info = qmt.get_account_info()
    
    st.markdown("### 💰 账户概览")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总资产", f"¥{account_info['total_value']:,.2f}")
    col2.metric("可用资金", f"¥{account_info['available_cash']:,.2f}")
    col3.metric("持仓数量", f"{account_info['positions_count']}个")
    col4.metric("总盈亏", f"¥{account_info['total_profit_loss']:,.2f}")
    
    # 获取持仓列表
    positions = qmt.get_all_positions()
    
    if not positions:
        st.info("当前无持仓")
        return
    
    st.markdown("### 📊 持仓列表")
    
    # 转换为DataFrame
    df = pd.DataFrame(positions)
    
    # 显示表格
    st.dataframe(
        df[[
            'stock_code', 'stock_name', 'quantity', 'can_sell',
            'cost_price', 'current_price', 'profit_loss', 'profit_loss_pct'
        ]],
        column_config={
            "stock_code": "代码",
            "stock_name": "名称",
            "quantity": "持仓",
            "can_sell": "可卖",
            "cost_price": "成本价",
            "current_price": "现价",
            "profit_loss": "盈亏",
            "profit_loss_pct": "盈亏%"
        },
        hide_index=True,
        width='stretch'
    )
    
    # 单只股票操作
    st.markdown("### ⚡ 快速操作")
    
    selected_stock = st.selectbox(
        "选择股票",
        options=[f"{p['stock_code']} {p['stock_name']}" for p in positions]
    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🔍 AI分析", type="secondary"):
            stock_code = selected_stock.split()[0]
            with st.spinner("分析中..."):
                result = engine.analyze_stock(stock_code, auto_trade=False)
                if result['success']:
                    st.success("分析完成，查看'实时分析'标签页")
    
    with col2:
        if st.button("📤 卖出", type="primary"):
            stock_code = selected_stock.split()[0]
            # 这里可以添加卖出确认对话框
            st.warning("请在'实时分析'中使用AI决策后卖出")


def render_history():
    """历史记录界面"""
    
    st.header("📜 历史记录")
    
    db = st.session_state.db
    
    tab1, tab2, tab3 = st.tabs(["AI决策历史", "交易记录", "通知记录"])
    
    # AI决策历史
    with tab1:
        st.subheader("🤖 AI决策历史")
        
        decisions = db.get_ai_decisions(limit=50)
        
        if not decisions:
            st.info("暂无决策记录")
        else:
            for dec in decisions:
                with st.expander(
                    f"{dec['decision_time']} - {dec['stock_code']} {dec['stock_name']} "
                    f"- {dec['action']} (信心度{dec['confidence']}%)"
                ):
                    col1, col2 = st.columns([1, 3])
                    
                    with col1:
                        st.write(f"**时段:** {dec['trading_session']}")
                        st.write(f"**风险:** {dec['risk_level']}")
                        st.write(f"**仓位:** {dec['position_size_pct']}%")
                    
                    with col2:
                        st.write("**决策理由:**")
                        st.text(dec['reasoning'])
    
    # 交易记录
    with tab2:
        st.subheader("💱 交易记录")
        
        trades = db.get_trade_records(limit=50)
        
        if not trades:
            st.info("暂无交易记录")
        else:
            df = pd.DataFrame(trades)
            st.dataframe(
                df[[
                    'trade_time', 'stock_code', 'stock_name', 'trade_type',
                    'quantity', 'price', 'amount', 'profit_loss'
                ]],
                column_config={
                    "trade_time": "时间",
                    "stock_code": "代码",
                    "stock_name": "名称",
                    "trade_type": "类型",
                    "quantity": "数量",
                    "price": "价格",
                    "amount": "金额",
                    "profit_loss": "盈亏"
                },
                hide_index=True,
                width='stretch'
            )
    
    # 通知记录
    with tab3:
        st.subheader("📬 通知记录")
        st.info("通知记录功能开发中...")


def render_settings():
    """系统设置界面（跳转到主程序的环境配置）"""
    
    st.header("⚙️ 系统设置")
    
    st.info("""
    ### 📌 配置说明
    
    智能盯盘使用主程序的统一配置系统，包括：
    - 🤖 **DeepSeek API** - AI决策引擎
    - 🔌 **MiniQMT** - 量化交易接口
    - 📧 **邮件通知** - SMTP配置
    - 🔔 **Webhook** - 钉钉/飞书通知
    
    请前往主程序的 **"环境配置"** 页面进行统一配置。
    """)
    
    # 显示当前配置状态
    st.markdown("### 📊 当前配置状态")
    
    config = config_manager.read_env()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**🤖 DeepSeek API**")
        api_key = config.get('DEEPSEEK_API_KEY', '')
        if api_key:
            st.success(f"✅ 已配置（{api_key[:8]}...）")
        else:
            st.error("❌ 未配置")
        
        st.markdown("**🔌 MiniQMT**")
        miniqmt_enabled = config.get('MINIQMT_ENABLED', 'false').lower() == 'true'
        if miniqmt_enabled:
            account_id = config.get('MINIQMT_ACCOUNT_ID', '')
            st.success(f"✅ 已启用（账户：{account_id or '未设置'}）")
        else:
            st.warning("⚠️ 未启用（使用模拟交易）")
    
    with col2:
        st.markdown("**📧 邮件通知**")
        email_enabled = config.get('EMAIL_ENABLED', 'false').lower() == 'true'
        if email_enabled:
            email_to = config.get('EMAIL_TO', '')
            st.success(f"✅ 已启用（{email_to}）")
        else:
            st.warning("⚠️ 未启用")
        
        st.markdown("**🔔 Webhook通知**")
        webhook_enabled = config.get('WEBHOOK_ENABLED', 'false').lower() == 'true'
        if webhook_enabled:
            webhook_type = config.get('WEBHOOK_TYPE', 'dingtalk')
            st.success(f"✅ 已启用（{webhook_type}）")
        else:
            st.warning("⚠️ 未启用")
    
    st.markdown("---")
    
    # 快速跳转按钮
    st.markdown("### 🔧 配置管理")
    
    st.info("""
    **配置步骤：**
    1. 点击左侧菜单 → **"环境配置"**
    2. 填写所需的配置项
    3. 点击 **"保存配置"**
    4. 返回智能盯盘页面
    5. 刷新页面使配置生效
    """)
    
    if st.button("🔄 重新加载配置", type="primary"):
        config_manager.reload_config()
        st.success("✅ 配置已重新加载")
        st.info("💡 如果修改了配置，请刷新页面（Ctrl+R）")
        st.rerun()


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


def _render_tdx_quote_panel(stock_code: str):
    raw = _fetch_tdx_quote_raw(stock_code)
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
    m4.metric("成交额(元)", f"{float(raw.get('Amount') or 0)/1000:.0f}")

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


def _render_tdx_minute_panel(stock_code: str):
    rows = _fetch_tdx_minute_raw(stock_code)
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
    st.plotly_chart(fig, use_container_width=True, config={'responsive': True})


def _render_tdx_trade_panel(stock_code: str):
    rows = _fetch_tdx_trade_raw(stock_code)
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
    st.plotly_chart(fig, use_container_width=True, config={'responsive': True})
    st.dataframe(df.tail(80), use_container_width=True, height=240)


def _render_task_kline_and_decisions(task: Dict, db: SmartMonitorDB, engine):
    """
    渲染单个任务的K线图和AI决策
    
    Args:
        task: 任务信息
        db: 数据库实例
        engine: 监控引擎实例
    """
    from modules.smart_monitor.smart_monitor_kline import SmartMonitorKline
    from modules.smart_monitor.smart_monitor_data import SmartMonitorDataFetcher
    
    stock_code = task['stock_code']
    stock_name = task.get('stock_name', stock_code)
    
    # 创建两列：左侧K线图，右侧AI决策列表
    col_chart, col_decisions = st.columns([2, 1])
    
    with col_chart:
        st.markdown("#### 📈 行情图表")

        if st.button("🔄 刷新K线", key=f"refresh_kline_{task['id']}"):
            st.rerun()

        tabs = st.tabs(["五档行情", "K线图", "分时图", "分时成交"])
        with tabs[0]:
            _render_tdx_quote_panel(stock_code)
        with tabs[2]:
            _render_tdx_minute_panel(stock_code)
        with tabs[3]:
            _render_tdx_trade_panel(stock_code)

        with tabs[1]:
            try:
                kline = SmartMonitorKline()
                data_fetcher = SmartMonitorDataFetcher()

                with st.spinner(f"正在获取 {stock_code} 的K线数据..."):
                    kline_data = kline.get_kline_data(stock_code, days=60, data_fetcher=data_fetcher)

                if kline_data is not None and not kline_data.empty:
                    ai_decisions = db.get_ai_decisions(
                        stock_code=stock_code,
                        limit=100
                    )

                    from datetime import timedelta
                    if ai_decisions:
                        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
                        ai_decisions = [
                            d for d in ai_decisions
                            if d.get('decision_time', '').split()[0] >= start_date
                        ]

                    fig = kline.create_kline_with_decisions(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        kline_data=kline_data,
                        ai_decisions=ai_decisions,
                        show_volume=True,
                        show_ma=True,
                        height=500
                    )

                    st.plotly_chart(fig, use_container_width=True, config={'responsive': True})
                    st.caption(f"📅 数据时间范围：{kline_data['日期'].min()} ~ {kline_data['日期'].max()}")
                else:
                    st.error(f"❌ 无法获取 {stock_code} 的K线数据")

            except Exception as e:
                st.error(f"❌ K线图加载失败: {str(e)}")
                import traceback
                st.text(traceback.format_exc())
    
    with col_decisions:
        st.markdown("#### 🤖 AI决策历史")
        
        # 添加刷新按钮
        if st.button("🔄 刷新决策", key=f"refresh_decisions_{task['id']}"):
            st.rerun()
        
        # 获取最近的AI决策（最近5条）
        try:
            recent_decisions = db.get_ai_decisions(
                stock_code=stock_code,
                limit=5
            )
            
            if recent_decisions:
                for idx, decision in enumerate(recent_decisions):
                    action = decision.get('action', 'unknown')
                    decision_time = decision.get('decision_time', '')
                    confidence = decision.get('confidence', 0)
                    reasoning = decision.get('reasoning', '无')
                    executed = decision.get('executed', 0)
                    
                    # 决策类型图标和颜色
                    action_icons = {
                        'buy': '🔺',
                        'sell': '🔻',
                        'add_position': '⬆️',
                        'reduce_position': '⬇️',
                        'hold': '⏸️'
                    }
                    
                    action_colors = {
                        'buy': '#ef5350',
                        'sell': '#26a69a',
                        'add_position': '#ff9800',
                        'reduce_position': '#9c27b0',
                        'hold': '#607d8b'
                    }
                    
                    action_names = {
                        'buy': '买入',
                        'sell': '卖出',
                        'add_position': '加仓',
                        'reduce_position': '减仓',
                        'hold': '持有'
                    }
                    
                    icon = action_icons.get(action, '❓')
                    action_name = action_names.get(action, action)
                    status_icon = '✅' if executed else '⏳'
                    reasoning_preview = f"{reasoning[:100]}{'...' if len(reasoning) > 100 else ''}"

                    with st.container():
                        st.markdown("<div class='agent-card'>", unsafe_allow_html=True)
                        st.markdown(f"**{icon} {action_name}** {status_icon}")
                        st.caption(decision_time)
                        st.write(f"**置信度:** {confidence}%")
                        st.write(f"**推理:** {reasoning_preview}")
                        st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.info("📭 暂无AI决策记录")
                st.caption("启动监控后，AI会定期分析并记录决策")
                
        except Exception as e:
            st.error(f"❌ 加载决策历史失败: {str(e)}")


if __name__ == '__main__':
    smart_monitor_ui()

