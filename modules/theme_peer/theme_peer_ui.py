"""同题材补涨 UI - 基于共现历史数据"""

import os
import logging
import db_adapter as sqlite3
from datetime import datetime
from typing import Dict, Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import config
from modules.longhubang.longhubang_history import LonghubangHistoryService
from modules.smart_monitor.smart_monitor_kline import SmartMonitorKline
from services.data_source_manager import data_source_manager
from ui.components import render_top_nav


logger = logging.getLogger(__name__)


def _resolve_history_db_path() -> str:
    base_dir = os.path.dirname(__file__)
    candidates = [
        os.getenv("LONGHUBANG_DB_PATH"),
        os.getenv("LONGHUBANG_DB"),
        os.path.join(base_dir, "data", "longhubang.db"),
        os.path.join(base_dir, "longhubang.db"),
    ]
    existing = []
    for item in candidates:
        path = str(item or "").strip()
        if path and os.path.exists(path):
            existing.append(path)

    # 优先选择有共现群组数据的数据库
    for path in existing:
        try:
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM longhubang_cooccur_group_2026").fetchone()[0]
            conn.close()
            if int(count or 0) > 0:
                return path
        except Exception:
            pass

    if existing:
        return existing[0]

    # 兜底：回退到项目根目录数据库
    return os.path.join(base_dir, "longhubang.db")


DB_PATH = _resolve_history_db_path()


def _fetch_tdx_quote_raw(code: str):
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


def _fetch_tdx_minute_raw(code: str):
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


def _fetch_tdx_trade_raw(code: str):
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


def _get_kline_data(code: str, days: int = 60):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - pd.Timedelta(days=max(int(days), 60) + 30)).strftime('%Y%m%d')
    hist = data_source_manager.get_stock_hist_data(symbol=str(code), start_date=start_date, end_date=end_date, adjust='qfq')
    if hist is None or isinstance(hist, dict) or getattr(hist, 'empty', True):
        logger.warning(
            "KLINE_MISS theme_peer_ui._get_kline_data code=%s start=%s end=%s hist_type=%s empty=%s",
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
            "KLINE_EMPTY_AFTER_CLEAN theme_peer_ui._get_kline_data code=%s start=%s end=%s rows_before=%s",
            str(code),
            start_date,
            end_date,
            int(len(work.index)) if hasattr(work, 'index') else 0,
        )
        return None
    today8 = datetime.now().strftime('%Y%m%d')
    if bool((df['日期'].dt.strftime('%Y%m%d') == today8).any()):
        return df
    raw = _fetch_tdx_quote_raw(code)
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
                        '日期': [pd.to_datetime(today8, format='%Y%m%d')],
                        '开盘': [open_price],
                        '最高': [max(high_price, price, open_price)],
                        '最低': [min(low_price, price, open_price)],
                        '收盘': [price],
                        '成交量': [float(raw.get('TotalHand') or 0)],
                    }
                )
                mask = df['日期'].dt.strftime('%Y%m%d') == today8
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


def _render_stock_detail(code: str, name: str):
    raw = _fetch_tdx_quote_raw(code)
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
        kline_data = _get_kline_data(code=code, days=60)
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
        rows = _fetch_tdx_minute_raw(code)
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
        rows = _fetch_tdx_trade_raw(code)
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


def display_theme_peer_selector():
    render_top_nav("🧩 同题材补涨", "输入股票代码，查询历史同题材跟涨股票（基于龙虎榜共现数据）")

    with st.expander("💡 功能说明", expanded=False):
        st.markdown(
            """
- 基于龙虎榜历史共现群组数据，查询与目标股票同题材一起上榜的其他股票
- 按历史平均涨幅排序，展示最强的同题材跟涨股票
- 共现次数越多，说明该股票与目标股票的题材关联越强
- 历史平均涨幅反映了该股票在同题材行情中的表现
            """
        )
        st.caption(f"数据库路径: {DB_PATH}")

    col1, col2, col3 = st.columns([2, 2, 1.5])
    with col1:
        symbol = st.text_input("目标股票代码", placeholder="例如 600410 或 002131", key="theme_peer_symbol")
    with col2:
        min_cooccur = st.slider("最小共现次数", min_value=1, max_value=5, value=1, step=1, key="theme_peer_min_cooccur")
    with col3:
        top_n = st.slider("返回数量", min_value=5, max_value=50, value=20, step=1, key="theme_peer_topn")

    run_col1, run_col2 = st.columns([1, 4])
    with run_col1:
        run = st.button("🚀 查询", type="primary", use_container_width=True, key="theme_peer_run")
    with run_col2:
        if st.button("🧹 清空结果", key="theme_peer_clear"):
            st.session_state.pop("theme_peer_result", None)
            st.rerun()

    if run:
        if not symbol.strip():
            st.warning("请输入目标股票代码")
        else:
            with st.spinner("正在查询历史共现数据，请稍候..."):
                try:
                    service = LonghubangHistoryService(db_path=DB_PATH)
                    result = service.query_theme_peer_stocks(
                        stock_code=symbol.strip(),
                        min_cooccur_count=min_cooccur,
                        top_n=top_n,
                        with_realtime=True,
                        realtime_timeout_sec=2.5,
                        realtime_weight=0.35,
                    )
                    st.session_state["theme_peer_result"] = result
                except Exception as e:
                    st.error(f"查询出错: {str(e)}")
                    import traceback
                    st.code(traceback.format_exc())

    if "theme_peer_result" in st.session_state:
        _render_result(st.session_state["theme_peer_result"])


def _render_result(result: Dict[str, Any]):
    if not result.get("success"):
        st.error(f"❌ 查询失败: {result.get('error', '未知错误')}")
        # 调试信息
        with st.expander("🔍 调试信息", expanded=False):
            st.json(result)
        return

    stock_name = result.get("stock_name") or ""
    stock_code = result.get("stock_code") or ""
    themes = result.get("themes") or []
    peer_stocks = result.get("peer_stocks") or []

    st.success("✅ 查询完成")
    with_realtime = bool(result.get("with_realtime", False))
    realtime_weight = float(result.get("realtime_weight", 0.35) or 0.35)
    realtime_meta = dict(result.get("realtime_meta") or {})
    realtime_base = str(realtime_meta.get("base_url", "") or "")
    realtime_ok = int(realtime_meta.get("ok_count", 0) or 0)
    realtime_total = int(realtime_meta.get("total", 0) or 0)
    target_rt = result.get("target_realtime_pct_chg", None)
    if target_rt is not None:
        target_rt_text = f"{float(target_rt):+.2f}%"
    else:
        target_rt_text = "--"
    st.markdown(
        f"**目标股票**: {stock_code} {stock_name}（实时{target_rt_text}）  |  "
        f"**参与题材**: {'、'.join(themes[:8]) if themes else '暂无'}  |  "
        f"**找到**: {len(peer_stocks)} 只同题材跟涨股票  |  "
        f"**实时增强**: {'开启' if with_realtime else '关闭'}（权重{realtime_weight:.2f}）"
    )
    if with_realtime:
        st.caption(f"实时接口: {realtime_base or '-'} | 实时成功: {realtime_ok}/{realtime_total}")

    if not peer_stocks:
        st.warning("未找到符合条件的同题材跟涨股票")
        return

    # 转换为DataFrame
    df = pd.DataFrame(peer_stocks)

    # 重命名列
    rename_map = {
        "code": "代码",
        "name": "名称",
        "cooccur_count": "共现次数",
        "avg_pct_chg": "历史平均涨幅%",
        "realtime_pct_chg": "实时涨跌幅%",
        "fusion_score": "融合得分",
        "realtime_update_time": "更新时间",
        "realtime_status": "实时状态",
        "themes": "题材",
    }
    df = df.rename(columns=rename_map)

    # 格式化题材列
    if "题材" in df.columns:
        df["题材"] = df["题材"].apply(
            lambda x: "、".join(x[:5]) if isinstance(x, list) else str(x)
        )

    if "实时状态" in df.columns:
        df["实时状态"] = df["实时状态"].apply(
            lambda x: "正常" if str(x) == "ok" else ("超时" if str(x) == "timeout" else "回退")
        )

    # 添加排名列
    df.insert(0, "排名", range(1, len(df) + 1))

    # 固定展示列顺序（实时涨跌幅在历史平均涨幅左侧）
    preferred_order = [
        "排名", "代码", "名称", "共现次数", "实时涨跌幅%", "历史平均涨幅%",
        "融合得分", "更新时间", "实时状态", "题材",
    ]
    ordered_columns = [col for col in preferred_order if col in df.columns]
    remaining_columns = [col for col in df.columns if col not in ordered_columns]
    table_df = df[ordered_columns + remaining_columns]

    left, right = st.columns([1.15, 1.85])
    with left:
        st.caption("点击表格行查看右侧详情")
        event = st.dataframe(
            table_df,
            use_container_width=True,
            height=580,
            on_select='rerun',
            selection_mode='single-row',
            key='theme_peer_table_select',
            column_config={
                "排名": st.column_config.NumberColumn("排名", width="small"),
                "代码": st.column_config.TextColumn("代码", width="small"),
                "名称": st.column_config.TextColumn("名称", width="medium"),
                "共现次数": st.column_config.NumberColumn("共现次数", width="small"),
                "历史平均涨幅%": st.column_config.NumberColumn(
                    "历史平均涨幅%",
                    format="%.2f%%",
                    width="medium",
                ),
                "实时涨跌幅%": st.column_config.NumberColumn(
                    "实时涨跌幅%",
                    format="%.2f%%",
                    width="medium",
                ),
                "融合得分": st.column_config.NumberColumn(
                    "融合得分",
                    format="%.2f",
                    width="small",
                ),
                "更新时间": st.column_config.TextColumn("更新时间", width="large"),
                "实时状态": st.column_config.TextColumn("实时状态", width="small"),
                "题材": st.column_config.TextColumn("题材", width="large"),
            },
        )

    selected_idx = 0
    selected_rows = (((event or {}).get('selection') or {}).get('rows') or []) if isinstance(event, dict) else []
    if selected_rows:
        selected_idx = int(selected_rows[0])
    selected_idx = max(0, min(selected_idx, len(peer_stocks) - 1))
    selected_stock = dict(peer_stocks[selected_idx] or {})
    selected_code = str(selected_stock.get('code') or '').strip()
    selected_name = str(selected_stock.get('name') or '')

    with right:
        st.markdown(f"**{selected_code} {selected_name}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("共现次数", f"{int(selected_stock.get('cooccur_count') or 0)}")
        c2.metric("历史平均涨幅", f"{float(selected_stock.get('avg_pct_chg') or 0):+.2f}%")
        rt = selected_stock.get('realtime_pct_chg', None)
        c3.metric("实时涨跌幅", "--" if rt is None else f"{float(rt):+.2f}%")
        c4.metric("融合得分", f"{float(selected_stock.get('fusion_score') or 0):.2f}")
        _render_stock_detail(code=selected_code, name=selected_name)

