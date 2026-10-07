# -*- coding: utf-8 -*-
"""极简运行期状态：当前在途请求数。

用途只有一个：`POST /api/system/reset` 要删库重建，而**并发请求持有数据库文件时
Windows 会拒绝删除**。所以重置前必须确认没有别的请求在途，否则直接 409 拒绝，
而不是删一半失败留下一个坏库。

刻意做得很小：不引入任何依赖，也不承担别的职责。
"""

import threading

_lock = threading.Lock()
_inflight = 0


def enter() -> int:
    """请求进入，返回进入后的在途数。"""
    global _inflight
    with _lock:
        _inflight += 1
        return _inflight


def leave() -> int:
    """请求结束，返回离开后的在途数。"""
    global _inflight
    with _lock:
        _inflight = max(_inflight - 1, 0)
        return _inflight


def inflight() -> int:
    with _lock:
        return _inflight
