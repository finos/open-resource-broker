import json
import sqlite3
from typing import Any, Dict, Optional, List
from .database_handler_interface import BaseDatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class SQLiteHandler(BaseDatabaseHandler):
    def __init__(self, db_file: str = "database.sqlite"):
        self.db_file = db_file
        self.conn = sqlite3.connect(self.db_file)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        with self.conn:
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    data TEXT
                )
            ''')
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS machines (
                    id TEXT PRIMARY KEY,
                    data TEXT
                )
            ''')

    def insert(self, table: str, key: str, value: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO {table} (id, data) VALUES (?, ?)",
                              (key, json.dumps(value)))

    def get(self, table: str, key: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute(f"SELECT data FROM {table} WHERE id = ?", (key,))
        row = cursor.fetchone()
        return json.loads(row['data']) if row else None

    def update(self, table: str, key: str, value: Dict[str, Any]) -> None:
        self.insert(table, key, value)  # SQLite UPSERT

    def delete(self, table: str, key: str) -> None:
        with self.conn:
            self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (key,))

    def query(self, table: str, conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
        where_clause = " AND ".join([f"json_extract(data, '$.{k}') = ?" for k in conditions.keys()])
        query = f"SELECT data FROM {table} WHERE {where_clause}"
        cursor = self.conn.execute(query, tuple(conditions.values()))
        return [json.loads(row['data']) for row in cursor.fetchall()]

    def scan(self, table: str) -> List[Dict[str, Any]]:
        cursor = self.conn.execute(f"SELECT data FROM {table}")
        return [json.loads(row['data']) for row in cursor.fetchall()]
