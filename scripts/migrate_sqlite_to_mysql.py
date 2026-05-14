#!/usr/bin/env python3
"""
Import existing SQLite databases into the MySQL layout used by db_adapter.py.

Each SQLite database file maps to a MySQL table namespace based on the old file
name.  Example: stock_monitor.db table notifications -> stock_monitor__notifications.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import pymysql

from db_adapter import _mysql_config, _namespace_from_path


DEFAULT_DB_FILES = [
    "stock_analysis.db",
    "longhubang_history.db",
    "news_flow.db",
    "sector_strategy.db",
    "low_price_bull_monitor.db",
    "main_force_batch.db",
    "stock_monitor.db",
    "portfolio_stocks.db",
    "longhubang.db",
    "smart_monitor.db",
    "profit_growth_monitor.db",
    "data/kline_cache.db",
    "data/monitor.db",
    "data/auth.db",
    "data/longhubang.db",
    "data/kline_cache_2026.db",
]

INDEX_TEXT_COLUMNS = {
    "code", "symbol", "stock_code", "username", "token", "config_key",
    "data_date", "trade_date", "date", "event_time", "dedupe_key", "channel",
    "status", "sector_code", "data_type", "news_date", "analysis_date",
    "group_id", "theme", "theme_norm", "theme_key", "key",
    "youzi_name", "name", "stock_name", "sector_name", "title", "source",
}

DATETIME_COLUMNS = {
    "created_at", "updated_at", "triggered_at", "sent_at", "last_checked",
    "last_login_at", "expires_at", "analysis_time", "timestamp", "position_date",
}


def quote_ident(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def mysql_type(column: dict[str, Any], pk: bool, indexed_columns: set[str] | None = None) -> str:
    name = str(column["name"]).lower()
    indexed_columns = indexed_columns or set()
    raw_type = str(column["type"] or "").upper()

    if pk and "INT" in raw_type:
        return "INT"
    if "INT" in raw_type:
        return "INT"
    if any(t in raw_type for t in ["REAL", "FLOA", "DOUB", "NUM"]):
        return "DOUBLE"
    if "BLOB" in raw_type:
        return "LONGBLOB"
    if name in DATETIME_COLUMNS or name.endswith("_at"):
        return "DATETIME"
    if name in INDEX_TEXT_COLUMNS or name in indexed_columns or pk:
        return "VARCHAR(255)"
    return "LONGTEXT"


def load_sqlite_schema(conn: sqlite3.Connection, table: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    conn.row_factory = sqlite3.Row
    columns = [dict(r) for r in conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()]
    indexes = [dict(r) for r in conn.execute(f"PRAGMA index_list({quote_ident(table)})").fetchall()]
    foreign_keys = [dict(r) for r in conn.execute(f"PRAGMA foreign_key_list({quote_ident(table)})").fetchall()]
    return columns, indexes, foreign_keys


def build_create_table(
    namespace: str,
    table: str,
    columns: list[dict[str, Any]],
    foreign_keys: list[dict[str, Any]],
    indexed_columns: set[str],
) -> str:
    mysql_table = f"{namespace}__{table}"
    pk_columns = [c for c in columns if int(c.get("pk") or 0) > 0]
    single_int_pk = len(pk_columns) == 1 and "INT" in str(pk_columns[0].get("type") or "").upper()

    defs = []
    for col in columns:
        name = col["name"]
        pk = int(col.get("pk") or 0) > 0
        col_type = mysql_type(col, pk, indexed_columns)
        part = f"{quote_ident(name)} {col_type}"
        if single_int_pk and pk:
            part += " AUTO_INCREMENT PRIMARY KEY"
        elif int(col.get("notnull") or 0) and not pk:
            part += " NOT NULL"
        default = col.get("dflt_value")
        if default is not None and str(default).upper() != "NULL":
            default_text = str(default)
            if default_text.upper() == "CURRENT_TIMESTAMP":
                part += " DEFAULT CURRENT_TIMESTAMP"
            elif col_type not in {"LONGTEXT", "LONGBLOB"}:
                part += f" DEFAULT {default_text}"
        defs.append(part)

    if pk_columns and not single_int_pk:
        pk_cols = ", ".join(quote_ident(c["name"]) for c in sorted(pk_columns, key=lambda x: int(x.get("pk") or 0)))
        defs.append(f"PRIMARY KEY ({pk_cols})")

    body = ",\n  ".join(defs)
    return f"CREATE TABLE IF NOT EXISTS {quote_ident(mysql_table)} (\n  {body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"


def build_index_sql(namespace: str, table: str, sqlite_conn: sqlite3.Connection, indexes: list[dict[str, Any]]) -> list[str]:
    statements = []
    mysql_table = f"{namespace}__{table}"
    for idx in indexes:
        idx_name = str(idx.get("name") or "")
        if idx_name.startswith("sqlite_autoindex") and int(idx.get("unique") or 0) == 0:
            continue
        if str(idx.get("origin") or "") == "pk":
            continue
        idx_info = sqlite_conn.execute(f"PRAGMA index_info({quote_ident(idx_name)})").fetchall()
        cols = [str(r[2]) for r in idx_info if r[2]]
        if not cols:
            continue
        if idx_name.startswith("sqlite_autoindex"):
            idx_name = f"uniq_{table}_{'_'.join(cols)}"
        mysql_index = f"{namespace}__{idx_name}"[:60]
        unique = "UNIQUE " if int(idx.get("unique") or 0) else ""
        col_sql = ", ".join(quote_ident(c) for c in cols)
        statements.append(
            f"CREATE {unique}INDEX {quote_ident(mysql_index)} ON {quote_ident(mysql_table)} ({col_sql})"
        )
    return statements


def get_indexed_columns(sqlite_conn: sqlite3.Connection, table: str, indexes: list[dict[str, Any]]) -> set[str]:
    cols = set()
    for idx in indexes:
        idx_name = str(idx.get("name") or "")
        idx_info = sqlite_conn.execute(f"PRAGMA index_info({quote_ident(idx_name)})").fetchall()
        for row in idx_info:
            if row[2]:
                cols.add(str(row[2]).lower())
    return cols


def create_database_if_needed() -> None:
    cfg = _mysql_config()
    db_name = str(cfg["database"])
    server_cfg = dict(cfg)
    server_cfg.pop("database", None)
    conn = pymysql.connect(**server_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS {quote_ident(db_name)} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def insert_rows(mysql_conn, mysql_table: str, columns: list[str], rows: list[sqlite3.Row], preserve_ids: bool) -> int:
    if not rows:
        return 0
    effective_columns = list(columns)
    if not preserve_ids and "id" in effective_columns:
        effective_columns.remove("id")
    col_sql = ", ".join(quote_ident(c) for c in effective_columns)
    placeholders = ", ".join(["%s"] * len(columns))
    placeholders = ", ".join(["%s"] * len(effective_columns))
    sql = f"INSERT IGNORE INTO {quote_ident(mysql_table)} ({col_sql}) VALUES ({placeholders})"
    values = [tuple(row[c] for c in effective_columns) for row in rows]
    with mysql_conn.cursor() as cur:
        cur.executemany(sql, values)
    return len(rows)


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    mysql_conn,
    namespace: str,
    table: str,
    truncate: bool,
    batch_size: int,
    preserve_ids: bool,
) -> int:
    columns, indexes, foreign_keys = load_sqlite_schema(sqlite_conn, table)
    if not columns:
        return 0

    mysql_table = f"{namespace}__{table}"
    with mysql_conn.cursor() as cur:
        if truncate:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            cur.execute(f"DROP TABLE IF EXISTS {quote_ident(mysql_table)}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        indexed_columns = get_indexed_columns(sqlite_conn, table, indexes)
        cur.execute(build_create_table(namespace, table, columns, foreign_keys, indexed_columns))
        if truncate:
            cur.execute(f"SET FOREIGN_KEY_CHECKS=0")
            cur.execute(f"TRUNCATE TABLE {quote_ident(mysql_table)}")
            cur.execute(f"SET FOREIGN_KEY_CHECKS=1")

    for stmt in build_index_sql(namespace, table, sqlite_conn, indexes):
        try:
            with mysql_conn.cursor() as cur:
                cur.execute(stmt)
        except pymysql.err.InternalError as exc:
            if exc.args and exc.args[0] in {1061, 1071}:
                continue
            raise
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] in {1061, 1071}:
                continue
            raise

    col_names = [c["name"] for c in columns]
    sqlite_conn.row_factory = sqlite3.Row
    source_cur = sqlite_conn.execute(f"SELECT * FROM {quote_ident(table)}")
    total = 0
    while True:
        rows = source_cur.fetchmany(batch_size)
        if not rows:
            break
        total += insert_rows(mysql_conn, mysql_table, col_names, rows, preserve_ids=preserve_ids)
        mysql_conn.commit()
    return total


def migrate_file(path: Path, mysql_conn, truncate: bool, batch_size: int, preserve_ids: bool) -> tuple[str, int, int]:
    namespace = _namespace_from_path(str(path))
    sqlite_conn = sqlite3.connect(path)
    try:
        tables = [
            r[0]
            for r in sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        table_count = 0
        row_count = 0
        for table in tables:
            count = migrate_table(sqlite_conn, mysql_conn, namespace, table, truncate, batch_size, preserve_ids)
            table_count += 1
            row_count += count
            print(f"  {namespace}__{table}: {count} rows")
        return namespace, table_count, row_count
    except sqlite3.DatabaseError as exc:
        print(f"Skipped {path}: SQLite database error: {exc}")
        return namespace, 0, 0
    finally:
        sqlite_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate project SQLite DB files to MySQL")
    parser.add_argument("--root", default=".", help="project root")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--append", action="store_true", help="append without truncating target tables")
    parser.add_argument("files", nargs="*", help="specific SQLite files to migrate")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    files = args.files or DEFAULT_DB_FILES
    paths = [root / f for f in files]
    paths = [p for p in paths if p.exists() and p.is_file() and p.stat().st_size > 0]

    create_database_if_needed()
    mysql_conn = pymysql.connect(**_mysql_config())
    try:
        grand_tables = 0
        grand_rows = 0
        seen_namespaces = set()
        for path in paths:
            rel = path.relative_to(root)
            namespace = _namespace_from_path(str(path))
            truncate_this_file = (not args.append) and namespace not in seen_namespaces
            preserve_ids = namespace not in seen_namespaces
            print(f"Migrating {rel} ...")
            namespace, tables, rows = migrate_file(
                path,
                mysql_conn,
                truncate=truncate_this_file,
                batch_size=args.batch_size,
                preserve_ids=preserve_ids,
            )
            seen_namespaces.add(namespace)
            grand_tables += tables
            grand_rows += rows
            print(f"Done {rel} -> namespace={namespace}, tables={tables}, rows={rows}")
        print(f"Migration complete: files={len(paths)}, tables={grand_tables}, rows={grand_rows}")
    finally:
        mysql_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
