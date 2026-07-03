"""
hkc-core / utils / id_gen.py
统一 ID 生成器。
每种类型有固定前缀，ID 格式：PREFIX_XXXXXXXX（8位补零）。
"""
import sqlite3
import threading
from pathlib import Path


# 前缀映射
_PREFIX = {
    "Entity":   "ENT",
    "Concept":  "CON",
    "Fact":     "FCT",
    "Claim":    "CLM",
    "Evidence": "EVD",
    "Ability":  "ABL",
    "Relation": "REL",
    "Conflict": "CFT",
    "Event":    "EVT",
    "Skill":    "SKL",
    "Workflow": "WFL",
}


class IDGenerator:
    """
    线程安全的自增 ID 生成器。
    计数器持久化到 SQLite，重启后不重置。
    """

    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._init_table()

    def _init_table(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS id_counters (
                prefix TEXT PRIMARY KEY,
                counter INTEGER DEFAULT 0
            )
        """)
        self._db.commit()

    def next(self, ku_type: str) -> str:
        prefix = _PREFIX.get(ku_type, "UNK")
        with self._lock:
            row = self._db.execute(
                "SELECT counter FROM id_counters WHERE prefix = ?",
                (prefix,)
            ).fetchone()

            if row:
                counter = row[0] + 1
                self._db.execute(
                    "UPDATE id_counters SET counter = ? WHERE prefix = ?",
                    (counter, prefix)
                )
            else:
                counter = 1
                self._db.execute(
                    "INSERT INTO id_counters (prefix, counter) VALUES (?, ?)",
                    (prefix, counter)
                )
            self._db.commit()

        return f"{prefix}_{counter:08d}"

    def peek(self, ku_type: str) -> int:
        """查看当前计数器，不自增。"""
        prefix = _PREFIX.get(ku_type, "UNK")
        with self._lock:      # 与 next() 共用同一连接,并发下读也要加锁
            row = self._db.execute(
                "SELECT counter FROM id_counters WHERE prefix = ?",
                (prefix,)
            ).fetchone()
        return row[0] if row else 0
