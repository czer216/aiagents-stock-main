"""同题材补涨 UI - 基于共现历史数据"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, Any

import pandas as pd
import streamlit as st

from longhubang_history import LonghubangHistoryService


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


def display_theme_peer_selector():
    st.markdown(
        """
    <div class="top-nav">
        <h1 class="nav-title">🧩 同题材补涨</h1>
        <p class="nav-subtitle">输入股票代码，查询历史同题材跟涨股票（基于龙虎榜共现数据）</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

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
    df = df[ordered_columns + remaining_columns]

    # 显示表格
    st.dataframe(
        df,
        use_container_width=True,
        height=580,
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
