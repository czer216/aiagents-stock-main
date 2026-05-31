#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import db_adapter as sqlite3


class ThemePeerHistoryDB:
    def __init__(self, db_path: str = "theme_peer_history.db"):
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bottom_volume_fetch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                end_date TEXT,
                top_n INTEGER,
                max_candidates INTEGER,
                breakout_min_vol_multiple REAL,
                breakout_min_body_pct REAL,
                pullback_max_retrace REAL,
                pullback_max_vol_ratio REAL,
                best_vol_ratio REAL,
                hotspot_weight REAL,
                bottom_lookback_days INTEGER,
                success INTEGER,
                error TEXT,
                latest_trade_date TEXT,
                candidate_universe INTEGER,
                scanned INTEGER,
                valid INTEGER,
                result_count INTEGER,
                params_json TEXT,
                kline_cache_sync_json TEXT,
                results_json TEXT
            )
            """
        )
        self._migrate_large_text_columns(cursor)

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bottom_volume_fetch_created_at
            ON bottom_volume_fetch_history(created_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bottom_volume_fetch_latest_trade_date
            ON bottom_volume_fetch_history(latest_trade_date)
            """
        )

        conn.commit()
        conn.close()

    @staticmethod
    def _migrate_large_text_columns(cursor):
        columns = ["params_json", "kline_cache_sync_json", "results_json", "error"]
        for col in columns:
            try:
                cursor.execute(
                    f"ALTER TABLE bottom_volume_fetch_history MODIFY COLUMN {col} LONGTEXT"
                )
            except Exception:
                pass

    def save_bottom_volume_fetch(self, request_id: str, request_params: Dict[str, Any], result: Dict[str, Any]) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        candidates = list((result or {}).get("candidates") or [])
        payload = {
            "params_json": json.dumps(request_params or {}, ensure_ascii=False, default=str),
            "kline_cache_sync_json": json.dumps((result or {}).get("kline_cache_sync") or {}, ensure_ascii=False, default=str),
            "results_json": json.dumps(candidates, ensure_ascii=False, default=str),
        }

        cursor.execute(
            """
            INSERT OR REPLACE INTO bottom_volume_fetch_history (
                request_id, created_at,
                end_date, top_n, max_candidates,
                breakout_min_vol_multiple, breakout_min_body_pct,
                pullback_max_retrace, pullback_max_vol_ratio,
                best_vol_ratio, hotspot_weight, bottom_lookback_days,
                success, error, latest_trade_date,
                candidate_universe, scanned, valid, result_count,
                params_json, kline_cache_sync_json, results_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(request_id or ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(request_params.get("end_date") or ""),
                int(request_params.get("top_n") or 0),
                int(request_params.get("max_candidates") or 0),
                float(request_params.get("breakout_min_vol_multiple") or 0),
                float(request_params.get("breakout_min_body_pct") or 0),
                float(request_params.get("pullback_max_retrace") or 0),
                float(request_params.get("pullback_max_vol_ratio") or 0),
                float(request_params.get("best_vol_ratio") or 0),
                float(request_params.get("hotspot_weight") or 0),
                int(request_params.get("bottom_lookback_days") or 0),
                1 if bool((result or {}).get("success")) else 0,
                str((result or {}).get("error") or ""),
                str((result or {}).get("latest_trade_date") or ""),
                int((result or {}).get("candidate_universe") or 0),
                int((result or {}).get("scanned") or 0),
                int((result or {}).get("valid") or 0),
                int(len(candidates)),
                payload["params_json"],
                payload["kline_cache_sync_json"],
                payload["results_json"],
            ),
        )

        record_id = int(cursor.lastrowid or 0)
        conn.commit()
        conn.close()
        return record_id

    def get_bottom_volume_fetch_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, request_id, created_at, end_date, top_n, max_candidates,
                   success, error, latest_trade_date, candidate_universe, scanned, valid, result_count,
                   params_json, kline_cache_sync_json, results_json
            FROM bottom_volume_fetch_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit or 1)),),
        )
        rows = cursor.fetchall()
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "request_id": row[1],
                    "created_at": row[2],
                    "end_date": row[3],
                    "top_n": row[4],
                    "max_candidates": row[5],
                    "success": bool(row[6]),
                    "error": row[7] or "",
                    "latest_trade_date": row[8] or "",
                    "candidate_universe": int(row[9] or 0),
                    "scanned": int(row[10] or 0),
                    "valid": int(row[11] or 0),
                    "result_count": int(row[12] or 0),
                    "params": self._loads_json(row[13], {}),
                    "kline_cache_sync": self._loads_json(row[14], {}),
                    "results": self._loads_json(row[15], []),
                }
            )
        return out

    def get_bottom_volume_fetch_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, request_id, created_at, end_date, top_n, max_candidates,
                   success, error, latest_trade_date, candidate_universe, scanned, valid, result_count,
                   params_json, kline_cache_sync_json, results_json
            FROM bottom_volume_fetch_history
            WHERE id = ?
            """,
            (int(record_id),),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "id": row[0],
            "request_id": row[1],
            "created_at": row[2],
            "end_date": row[3],
            "top_n": row[4],
            "max_candidates": row[5],
            "success": bool(row[6]),
            "error": row[7] or "",
            "latest_trade_date": row[8] or "",
            "candidate_universe": int(row[9] or 0),
            "scanned": int(row[10] or 0),
            "valid": int(row[11] or 0),
            "result_count": int(row[12] or 0),
            "params": self._loads_json(row[13], {}),
            "kline_cache_sync": self._loads_json(row[14], {}),
            "results": self._loads_json(row[15], []),
        }

    def delete_bottom_volume_fetch(self, record_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bottom_volume_fetch_history WHERE id = ?", (int(record_id),))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return bool(affected and affected > 0)

    @staticmethod
    def _loads_json(text: Any, fallback: Any):
        try:
            if text is None or text == "":
                return fallback
            return json.loads(text)
        except Exception:
            return fallback


theme_peer_history_db = ThemePeerHistoryDB()
