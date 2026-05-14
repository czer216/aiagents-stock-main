# MySQL 数据库配置说明

本项目业务数据已改为通过 MySQL 存储，不再直接写入 SQLite 文件。

## 环境变量

在 `.env` 中配置：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=aiagents_stock
MYSQL_CHARSET=utf8mb4
MYSQL_AUTO_CREATE_DATABASE=true
```

`MYSQL_AUTO_CREATE_DATABASE=true` 时，应用首次连接发现数据库不存在会尝试自动创建数据库；如果使用低权限账号，请先手工创建：

```sql
CREATE DATABASE aiagents_stock CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## 表名映射

旧版本使用多个 SQLite 文件，例如 `stock_monitor.db`、`auth.db`、`longhubang.db`。为了避免不同 SQLite 文件里的同名表在 MySQL 中冲突，应用会按旧文件名给表加前缀：

- `auth.db` 的 `users` -> `auth__users`
- `stock_monitor.db` 的 `notifications` -> `stock_monitor__notifications`
- `longhubang.db` 的 `longhubang_records` -> `longhubang__longhubang_records`
- `data/kline_cache_2026.db` 的 `kline_daily` -> `kline_cache_2026__kline_daily`

## 依赖

重新安装依赖：

```bash
pip install -r requirements.txt
```

其中 MySQL 驱动为 `PyMySQL`。如果 MySQL 用户使用 `caching_sha2_password` 或 `sha256_password` 认证，还需要 `cryptography`，已包含在 `requirements.txt`。

## 导入旧 SQLite 数据

项目提供一次性迁移脚本：

```bash
MYSQL_HOST=127.0.0.1 \
MYSQL_PORT=3306 \
MYSQL_USER=root \
MYSQL_PASSWORD=你的密码 \
MYSQL_DATABASE=aiagents_stock \
python3 migrate_sqlite_to_mysql.py
```

默认会导入项目根目录和 `data/` 下已有的 `.db` 文件，并清空同名前缀的目标表后重建数据。若要追加导入，使用：

```bash
python3 migrate_sqlite_to_mysql.py --append
```
