## 根目录文件归类计划

### 文件分类统计

根目录共有 **114 个 Python 文件**，需要按功能模块归类整理。

---

## 归类方案

### 1. 功能模块目录（按业务领域）

#### `modules/longhubang/` - 龙虎榜模块
```
longhubang_agents.py
longhubang_data.py
longhubang_db.py
longhubang_engine.py
longhubang_history.py
longhubang_hot_signals.py
longhubang_kline_signals.py
longhubang_limit_concept.py
longhubang_p1_signals.py
longhubang_pdf.py
longhubang_scoring.py
longhubang_ui.py
```

#### `modules/news_flow/` - 新闻流量模块
```
news_flow_agents.py
news_flow_agents_refactored.py
news_flow_alert.py
news_flow_data.py
news_flow_db.py
news_flow_engine.py
news_flow_model.py
news_flow_pdf.py
news_flow_scheduler.py
news_flow_sentiment.py
news_flow_ui.py
```

#### `modules/sector_strategy/` - 板块策略模块
```
sector_strategy_agents.py
sector_strategy_data.py
sector_strategy_db.py
sector_strategy_engine.py
sector_strategy_pdf.py
sector_strategy_scheduler.py
sector_strategy_ui.py
```

#### `modules/smart_monitor/` - 智能盯盘模块
```
smart_monitor_data.py
smart_monitor_db.py
smart_monitor_deepseek.py
smart_monitor_engine.py
smart_monitor_kline.py
smart_monitor_qmt.py
smart_monitor_tdx_data.py
smart_monitor_ui.py
```

#### `modules/macro_analysis/` - 宏观分析模块
```
macro_analysis_agents.py
macro_analysis_data.py
macro_analysis_engine.py
macro_analysis_ui.py
```

#### `modules/macro_cycle/` - 宏观周期模块
```
macro_cycle_agents.py
macro_cycle_data.py
macro_cycle_engine.py
macro_cycle_pdf.py
macro_cycle_ui.py
```

#### `modules/main_force/` - 主力选股模块
```
main_force_analysis.py
main_force_batch_db.py
main_force_history_ui.py
main_force_pdf_generator.py
main_force_selector.py
main_force_ui.py
```

#### `modules/theme_peer/` - 同题材补涨模块
```
theme_peer_selector.py
theme_peer_ui.py
```

#### `modules/portfolio/` - 组合管理模块
```
portfolio_db.py
portfolio_manager.py
portfolio_scheduler.py
portfolio_ui.py
```

#### `modules/low_price_bull/` - 低价牛模块
```
low_price_bull_monitor.py
low_price_bull_monitor_ui.py
low_price_bull_selector.py
low_price_bull_service.py
low_price_bull_strategy.py
low_price_bull_ui.py
```

#### `modules/profit_growth/` - 业绩增长模块
```
profit_growth_monitor.py
profit_growth_selector.py
profit_growth_ui.py
```

#### `modules/value_stock/` - 价值股模块
```
value_stock_selector.py
value_stock_strategy.py
value_stock_ui.py
```

#### `modules/small_cap/` - 小盘股模块
```
small_cap_selector.py
small_cap_ui.py
```

---

### 2. 基础设施目录（已存在，需补充）

#### `infrastructure/data/` - 数据获取
```
news_announcement_data.py
qstock_news_data.py
quarterly_report_data.py
market_sentiment_data.py
ths_feature_data.py
risk_data_fetcher.py
fund_flow_akshare.py
```

#### `infrastructure/db/` - 数据库
```
auth_db.py
monitor_db.py
```

#### `infrastructure/notification/` - 通知服务
```
rapid_rise_monitor_service.py
```

#### `infrastructure/external/` - 外部接口
```
tushare_client.py
tushare_proxy_rate_limit.py
miniqmt_interface.py
```

#### `infrastructure/pdf/` - PDF 生成
```
pdf_generator.py
pdf_generator_fixed.py
pdf_generator_pandoc.py
```

---

### 3. 配置和管理目录

#### `config/` - 配置文件
```
config.py
model_config.py
```

#### `services/` - 通用服务
```
config_manager.py
data_source_manager.py
monitor_manager.py
trade_calendar_service.py
```

---

### 4. 工具和脚本目录

#### `scripts/` - 脚本工具
```
init_history_data.py
migrate_sqlite_to_mysql.py
update_env_example.py
run.py
stm.py
```

#### `tests/` - 测试文件
```
test_cooccur_groups.py
test_tdx_api.py
test_theme_diversify.py
test_theme_peer.py
```

---

### 5. 保留在根目录

```
app.py                    # 主入口
ai_agents.py             # 兼容层
database.py              # 兼容层
db_adapter.py            # 兼容层
deepseek_client.py       # 兼容层
stock_data.py            # 兼容层
monitor_service.py       # 兼容层
notification_service.py  # 兼容层
```

---

## 执行步骤

### Step 1: 创建模块目录结构
```bash
mkdir -p modules/{longhubang,news_flow,sector_strategy,smart_monitor,macro_analysis,macro_cycle,main_force,theme_peer,portfolio,low_price_bull,profit_growth,value_stock,small_cap}
mkdir -p infrastructure/{external,pdf}
mkdir -p config services scripts tests
```

### Step 2: 移动文件到对应目录
- 按模块批量移动
- 每个模块创建 `__init__.py`

### Step 3: 更新 import 路径
- 更新所有引用这些文件的 import 语句
- 保持兼容层不变

### Step 4: 验证
- 编译检查所有文件
- 运行测试确保功能正常

---

## 预期效果

**整理前：**
- 根目录 114 个文件，混乱难找

**整理后：**
- 根目录 ~10 个文件（入口 + 兼容层）
- 13 个功能模块目录，清晰分类
- 4 个基础设施子目录
- 3 个管理目录

**收益：**
- ✅ 文件组织清晰，易于查找
- ✅ 模块边界明确
- ✅ 便于团队协作
- ✅ 降低认知负担
