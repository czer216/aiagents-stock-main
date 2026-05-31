import db_adapter as sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from modules.longhubang.longhubang_db import LonghubangDatabase


class DoubanAuthorDatabase:
    def __init__(self, db_path: str = "douban_author_strategy.db"):
        self.db_path = db_path
        self.init_database()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                post_url TEXT NOT NULL,
                post_title TEXT,
                post_published_at TEXT,
                clean_content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fetch_date TEXT,
                ingest_status TEXT DEFAULT 'new',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(author_id, post_url)
            )
            '''
        )
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_douban_posts_author_time ON douban_posts(author_id, post_published_at)')

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_post_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                post_id INTEGER,
                post_url TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                comment_time TEXT,
                comment_user TEXT,
                like_count INTEGER DEFAULT 0,
                page_idx INTEGER DEFAULT 1,
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_url, comment_id)
            )
            '''
        )
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_douban_post_comments_post ON douban_post_comments(post_url, fetched_at DESC)')

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_post_comment_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                post_id INTEGER,
                post_url TEXT NOT NULL,
                comment_count INTEGER DEFAULT 0,
                sentiment_score REAL DEFAULT 50,
                sentiment_label TEXT,
                operation_bias_json TEXT,
                summary_json TEXT,
                analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_url)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_pattern_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                lookback_days INTEGER NOT NULL,
                input_post_ids TEXT,
                pattern_json TEXT NOT NULL,
                model_used TEXT,
                analysis_time REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_stock_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                post_id INTEGER,
                candidate_json TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                review_comment TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (report_id) REFERENCES douban_pattern_reports(id)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_scheduler_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                retry_count INTEGER DEFAULT 0,
                duration REAL,
                related_post_id INTEGER,
                executed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_author_learning_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                report_id INTEGER,
                memory_type TEXT NOT NULL,
                memory_json TEXT NOT NULL,
                quality_score REAL DEFAULT 0,
                as_of_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_author_candidate_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                author_id TEXT NOT NULL,
                horizon TEXT NOT NULL,
                ret_pct REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                hit_trigger INTEGER DEFAULT 0,
                outcome_label TEXT DEFAULT 'neutral',
                computed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_authors (
                author_id TEXT PRIMARY KEY,
                author_name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS douban_author_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT NOT NULL,
                pattern_version INTEGER NOT NULL,
                pattern_json TEXT NOT NULL,
                summary TEXT,
                source_post_ids TEXT,
                as_of_date TEXT,
                confidence REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(author_id, pattern_version)
            )
            '''
        )
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_douban_author_patterns_author_ver ON douban_author_patterns(author_id, pattern_version DESC)')

        self._migrate_compatible_columns(cursor)
        self._migrate_longtext_columns(cursor)
        conn.commit()
        conn.close()

    def _migrate_compatible_columns(self, cursor) -> None:
        alters = [
            "ALTER TABLE douban_stock_candidates ADD COLUMN pattern_version INTEGER DEFAULT 0",
            "ALTER TABLE douban_stock_candidates MODIFY report_id INT NULL",
            "ALTER TABLE douban_stock_candidates ADD COLUMN batch_type TEXT DEFAULT 'offline'",
            "ALTER TABLE douban_stock_candidates ADD COLUMN batch_id TEXT",
        ]
        for sql in alters:
            try:
                cursor.execute(sql)
            except Exception:
                pass

    def _migrate_longtext_columns(self, cursor) -> None:
        alters = [
            "ALTER TABLE douban_posts MODIFY clean_content LONGTEXT",
            "ALTER TABLE douban_pattern_reports MODIFY input_post_ids LONGTEXT",
            "ALTER TABLE douban_pattern_reports MODIFY pattern_json LONGTEXT",
            "ALTER TABLE douban_stock_candidates MODIFY candidate_json LONGTEXT",
            "ALTER TABLE douban_stock_candidates MODIFY review_comment LONGTEXT",
            "ALTER TABLE douban_scheduler_logs MODIFY message LONGTEXT",
            "ALTER TABLE douban_author_learning_memory MODIFY memory_json LONGTEXT",
            "ALTER TABLE douban_author_patterns MODIFY pattern_json LONGTEXT",
            "ALTER TABLE douban_author_patterns MODIFY summary LONGTEXT",
            "ALTER TABLE douban_author_patterns MODIFY source_post_ids LONGTEXT",
            "ALTER TABLE douban_post_comments MODIFY comment_text LONGTEXT",
            "ALTER TABLE douban_post_comment_summaries MODIFY operation_bias_json LONGTEXT",
            "ALTER TABLE douban_post_comment_summaries MODIFY summary_json LONGTEXT",
        ]
        for sql in alters:
            try:
                cursor.execute(sql)
            except Exception:
                pass

    def upsert_post(self, author_id: str, post: Dict) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR IGNORE INTO douban_posts
            (author_id, post_url, post_title, post_published_at, clean_content, content_hash, fetch_date, ingest_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
            ''',
            (
                author_id,
                post.get('post_url', ''),
                post.get('post_title', ''),
                post.get('post_published_at', ''),
                post.get('clean_content', ''),
                post.get('content_hash', ''),
                datetime.now().strftime('%Y-%m-%d'),
            ),
        )
        conn.commit()
        row_id = int(cursor.lastrowid or 0)
        conn.close()
        return row_id if row_id > 0 else None

    def list_new_posts_for_author(self, author_id: str, limit: int = 30) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_posts
            WHERE author_id = ? AND ingest_status = 'new'
            ORDER BY id DESC
            LIMIT ?
            ''',
            (author_id, int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def upsert_post_comment(self, author_id: str, post_id: Optional[int], post_url: str, comment: Dict) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        comment_id = str(comment.get('comment_id') or '').strip()
        if not comment_id:
            raw = f"{post_url}|{comment.get('comment_time') or ''}|{comment.get('comment_text') or ''}"
            comment_id = str(abs(hash(raw)))
        cursor.execute(
            '''
            INSERT OR REPLACE INTO douban_post_comments
            (author_id, post_id, post_url, comment_id, comment_text, comment_time, comment_user, like_count, page_idx, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                str(author_id or ''),
                int(post_id) if post_id else None,
                str(post_url or ''),
                comment_id,
                str(comment.get('comment_text') or ''),
                str(comment.get('comment_time') or ''),
                str(comment.get('comment_user') or ''),
                int(comment.get('like_count') or 0),
                int(comment.get('page_idx') or 1),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        rid = int(cursor.lastrowid or 0)
        conn.close()
        return rid

    def replace_post_comment_summary(
        self,
        author_id: str,
        post_id: Optional[int],
        post_url: str,
        comment_count: int,
        sentiment_score: float,
        sentiment_label: str,
        operation_bias: Dict,
        summary_payload: Dict,
    ) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR REPLACE INTO douban_post_comment_summaries
            (author_id, post_id, post_url, comment_count, sentiment_score, sentiment_label, operation_bias_json, summary_json, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                str(author_id or ''),
                int(post_id) if post_id else None,
                str(post_url or ''),
                int(comment_count or 0),
                float(sentiment_score or 50),
                str(sentiment_label or ''),
                json.dumps(operation_bias or {}, ensure_ascii=False),
                json.dumps(summary_payload or {}, ensure_ascii=False),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        rid = int(cursor.lastrowid or 0)
        conn.close()
        return rid

    def list_post_comments(self, post_url: str, limit: int = 500) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_post_comments
            WHERE post_url = ?
            ORDER BY id DESC
            LIMIT ?
            ''',
            (str(post_url or ''), int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def list_comment_summaries(self, author_id: str, limit: int = 50) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_post_comment_summaries
            WHERE author_id = ?
            ORDER BY analyzed_at DESC, id DESC
            LIMIT ?
            ''',
            (str(author_id or ''), int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def mark_posts_processed(self, post_ids: List[int]) -> None:
        ids = [int(x) for x in (post_ids or []) if x]
        if not ids:
            return
        conn = self.get_connection()
        cursor = conn.cursor()
        placeholders = ','.join(['?'] * len(ids))
        cursor.execute(f"UPDATE douban_posts SET ingest_status = ? WHERE id IN ({placeholders})", ['processed'] + ids)
        conn.commit()
        conn.close()

    def get_posts_for_lookback(self, author_id: str, limit: int = 30) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_posts
            WHERE author_id = ?
            ORDER BY id DESC
            LIMIT ?
            ''',
            (author_id, int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def save_pattern_report(self, author_id: str, lookback_days: int, input_post_ids: List[int], pattern_json: Dict, model_used: str, analysis_time: float) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_pattern_reports
            (author_id, as_of_date, lookback_days, input_post_ids, pattern_json, model_used, analysis_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                author_id,
                datetime.now().strftime('%Y-%m-%d'),
                int(lookback_days),
                json.dumps(input_post_ids, ensure_ascii=False),
                json.dumps(pattern_json, ensure_ascii=False),
                model_used,
                float(analysis_time or 0),
            ),
        )
        conn.commit()
        rid = int(cursor.lastrowid or 0)
        conn.close()
        return rid

    def save_candidate(
        self,
        author_id: str,
        report_id: Optional[int],
        post_id: Optional[int],
        candidate_json: Dict,
        status: str = 'draft',
        pattern_version: int = 0,
        batch_type: str = 'offline',
        batch_id: str = '',
    ) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_stock_candidates
            (author_id, report_id, post_id, candidate_json, status, pattern_version, batch_type, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                author_id,
                int(report_id) if report_id and int(report_id) > 0 else None,
                int(post_id) if post_id else None,
                json.dumps(candidate_json, ensure_ascii=False),
                status,
                int(pattern_version or 0),
                str(batch_type or 'offline'),
                str(batch_id or ''),
            ),
        )
        conn.commit()
        cid = int(cursor.lastrowid or 0)
        conn.close()
        return cid

    def list_candidates(self, status: Optional[str] = None, limit: int = 50, author_id: Optional[str] = None) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if status and author_id:
            cursor.execute(
                'SELECT * FROM douban_stock_candidates WHERE status = ? AND author_id = ? ORDER BY id DESC LIMIT ?',
                (status, author_id, limit),
            )
        elif status:
            cursor.execute('SELECT * FROM douban_stock_candidates WHERE status = ? ORDER BY id DESC LIMIT ?', (status, limit))
        elif author_id:
            cursor.execute('SELECT * FROM douban_stock_candidates WHERE author_id = ? ORDER BY id DESC LIMIT ?', (author_id, limit))
        else:
            cursor.execute('SELECT * FROM douban_stock_candidates ORDER BY id DESC LIMIT ?', (limit,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def update_candidate_status(self, candidate_id: int, status: str, reviewed_by: str = '', review_comment: str = '') -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE douban_stock_candidates
            SET status = ?, reviewed_by = ?, review_comment = ?, reviewed_at = ?
            WHERE id = ?
            ''',
            (status, reviewed_by, review_comment, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), int(candidate_id)),
        )
        conn.commit()
        conn.close()

    def mark_post_processed(self, post_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE douban_posts SET ingest_status = ? WHERE id = ?', ('processed', int(post_id)))
        conn.commit()
        conn.close()

    def save_scheduler_log(self, task_type: str, status: str, message: str, retry_count: int = 0, duration: float = 0, related_post_id: Optional[int] = None) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_scheduler_logs
            (task_type, status, message, retry_count, duration, related_post_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (task_type, status, message, int(retry_count), float(duration or 0), int(related_post_id) if related_post_id else None),
        )
        conn.commit()
        conn.close()

    def get_scheduler_logs(self, limit: int = 50) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_scheduler_logs
            ORDER BY id DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def save_learning_memory(self, author_id: str, report_id: Optional[int], memory_type: str, memory_json: Dict, quality_score: float = 0) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_author_learning_memory
            (author_id, report_id, memory_type, memory_json, quality_score, as_of_date)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                author_id,
                int(report_id) if report_id else None,
                str(memory_type or ''),
                json.dumps(memory_json, ensure_ascii=False),
                float(quality_score or 0),
                datetime.now().strftime('%Y-%m-%d'),
            ),
        )
        conn.commit()
        mid = int(cursor.lastrowid or 0)
        conn.close()
        return mid

    def list_top_memories(self, author_id: str, memory_type: str, limit: int = 10) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_author_learning_memory
            WHERE author_id = ? AND memory_type = ?
            ORDER BY quality_score DESC, id DESC
            LIMIT ?
            ''',
            (author_id, memory_type, int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def save_candidate_outcome(self, candidate_id: int, author_id: str, horizon: str, ret_pct: float, max_drawdown: float, hit_trigger: bool, outcome_label: str) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_author_candidate_outcomes
            (candidate_id, author_id, horizon, ret_pct, max_drawdown, hit_trigger, outcome_label, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                int(candidate_id),
                author_id,
                str(horizon or 'T1'),
                float(ret_pct or 0),
                float(max_drawdown or 0),
                1 if hit_trigger else 0,
                str(outcome_label or 'neutral'),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        oid = int(cursor.lastrowid or 0)
        conn.close()
        return oid

    def aggregate_author_feedback(self, author_id: str, lookback_days: int = 60) -> Dict:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT
                COUNT(*) AS total,
                AVG(ret_pct) AS avg_ret,
                AVG(max_drawdown) AS avg_drawdown,
                SUM(CASE WHEN outcome_label = 'good' THEN 1 ELSE 0 END) AS good_count,
                SUM(CASE WHEN outcome_label = 'bad' THEN 1 ELSE 0 END) AS bad_count
            FROM douban_author_candidate_outcomes
            WHERE author_id = ?
              AND computed_at >= datetime('now', '-' || ? || ' days')
            ''',
            (author_id, int(lookback_days)),
        )
        row = cursor.fetchone()
        conn.close()
        total = int((row or {}).get('total') or 0)
        good_count = int((row or {}).get('good_count') or 0)
        win_rate = round((good_count / total) * 100, 2) if total > 0 else 0.0
        return {
            'total': total,
            'avg_ret': round(float((row or {}).get('avg_ret') or 0), 4),
            'avg_drawdown': round(float((row or {}).get('avg_drawdown') or 0), 4),
            'good_count': good_count,
            'bad_count': int((row or {}).get('bad_count') or 0),
            'win_rate': win_rate,
        }

    def retrieve_author_memories(self, author_id: str, query_text: str, top_k: int = 5, mode: str = 'rule') -> List[Dict]:
        q = str(query_text or '').strip().lower()
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_author_learning_memory
            WHERE author_id = ?
            ORDER BY quality_score DESC, id DESC
            LIMIT ?
            ''',
            (author_id, max(5, int(top_k) * 4)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        if mode != 'rule' or not q:
            return rows[:int(top_k)]

        scored = []
        for r in rows:
            raw = str(r.get('memory_json') or '')
            score = float(r.get('quality_score') or 0)
            if q and q in raw.lower():
                score += 1.0
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:int(top_k)]]

    def ensure_author(self, author_id: str, author_name: str) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_authors(author_id, author_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                author_name = excluded.author_name,
                updated_at = excluded.updated_at
            ''',
            (
                str(author_id or '').strip(),
                str(author_name or '').strip() or str(author_id or '').strip(),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        conn.close()

    def list_authors(self, limit: int = 200) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT a.author_id, a.author_name, a.updated_at,
                   COALESCE(MAX(p.pattern_version), 0) AS latest_pattern_version,
                   COALESCE(COUNT(p.id), 0) AS pattern_count
            FROM douban_authors a
            LEFT JOIN douban_author_patterns p ON p.author_id = a.author_id
            GROUP BY a.author_id, a.author_name, a.updated_at
            ORDER BY a.updated_at DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_next_pattern_version(self, author_id: str) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COALESCE(MAX(pattern_version), 0) AS max_ver FROM douban_author_patterns WHERE author_id = ?',
            (author_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return int((row or {}).get('max_ver') or 0) + 1

    def save_author_pattern(
        self,
        author_id: str,
        pattern_json: Dict,
        source_post_ids: List[int],
        confidence: float = 0,
        as_of_date: Optional[str] = None,
    ) -> int:
        version = self.get_next_pattern_version(author_id)
        summary = ''
        if isinstance(pattern_json, dict):
            summary = str(pattern_json.get('summary') or pattern_json.get('trading_style') or '')
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO douban_author_patterns
            (author_id, pattern_version, pattern_json, summary, source_post_ids, as_of_date, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                author_id,
                int(version),
                json.dumps(pattern_json, ensure_ascii=False),
                summary,
                json.dumps(source_post_ids or [], ensure_ascii=False),
                as_of_date or datetime.now().strftime('%Y-%m-%d'),
                float(confidence or 0),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        rid = int(cursor.lastrowid or 0)
        conn.close()
        return rid

    def list_author_patterns(self, author_id: str, limit: int = 30) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_author_patterns
            WHERE author_id = ?
            ORDER BY pattern_version DESC
            LIMIT ?
            ''',
            (author_id, int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_author_pattern(self, author_id: str, pattern_version: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_author_patterns
            WHERE author_id = ? AND pattern_version = ?
            LIMIT 1
            ''',
            (author_id, int(pattern_version)),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_latest_author_pattern(self, author_id: str) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM douban_author_patterns
            WHERE author_id = ?
            ORDER BY pattern_version DESC
            LIMIT 1
            ''',
            (author_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_longhubang_stock_pool(self, end_date: str, lookback_days: int = 20, limit: int = 200) -> List[Dict]:
        try:
            end_dt = datetime.strptime(str(end_date), '%Y-%m-%d')
        except Exception:
            end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=max(1, int(lookback_days)))
        db = LonghubangDatabase()
        top_df = db.get_top_stocks(
            start_date=start_dt.strftime('%Y-%m-%d'),
            end_date=end_dt.strftime('%Y-%m-%d'),
            limit=max(10, int(limit)),
        )
        if top_df is None or getattr(top_df, 'empty', True):
            return []
        out: List[Dict] = []
        for _, row in top_df.iterrows():
            code = str(row.get('stock_code') or '').strip()
            if not code:
                continue
            theme = str(row.get('all_concepts') or '').strip()
            out.append(
                {
                    'code': code,
                    'name': str(row.get('stock_name') or ''),
                    'score': float(row.get('total_net_inflow') or 0),
                    'source': 'longhubang_top',
                    'theme': theme,
                }
            )
        return out


douban_author_db = DoubanAuthorDatabase()
