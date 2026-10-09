# -*- coding: utf-8 -*-
"""SUT 数据库只读断言助手。

为什么需要它：断言体系分 4 层（状态码/错误码 → JSON Schema → 业务字段 → **关键数据落库**）。
第 4 层才是把「接口返回对了」升级成「数据真的对了」——超卖、幂等、库存回滚这三个
★ 场景**必须靠落库断言才能证明**。

★ 只读打开（sqlite URI mode=ro）：绝不允许测试代码绕过 API 直接改库，
  否则「通过」就失去意义。（需要构造时间态时由 SUT 的测试后门提供能力。）
"""

import sqlite3
from typing import Any, Dict, List, Optional, Sequence


class SutDatabase:
    """SUT（mock 电商）数据库的只读视图。"""

    def __init__(self, path: str) -> None:
        self.path = path

    # ------------------------------------------------------------- 基础查询
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect("file:{}?mode=ro".format(self.path), uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.one(sql, params)
        return list(row.values())[0] if row else None

    # ------------------------------------------------------------- 业务断言快捷方法
    def product_stock(self, product_id: int) -> Optional[int]:
        return self.scalar("SELECT stock FROM products WHERE id = ?", (product_id,))

    def product_status(self, product_id: int) -> Optional[int]:
        return self.scalar("SELECT status FROM products WHERE id = ?", (product_id,))

    def order(self, order_no: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM orders WHERE order_no = ?", (order_no,))

    def order_status(self, order_no: str) -> Optional[str]:
        return self.scalar("SELECT status FROM orders WHERE order_no = ?", (order_no,))

    def order_items(self, order_no: str) -> List[Dict[str, Any]]:
        return self.query(
            "SELECT i.* FROM order_items i JOIN orders o ON o.id = i.order_id"
            " WHERE o.order_no = ? ORDER BY i.id",
            (order_no,),
        )

    def payment(self, pay_no: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM payments WHERE pay_no = ?", (pay_no,))

    def payment_callback_count(self, pay_no: str) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM payment_callbacks WHERE pay_no = ?", (pay_no,)
            )
            or 0
        )

    def cart_size(self, username: str) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM cart_items c JOIN users u ON u.id = c.user_id"
                " WHERE u.username = ?",
                (username,),
            )
            or 0
        )

    def user(self, username: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM users WHERE username = ?", (username,))
