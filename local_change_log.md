# Local Change Log

## 2026-04-12

### 变更目标
- 修复 Web 界面保存配置后，`.env` 中以下键会消失的问题：
  - `TUSHARE_TOKEN`
  - `TDX_ENABLED`
  - `TDX_BASE_URL`

### 变更文件

#### 1) `config_manager.py`
- 在默认配置中补充并统一管理：
  - `TDX_ENABLED`
  - `TDX_BASE_URL`
- 调整 `write_env(config)` 写入策略：
  - 保存前先读取现有 `.env`，与本次提交配置进行合并，避免“部分字段提交”导致其它字段被覆盖丢失。
  - 在固定写入块中显式写入：
    - `DEFAULT_MODEL_NAME`
    - `TUSHARE_TOKEN`
    - `TDX_ENABLED`
    - `TDX_BASE_URL`
  - 增加“其他保留配置”写回逻辑，保留未知/未来新增键，降低后续配置丢失风险。

#### 2) `app.py`
- `display_config_manager()` 中增强 session 兼容：
  - 对已有 `st.session_state.temp_config` 使用 `setdefault` 补齐新增配置键，避免旧会话缺键导致保存覆盖。
- “数据源配置”页新增 TDX 配置项：
  - `TDX_ENABLED`（checkbox）
  - `TDX_BASE_URL`（text input）
- `.env` 预览区域新增显示：
  - `DEFAULT_MODEL_NAME`
  - `TDX_ENABLED`
  - `TDX_BASE_URL`

### 验证记录
- 使用 `python3` 进行了临时 `.env` 回归验证（不改动真实 `.env`）：
  - 场景 A：仅提交部分配置字段，确认 `TUSHARE_TOKEN/TDX_*` 不丢失。
  - 场景 B：显式修改 `TUSHARE_TOKEN/TDX_*` 后保存，确认值正确落盘。
  - 同时确认自定义额外键（如 `CUSTOM_X`）可被保留。
- 语法检查：
  - `python3 -m py_compile app.py config_manager.py` 通过。

### 部署说明（Docker Compose）
- 本次属于代码变更，若容器代码来自镜像内 `COPY . .`，需要重建镜像后生效：
  - `docker compose up -d --build`
- 若仅改 `.env`（已挂载），一般仅需重启容器：
  - `docker compose restart`

---

## 2026-04-12（功能新增）

### 变更目标
- 在“普通股票分析”流程中新增两类数据能力：
  - 个股概念板块
  - 同花顺历史异动（基于问财检索）

### 新增文件

#### `ths_feature_data.py`
- 新增 `THSFeatureDataFetcher`：
  - `get_context_data(symbol)`：统一获取概念板块 + 历史异动。
  - `format_context_for_ai(data)`：将扩展数据格式化为 AI 可读文本。
  - 支持多查询语句回退、结果结构兼容（DataFrame / dict / list）、噪音过滤与摘要统计。
- 核心输出字段：
  - `concept_data`: `has_data / concepts / count`
  - `abnormal_data`: `has_data / records / summary(total_records, latest_date, top_types)`

### 修改文件

#### 1) `app.py`
- `run_stock_analysis()`：
  - 在展示基础信息前新增扩展数据抓取（A股）。
  - 获取成功后将 `concept_boards`、`ths_abnormal_data` 写入 `stock_info`。
  - 新增进度与提示信息（成功/缺失/异常）。
- `analyze_single_stock_for_batch()`：
  - 批量分析链路同样接入扩展数据抓取，并写入 `stock_info`（静默容错，不阻塞主流程）。
- `display_stock_info()`：
  - 新增“概念板块与同花顺历史异动”展示区：
    - 概念板块列表（前12个）
    - 历史异动摘要（条数、最近日期、高频类型）
    - 最近10条异动表格

#### 2) `deepseek_client.py`
- 新增 `_build_ths_context(stock_info)`：
  - 将概念板块与异动摘要拼装成统一上下文文本。
- 在以下分析提示词中注入该上下文：
  - `technical_analysis()`
  - `fundamental_analysis()`
  - `fund_flow_analysis()`

#### 3) `ai_agents.py`
- 新增 `_build_ths_context_text(stock_info)` 辅助方法。
- 在以下分析提示词中补充概念/异动上下文：
  - `risk_management_agent()`
  - `market_sentiment_agent()`
  - `news_analyst_agent()`

### 验证记录
- 语法检查通过：
  - `python3 -m py_compile app.py ai_agents.py deepseek_client.py ths_feature_data.py stock_data.py config_manager.py`
- 兼容性策略：
  - 扩展数据全部为“可选获取”，失败不影响原有分析流程与保存逻辑。

### 追加调整（同日）
- 历史异动窗口从“全量/不确定范围”调整为“最近7天（含今天）”。
- 异动解析结构增强为更接近同花顺 App「异动解读」：
  - `标签`（如大涨/涨停/大跌）
  - `标题`（如题材/异动主线）
  - `原因`（行业原因/触发说明）
- 页面展示从纯表格改为“时间线摘要风格”列表，支持快速浏览“日期 + 标签 + 标题 + 原因”。
- AI 提示词补充最近7天异动解读样本（前3条），便于在分析结论中引用。
- 移除“弱兜底占位记录”写入逻辑，避免出现“共1条但日期未知/标题仅异动解读”的假阳性显示。
- 页面展示端增加二次过滤，对历史旧记录中的占位数据也会自动隐藏，并提示“未获取到可用异动解读数据”。

### 追加调整（同日，新闻/财报时效修复）
- 修复“新闻分析师引用前年旧财报”问题的输入侧原因：
  - `qstock_news_data.py` 新增新闻时效控制：默认只保留最近90天新闻。
  - 新闻项统一提取发布时间并按时间降序排序，再去重后取前 `max_items`。
  - `news_data.date_range` 改为真实时间区间（最早~最新）。
- 修复季报/年报期数选取可能偏旧的问题：
  - `quarterly_report_data.py` 在利润表、资产负债表、现金流量表中新增“按报告期降序排序”后再取最近8期。
  - 财务指标日期列也改为按报告期排序再取最近8期，避免按原列顺序误取旧期。

### 追加调整（同日，新增雪球热度支持）
- 新增“雪球热度前10条”数据接入，作为新闻分析师的数据支撑来源之一：
  - 在 `qstock_news_data.py` 中新增雪球抓取逻辑（优先尝试 `stock_hot_tweet_xq` / `stock_hot_deal_xq`，兼容不同参数格式）。
  - 统一计算 `heat_score`（基于热度/评论/转发/点赞/浏览等字段加权）并按热度降序取前10条。
  - 在最终新闻集合中优先保留雪球前10条，再补充其他新闻源。
  - 当存在发布时间字段时继续受“最近90天”过滤；无时间字段的雪球热度项可作为补充保留。

### 追加调整（同日，历史异动可读性修复）
- 修复“历史异动展示为大段不可读文本、混入其他股票”的问题，目标仅保留当前分析股票的异动解读：
  - `ths_feature_data.py`：
    - 新增按股票代码严格过滤问财结果（优先代码列匹配，兜底整行匹配并剔除多股票汇总行）。
    - 增强日期解析（支持 `YYYY-MM-DD`/`YYYYMMDD` 及混合文本提取）并继续限制最近7天。
    - 新增文本去噪（过滤 `rows x columns`、多股票拼接串）与字段长度控制，避免污染标题/原因/高频类型。
    - 新增异动记录去重与摘要标签清洗，防止出现超长“高频异动类型”。
  - `app.py`：
    - 历史异动展示改为“纯异动列表”视图，默认仅显示日期+标签+标题+原因，移除噪声高频类型展示。
    - 概念板块改为折叠区域，减少对异动阅读的干扰。

### 追加调整（同日，时间一致性与新闻时效修复）
- 修复“团队讨论出现 2025 年会议时间模板”的问题：
  - `ai_agents.py` 与 `deepseek_client.py` 的会议提示词新增“当前系统时间（Asia/Shanghai）”，并明确禁止编造历史模板日期（如 2024/2025）。
  - `app.py` 增加团队讨论文本清洗：自动移除“投资决策团队会议记录 / 会议时间 / 主持人 / 参会人员”等模板头，避免旧时间误导。
- 修复“新闻数据混入过旧年份”的问题：
  - `qstock_news_data.py` 将新闻时效窗口从 90 天收紧到 30 天。
  - 增加 `YYYYMMDD` 时间解析与新闻项二次时间提取，提升发布时间识别率。
  - 当普通新闻全部无法解析发布时间时，不再保留这些新闻（仅保留雪球热度补充项），避免 2024 等历史旧闻混入“最新新闻”。

### 追加调整（同日，团队讨论模板恢复）
- 按需求恢复“分析团队讨论”原模板风格，并恢复总监角色显示：
  - `ai_agents.py`：
    - 在团队讨论提示词中显式加入 `主持人：投资总监（张总）`。
    - 输出模板恢复为“投资决策团队会议记录 + 会议时间 + 主持人 + 参会人员 + 讨论正文”。
    - 会议时间仍使用当前系统时间，避免旧年份。
  - `app.py`：
    - 取消团队讨论内容清洗逻辑，恢复原样展示模型输出，确保模板字段（含主持人）完整可见。

### 追加调整（同日，龙虎榜新增“最强涨停概念板块/轮动”评分项）
- 按需求接入 **Tushare `limit_cpt_list`**，新增“最强概念板块 + 轮动 + 资金动向”能力并进入龙虎榜评分体系：
  - 新增 `longhubang_limit_concept.py`：
    - 基于 `limit_cpt_list` 获取最近N个交易日概念板块数据。
    - 提取每日“涨停家数最强概念板块”、TOP概念列表、轮动摘要与资金动向信号。
    - 生成 `concept_strength_map`（概念强度映射）供评分模块使用。
  - `longhubang_engine.py`：
    - 在评分前新增“概念轮动数据”抓取，并写入 `results["concept_rotation"]`。
    - 评分调用升级为 `score_all_stocks(..., concept_rotation_data=...)`。
    - 保存历史报告时，将 `concept_rotation` 一并持久化。
  - `longhubang_scoring.py`：
    - 评分体系升级为6维（总分仍100）：
      - 资金含金量30 + 净买入额25 + 卖出压力20 + 机构共振15 + 加分项6 + 板块轮动4
    - 新增 `板块轮动` 评分：个股概念命中当日最强/主线概念时加分。
    - 输出新增字段：`板块轮动`、`主线匹配`（命中的强势概念）。
  - `longhubang_ui.py`：
    - 评分页新增“涨停概念轮动（Tushare）”摘要展示（最强概念、涨停家数、资金动向、近5日明细）。
    - 评分表新增 `板块轮动` 列与 `主线匹配` 列。
    - 雷达图由“五维”升级为“六维”。
    - 历史报告页同步支持新字段与轮动摘要显示。

### 追加调整（同日，龙虎榜新增“连板晋级 + 同花顺A股热榜”评分项）
- 按需求新增两项评分输入：
  - **连板晋级热度**：使用 Tushare `limit_step`
  - **同花顺App热榜热度**：使用 Tushare `ths_hot`（仅A股，过滤港股/美股）
- 新增文件 `longhubang_hot_signals.py`：
  - `LimitStepHeatFetcher`：
    - 获取最近多日 `limit_step`，支持区间查询 + 单日回退。
    - 生成 `daily_step_stats`、`stock_step_heat_map`、`market_heat_summary`。
    - 新增“每日晋级个数/晋级率”统计（相邻交易日连板高度对比）。
  - `THSHotHeatFetcher`：
    - 获取最近多日 `ths_hot`，支持区间查询 + 单日回退。
    - 仅保留A股代码（`SH/SZ` 或 `0/3/6` 开头6位码）。
    - 生成 `daily_hot_stats`、`stock_hot_map`、`hot_summary`。
- `longhubang_engine.py`：
  - 分析流程新增两路数据抓取并写入结果：
    - `limit_step_heat`
    - `ths_hot_heat`
  - 评分调用升级为：
    - `score_all_stocks(..., concept_rotation_data, limit_step_data, ths_hot_data)`
  - 历史报告保存结构同步新增这两块数据，支持回放展示。
- `longhubang_scoring.py`：
  - 评分体系扩展为8维，总分仍100分（30+25+20+15+4+3+2+1）。
  - 新增：
    - `_calculate_limit_step_score`（0-2分）
    - `_calculate_ths_hot_score`（0-1分）
  - 排名表新增字段：
    - `连板晋级`、`同花顺热榜`、`连板高度`、`热榜最佳`
  - `热榜最佳` 展示优化：无数据时显示 `-`，避免出现 `TOP 0`。
- `longhubang_ui.py`：
  - 评分页新增两块摘要：
    - 连板晋级热度（含平均连板高度、平均晋级个数、最新交易日晋级）
    - 同花顺A股热榜概览
  - 评分说明升级为8维说明。
  - 主页面与历史报告页面评分表、雷达图、历史加载逻辑全部同步新字段。
  - 历史报告与当前分析页的类型转换逻辑同步兼容 `热榜最佳` 文本展示。
- 验证：
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_scoring.py longhubang_engine.py longhubang_ui.py`

### 追加调整（同日，Tushare全量切换代理地址）
- 目标：所有 Tushare 请求统一走代理地址，`token` 仍然从配置读取，不写死。
- 新增 `tushare_client.py`：
  - 提供 `create_tushare_pro(token, base_url)` 统一初始化入口。
  - 默认读取：
    - `TUSHARE_TOKEN`（token）
    - `TUSHARE_BASE_URL`（代理地址，默认 `http://8.136.22.187:8010/`）
  - 统一设置 `pro._DataApi__http_url` 到代理地址。
- 业务模块改造（替换所有 `ts.pro_api()` 直接初始化）：
  - `data_source_manager.py`
  - `smart_monitor_data.py`
  - `sector_strategy_data.py`
- `smart_monitor_data.py` 补充修正：
  - 移除 `ts.pro_bar` 降级分支，改为基于同一个 `self.ts_pro.daily`，避免出现“部分接口仍走默认地址”的风险。
- 配置体系扩展：
  - `config.py` 增加 `TUSHARE_BASE_URL`。
  - `config_manager.py` 增加 `TUSHARE_BASE_URL` 读写与默认值，保存 `.env` 时会持久化该项。
  - `app.py` 配置页面新增 `TUSHARE_BASE_URL` 输入框，并在“查看当前 .env”中展示该项。
  - 示例文件同步：
    - `.env.example`
    - `env_example.txt`
    - `update_env_example.py`
- 验证：
  - `python3 -m py_compile tushare_client.py data_source_manager.py smart_monitor_data.py sector_strategy_data.py config.py config_manager.py app.py update_env_example.py`

### 追加调整（同日，龙虎榜补充信号接入AI分析师团队）
- 按“分角色分配信号”的策略，将新增的龙虎榜补充数据真正注入 5 位分析师提示词（而非仅用于评分/UI）：
  - 题材追踪分析师：注入最完整上下文（概念轮动近5日、连板晋级近5日、同花顺热榜近3日）。
  - 个股潜力分析师：注入个股级信号（连板高度/晋级天数/连板分 + 热榜排名/在榜天数/热榜分），并对龙虎榜TOP20映射输出候选清单。
  - 风险控制专家：注入拥挤与退潮信号（高标拥挤、晋级回落、轮动过快、热榜拥挤等）。
  - 游资行为分析师：注入精简版热度摘要（主线、晋级热度、热榜概览）。
  - 首席策略师：注入摘要 + “连板+热榜”综合热度候选TOP清单，用于最终综合决策。
- `longhubang_agents.py`：
  - 新增多种上下文构建器：
    - `_build_brief_signal_context`
    - `_build_theme_signal_context`
    - `_build_stock_signal_context`
    - `_build_risk_signal_context`
    - `_build_chief_signal_context`
  - 五位分析师方法签名扩展为接收：
    - `concept_rotation_data`
    - `limit_step_data`
    - `ths_hot_data`
  - 各自 prompt 已按角色注入对应上下文内容。
- `longhubang_engine.py`：
  - 阶段4调用链已改为把三类信号数据传入所有分析师方法。
  - `chief_strategist` 调用新增传入 `summary` 与三类信号数据。
- 验证：
  - `python3 -m py_compile longhubang_agents.py longhubang_engine.py`

### 追加调整（同日，板块轮动评分提升至0-15分）
- 按需求将“板块轮动”评分从 `0-3分` 提升到 `0-15分`。
- 为保持总分100分不变，评分权重改为：
  - 资金含金量：`0-26`（原0-30）
  - 净买入额：`0-21`（原0-25）
  - 卖出压力：`0-16`（原0-20）
  - 机构共振：`0-15`（不变）
  - 加分项：`0-4`（不变）
  - 板块轮动：`0-15`（原0-3）
  - 连板晋级：`0-2`（不变）
  - 同花顺热榜：`0-1`（不变）
- `longhubang_scoring.py`：
  - 更新各维度注释与说明文本。
  - 资金含金量/净买入额/卖出压力采用“原有分段逻辑先计算，再映射到新量纲”。
  - 板块轮动评分内部加权按5倍放大（上限15分）。
- `longhubang_ui.py`：
  - 主页面与历史页的评分说明、进度条上限、雷达图归一化分母全部同步新量纲。
- 验证：
  - `python3 -m py_compile longhubang_scoring.py longhubang_ui.py`

### 追加调整（同日，连板梯队最强概念纳入评分）
- 需求背景：
  - 连板梯队的“最强概念”对短线买入决策影响很大，需要作为评分依据而非仅展示。
- `longhubang_hot_signals.py`：
  - `LimitStepHeatFetcher` 新增概念维度解析：
    - 从 `limit_step` 行数据中提取概念字段（概念/题材/板块等）
    - 输出 `daily_step_concept_rankings`
    - 输出 `concept_step_strength_map`（0-4强度映射）
    - 输出 `strongest_step_concepts`（梯队最强概念TOP）
  - 个股热度映射 `stock_step_heat_map` 增加 `concepts` 字段，便于评分与回退匹配。
- `longhubang_scoring.py`：
  - 板块轮动评分 `0-15` 从“单源（limit_cpt_list）”升级为“双源融合”：
    - 主线轮动：`limit_cpt_list` 概念强度
    - 连板梯队：`limit_step` 概念强度
  - 当龙虎榜记录缺少概念字段时，自动回退到 `limit_step` 的个股概念，避免短线概念缺失导致低估。
  - `主线匹配` 字段改为融合两路概念匹配结果，优先展示最相关概念。
- `longhubang_ui.py`：
  - 连板晋级摘要新增“梯队最强概念”显示（主页面 + 历史报告页）。
  - 评分说明同步标注“板块轮动评分 = limit_cpt_list + limit_step 概念融合”。
- `longhubang_agents.py`：
  - AI分析师上下文中的连板晋级摘要新增“梯队最强概念”，提升题材分析与短线判断一致性。
- 验证：
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_scoring.py longhubang_ui.py longhubang_agents.py longhubang_engine.py`

### 追加调整（同日，连板梯队概念用于评分并补全概念板块缺失）
- 问题确认：
  - 连板梯队最强概念已进入评分链路，但龙虎榜原始概念字段解析仅按英文逗号分割，遇到 `， ; ； / | 、 空格` 等分隔符会漏拆，导致“概念板块信息缺失/不完整”。
  - 评分中 `limit_step` 概念此前仅在“原始概念为空”时回退，无法覆盖“部分缺失”场景。
- `longhubang_scoring.py`：
  - 新增多分隔符概念解析 `_split_concepts`（支持 `,，;；/|、空格`，并过滤噪声词）。
  - `_extract_stock_concepts` 改为复用统一解析逻辑。
  - 新增 `_get_enriched_stock_concepts`：将“龙虎榜原始概念 + limit_step个股概念”做并集去重。
  - 板块轮动评分 `_calculate_board_rotation_score` 改为优先使用并集补全概念，不再仅在空值时回退。
  - `score_all_stocks` 中“主线匹配”改为基于并集概念进行匹配，提升概念命中稳定性。
  - `bonus` 的热门概念加分也改为使用统一概念解析，避免分隔符导致漏计。
- `longhubang_data.py`：
  - 热门概念统计由单一 `split(',')` 升级为统一多分隔符解析，提升概念统计准确性。
- 验证：
  - `python3 -m py_compile longhubang_scoring.py longhubang_data.py`

### 追加调整（同日，概念板块获取改为 Tushare `kpl_concept_cons`）
- 需求：将“概念板块获取”从问财改为直接使用 Tushare 接口 `kpl_concept_cons`。
- `ths_feature_data.py`：
  - 模块定位调整为“概念: Tushare / 异动: 问财”。
  - 新增 `DataSourceManager` 接入，概念数据读取统一走已配置的 Tushare（含代理地址与Token配置）。
  - `_get_concept_data` 重构为仅调用 `kpl_concept_cons`：
    - 支持 `ts_code / stock_code / code / symbol` 多参数兼容尝试（不同版本参数命名兼容）。
    - 增加权限/可用性错误提示（Token未配置、接口不可用时返回结构化 error）。
  - 新增 `_fetch_kpl_concept_cons` 与 `_extract_kpl_concepts`：
    - 从接口返回中自适配识别概念列（`concept/cpt/概念/题材/板块` 等）。
    - 仅在检测到代码列中存在目标股票时启用按股票过滤，避免误把“概念代码列”当股票代码过滤掉全部结果。
    - 保留概念去重与噪声过滤。
  - 新增 `_normalize_stock_code`、`_to_ts_code` 工具函数。
  - `source` 字段改为 `concept:tushare.kpl_concept_cons;abnormal:pywencai`，便于前端和日志区分来源。
- 兼容性：
  - 输出结构保持不变（`concept_data.has_data/concepts/count`），无需改动 `app.py` 现有展示与AI上下文拼接。
- 验证：
  - `python3 -m py_compile ths_feature_data.py`

### 追加调整（同日，龙虎榜概念源切换为 `kpl_concept_cons`，主线匹配使用同源数据）
- 需求：
  1) 龙虎榜中的概念板块数据改为 Tushare `kpl_concept_cons`
  2) 评分结果中的“主线匹配”概念也使用该数据
- `longhubang_limit_concept.py`：
  - 重构 `LimitConceptRotationFetcher`，数据源由 `limit_cpt_list` 切换为 `kpl_concept_cons`。
  - `get_rotation_data(...)` 新增 `stock_codes` 入参，按“本次龙虎榜股票池”构建概念映射。
  - 新增“全量 + 单股回退”双路径拉取：
    - 优先尝试全量拉取后按目标股票过滤。
    - 对未命中的股票逐只回退拉取（兼容 `ts_code/stock_code/code/symbol` 多参数签名）。
  - 新增 `stock_concept_map` 输出（`{股票代码: [概念...]}`），供评分与主线匹配直接使用。
  - 保持外部兼容字段：`concept_strength_map`、`daily_rankings`、`strongest_today`、`rotation_summary` 继续输出。
  - 增加“代码列误判保护”：若采样完全命不中目标股票，放弃该批映射，避免把概念代码当股票代码。
- `longhubang_engine.py`：
  - 在阶段3从龙虎榜原始数据提取去重股票代码并传入 `get_rotation_data(..., stock_codes=...)`。
  - 新增辅助方法 `_extract_stock_codes` / `_normalize_code`。
- `longhubang_scoring.py`：
  - `板块轮动`中个股概念来源升级为：
    - 优先 `concept_rotation_data.stock_concept_map`（即 `kpl_concept_cons`）
    - 再叠加龙虎榜原始概念字段与 `limit_step` 概念兜底。
  - `主线匹配`复用上述同一概念集合，因此已直接使用 `kpl_concept_cons` 数据参与匹配。
  - 评分说明文案同步更新为 `kpl_concept_cons + limit_step`。
- `longhubang_ui.py`：
  - 相关文案从 `limit_cpt_list` 更新为 `kpl_concept_cons`。
  - 指标展示文案从“涨停家数”调整为“关联个股数”，与新数据口径一致。
- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_scoring.py longhubang_ui.py`

### 追加修复（同日，主线匹配混入个股名称）
- 现象：`主线匹配` 中出现“弘信电子/东山精密/英唐智控”等个股名称，导致看起来像把个股塞进概念。
- 根因：
  - `kpl_concept_cons` 解析中，概念列识别过宽（含 `name` 兜底），在部分返回结构下会把“股票名称列”误当概念列。
  - 单股回退查询在参数未生效时可能返回全量数据，旧逻辑会继续提取，导致整表名称污染到概念池。
- 修复：`longhubang_limit_concept.py`
  - 新增 `_find_stock_code_cols`：股票代码列精确识别，并排除 `con_code/cpt_code/concept_code/bk_code` 等概念代码列。
  - 新增 `_find_concept_cols`：优先识别 `con_name/cpt_name/concept_name/concept/概念/题材/板块`，仅在无明显股票名称列时才谨慎使用 `name` 兜底。
  - `_extract_target_mapping_from_df` 改为使用新列识别函数。
  - `_extract_single_stock_concepts` 增加防呆：若检测到返回结果包含多只股票或无代码列且行数过大，直接放弃该批，避免“全市场误灌”。
- 结果：
  - `主线匹配` 继续使用 `kpl_concept_cons` 数据，但会显著减少/消除个股名称混入概念的问题。
- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_scoring.py`
  - 运行时模拟校验受当前终端依赖限制（缺少 pandas），未执行到DataFrame级单测。

### 追加调整（同日，新增 `kpl_list` 数据并接入AI分析师团队）
- 需求：新增 Tushare `kpl_list` 接口数据，并纳入龙虎榜 AI 分析师团队报告。
- `longhubang_hot_signals.py`：
  - 新增 `KPLListHeatFetcher`（`source=tushare.kpl_list`）。
  - 支持区间拉取 + 单日回退拉取（兼容不同参数签名）。
  - 结构化输出：
    - `daily_kpl_stats`：按日TOP榜单与题材分布
    - `stock_kpl_map`：个股KPL热度分（含最佳排名、在榜天数、题材）
    - `theme_heat_map`：题材热度映射
    - `kpl_summary`：摘要（覆盖天数、跟踪股票、最热题材、今日题材TOP）
- `longhubang_engine.py`：
  - 引擎初始化新增 `KPLListHeatFetcher`。
  - 阶段3新增拉取 `kpl_list_data`，结果键：`kpl_list_heat`。
  - 在阶段4调用 5 位分析师和首席策略师时，新增传入 `kpl_list_data`。
  - 在完整报告 `full_analysis_content` 中新增 `kpl_list_heat` 持久化。
- `longhubang_agents.py`：
  - 所有信号上下文构建器新增 `kpl_list_data` 入参：
    - `_build_brief_signal_context`
    - `_build_theme_signal_context`
    - `_build_stock_signal_context`
    - `_build_risk_signal_context`
    - `_build_chief_signal_context`
  - 5位分析师 + 首席策略师方法签名新增 `kpl_list_data`，并将 `kpl_list` 信号纳入提示词上下文。
  - 个股候选综合信号由“连板+热榜”升级为“连板+热榜+KPL”。
  - 风险上下文新增“开盘啦榜单拥挤度”风险提示。
- `longhubang_ui.py`：
  - 实时结果与历史报告详情新增“开盘啦热度”摘要展示。
  - 历史报告加载结果结构新增 `kpl_list_heat`。
- 验证：
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_engine.py longhubang_agents.py longhubang_ui.py`

### 追加修正（同日，仅针对“筛选股票”获取当前概念并做板块分析）
- 目标：只围绕筛选出的股票获取“当前所属概念板块”，避免全市场概念混入，提升板块间分析准确性。

- `ths_feature_data.py`：
  - `kpl_concept_cons` 查询参数新增优先尝试 `con_code`（`con_code=600105.SH/600105`），兼容代理接口实测行为。
  - 新增 `kpl_concept_cons` 字段形态识别：
    - 当返回结构为 `ts_code/name/con_name/con_code/trade_date` 时，明确映射为：
      - `con_code` = 股票代码
      - `name` = 概念名称
      - `con_name` = 股票名称（不参与概念提取）
  - 概念提取逻辑改为：
    1. 先按目标股票代码过滤行；
    2. 再只保留最新 `trade_date`；
    3. 最后提取概念名并去噪。
  - 防止接口参数未生效导致全市场数据被误当作单股概念。

- `longhubang_limit_concept.py`：
  - `get_rotation_data` 改为优先获取“最近有数据交易日”的快照，再对目标股票池做概念映射。
  - 新增 `_fetch_latest_snapshot_data`：
    - 优先按 `trade_date` 回溯近15天拉取最新快照；
    - 回退到全量时自动裁剪到最大 `trade_date`。
  - 单股回退查询新增 `con_code` 参数优先级。
  - 概念映射逻辑改为“按股票维度取最新交易日概念”，确保主线匹配基于当前概念而不是历史混合结果。
  - 新增 schema 解析 `_resolve_schema` 与日期标准化工具，明确处理 `kpl_concept_cons` 代理返回结构。
  - 输出新增 `effective_trade_date`，用于前端/分析团队确认当前概念分析日期。

- 验证：
  - 语法检查通过：
    - `python3 -m py_compile ths_feature_data.py longhubang_limit_concept.py`
  - 说明：当前 `docker-compose.yml` 未挂载代码目录（仅挂载 `data` 与 `.env`），容器内运行的是镜像旧代码；要让上述修复在容器生效，需要重新构建并重启容器。

### 追加调整（同日，板块轮动评分接入 `limit_cpt_list` 最强概念）
- 需求：在“筛选股票概念分析”基础上，进一步依据 Tushare `limit_cpt_list` 的最强概念板块进行打分。

- `longhubang_hot_signals.py`：
  - 新增 `LimitCPTHeatFetcher`（`source=tushare.limit_cpt_list`）。
  - 支持区间 + 单日回退拉取（`start_date/end_date` 与 `trade_date/date` 兼容）。
  - 新增结构化输出：
    - `daily_cpt_stats`：按交易日的最强概念榜单
    - `concept_strength_map`：概念强度映射（0-4标准化）
    - `strongest_concepts`：最强概念TOP列表（含 rank/up_nums/pct_chg）
    - `market_summary`：汇总（最强概念、强度分、今日TOP平均涨停家数）

- `longhubang_engine.py`：
  - 引擎初始化新增 `LimitCPTHeatFetcher`。
  - 阶段3新增拉取 `limit_cpt_data`，结果键：`limit_cpt_heat`。
  - 评分阶段 `score_all_stocks(...)` 新增传入 `limit_cpt_data`。
  - 报告持久化 `full_analysis_content` 新增 `limit_cpt_heat`。

- `longhubang_scoring.py`：
  - `calculate_stock_score`、`score_all_stocks`、`_calculate_board_rotation_score` 新增 `limit_cpt_data` 入参。
  - 板块轮动评分 (0-15) 新增第3路信号：
    - `limit_cpt_list` 概念强度命中加分
    - 命中当日最强概念、TOP3概念、多概念命中额外加分
  - `主线匹配` 列新增纳入 `limit_cpt_list` 命中概念（与 kpl_concept_cons / limit_step 一并展示）。
  - 评分说明文案同步更新：板块轮动评分来源改为三路融合（`kpl_concept_cons + limit_step + limit_cpt_list`）。

- `longhubang_ui.py`：
  - 评分页新增“🧱 涨停最强概念板块（Tushare）”摘要展示（最强概念、强度分、平均涨停家数）。
  - 历史报告详情页同步展示 `limit_cpt_heat` 摘要。
  - 评分维度说明文案同步更新为三路融合口径。
  - 历史报告“加载到分析页”时，新增恢复 `limit_cpt_heat` 到会话结果。

- 验证：
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_engine.py longhubang_scoring.py longhubang_ui.py`

### 追加修复（同日，龙虎榜分析启动卡住且未进入AI请求）
- 现象：点击“开始分析”后长期停留在“正在获取龙虎榜数据”，日志中未进入 AI 分析师阶段。
- 结论：卡点主要在阶段3的外部数据补充（`kpl_concept_cons / limit_step / limit_cpt_list / ths_hot / kpl_list`）同步阻塞，导致主流程无法推进到AI请求。

- `longhubang_engine.py`：
  - 新增 `_safe_fetch_with_timeout(...)`，为阶段3外部接口统一加超时降级保护。
  - 对以下抓取全部改为“超时跳过并继续主流程”：
    - `kpl_concept_cons`
    - `limit_step`
    - `limit_cpt_list`
    - `ths_hot`
    - `kpl_list`
  - 关键修复：超时后 `executor.shutdown(wait=False, cancel_futures=True)`，避免线程池默认等待导致“表面超时、实际仍卡住”。
  - 结果：即使个别接口慢/卡，流程也会继续进入阶段4 AI分析师请求。

- `longhubang_limit_concept.py`：
  - `_fetch_latest_snapshot_data` 回溯窗口从 15 天降为 4 天，减少阻塞调用。
  - 每日候选请求由 3 组降为 2 组，降低网络抖动影响。
  - 兜底全量拉取改为一次轻量 `limit=4000`，避免重型全量请求拖慢流程。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_limit_concept.py longhubang_hot_signals.py longhubang_scoring.py longhubang_ui.py`

- 部署说明：
  - 当前 `docker-compose.yml` 未挂载代码目录（仅挂载 `data` 与 `.env`），容器运行的是镜像内代码；本修复需重建镜像后生效。

### 追加调整（同日，增强“龙虎榜卡住”排查日志）
- 需求：分析开始后若长时间无输出，需要快速判断卡在“数据抓取”还是“AI请求”。

- `longhubang_engine.py`：
  - `_safe_fetch_with_timeout` 增加详细日志：
    - 拉取开始（含 timeout）
    - 拉取完成（含 elapsed 与 data_success）
    - 非dict回包降级提示
    - 超时/异常降级提示
  - 阶段3.5新增评分耗时日志：`评分计算结束 | elapsed=...s`。
  - 阶段4新增显式日志：`即将发起AI模型请求（5位分析师 + 首席策略师）`。

- `deepseek_client.py`：
  - `call_api` 增加请求级日志：
    - 请求开始（model、messages数、user prompt长度、max_tokens）
    - 请求完成（elapsed、output长度）
    - 请求失败（exception stack）
  - OpenAI client 新增 `timeout=120.0`，避免模型请求无限等待。

- `longhubang_agents.py`：
  - 新增 `_run_agent_call` 统一记录每位分析师实际模型调用的开始/结束日志与耗时。
  - 5位分析师 + 首席策略师从直接 `call_api` 改为 `_run_agent_call`。
  - 初始化与分析开始/结束日志统一使用 `logging`，减少 `print` 缓冲导致的日志延迟错觉。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_agents.py deepseek_client.py`

- 部署说明：
  - 当前 docker-compose 仅挂载 `data` 与 `.env`，代码未挂载；日志增强需重建镜像后生效。

### 追加调整（同日，同花顺热榜评分提升到0-10并从前3项拆分）
- 需求确认：
  - 同花顺热榜评分由 `0-1` 提升至 `0-10`；
  - 增量分值必须从前3项拆分，不改变总分100。

- 最终权重（总分100）：
  - 买入资金含金量：`0-22`（原26）
  - 净买入额评分：`0-18`（原21）
  - 卖出压力评分：`0-14`（原16）
  - 机构共振：`0-15`
  - 其他加分项：`0-4`
  - 板块轮动：`0-15`
  - 连板晋级：`0-2`
  - 同花顺热榜：`0-10`（原1）

- `longhubang_scoring.py`：
  - 同花顺热榜评分由 `ths_hot` 原始 `0-1` 分值映射到 `0-10`。
  - 评分结果输出中“同花顺热榜”保留1位小数。
  - 评分说明文案更新为新分值区间。

- `longhubang_ui.py`：
  - 修复评分说明中的旧分值残留与重复段落（删除“0-26分”重复项）。
  - 雷达图归一化修正为新上限（资金含金量由 `/26` 改为 `/22`）。
  - 实时TOP表与历史报告TOP表的进度条上限统一为：
    - 资金含金量22、净买入额18、卖出压力14、同花顺热榜10。
  - 同花顺热榜展示精度统一为 `%.1f`。
  - 历史报告评分说明同步更新到新权重口径。

- 验证：
  - `python3 -m py_compile longhubang_scoring.py longhubang_ui.py`

- 部署说明：
  - 当前 `docker-compose.yml` 仅挂载 `data` 与 `.env`，代码不热更新；
  - 本次代码改动需重新构建镜像并重启容器后生效。

### 追加修正（同日，kpl_concept_cons 请求条件改为优先 `trade_date + con_code`）
- 背景：用户确认期望请求条件为 `trade_date + con_code`，用于获取“当前交易日下该股票所属概念”，并减少无关数据返回。

- `longhubang_limit_concept.py`：
  - `get_rotation_data` 为缺失股票回退查询新增 `preferred_trade_date`（优先 `snapshot_trade_date`，回退 `end_date`）。
  - `_fetch_single_stock_data` 改为优先尝试以下组合（并回溯近4天）：
    - `trade_date + con_code`
    - `trade_date + ts_code`
    - `trade_date + stock_code`
  - 仅在上述组合都失败时，才回退到原有 `con_code/ts_code/stock_code/code/symbol` 单参数查询。

- `ths_feature_data.py`：
  - `_fetch_kpl_concept_cons` 调用顺序调整为：先尝试最近4天的 `trade_date + con_code`（及 `trade_date + ts_code/stock_code`），再走原有回退参数。
  - 目的：单股概念查询优先命中“日期+股票”精确条件，降低超时与全量返回概率。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py ths_feature_data.py`

### 追加修正（同日，其余Tushare接口统一补充日期参数）
- 需求：除 `kpl_concept_cons` 外，其余热度相关接口请求也统一携带“对应日期”。

- `longhubang_hot_signals.py`：
  - `limit_step`：
    - `get_heat_data` 的区间拉取入口新增 `query_date=trading_dates[0]`。
    - `_fetch_range_data` 调整为优先尝试带日期单日参数：
      - `trade_date=query_date`（含/不含 `limit_type`）
      - 再回退 `start_date/end_date` 区间查询。
  - `ths_hot`：
    - 区间拉取新增优先候选：`trade_date=query_date`（含 `market=A`）/`date=query_date`（含 `market=A`）。
    - 单日拉取候选顺序调整为优先 `trade_date + market=A`，并补充 `date + market=A`。
  - `kpl_list`：
    - 区间拉取新增优先候选：`trade_date=query_date`（含 `market=A`）/`date=query_date`（含 `market=A`）。
    - 单日拉取候选顺序调整为优先 `trade_date + market=A`，并补充 `date + market=A`。
  - `limit_cpt_list`：
    - 区间拉取新增优先候选：`trade_date=query_date` / `date=query_date`（均携带 `limit=2000`）。
    - 单日拉取新增优先候选：`trade_date + limit=2000` 与 `date + limit=2000`。

- 验证：
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_engine.py`
  - 容器内冒烟测试：
    - `ths_hot` / `kpl_list` / `limit_cpt_list` 调用链正常返回；
    - `limit_step` 当前返回空数据（无参数异常，属于数据/权限侧结果）。

### 追加修正（同日，kpl_concept_cons 超时稳定性增强）
- 问题复盘：
  - `kpl_concept_cons` 仍存在偶发慢响应，18秒阈值下容易触发误降级超时。
  - 触发场景与代理链路抖动、非交易日回溯及多候选参数重试叠加有关。

- `longhubang_limit_concept.py`：
  - 新增交易日构建工具 `_build_recent_trade_dates(...)`，回溯日期改为优先交易日（跳过周末）。
  - `get_rotation_data` 增加单股回退时间预算：
    - 总耗时超过阈值时提前结束回退，优先返回已拿到的有效映射，避免整段阻塞。
  - `_fetch_latest_snapshot_data` 与 `_fetch_single_stock_data` 的日期回溯均改为交易日序列。

- `longhubang_engine.py`：
  - 仅对 `kpl_concept_cons` 的引擎级超时阈值从 `18s` 提升到 `25s`，减少网络抖动导致的误超时降级。
  - 其他接口超时阈值保持不变。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py`

### 追加修正（同日，kpl_concept_cons 严格“仅当天数据”）
- 需求：按用户要求，概念查询只取当天数据，不再回溯历史日期。

- `longhubang_limit_concept.py`：
  - `_fetch_latest_snapshot_data` 改为只请求锚点日期（`end_date` 或当天）：
    - `trade_date=day, limit=6000`
    - `trade_date=day`
  - 删除“回溯近N天 + 全量兜底(limit=4000)”逻辑。
  - `_fetch_single_stock_data` 改为仅用当天日期组合参数：
    - `trade_date + con_code`
    - `trade_date + ts_code`
    - `trade_date + stock_code`
  - 删除无日期参数回退，避免拿到非当日/全量数据。

- `ths_feature_data.py`：
  - `_fetch_kpl_concept_cons` 改为仅以当天 `trade_date` 发起单股概念查询；
  - 删除“近4天回溯”与无日期回退逻辑。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py ths_feature_data.py`
  - 容器内实测 `get_rotation_data(end_date=20260412/20260410)` 可返回（按传入日期取数）。

### 追加修正（同日，“当天”统一改为前端指定日期透传）
- 需求澄清：当天日期应使用“前端传入的指定日期”，而非系统当前时间。

- `ths_feature_data.py`：
  - `THSFeatureDataFetcher.get_context_data` 新增 `query_date` 参数（支持 `YYYY-MM-DD` / `YYYYMMDD`）。
  - 新增 `_normalize_query_date`，统一标准化为 `YYYYMMDD`。
  - 概念查询链路 `_get_concept_data` / `_fetch_kpl_concept_cons` 改为优先使用 `query_date`；
  - 仅当未传 `query_date` 时，才回退系统当天。
  - 结果中新增 `query_trade_date` 字段，便于前端/日志确认实际查询日期。

- `app.py`：
  - 新增 `_infer_query_trade_date(stock_data)`，从当前前端分析数据的最新交易日推导查询日期（`YYYY-MM-DD`）。
  - 在两个 `THSFeatureDataFetcher.get_context_data(...)` 调用点显式传入 `query_date`，
    避免隐式使用系统时间。

- 验证：
  - `python3 -m py_compile ths_feature_data.py app.py`
  - 说明：容器内仍是旧镜像代码（未挂载源码），需重建后才可验证 `query_date` 新参数运行效果。

### 追加修正（同日，kpl_concept_cons 严格限定 `trade_date + con_code`）
- 需求：接口参数严格按照 `trade_date + con_code`，不再混用 `ts_code/stock_code/code`。

- `longhubang_limit_concept.py`：
  - `get_rotation_data` 改为逐只股票查询，不再先做按日期全量快照。
  - `_fetch_single_stock_data` 的候选参数严格限定为：
    - `{"trade_date": day, "con_code": ts_code}`
    - `{"trade_date": day, "con_code": code}`
  - 删除 `trade_date + ts_code`、`trade_date + stock_code` 查询分支。

- `ths_feature_data.py`：
  - `_fetch_kpl_concept_cons` 同步收敛为仅两种参数：
    - `trade_date + con_code(ts_code)`
    - `trade_date + con_code(symbol)`
  - 删除 `trade_date + ts_code`、`trade_date + stock_code` 分支。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py ths_feature_data.py`

### 追加修正（同日，limit_step/limit_cpt_list超时改为60秒 + kpl_list定向请求）
- 需求：
  1) `limit_step`、`limit_cpt_list` 接口请求超时时间调整到 1 分钟；
  2) `kpl_list` 请求必须带 `trade_date` 与 `ts_code`，并只分析龙虎榜股票池。

- `longhubang_engine.py`：
  - `limit_step` 的 `_safe_fetch_with_timeout` 从 `12s` 调整为 `60s`。
  - `limit_cpt_list` 的 `_safe_fetch_with_timeout` 从 `12s` 调整为 `60s`。
  - `kpl_list_fetcher.get_heat_data(...)` 新增透传 `stock_codes=stock_codes`（龙虎榜股票代码池）。

- `longhubang_hot_signals.py`（`KPLListHeatFetcher`）：
  - `get_heat_data` 新增 `stock_codes` 入参；
  - 当 `stock_codes` 为空时直接返回错误，避免再走全市场模式；
  - 拉取逻辑改为“按交易日 × 按目标股票”定向请求并聚合；
  - 单次请求参数严格使用：
    - `{"trade_date": trade_date, "ts_code": ts_code}`
    - `{"trade_date": trade_date, "ts_code": stock_code}`
  - `market` 参数按接口示例优先使用 `market="热股"`：
    - 优先 `trade_date + ts_code + market=热股`
    - 回退 `trade_date + ts_code`（兼容不同代理实现）
  - `kpl_summary` 新增 `requested_stocks` 字段，便于核对股票池覆盖情况。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_hot_signals.py longhubang_limit_concept.py ths_feature_data.py`

### 追加修正（同日，所有 `market` 参数统一为 `热股`）
- 需求补充：所有包含 `market` 参数的请求统一改为 `market="热股"`。

- `longhubang_hot_signals.py`：
  - `ths_hot` 的 `market` 参数由 `A` 统一改为 `热股`（范围查询与单日查询均调整）。
  - `kpl_list` 保持 `market="热股"`（范围查询与定向单股查询）。
  - 结果：当前代码中所有带 `market` 的候选参数均为 `热股`。

- 验证：
  - `rg -n "\"market\"\\s*:\\s*\"" longhubang_hot_signals.py`（仅剩 `热股`）
  - `python3 -m py_compile longhubang_hot_signals.py longhubang_engine.py`

### 追加修正（同日，停用 `limit_cpt_list`，最强板块改为 `kpl_concept_cons(name+hot_num)`）
- 需求：
  - `limit_cpt_list` 接口不再使用；
  - 最强板块统一使用 `kpl_concept_cons` 的 `name + hot_num` 计算；
  - 仅对龙虎榜股票池逐只获取并统一汇总。

- `longhubang_engine.py`：
  - 移除 `LimitCPTHeatFetcher` 的初始化与调用链；
  - 阶段3不再抓取 `limit_cpt_list`，结果中不再写入 `limit_cpt_heat`；
  - 评分调用 `score_all_stocks(...)` 移除 `limit_cpt_data` 入参；
  - 报告持久化 `full_analysis_content` 中移除 `limit_cpt_heat` 字段。

- `longhubang_scoring.py`：
  - 删除 `limit_cpt_data` 相关入参与逻辑分支；
  - 板块轮动评分来源收敛为：
    - `kpl_concept_cons`（主线概念强度）
    - `limit_step`（连板梯队概念强度）
  - `主线匹配` 列不再拼接 `limit_cpt_list` 概念；
  - 评分说明文案同步移除 `limit_cpt_list` 描述。

- `longhubang_limit_concept.py`：
  - `get_rotation_data` 改为在龙虎榜股票池上聚合 `name + hot_num`：
    - 每只股票仍按 `trade_date + con_code` 查询；
    - 解析每只股票的概念列表与概念 `hot_num`；
    - 全池汇总得到 `concept_hot_num_total` 与 `concept_stock_hits`。
  - 新增 `top_concepts` 结构（按 `hot_num_total` 排序）：
    - `concept`, `stock_count`, `hot_num_total`, `hot_num_avg`。
  - `strongest_today` 与 `daily_rankings` 新增 `hot_num` 口径字段：
    - `hot_num_total`、`strongest_hot_num`。
  - `concept_strength_map` 改为基于 `hot_num + 覆盖股票数` 计算强度（并保留排名加权）。
  - `rotation_summary` 新增：
    - `strongest_hot_num`、`hot_num_total`，并以热度集中度判断资金动向。
  - 新增 schema 识别 `hot_num_col` 与热度值解析。

- `longhubang_ui.py`：
  - 评分页/历史报告页移除 `limit_cpt_heat` 展示区块；
  - 概念主线展示改为 `stock_count + hot_num`；
  - “最近轮动明细”新增 `最强hot_num` 列；
  - 板块轮动评分说明更新为：`kpl_concept_cons(name+hot_num) + limit_step`。
  - 历史报告“加载到分析页”结构中移除 `limit_cpt_heat` 回填。

- `longhubang_agents.py`：
  - 分析师提示上下文中的概念轮动摘要改为展示 `hot_num`（最强概念与TOP3）。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_scoring.py longhubang_limit_concept.py longhubang_ui.py longhubang_agents.py`

### 追加修正（同日，板块强度按“单次龙虎榜全量股票”统一计算，仅输出TOP10）
- 需求澄清：
  - 概念板块强度必须基于“本次龙虎榜分析中的全部股票”统一计算；
  - 最终只需要 Top10 板块。

- `longhubang_limit_concept.py`：
  - 移除按耗时提前 `break` 的截断逻辑，不再只采样部分股票；
  - 改为对本次龙虎榜去重后的全部股票代码逐只查询并聚合概念强度；
  - `top_concepts` 输出由最多30条收敛为最多10条；
  - `concept_strength_map` 仅保留Top10概念参与后续匹配与评分；
  - 新增 `board_top10` 字段，直接提供板块强度Top10结果。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py`

### 追加修正（同日，`kpl_concept_cons` 与 `kpl_list` 超时统一到60秒）
- 需求：
  - `kpl_concept_cons` 接口超时调整为 `60s`；
  - `kpl_list` 接口超时调整为 `60s`。

- `longhubang_engine.py`：
  - 阶段3的 `kpl_concept_cons` 调用：`timeout_sec` 由 `25` 调整为 `60`；
  - 阶段3的 `kpl_list` 调用：`timeout_sec` 由 `10` 调整为 `60`。

- 验证：
  - `python3 -m py_compile longhubang_engine.py`

### 追加修正（同日，代理接口限流：每30次请求后休眠20秒）
- 需求：
  - 代理接口存在限流，累计请求30次后暂停20秒再继续请求。

- 新增 `tushare_proxy_rate_limit.py`：
  - 增加全局节流器 `throttle_tushare_proxy_request`；
  - 默认规则：每累计30次请求，在下一次请求发起前 `sleep 20s`；
  - 支持环境变量覆盖：
    - `TUSHARE_PROXY_LIMIT_COUNT`（默认30）
    - `TUSHARE_PROXY_LIMIT_SLEEP`（默认20）

- 接入点：
  - `longhubang_limit_concept.py`
    - `kpl_concept_cons` 调用前统一走节流器；
  - `ths_feature_data.py`
    - `kpl_concept_cons` 调用前统一走节流器；
  - `longhubang_hot_signals.py`
    - `limit_step` / `ths_hot` / `kpl_list` / `limit_cpt_list` 调用前统一走节流器；
    - 其中你重点关注的 `kpl_list` 已按同规则生效。

- 验证：
  - `python3 -m py_compile tushare_proxy_rate_limit.py longhubang_limit_concept.py ths_feature_data.py longhubang_hot_signals.py`

### 追加修正（同日，`kpl_concept_cons`/`kpl_list` 改为“单次请求60秒超时”，非整体60秒）
- 需求：
  - `kpl_concept_cons`、`kpl_list` 的超时应按“单次请求”控制为 `60s`，而不是整个股票池任务总时长 `60s`。

- 实现：
  - `tushare_proxy_rate_limit.py`
    - 新增 `call_tushare_with_timeout(...)`：
      - 先走代理限流节流；
      - 再对单次接口调用执行 `future.result(timeout=60)`。
  - `longhubang_limit_concept.py`
    - `pro.kpl_concept_cons(...)` 全部改为 `call_tushare_with_timeout(..., timeout_sec=60)`。
  - `ths_feature_data.py`
    - `pro.kpl_concept_cons(...)` 改为单次60秒超时调用。
  - `longhubang_hot_signals.py`
    - `pro.kpl_list(...)`（范围/单日）改为单次60秒超时调用。
  - `longhubang_engine.py`
    - `kpl_concept_cons`、`kpl_list` 的引擎任务级超时改为 `1800s`（保护兜底）；
    - 避免任务级60秒提前打断单次60秒策略。

- 验证：
  - `python3 -m py_compile tushare_proxy_rate_limit.py longhubang_limit_concept.py ths_feature_data.py longhubang_hot_signals.py longhubang_engine.py`

### 追加修正（同日，仅 `ths_hot` 使用系统当前日期）
- 用户更正需求：
  - 只调整 `ths_hot` 的取数日期为系统当前日期；
  - `kpl_list` 继续沿用原逻辑（跟随分析日期参数）。

- `longhubang_engine.py`：
  - `ths_hot_fetcher.get_hot_data(...)` 保持 `end_date=datetime.now().strftime("%Y-%m-%d")`；
  - `kpl_list_fetcher.get_heat_data(...)` 的 `end_date` 恢复为 `date`（前端/分析参数）。

- 验证：
  - `python3 -m py_compile longhubang_engine.py`

### 追加修正（同日，代理限流改为“1分钟内50次”）
- 需求调整：
  - 将原“累计30次后sleep20秒”改为：
    - **1分钟内请求50次后**，限流 `sleep 20s`。

- `tushare_proxy_rate_limit.py`：
  - 节流逻辑从“累计计数”改为“滑动时间窗”；
  - 默认参数改为：
    - `TUSHARE_PROXY_LIMIT_COUNT=50`
    - `TUSHARE_PROXY_LIMIT_WINDOW_SEC=60`
    - `TUSHARE_PROXY_LIMIT_SLEEP=20`
  - 超限提示文案同步更新为“60s内请求达阈值”。

- 验证：
  - `python3 -m py_compile tushare_proxy_rate_limit.py`

### 追加优化（同日，`kpl_concept_cons`/`kpl_list` 改为“按日快照+内存匹配”）
- 背景：
  - 逐股票请求 `kpl_concept_cons`、`kpl_list` 导致代理请求次数过多，整体耗时偏长。

- `longhubang_limit_concept.py`（`kpl_concept_cons`）：
  - 调整为先按 `trade_date` 拉取当天整表快照（单次请求）；
  - 再在内存中按目标股票 `ts_code/con_code` 匹配概念与 `hot_num`；
  - 基于匹配结果继续计算板块强度与Top10，减少外部请求次数。

- `longhubang_hot_signals.py`（`kpl_list`）：
  - 调整为按每个交易日拉取一次整日热榜快照（单次请求）；
  - 再在内存中过滤本次龙虎榜股票池代码并计算热榜评分映射；
  - 不再按“交易日 × 股票”逐只请求。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_hot_signals.py longhubang_engine.py`

### 追加修正（同日，`ths_hot` 任务级超时恢复为60秒）
- 问题：
  - `ths_hot` 在引擎层误为 `10s`，与期望 `60s` 不一致。

- 修复：
  - `longhubang_engine.py` 中 `ths_hot` 的 `_safe_fetch_with_timeout(..., timeout_sec=60)`。

- 当前龙虎榜主流程超时配置核对：
  - `kpl_concept_cons`：任务级 `1800s`（单次请求级 `60s`）
  - `kpl_list`：任务级 `1800s`（单次请求级 `60s`）
  - `limit_step`：任务级 `60s`
  - `ths_hot`：任务级 `60s`

- 验证：
  - `python3 -m py_compile longhubang_engine.py`

### 追加功能（同日，龙虎榜首席策略师报告Webhook分层推送）
- 需求：
  - 龙虎榜分析完成后，将“首席策略师”报告通过 Webhook 发送；
  - 按报告中的分层结构（如 `1. 市场总体研判`、`2. 次日重点推荐股票`）拆分后逐条发出。

- `notification_service.py`：
  - 新增 `send_longhubang_chief_report(...)`：
    - 自动重载 `.env` 配置，读取最新 `WEBHOOK_*`；
    - 按标题层级切分首席策略师报告内容；
    - 先发送报告头信息（时间/范围/报告ID/层数），再按层逐条推送；
    - 长内容自动分片，避免单条消息过长。
  - 新增辅助方法：
    - `_split_longhubang_report_layers(...)`
    - `_match_layer_title(...)`
    - `_chunk_text_for_webhook(...)`
    - `_send_structured_webhook_message(...)`（兼容钉钉/飞书）

- `longhubang_engine.py`：
  - 在阶段7保存报告后新增阶段7.5：
    - 调用 `_send_chief_report_webhook(...)` 推送首席策略师报告；
    - 推送结果写入 `results["chief_webhook"]`（enabled/sent/success/error）；
    - 发送失败仅记录日志，不影响主流程成功返回。
  - 新增 `_send_chief_report_webhook(...)` 封装发送逻辑与异常保护。

- 验证：
  - `python3 -m py_compile notification_service.py longhubang_engine.py`

### 追加优化（同日，龙虎榜Webhook仅推送三项核心并表格化展示）
- 需求调整：
  - 通知仅发送：
    1) 次日推荐股票
    2) 高风险警示股票
    3) 热点题材总结
  - 股票信息采用表格方式推送，并提升整体展示效果。

- `notification_service.py`：
  - `send_longhubang_chief_report(...)` 改为“核心三段推送”：
    - 从首席策略师报告中仅筛选上述三类内容；
    - 发送一个总览头消息 + 三条分项消息；
    - 推荐/风险股票统一转为 Markdown 表格；
    - 热点题材转为“题材-代表标的-研判要点”表格。
  - 新增文本解析与展示辅助：
    - `_pick_longhubang_notify_sections(...)`
    - `_extract_stock_rows_for_table(...)`
    - `_extract_stock_name_from_line(...)`
    - `_extract_stock_note_from_line(...)`
    - `_extract_theme_rows_for_table(...)`
    - `_extract_stock_mentions(...)`
    - `_build_stock_table_message(...)`
    - `_build_theme_table_message(...)`
    - `_to_markdown_table(...)`
    - `_escape_table_cell(...)`
  - 展示优化：
    - 钉钉继续使用 markdown；
    - 飞书改为 interactive card + markdown 元素，提升可读性与观感。

- `longhubang_engine.py`：
  - 阶段7.5调用Webhook时，额外传入 `recommended_stocks` 作为推荐表格的兜底数据来源。

- 验证：
  - `python3 -m py_compile notification_service.py longhubang_engine.py`

### 追加调整（同日，Webhook仅“次日推荐股票”使用表格）
- 需求细化：
  - 仅 `次日推荐股票` 使用表格展示；
  - `高风险警示股票`、`热点题材总结` 改为文本要点展示。

- `notification_service.py`：
  - `send_longhubang_chief_report(...)` 中：
    - 保留推荐股票表格；
    - 风险与题材改为文本段落推送。
  - 新增 `_build_plain_section_message(...)`：
    - 对文本章节做精炼提取（去标题、去编号、保留要点），控制展示行数；
    - 无数据时输出友好提示。

- 验证：
  - `python3 -m py_compile notification_service.py longhubang_engine.py`

### 追加功能（2026-04-13，龙虎榜 P1 胜率增强数据接入）
- 需求目标：
  - 按优先级接入 3 组对“次日胜率”提升直接相关的数据到龙虎榜分析闭环：
    1) `top_list + top_inst`
    2) `moneyflow_ths + moneyflow_cnt_ths + moneyflow_ind_ths`
    3) `limit_list_ths + limit_list_d`
  - 将新增数据同时用于评分与 AI 分析团队，不仅做展示。

- 新增文件 `longhubang_p1_signals.py`：
  - 新增 `AdvancedP1SignalFetcher`，统一封装 P1 数据抓取、归一与聚合；
  - 统一输出：
    - `stock_signal_map`（按股票聚合的信号）
    - `top_candidates`（高优先候选）
    - `lhb_summary`（龙虎榜/机构摘要）
    - `moneyflow_summary`（个股/概念/行业资金摘要）
    - `limit_quality_summary`（封板质量摘要）
    - `concept_flow_top`、`industry_flow_top`（主线方向）
  - 所有接口调用沿用代理与超时/限流框架，避免阻塞主流程。

- `longhubang_engine.py`：
  - 引入并初始化 `AdvancedP1SignalFetcher`；
  - 在阶段3新增并行任务 `p1_advanced_signals`，结果写入 `results["p1_advanced_signals"]`；
  - 将 `p1_signal_data` 透传给：
    - 评分模块
    - 四位分析师 + 首席策略师
  - 在 `full_analysis_content` 中落盘 `p1_advanced_signals`，便于追溯。

- `longhubang_scoring.py`：
  - `calculate_stock_score`、`score_all_stocks` 新增可选参数 `p1_signal_data`；
  - 在不新增总分维度的前提下，增强既有维度打分：
    - 买入资金含金量（capital quality）
    - 净买入额评分（net inflow）
    - 卖出压力评分（sell pressure）
    - 机构评分（institution）
  - 新增 `_get_p1_detail(...)` 统一解析个股 P1 信号；
  - 明细表新增列：`P1增强`、`机构净额`、`主力净流`、`封板质量`；
  - 评分解释文本加入 P1 贡献说明。

- `longhubang_agents.py`：
  - 分析师上下文构建函数新增 `p1_signal_data` 入参并注入摘要：
    - `_build_brief_signal_context`
    - `_build_theme_signal_context`
    - `_build_stock_signal_context`
    - `_build_risk_signal_context`
    - `_build_chief_signal_context`
  - 对应公共分析方法与 `chief_strategist` 透传 P1 数据，确保团队讨论使用同一数据底座。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_agents.py longhubang_scoring.py longhubang_p1_signals.py`
  - 结果：通过（无语法错误）。

- 备注：
  - 本次为 P1 数据能力接入与链路打通；后续可继续按同样方式接入 `hm_list/hm_detail` 等游资画像数据。

### 追加功能（2026-04-13，龙虎榜 P2：游资画像 `hm_list + hm_detail` 接入）
- 需求目标：
  - 在既有 P1（机构/资金/封板）基础上，补充游资行为画像因子，进一步提升“次日胜率”识别精度；
  - 新增数据不仅采集，还要进入评分和 AI 分析团队上下文。

- `longhubang_p1_signals.py`：
  - 扩展数据源：新增 `hm_list + hm_detail`；
  - 新增采集流程：
    - `_collect_hm_list(...)`：提取活跃游资画像（出现频次、热度、胜率、风格、席位）；
    - `_collect_hm_detail(...)`：按股票归集游资买卖净额与正负向次数；
  - 新增解析函数：
    - `_extract_hm_list_rows(...)`
    - `_extract_hm_detail_rows(...)`
  - 扩展个股状态与聚合字段：
    - `youzi_hits / youzi_net_amt_sum / youzi_positive_hits / youzi_negative_hits`
    - `youzi_profile_score_sum / youzi_name_counter / youzi_style_counter`
  - P1总分改为四因子融合：
    - `龙虎榜机构信号(30%) + 资金确认(30%) + 封板质量(25%) + 游资画像(15%)`
  - `stock_signal_map` 新增输出字段：
    - `youzi_signal_score`、`youzi_net_amt_sum`、`top_hm_name`、`top_hm_style` 等；
  - 结果新增：
    - `youzi_profile_summary`
    - `youzi_profile_top`

- `longhubang_scoring.py`：
  - 在 4 个核心评分维度中加入游资画像修正：
    - 资金含金量：活跃游资持续净买入加分；
    - 净买入额：游资净买入与正向占比加分；
    - 卖出压力：游资净卖出占比高时惩罚；
    - 机构共振：游资画像强时小幅协同加分；
  - 排名明细新增列：
    - `游资信号`
    - `游资画像`
  - 评分说明文案补充 `hm_list + hm_detail`。

- `longhubang_agents.py`：
  - 分析师上下文加入游资画像摘要：
    - `P1增强信号` 中补充游资净买入正向占比与游资画像TOP；
    - 题材上下文补充“活跃游资画像TOP”；
    - 个股上下文逐股补充 `游资信号`、`游资画像(游资名/风格)`；
    - 风险上下文新增“游资净卖出偏多”风险旗标。

- 验证：
  - `python3 -m py_compile longhubang_p1_signals.py longhubang_scoring.py longhubang_agents.py longhubang_engine.py`
  - 结果：通过（无语法错误）。
- 补充修正：
  - 当 `hm_detail` 无有效数据时，P1聚合总分自动回退到原三因子权重（`lhb 35% + money 35% + limit 30%`），避免空游资数据导致评分被动下滑。

### 追加功能（2026-04-13，龙虎榜 P3：概念主线切换为 `dc_concept_cons` 优先）
- 需求目标：
  - 将概念板块链路从单一 `kpl_concept_cons` 升级为：
    - 优先 `dc_concept_cons`
    - 失败自动回退 `kpl_concept_cons`
  - 保持原有评分与分析师上下文结构不变，提升“概念主线匹配”稳定性与时效性。

- `longhubang_limit_concept.py`：
  - 主流程改造：
    - 新增 `_choose_concept_source(...)` 自动选择数据源；
    - 新增 `_fetch_trade_date_snapshot(trade_date, source)`，按来源动态调用；
    - 当 `dc_concept_cons` 当日快照为空时，自动回退 `kpl_concept_cons`；
  - 概念强度增强：
    - 新增 `_fetch_dc_concept_snapshot(...)` + `_extract_concept_strength_from_df(...)`；
    - 当成分数据缺少 `hot_num` 时，用概念快照（hot/涨跌幅/成交额）补足强度，再结合成分命中数计算主线；
  - 字段识别稳健化：
    - `_resolve_schema(...)` 支持 `source` 维度识别 `dc/kpl` 不同表结构；
    - 新增 `_filter_stock_code_cols(...)`，过滤非股票代码列；
    - 新增 `_pick_low_cardinality_col(...)`，在 `name/con_name` 间自动判定更像“概念名”的列，避免把股票名误识别为概念；
  - 结果新增：
    - `concept_source`
    - `concept_market_strength_top`
  - `rotation_summary` 新增 `concept_source` 便于前端和日志追踪。

- `longhubang_engine.py`：
  - 阶段3“概念主线”日志与默认兜底描述更新为 `dc_concept_cons|kpl_concept_cons`，避免误导为仅kpl。

- `longhubang_scoring.py`：
  - 板块轮动评分文案更新为“优先 `dc_concept_cons`，失败回退 `kpl_concept_cons`”；
  - 评分说明同步更新概念来源描述。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_scoring.py longhubang_agents.py longhubang_p1_signals.py`
  - 结果：通过（无语法错误）。

### 追加功能（2026-04-13，前端展示 `concept_source`）
- 需求：
  - 将概念主线数据来源 `concept_source` 在前端可视化展示，便于确认本次使用的是 `dc` 还是 `kpl`。

- `longhubang_ui.py`：
  - 新增 `_format_concept_source_label(...)`：
    - `dc` -> 东方财富概念成分（`dc_concept_cons`）
    - `kpl` -> 开盘啦概念成分（`kpl_concept_cons`，回退）
    - 其他 -> 原值/未知
  - 在“AI评分排名 > 概念主线”信息条增加：`数据源：...`；
  - 在“历史报告回显 > 概念主线”信息条增加：`数据源：...`；
  - 评分说明文案同步更新为：
    - 优先 `dc_concept_cons`，失败回退 `kpl_concept_cons`。

- 验证：
  - `python3 -m py_compile longhubang_ui.py longhubang_engine.py longhubang_limit_concept.py`
  - 结果：通过（无语法错误）。

### 追加功能（2026-04-13，当晚龙虎榜无数据时兜底 `top_list`）
- 需求：
  - 当日晚上 StockAPI 龙虎榜接口可能尚未返回数据；
  - 增加兜底：自动使用 Tushare `top_list` 获取“龙虎榜每日明细”，保障分析可继续执行。

- `longhubang_data.py`：
  - 新增依赖：
    - `data_source_manager`（复用已配置的 Tushare Token/代理）
    - `call_tushare_with_timeout`（复用超时与限流）
  - 新增兜底链路：
    - `_get_tushare_top_list_data(date)`：按 `trade_date/date` 调用 `top_list`；
    - `_extract_top_list_records(...)`：将 `top_list` 数据转换为现有系统可消费的统一字段（`rq/gpdm/gpmc/mrje/mcje/jlrje...`）；
  - `get_longhubang_data(date)` 逻辑升级：
    - 先走 StockAPI；
    - 若为空，自动回退 Tushare `top_list`；
    - 两者都失败才返回空。
  - 金额单位处理：
    - 对 `top_list` 金额字段做启发式单位识别并统一到“元”，避免评分阈值失真。

- 兼容性说明：
  - 兜底返回结构保持与原链路一致（`{"code":20000,"data":[...]}`），引擎与数据库无需额外改造。

- 验证：
  - `python3 -m py_compile longhubang_data.py longhubang_engine.py longhubang_ui.py`
  - 结果：通过（无语法错误）。

### 追加修复（2026-04-13，概念主线误把个股名当概念）
- 现象：
  - 概念主线中出现明显个股名（如“华光源海/埃科光电”），与预期不符。

- 原因定位：
  - `dc_concept_cons` 在部分字段形态下，概念列与个股名称列存在歧义，导致误选列后把个股名纳入概念强度统计。

- 修复内容（`longhubang_limit_concept.py`）：
  - `dc`源概念列选择策略调整：
    - 优先 `cpt_name` / `concept_name`；
    - 其次优先 `name`（不再默认在 `name/con_name` 间做低基数二选一）。
  - 新增 `stock_name_col` 识别并过滤：
    - 在逐行提取概念时，若概念词与该行股票名相同，直接剔除；
    - 避免“股票名=概念名”的污染进入主线统计。
  - 在结果中增加 `concept_field`，用于追踪本次实际采用的概念字段。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_ui.py`
  - 结果：通过（无语法错误）。

### 追加功能（2026-04-13，龙虎榜默认近3个交易日 + 同股取最近榜单）
- 需求：
  - 龙虎榜分析默认扩大到近3个交易日，不再仅当天；
  - 若同一股票在多天重复上榜，以最近交易日榜单为准。

- `longhubang_data.py`：
  - 新增交易日工具：
    - `_build_recent_trade_dates(days, end_date)`：按交易日窗口生成日期列表；
    - `_date_to_key(...)`、`_parse_date_safe(...)`：统一日期比较键。
  - `get_recent_days_data(...)` 升级：
    - 支持 `end_date`（指定日期模式下作为截止日）；
    - 按“近N个交易日”逐日获取数据（含原有 `top_list` 兜底）。
  - 新增 `prioritize_latest_stock_records(data_list)`：
    - 按股票代码聚合后，仅保留每只股票“最近交易日”的全部榜单记录；
    - 同一最近交易日内多席位记录全部保留。

- `longhubang_engine.py`：
  - `run_comprehensive_analysis(..., days=3)` 默认窗口改为3天；
  - 指定日期模式也改为“以该日期为截止的近3个交易日窗口”；
  - 阶段1新增同股跨天去重日志：`raw -> dedup`。

- `longhubang_ui.py`：
  - “最近N天”默认值从 `1` 调整为 `3`；
  - “指定日期”点击分析时传入 `days=3`；
  - `run_longhubang_analysis` 默认 `days=3`。

- 验证：
  - `python3 -m py_compile longhubang_data.py longhubang_engine.py longhubang_ui.py`
  - 结果：通过（无语法错误）。

### 追加功能（2026-04-13，龙虎榜接入“近7日K线趋势阶段”避免误判启动期）
- 需求：
  - 用户反馈部分个股已连续上涨多日，但AI仍判断“启动初期”；
  - 按用户要求，仅拉取近7天日线用于阶段识别。

- 新增 `longhubang_kline_signals.py`：
  - 新增 `SevenDayKlineTrendFetcher`（数据源：Tushare `daily`）；
  - 采用“按交易日快照一次拉取 + 内存按股票池匹配”模式，避免逐股高频请求；
  - 单次请求超时使用 `call_tushare_with_timeout(..., timeout_sec=60)`；
  - 输出 `stock_trend_map`，核心字段：
    - `trend_stage`（启动期/加速期/高位震荡/震荡期/退潮期）
    - `change_7d_pct`、`up_streak`、`latest_change_pct`、`drawdown_from_high_pct`、`vol_ratio`。

- `longhubang_engine.py`：
  - 新增趋势抓取器初始化：`self.kline_trend_fetcher = SevenDayKlineTrendFetcher()`；
  - 阶段3新增任务 `daily_kline_trend`，固定 `days=7`，并使用前端传入 `date` 作为截止日；
  - 将 `daily_kline_trend` 接入：
    - 评分计算（`score_all_stocks`）
    - 4位分析师 + 首席策略师提示词上下文
    - 报告存储结构化结果（`full_analysis_content`）。

- `longhubang_agents.py`：
  - 所有分析师方法新增可选入参 `daily_kline_data`；
  - 信号上下文新增“7日趋势阶段”摘要（覆盖数、阶段分布、强势TOP）；
  - 个股级候选展示新增：`7日阶段/7日涨跌/连涨天数`；
  - 风险信号新增：
    - 退潮期股票偏多风险
    - 高位震荡股票偏多风险；
  - 首席策略师候选列表新增趋势阶段信息，减少“连涨多日仍判启动”的误导。

- `longhubang_scoring.py`：
  - `score_all_stocks(...)` 新增 `daily_kline_data` 入参；
  - 输出列新增：
    - `趋势阶段`
    - `7日涨跌`
    - `连涨天数`。

- `longhubang_ui.py`：
  - 评分表（当前分析 / 历史报告）新增趋势列展示：
    - `趋势阶段`、`7日涨跌(%)`、`连涨天数`；
  - 历史报告“加载到分析页”时同步回填 `daily_kline_trend`，保持前后端一致。

- 验证：
  - `python3 -m py_compile longhubang_kline_signals.py longhubang_engine.py longhubang_agents.py longhubang_scoring.py longhubang_ui.py`
  - 结果：通过（无语法错误）。

### 追加修复（2026-04-13，概念板块归属优先改为`kpl_concept_cons`并增强`dc`防污染）
- 现象：
  - 概念主线偶发出现“股票名被识别为概念”（如截图中的“华光源海”）。

- 调整目标：
  - 你要的是“个股属于什么概念板块（如CPO、光通信）”，优先保证个股概念归属准确。

- `longhubang_limit_concept.py` 调整：
  1. 数据源优先级调整：
     - `_choose_concept_source(...)` 改为 **优先 `kpl_concept_cons`**，`dc_concept_cons` 作为回退。
  2. `dc`数据白名单过滤：
     - 新增 `_extract_valid_concepts_from_snapshot(...)`：从 `dc_concept` 快照提取有效概念集合；
     - 新增 `_filter_mapping_by_concept_whitelist(...)`：对 `dc_concept_cons` 的映射结果做概念白名单过滤，剔除异常概念值。
  3. 自动回退策略：
     - 当 `dc` 命中覆盖率偏低或映射为空时，自动回退 `kpl_concept_cons`，优先保障“个股-概念板块”可用且可读。

- 结果预期：
  - “最强概念/主线匹配”会更稳定地显示概念板块名，而不是个股名；
  - 更符合短线语义（CPO、光通信等题材标签）。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_agents.py longhubang_scoring.py longhubang_ui.py longhubang_kline_signals.py`
  - 结果：通过（无语法错误）。

### 追加调整（2026-04-13，概念来源优先级回调为`dc_concept_cons`优先）
- 背景：
  - 用户反馈 `kpl_concept_cons` 已无增量更新，不适合作为主链。
- 调整：
  - `longhubang_limit_concept.py` 中 `_choose_concept_source(...)` 恢复为：
    - 优先 `dc_concept_cons`
    - 回退 `kpl_concept_cons`
- 说明：
  - 仍保留此前新增的 `dc` 白名单过滤与自动回退逻辑，防止概念污染并保证可用性。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py`
  - 结果：通过（无语法错误）。

### 追加升级（2026-04-13，概念板块主链切换为 `dc_member + dc_index`，并增强防污染）
- 背景：
  - 用户反馈希望获取更准确的“个股所属概念板块（如 CPO / 光通信）”，且 `kpl_concept_cons` 增量不足。

- `longhubang_limit_concept.py`：
  1. 主链接口升级：
     - 概念来源优先级改为：`dc_member + dc_index` -> `dc_concept_cons` -> `kpl_concept_cons`。
     - 新增 `_is_source_available(...)`，支持按源动态组合候选链路。
  2. 新增 `dc_member` 专用解析：
     - `_fetch_trade_date_snapshot(...)` 新增 `dc_member` 分支（按交易日快照拉取）。
     - `_fetch_dc_index_name_map(...)`：拉取 `dc_index`，构建“概念代码 -> 概念名称”映射。
     - `_extract_target_mapping_from_dc_member_df(...)`：从 `dc_member` 快照中提取“目标股票 -> 概念集合”。
  3. 防止“股票名混入概念”：
     - `dc_member` 的概念列识别改为仅优先 `cpt_name/concept_name`；
     - 若无明确概念名称列，转为使用概念代码 + `dc_index` 映射反查，不再直接使用 `name` 兜底；
     - 解析时增加股票名称列剔除逻辑，避免将股票简称误计入概念集合。
  4. 展示字段：
     - `concept_source` 增加 `dc_member`；
     - `concept_field` 在 `dc_member` 场景标识为 `dc_index.name` 便于排查来源。

- `longhubang_ui.py`：
  - `_format_concept_source_label(...)` 增加 `dc_member` 显示文案：
    - “东方财富概念成分（dc_member + dc_index）”。

- `longhubang_scoring.py`：
  - 评分说明文案同步更新为：
    - 优先 `dc_member + dc_index`，失败回退 `dc_concept_cons / kpl_concept_cons`。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_engine.py longhubang_agents.py longhubang_scoring.py longhubang_ui.py longhubang_kline_signals.py`
  - 结果：通过（无语法错误）。

### 追加调整（2026-04-13，前端“概念主线”展示改为优先开盘啦热度并过滤ST）
- 需求：
  - “概念主线”展示希望直接使用下方“开盘啦热度（kpl_list）”的数据；
  - 过滤 `ST` 板块相关题材；
  - 按最新要求展示全部可用题材（而非仅最强一个）。

- `longhubang_ui.py`：
  1. 新增辅助函数：
     - `_is_st_theme(...)`：识别并过滤 `ST`/`*ST`/`ST板块` 等题材。
     - `_get_kpl_themes_without_st(...)`：提取并过滤开盘啦题材列表。
     - `_build_kpl_mainline_text(...)`：构建“题材(出现次数)”展示文本。
  2. 当前分析页 `display_scoring_ranking(...)`：
     - “🔥概念主线”改为优先读取 `kpl_list_heat.kpl_summary.top_themes_today`；
     - 过滤 `ST` 后展示“最强题材 + 全部题材列表”；
     - 若开盘啦无有效题材，回退原 `concept_rotation` 展示逻辑。
  3. 历史报告详情页：
     - 同步采用相同逻辑（优先开盘啦、过滤ST、展示全部题材、失败回退原概念主线）。
  4. “🧭开盘啦热度”区块：
     - 文案同步为“最热题材（过滤ST）/今日题材TOP（过滤ST）”。

- 验证：
  - `python3 -m py_compile longhubang_ui.py`
  - 结果：通过（无语法错误）。

### 追加修复（2026-04-13，买入价位区间注入真实价格锚点并做强约束）
- 背景：
  - 用户反馈“买入价位区间与实际价格对不上”，根因是最终决策由模型生成，缺少足够强的价格锚点和后置校验。

- `stock_data.py`：
  - `get_latest_indicators(...)` 新增输出：
    - `high_7d`：近7日最高价（基于 `High`）
    - `low_7d`：近7日最低价（基于 `Low`）
  - 作为后续最终决策提示词与校验的真实价格锚点输入。

- `deepseek_client.py`：
  1. `final_decision(...)` 提示词增强：
     - 注入“当前价 / 7日高点 / 7日低点”价格锚点；
     - 新增硬约束：
       - `entry_range` 必须是 `xx.xx-yy.yy` 纯数字格式；
       - 区间中位数偏离当前价不超过10%；
       - 存在7日高低点时，区间需落在 `[low_7d*0.97, high_7d*1.03]`；
       - `stop_loss < entry_min < entry_max < take_profit`。
  2. 新增结果后处理（防模型偏离）：
     - `_normalize_decision_price_fields(...)`：对 `entry_range/take_profit/stop_loss` 做解析、夹逼、顺序修正与统一格式化；
     - `_get_price_anchor(...)`：锚点提取与缺失兜底（可结合 `price/MA20/布林带`）；
     - `_safe_float(...)`、`_extract_numbers_from_text(...)`、`_fmt_price(...)` 辅助方法。
  3. 输出新增 `price_anchor` 字段，便于核对本次决策使用的价格锚点。

- 验证：
  - `python3 -m py_compile deepseek_client.py stock_data.py app.py ai_agents.py`
  - 结果：通过（无语法错误）。

### 追加优化（2026-04-13，买入点建议对齐“次日可执行”并锚定最新交易日）
- 需求：
  - 买入点要考虑“隔天可操作”，尽量以最新交易日数据给出建议，避免偏离盘面。

- `stock_data.py`：
  - `get_latest_indicators(...)` 新增 `latest_trade_date` 字段（最新K线交易日）。

- `deepseek_client.py`：
  1. 价格锚点来源调整：
     - `_get_price_anchor(...)` 改为优先使用 `indicators.price`（最新交易日收盘），再回退 `stock_info.current_price`。
  2. 提示词锚点补充：
     - `final_decision(...)` 的锚点段新增“最新交易日”字段，明确要求基于最新交易日数据做次日建议。

- `longhubang_kline_signals.py`：
  - 7日趋势结果新增：
    - `high_7d`
    - `low_7d`
  - 便于首席策略师给出次日可执行区间时使用区间边界。

- `longhubang_agents.py`：
  1. 首席策略师候选上下文增强：
     - 在高热度候选中加入 `最新交易日 / 最新收盘 / 7日区间` 信息。
  2. 首席策略师提示词新增“次日可执行价格约束”：
     - 买入区间需锚定最新交易日数据；
     - 区间中位数与最新收盘偏离建议不超过 ±8%；
     - 强制满足 `止损 < 买入下沿 < 买入上沿 < 目标价`；
     - 输出优先使用机器可解析数字区间格式（如 `18.20-18.80`）。

- 验证：
  - `python3 -m py_compile longhubang_kline_signals.py longhubang_agents.py deepseek_client.py stock_data.py app.py ai_agents.py`
  - 结果：通过（无语法错误）。

### 追加调整（2026-04-13，评分规则中“涨跌幅”移出评分，仅做提示展示）
- 需求：
  - 短线分析场景下，涨跌幅不作为评分标准，仅用于提示展示。

- `longhubang_limit_concept.py`：
  - 概念强度回退逻辑调整：
    - `_extract_concept_strength_from_df(...)` 在 `hot_num` 缺失时，**不再使用 `pct_chg/涨跌幅` 参与评分构造**；
    - 改为仅使用成交额类字段（`amount/turnover`）做弱回退。

- `longhubang_scoring.py`：
  - 在结果组装处增加注释，明确 `趋势阶段/7日涨跌/连涨天数` 为展示字段，不参与分数计算。
  - `get_score_explanation()` 新增说明：
    - “涨跌幅（如7日涨跌）仅用于提示展示，不参与综合评分”。

- `longhubang_ui.py`：
  - 两处“评分维度说明”文案同步新增：
    - “涨跌幅（如7日涨跌）仅用于提示展示，不参与评分”。

- 验证：
  - `python3 -m py_compile longhubang_limit_concept.py longhubang_scoring.py longhubang_ui.py longhubang_engine.py longhubang_agents.py`
  - 结果：通过（无语法错误）。

### 追加回滚（2026-04-14，移除龙虎榜“7日K线趋势”与价格区间约束）
- 需求：
  - “7日K线”不再获取、不参与评分、不提供给AI分析团队；
  - “买入区间”不再输出具体价格，恢复为文字化次日策略建议。

- `longhubang_engine.py`：
  - 删除阶段3中的 `daily_kline_trend` 抓取与结果字段写入；
  - 从 `full_analysis_content` 中移除 `daily_kline_trend` 字段；
  - 不再向评分器与4位分析师/首席策略师传入 `daily_kline_data`。

- `longhubang_agents.py`：
  - 移除分析上下文中的7日趋势摘要与个股7日趋势字段；
  - 清理 `daily_kline_data` 参数占位与透传调用，避免代码层面误判“仍在使用7日K线”；
  - 首席策略师提示词改为“买入时机建议 + 交易触发条件（仅文字，不写价格）”；
  - 个股潜力分析师提示词移除“买入价位/目标价位/止损价位”数值约束。

- `longhubang_scoring.py`：
  - 清理 `score_all_stocks(...)` 中 `daily_kline_data` 参数占位；
  - 移除评分结果中的趋势展示字段：
    - `趋势阶段`
    - `7日涨跌`
    - `连涨天数`

- `longhubang_ui.py`：
  - 移除评分表格与历史报告中的7日趋势列展示；
  - 文案从“关键价位”调整为“次日策略建议”；
  - 评分说明文案更新为“涨跌幅信息仅用于提示展示，不参与评分”。

- 验证：
  - `python3 -m py_compile longhubang_engine.py longhubang_agents.py longhubang_scoring.py longhubang_ui.py`
  - 结果：通过（无语法错误）。
