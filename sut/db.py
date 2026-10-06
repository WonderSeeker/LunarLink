# -*- coding: utf-8 -*-
"""SQLite 连接与事务封装。

★ 并发用例（test_07_concurrency.py）完全依赖本模块的以下几点，改动前请先读用例：

  1. 连接不跨线程复用
     每次调用新建连接、用完即关。原因：sqlite3 连接默认不允许跨线程使用
     （check_same_thread），而 FastAPI 的**同步 def** 路由跑在 anyio 线程池里、
     线程是被复用的，所以连接必须 per-call，不能做成全局单例。

  2. journal_mode = WAL
     读不阻塞写、写不阻塞读。WAL 是数据库文件的持久属性，只需在 init_schema()
     里设置一次；不在每个连接上重复设置，因为设置它需要短暂持有写锁，
     高并发下反而可能自己把自己锁住。

  3. busy_timeout = 15000ms
     WAL 下写操作仍是串行的，10 个并发请求会在写锁上排队。

  4. 对 "database is locked" 做带退避的有限重试
     排队超时属于**环境问题**，不应以 500 的形式暴露成「实现 bug」。

  5. BEGIN IMMEDIATE（而不是默认的 DEFERRED）
     写事务在一开始就申请写锁。默认的 DEFERRED 会先拿读锁、写的时候再升级，
     并发下极易触发 SQLITE_BUSY 且无法重试。IMMEDIATE 把「等锁」提前到事务入口，
     重试点变得清晰可控。
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import config

# --------------------------------------------------------------------- 内部工具


def _is_locked_error(exc: sqlite3.Error) -> bool:
    """判断是否为「等锁超时」类错误。"""
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _new_conn() -> sqlite3.Connection:
    """新建一个配置好的连接。

    isolation_level=None 表示关闭 sqlite3 模块的隐式事务管理，
    事务完全由本模块的 transaction() 显式控制（BEGIN IMMEDIATE / COMMIT）。
    """
    conn = sqlite3.connect(
        config.DB_PATH,
        timeout=config.BUSY_TIMEOUT_MS / 1000.0,  # 与下面的 PRAGMA 等价，双保险
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = %d" % config.BUSY_TIMEOUT_MS)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --------------------------------------------------------------------- 只读


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """只读连接上下文。WAL 下读之间不互斥，一般不会被阻塞。"""
    conn = _new_conn()
    try:
        yield conn
    finally:
        conn.close()


def query_all(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def query_scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row is not None else None


# --------------------------------------------------------------------- 写入


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """写事务上下文：BEGIN IMMEDIATE + 等锁退避重试。

    只有「申请写锁」这一步会重试。事务体一旦开始执行就不再重试 ——
    因为重试意味着要重新执行调用方的语句，语义上不成立。
    """
    last_error: Optional[sqlite3.Error] = None

    for attempt in range(1, config.WRITE_RETRY_TIMES + 1):
        conn = _new_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            conn.close()
            if _is_locked_error(exc) and attempt < config.WRITE_RETRY_TIMES:
                last_error = exc
                # 线性退避：0.05s / 0.10s / ...
                time.sleep(config.WRITE_RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise

        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        else:
            conn.execute("COMMIT")
        finally:
            conn.close()
        return  # 成功，退出重试循环

    # 理论上不可达（最后一次 attempt 若被锁会直接 raise），保留以防配置被改成 0 次
    raise last_error if last_error else sqlite3.OperationalError("database is locked")


def execute_write(sql: str, params: Sequence[Any] = ()) -> int:
    """执行单条写语句，返回 lastrowid（无自增主键时返回受影响行数意义不大）。"""
    with transaction() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


def execute_script(script: str) -> None:
    """执行多语句脚本（仅用于初始化，不参与并发路径）。"""
    conn = _new_conn()
    try:
        conn.executescript(script)
    finally:
        conn.close()


# --------------------------------------------------------------------- 初始化

_SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def init_schema() -> None:
    """建表（幂等），并设置 WAL 模式。

    WAL 是数据库文件的持久属性，设置一次即可，所以只放在这里，
    不放进 _new_conn()（见模块顶部第 2 条说明）。
    """
    db_dir = os.path.dirname(os.path.abspath(config.DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = _new_conn()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        with open(_SCHEMA_FILE, "r", encoding="utf-8") as fp:
            conn.executescript(fp.read())
    finally:
        conn.close()


def reset_database() -> None:
    """删除数据库文件（含 WAL/SHM 附属文件）。

    供 seed.py --reset 与测试后门使用。调用方需保证当前没有进程持有该库。
    """
    for suffix in ("", "-wal", "-shm"):
        path = config.DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)
