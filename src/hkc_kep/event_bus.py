"""
hkc-kep / event_bus.py
Knowledge Event Protocol — 事件总线。

v1：进程内同步执行，零依赖。
v2：可替换为 Redis Pub/Sub，接口不变。

所有事件统一格式：
{
    "event_id":  "EVT_00000001",
    "event":     "knowledge.created",
    "version":   "1.0",
    "timestamp": "2025-01-01T00:00:00Z",
    "source":    "KDE",
    "payload":   { ... }
}
"""
import json
import logging
import sqlite3
import threading
import traceback

logger = logging.getLogger(__name__)
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# ── 事件类型常量 ─────────────────────────────────────────────

class KEPEvents:
    # KDE 产出
    KNOWLEDGE_CREATED  = "knowledge.created"
    KNOWLEDGE_UPDATED  = "knowledge.updated"
    KNOWLEDGE_MERGED   = "knowledge.merged"
    KNOWLEDGE_DELETED  = "knowledge.deleted"

    # KEE 产出
    CONFLICT_DETECTED  = "conflict.detected"
    CONFLICT_RESOLVED  = "conflict.resolved"
    CLAIM_STATUS_CHANGED = "claim.status_changed"

    # ACE 产出
    ABILITY_CREATED    = "ability.created"
    ABILITY_UPDATED    = "ability.updated"

    # HMR 来源
    MEMORY_CREATED     = "memory.created"
    MEMORY_UPDATED     = "memory.updated"
    MEMORY_DELETED     = "memory.deleted"

    # HAR 来源
    AGENT_USED_ABILITY = "agent.used_ability"   # Feedback Loop 入口


# ── EventBus ─────────────────────────────────────────────────

class EventBus:
    """
    线程安全的进程内事件总线。
    事件审计日志写入 SQLite，方便调试和回放。
    """

    def __init__(self, db_path: str = ":memory:"):
        self._lock      = threading.RLock()
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._counter   = 0

        # 审计日志
        log_path = Path(db_path)
        if db_path != ":memory:":
            log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_log_table()

    def _init_log_table(self):
        self._log_db.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                event_id   TEXT PRIMARY KEY,
                event      TEXT NOT NULL,
                source     TEXT,
                payload    TEXT,
                created_at TEXT
            )
        """)
        self._log_db.commit()

    def _gen_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"EVT_{self._counter:08d}"

    # ── 订阅 ─────────────────────────────────────────────────

    def subscribe(self, event: str, handler: Callable) -> None:
        """
        注册事件处理器。
        同一事件可注册多个处理器，按注册顺序执行。
        """
        with self._lock:
            self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            handlers = self._handlers.get(event, [])
            if handler in handlers:
                handlers.remove(handler)

    # ── 发布 ─────────────────────────────────────────────────

    def publish(self, event_dict: dict) -> str:
        """
        发布事件，同步调用所有已注册处理器。
        返回 event_id。
        处理器异常不阻断后续处理器执行。
        """
        event_id = self._gen_id()
        event_dict = {
            "event_id":  event_id,
            "version":   "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event_dict,
        }

        event_type = event_dict.get("event", "")
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        # 写审计日志(处理器不在锁内执行,避免长时间持锁/重入死锁)
        self._write_log(event_dict)

        # 调用处理器
        for handler in handlers:
            try:
                handler(event_dict)
            except Exception:
                # 记录错误但不中断其他处理器
                logger.error("Handler error for %s", event_type, exc_info=True)

        return event_id

    def _write_log(self, event_dict: dict):
        try:
            # 加锁:并发 publish(两个摄入任务)会从多个线程共用这一个连接,
            # 不加锁 sqlite3 会 "recursive use of cursors"/损坏。与 GraphStore 一致。
            with self._lock:
                self._log_db.execute(
                    "INSERT OR IGNORE INTO event_log "
                    "(event_id,event,source,payload,created_at) VALUES (?,?,?,?,?)",
                    (
                        event_dict.get("event_id"),
                        event_dict.get("event"),
                        event_dict.get("source"),
                        json.dumps(event_dict.get("payload", {})),
                        event_dict.get("timestamp"),
                    )
                )
                self._log_db.commit()
        except Exception:
            pass  # 日志失败不影响主流程

    # ── 查询日志 ─────────────────────────────────────────────

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._log_db.execute(
                "SELECT * FROM event_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [
            {
                "event_id":  r[0],
                "event":     r[1],
                "source":    r[2],
                "payload":   json.loads(r[3] or "{}"),
                "created_at": r[4],
            }
            for r in rows
        ]

    def events_by_type(self, event_type: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._log_db.execute(
                "SELECT * FROM event_log WHERE event=? "
                "ORDER BY created_at DESC LIMIT ?",
                (event_type, limit)
            ).fetchall()
        return [
            {
                "event_id":  r[0],
                "event":     r[1],
                "source":    r[2],
                "payload":   json.loads(r[3] or "{}"),
                "created_at": r[4],
            }
            for r in rows
        ]
