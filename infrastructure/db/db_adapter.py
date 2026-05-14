"""
MySQL-backed compatibility layer for the subset of sqlite3 used by this app.

The project was originally split across many SQLite files.  To keep the
business modules mostly unchanged while moving to MySQL, this adapter maps each
former database file to a table namespace.  For example stock_monitor.db table
`notifications` becomes `stock_monitor__notifications` in MySQL.
"""

from __future__ import annotations

import os
import re
import sqlite3 as _sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except Exception:
    pass

try:
    import pymysql
    from pymysql.cursors import Cursor as PyMySQLCursor
except ImportError:  # pragma: no cover - surfaced at runtime with a clear error
    pymysql = None
    PyMySQLCursor = object


class Error(Exception):
    pass


class OperationalError(Error):
    pass


class IntegrityError(Error):
    pass


class Row(dict):
    """sqlite3.Row-like object supporting both key and index access."""

    def __init__(self, keys: list[str], values: Iterable[Any]):
        super().__init__(zip(keys, values))
        self._keys = list(keys)
        self._values = tuple(values)

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, int):
            return self._values[item]
        return super().__getitem__(item)

    def keys(self):  # noqa: D401 - match sqlite Row API shape
        return self._keys


class _Excluded:
    pass


_POOLED_CONNECTIONS: dict[tuple[int, str], Any] = {}


def _mysql_config() -> dict[str, Any]:
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "aiagents_stock"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "autocommit": False,
    }


def _server_config() -> dict[str, Any]:
    cfg = _mysql_config()
    cfg.pop("database", None)
    return cfg


def _namespace_from_path(db_path: str) -> str:
    stem = Path(str(db_path or "app")).stem.lower()
    stem = re.sub(r"[^0-9a-zA-Z_]+", "_", stem).strip("_")
    if not stem:
        stem = "app"
    if stem[0].isdigit():
        stem = f"db_{stem}"
    return stem


def _prefixed_table(namespace: str, table: str) -> str:
    raw = str(table or "").strip("`")
    if raw.startswith(f"{namespace}__"):
        return f"`{raw}`"
    return f"`{namespace}__{raw}`"


class Cursor:
    def __init__(self, cursor: PyMySQLCursor, namespace: str, connection: "Connection"):
        self._cursor = cursor
        self._namespace = namespace
        self._connection = connection
        self._last_description: Optional[tuple] = None

    @property
    def lastrowid(self) -> int:
        return int(getattr(self._cursor, "lastrowid", 0) or 0)

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", 0) or 0)

    @property
    def description(self):
        return self._last_description or self._cursor.description

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        translated = self._translate(sql)
        if translated is None:
            return self
        params = self._normalize_params(params)
        started_at = time.time()
        try:
            self._cursor.execute(translated, params)
            self._last_description = self._cursor.description
            self._log_if_slow(translated, started_at)
            return self
        except Exception as exc:
            self._log_if_slow(translated, started_at, failed=True)
            if self._is_ignorable_mysql_error(exc, translated):
                return self
            self._raise_compatible(exc)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        translated = self._translate(sql)
        if translated is None:
            return self
        started_at = time.time()
        try:
            self._cursor.executemany(translated, [self._normalize_params(p) for p in seq_of_params])
            self._last_description = self._cursor.description
            self._log_if_slow(translated, started_at, many=True)
            return self
        except Exception as exc:
            self._log_if_slow(translated, started_at, many=True, failed=True)
            if self._is_ignorable_mysql_error(exc, translated):
                return self
            self._raise_compatible(exc)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return self._format_row(row)

    def fetchall(self):
        return [self._format_row(row) for row in self._cursor.fetchall()]

    def close(self):
        return self._cursor.close()

    def _format_row(self, row: Any):
        if self._connection.row_factory is Row and self._last_description:
            keys = [col[0] for col in self._last_description]
            return Row(keys, row)
        return row

    def _normalize_params(self, params: Optional[Iterable[Any]]):
        if params is None:
            return None
        if isinstance(params, dict):
            return params
        return tuple(params)

    def _translate(self, sql: str) -> Optional[str]:
        text = str(sql or "").strip()
        if not text:
            return text

        # SQLite schema introspection used by news_flow_db.
        pragma_match = re.match(r"PRAGMA\s+table_info\((`?)([A-Za-z_][\w]*)\1\)", text, flags=re.I)
        if pragma_match:
            table = f"{self._namespace}__{pragma_match.group(2)}"
            return (
                "SELECT ORDINAL_POSITION AS cid, COLUMN_NAME AS name, DATA_TYPE AS type, "
                "IF(IS_NULLABLE='NO', 1, 0) AS notnull, COLUMN_DEFAULT AS dflt_value, "
                "IF(COLUMN_KEY='PRI', 1, 0) AS pk "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION"
            ).replace("%s", f"'{table}'", 1)

        upper = text.upper()
        if upper.startswith("PRAGMA"):
            return None

        text = self._rewrite_sqlite_types(text)
        text = self._rewrite_datetime_functions(text)
        text = self._rewrite_insert_conflicts(text)
        text = self._rewrite_table_names(text)
        text = self._quote_reserved_key_column(text)
        text = text.replace("?", "%s")
        return text

    def _rewrite_sqlite_types(self, text: str) -> str:
        text = re.sub(r"\s+CHECK\s*\(\s*role\s+IN\s+\('[^']+'\s*,\s*'[^']+'\)\s*\)", "", text, flags=re.I)
        text = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "INT AUTO_INCREMENT PRIMARY KEY", text, flags=re.I)
        text = re.sub(r"\bTEXT\s+PRIMARY\s+KEY\b", "VARCHAR(255) PRIMARY KEY", text, flags=re.I)
        text = re.sub(r"\bTEXT\s+NOT\s+NULL\s+UNIQUE\b", "VARCHAR(255) NOT NULL UNIQUE", text, flags=re.I)
        text = re.sub(r"\bTEXT\s+UNIQUE\b", "VARCHAR(255) UNIQUE", text, flags=re.I)
        text = self._rewrite_text_columns(text)
        text = re.sub(r"\bBOOLEAN\b", "TINYINT(1)", text, flags=re.I)
        text = re.sub(r"\bTRUE\b", "1", text, flags=re.I)
        text = re.sub(r"\bFALSE\b", "0", text, flags=re.I)
        text = re.sub(r"\bTIMESTAMP\s+DEFAULT\s+CURRENT_TIMESTAMP\b", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP", text, flags=re.I)
        return text

    def _rewrite_text_columns(self, text: str) -> str:
        """Map SQLite TEXT columns to practical MySQL string types."""
        datetime_columns = {
            "created_at", "updated_at", "triggered_at", "sent_at", "last_checked",
            "last_login_at", "expires_at", "analysis_time", "timestamp", "position_date",
        }
        long_text_columns = {
            "stock_info", "agents_results", "discussion_result", "final_decision",
            "entry_range", "quant_config", "message", "payload_json", "analysis_content",
            "recommended_sectors", "summary", "content", "related_sectors", "error_message",
            "raw_data", "analysis_result", "analysis", "details", "market_data",
            "account_info", "key_price_levels", "reasoning", "note", "notes",
            "platforms_data", "stock_news", "hot_topics", "flow_data", "model_data",
            "sentiment_data", "ai_analysis", "trading_signals", "report_content",
            "input_data", "output_data", "result_json", "metadata_json", "config_value",
            "description", "theme_tokens", "themes_json", "members_json", "evidence_json",
            "pattern_json", "candidate_json", "clean_content", "input_post_ids", "review_comment",
        }

        def repl(match: re.Match) -> str:
            column = match.group(1)
            constraints = match.group(2) or ""
            column_key = column.lower()
            if column_key in datetime_columns or column_key.endswith("_at"):
                sql_type = "DATETIME"
            elif column_key in long_text_columns:
                sql_type = "LONGTEXT"
            else:
                sql_type = "VARCHAR(255)"
            return f"{column} {sql_type}{constraints}"

        return re.sub(
            r"\b([A-Za-z_][\w]*)\s+TEXT\b([^,\n)]*)",
            repl,
            text,
            flags=re.I,
        )

    def _rewrite_datetime_functions(self, text: str) -> str:
        text = re.sub(
            r"datetime\(\s*'now'\s*,\s*'-'\s*\|\|\s*\?\s*\|\|\s*'\s*minutes'\s*\)",
            "DATE_SUB(NOW(), INTERVAL ? MINUTE)",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"datetime\(\s*'now'\s*,\s*'-'\s*\|\|\s*\?\s*\|\|\s*'\s*days'\s*\)",
            "DATE_SUB(NOW(), INTERVAL ? DAY)",
            text,
            flags=re.I,
        )
        text = re.sub(r"datetime\(\s*([A-Za-z_][\w]*)\s*\)", r"\1", text, flags=re.I)
        return text

    def _rewrite_insert_conflicts(self, text: str) -> str:
        text = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", text, flags=re.I)
        text = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", text, flags=re.I)
        text = re.sub(
            r"\s+ON\s+CONFLICT\s*\([^)]+\)\s+DO\s+UPDATE\s+SET\s+",
            " ON DUPLICATE KEY UPDATE ",
            text,
            flags=re.I | re.S,
        )
        text = re.sub(r"\bexcluded\.([A-Za-z_][\w]*)\b", r"VALUES(\1)", text, flags=re.I)
        return text

    def _rewrite_table_names(self, text: str) -> str:
        table_names = set()
        for finder in [
            r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`?([A-Za-z_][\w]*)`?",
            r"\bALTER\s+TABLE\s+`?([A-Za-z_][\w]*)`?",
            r"\bINSERT(?:\s+IGNORE)?\s+INTO\s+`?([A-Za-z_][\w]*)`?",
            r"\bREPLACE\s+INTO\s+`?([A-Za-z_][\w]*)`?",
            r"(?<!KEY\s)\bUPDATE\s+`?([A-Za-z_][\w]*)`?",
            r"\bFROM\s+`?([A-Za-z_][\w]*)`?",
            r"\bJOIN\s+`?([A-Za-z_][\w]*)`?",
            r"\bDELETE\s+FROM\s+`?([A-Za-z_][\w]*)`?",
            r"\bREFERENCES\s+`?([A-Za-z_][\w]*)`?",
        ]:
            table_names.update(re.findall(finder, text, flags=re.I))

        patterns = [
            r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bALTER\s+TABLE\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bINSERT(?:\s+IGNORE)?\s+INTO\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bREPLACE\s+INTO\s+(`?)([A-Za-z_][\w]*)\1",
            r"(?<!KEY\s)\bUPDATE\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bFROM\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bJOIN\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bDELETE\s+FROM\s+(`?)([A-Za-z_][\w]*)\1",
            r"\bREFERENCES\s+(`?)([A-Za-z_][\w]*)\1",
        ]
        for pattern in patterns:
            text = re.sub(pattern, self._table_repl, text, flags=re.I)

        text = re.sub(
            r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(`?)([A-Za-z_][\w]*)\2\s+ON\s+(`?)([A-Za-z_][\w]*)\4",
            self._index_repl,
            text,
            flags=re.I,
        )
        for table in sorted(table_names, key=len, reverse=True):
            text = re.sub(
                rf"(?<![`.\w]){re.escape(table)}\.",
                f"{_prefixed_table(self._namespace, table)}.",
                text,
            )
        return text

    def _table_repl(self, match: re.Match) -> str:
        full = match.group(0)
        table = match.group(match.lastindex)
        quote = ""
        if match.lastindex and match.lastindex >= 2:
            try:
                quote = match.group(match.lastindex - 1) or ""
            except Exception:
                quote = ""

        token = f"{quote}{table}{quote}" if quote else table
        idx = full.rfind(token)
        if idx < 0:
            idx = full.rfind(table)
        if idx < 0:
            return full

        prefix = full[:idx]
        return f"{prefix}{_prefixed_table(self._namespace, table)}"

    def _index_repl(self, match: re.Match) -> str:
        unique = match.group(1) or ""
        index = f"{self._namespace}__{match.group(3)}"
        table = _prefixed_table(self._namespace, match.group(5))
        return f"CREATE {unique}INDEX `{index}` ON {table}"

    def _quote_reserved_key_column(self, text: str) -> str:
        # kline_sync_meta has a column named "key", which is reserved in MySQL.
        text = re.sub(r"([(\n,]\s*)key\s+(VARCHAR\(\d+\)|LONGTEXT|DATETIME|INT|DOUBLE|LONGBLOB)", r"\1`key` \2", text, flags=re.I)
        text = re.sub(r"\bkey\s+TEXT\b", "`key` VARCHAR(255)", text, flags=re.I)
        text = re.sub(r"\bkey\s*=", "`key`=", text, flags=re.I)
        text = re.sub(r"\(\s*key\s*,", "(`key`,", text, flags=re.I)
        text = re.sub(r"\bWHERE\s+key\s*=", "WHERE `key` =", text, flags=re.I)
        text = re.sub(r"\bSELECT\s+value\s+FROM\s+(`[^`]+`)\s+WHERE\s+key\s*=", r"SELECT value FROM \1 WHERE `key` =", text, flags=re.I)
        text = re.sub(r"\bkey\s*=", "`key` =", text, flags=re.I)
        text = re.sub(r"\bkey\s+ASC\b", "`key` ASC", text, flags=re.I)
        return text

    def _raise_compatible(self, exc: Exception):
        if pymysql and isinstance(exc, pymysql.err.IntegrityError):
            raise IntegrityError(str(exc)) from exc
        if pymysql and isinstance(exc, pymysql.err.OperationalError):
            raise OperationalError(str(exc)) from exc
        raise exc

    def _is_ignorable_mysql_error(self, exc: Exception, sql: str) -> bool:
        if not pymysql:
            return False
        code = exc.args[0] if getattr(exc, "args", None) else None
        upper_sql = str(sql or "").lstrip().upper()
        if upper_sql.startswith("CREATE ") and " INDEX " in upper_sql and code == 1061:
            return True
        if upper_sql.startswith("ALTER TABLE") and "ADD COLUMN" in upper_sql and code == 1060:
            return True
        return False

    def _log_if_slow(self, sql: str, started_at: float, many: bool = False, failed: bool = False) -> None:
        try:
            threshold_ms = float(os.getenv("MYSQL_SLOW_QUERY_LOG_MS", "500") or "500")
        except ValueError:
            threshold_ms = 500.0
        elapsed_ms = (time.time() - started_at) * 1000
        if elapsed_ms < threshold_ms and not failed:
            return
        compact_sql = re.sub(r"\s+", " ", str(sql or "")).strip()
        if len(compact_sql) > 500:
            compact_sql = compact_sql[:500] + "..."
        status = "failed" if failed else "slow"
        mode = "executemany" if many else "execute"
        print(f"[MySQL {status}] {mode} elapsed={elapsed_ms:.1f}ms ns={self._namespace} sql={compact_sql}", flush=True)


class Connection:
    def __init__(self, db_path: str):
        if pymysql is None:
            raise OperationalError("缺少 PyMySQL 依赖，请先安装 requirements.txt")
        self.db_path = db_path
        self.namespace = _namespace_from_path(db_path)
        self.row_factory = None
        self._conn = self._get_pooled_connection()

    def _get_pooled_connection(self):
        key = (threading.get_ident(), self.namespace)
        conn = _POOLED_CONNECTIONS.get(key)
        if conn is not None:
            try:
                conn.ping(reconnect=True)
                return conn
            except Exception:
                _POOLED_CONNECTIONS.pop(key, None)
        try:
            conn = pymysql.connect(**_mysql_config())
        except Exception as exc:
            if self._try_create_database(exc):
                conn = pymysql.connect(**_mysql_config())
            else:
                raise OperationalError(
                    "连接 MySQL 失败，请检查 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/"
                    f"MYSQL_PASSWORD/MYSQL_DATABASE 配置 | 原始错误: {type(exc).__name__}: {exc}"
                ) from exc
        _POOLED_CONNECTIONS[key] = conn
        return conn

    def cursor(self) -> Cursor:
        return Cursor(self._conn.cursor(), self.namespace, self)

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None) -> Cursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Cursor:
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        # Keep MySQL TCP connections warm.  The old sqlite3 API closed file
        # handles aggressively; doing that with MySQL makes Streamlit reruns
        # noticeably slower.
        return None

    def _try_create_database(self, original_exc: Exception) -> bool:
        if os.getenv("MYSQL_AUTO_CREATE_DATABASE", "true").lower() != "true":
            return False
        cfg = _mysql_config()
        db_name = str(cfg.get("database") or "").strip()
        charset = str(cfg.get("charset") or "utf8mb4").strip() or "utf8mb4"
        if not db_name or not re.match(r"^[A-Za-z0-9_]+$", db_name):
            return False
        if pymysql and isinstance(original_exc, pymysql.err.OperationalError):
            code = original_exc.args[0] if original_exc.args else None
            if code not in {1049, 1044}:
                return False
        try:
            server_conn = pymysql.connect(**_server_config())
            with server_conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET {charset} COLLATE {charset}_unicode_ci")
            server_conn.commit()
            server_conn.close()
            return True
        except Exception:
            return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def connect(db_path: str, *args, **kwargs) -> Connection:
    return Connection(db_path)
