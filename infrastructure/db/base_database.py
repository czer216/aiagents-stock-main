"""
数据库操作基础封装
提供统一的连接管理、JSON 序列化、错误处理
"""
import sqlite3
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from abc import ABC, abstractmethod
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class BaseDatabase(ABC):
    """数据库基类"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @abstractmethod
    def _init_db(self):
        """初始化数据库表结构"""
        pass

    @contextmanager
    def get_connection(self):
        """获取数据库连接（上下文管理器）"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # 返回字典格式
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ 数据库操作失败: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch_one: bool = False,
        fetch_all: bool = True,
    ) -> Optional[Any]:
        """
        执行查询

        Args:
            query: SQL 查询语句
            params: 查询参数
            fetch_one: 是否只返回一条记录
            fetch_all: 是否返回所有记录

        Returns:
            查询结果
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())

                if fetch_one:
                    row = cursor.fetchone()
                    return dict(row) if row else None
                elif fetch_all:
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]
                else:
                    return cursor.lastrowid

        except Exception as e:
            logger.error(f"❌ 查询失败: {query}, 错误: {e}")
            return None

    def execute_insert(
        self, table: str, data: Dict, return_id: bool = True
    ) -> Optional[int]:
        """
        插入数据

        Args:
            table: 表名
            data: 数据字典
            return_id: 是否返回插入的 ID

        Returns:
            插入的记录 ID
        """
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(data.values()))
                if return_id:
                    return cursor.lastrowid
                return None
        except Exception as e:
            logger.error(f"❌ 插入失败: {table}, 错误: {e}")
            return None

    def execute_update(
        self, table: str, data: Dict, where: str, params: Tuple
    ) -> bool:
        """
        更新数据

        Args:
            table: 表名
            data: 更新的数据字典
            where: WHERE 条件
            params: WHERE 参数

        Returns:
            是否成功
        """
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(data.values()) + params)
                return True
        except Exception as e:
            logger.error(f"❌ 更新失败: {table}, 错误: {e}")
            return False

    def execute_delete(self, table: str, where: str, params: Tuple) -> bool:
        """
        删除数据

        Args:
            table: 表名
            where: WHERE 条件
            params: WHERE 参数

        Returns:
            是否成功
        """
        query = f"DELETE FROM {table} WHERE {where}"

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return True
        except Exception as e:
            logger.error(f"❌ 删除失败: {table}, 错误: {e}")
            return False

    @staticmethod
    def serialize_json(data: Any) -> str:
        """将 Python 对象序列化为 JSON 字符串"""
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ JSON 序列化失败: {e}")
            return "{}"

    @staticmethod
    def deserialize_json(json_str: str, default: Any = None) -> Any:
        """将 JSON 字符串反序列化为 Python 对象"""
        try:
            return json.loads(json_str) if json_str else default
        except Exception as e:
            logger.error(f"❌ JSON 反序列化失败: {e}")
            return default

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
        result = self.execute_query(query, (table_name,), fetch_one=True)
        return result is not None
