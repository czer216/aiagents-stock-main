# 龙虎榜 AI 输入字段对照表

更新时间：2026-04-18

## 1. 各分析师实际拿到的数据

| 分析师 | 输入主数据 | 补充信号上下文 | 固定候选池 | 题材白名单 | 个股题材绑定 | 配额上下文 | 9日K线上下文 |
|---|---|---|---|---|---|---|---|
| 游资行为分析师 | `formatted_data_for_ai` + `summary_for_ai` | 是 | 是 | 否 | 否 | 否 | 通过补充信号摘要给到 |
| 个股潜力分析师 | `formatted_data_for_ai` + `summary_for_ai` | 是 | 是 | 是 | 是 | 否 | 通过补充信号摘要给到 |
| 题材追踪分析师 | `formatted_data_for_ai` + `summary_for_ai` | 是 | 是 | 是 | 是 | 否 | 通过补充信号摘要给到 |
| 风险控制专家 | `formatted_data_for_ai` + `summary_for_ai` | 是 | 是 | 否 | 否 | 否 | 通过补充信号摘要给到 |
| 首席策略师 | 四位分析师报告 + `summary_for_ai` + `formatted_data_for_ai` | 是 | 间接（由团队报告与上下文体现） | 是 | 是 | 是 | 当前主流程默认未单独注入 `candidate_kline_context` |

说明：
- `formatted_data_for_ai` 会先剔除“当日跌停池”股票后再提供给 AI。
- “固定候选池”来自评分结果与摘要融合后的候选列表（`recommendation_candidate_pool`）。

## 2. 你最关心的配额三字段（仅首席显式拿到）

首席提示词里的 `candidate_quota_context` 明确包含：

- `pct_chg`：候选股当日涨跌幅（%），系统聚合后的最新值。
- `is_today_limit_up`：是否命中“当日真实涨停”标记（涨停识别链路）。
- `is_limit_like_for_quota`：是否命中“近涨停标签”（只用于配额分层，不等同真实涨停）。

并且同时给出：
- 推荐总数
- 近涨停最多只数
- 非近涨停最少只数

## 3. 题材相关字段怎么来的

### 3.1 AI可用题材词池（逐股）
- 主来源：`summary_for_ai.top_stock_lu_desc`
- 构成方式：
  - `theme_top`：由 `gl/概念` 拆词统计
  - `lu_desc_top`：原始题材文本（去重截断）
- 兜底来源：`summary_for_ai.stock_gl_fallback_map`

### 3.2 题材白名单与逐股绑定
- 白名单：`_build_theme_whitelist_context(summary)`
- 逐股绑定：`_build_stock_theme_binding_context(summary)`
- 约束规则：
  - 分析某只股票时，只允许用该股自己的词池；
  - 不能跨股票借词；
  - 若该股词池为空，才允许 `AI知识标签：xxx`。

## 4. AI上下文中的核心块（按注入顺序理解）

1. 龙虎榜主文本：`formatted_data_for_ai`
2. 补充热度信号：概念轮动 / 连板晋级 / 同花顺热榜 / 开盘啦 / P1 / 9日趋势摘要
3. 固定候选池（统一上下文）
4. （部分分析师）题材白名单 + 逐股题材绑定
5. （首席）配额上下文（含三字段释义与目标约束）
6. 四位分析师报告（首席综合时）

## 5. 代码定位（便于你快速改）

- 引擎主流程与注入点：`longhubang_engine.py`
  - 阶段3构造 AI 数据：`summary_for_ai`、`formatted_data_for_ai`
  - 阶段4调用分析师：`youzi/stock/theme/risk/chief`
  - 首席配额上下文构建：`_build_chief_candidate_quota_context`
- 分析师提示词拼接：`longhubang_agents.py`
  - 白名单：`_build_theme_whitelist_context`
  - 逐股绑定：`_build_stock_theme_binding_context`
  - 通用补充信号：`_build_brief_signal_context`
  - 首席提示词：`chief_strategist`
- 摘要与题材词池生成：`longhubang_data.py`
  - `analyze_data_summary`

## 6. 当前“看起来乱”的本质

不是字段错误，而是“分层上下文”并行：
- 主数据（龙虎榜文本）
- 统计摘要（summary）
- 信号摘要（多源热度）
- 固定候选池（评分后）
- 首席专用配额上下文

这 5 层不是每位分析师都全拿，所以在日志和结果上会感觉信息来源不一致。
