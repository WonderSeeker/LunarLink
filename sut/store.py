# -*- coding: utf-8 -*-
"""数据访问层（手写 SQL，不引入 ORM）。

按领域分节，随改造推进逐节补充：
    [本阶段] 用户、商品
    [后续]   token、购物车、订单、支付

所有写操作走 db.transaction()（BEGIN IMMEDIATE + 等锁退避重试），
所有读操作走 db.query_*()。
"""

from typing import Any, Dict, List, Optional

from . import config, db, errors, security

# ===================================================================== 用户


USER_PUBLIC_FIELDS = ("id", "username", "name", "role", "is_active", "created_at")


def public_user(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """过滤出可以对外暴露的用户字段。

    ★ 绝不能把 password_hash / password_salt 返回给客户端 ——
      test_08_security.py 有「响应体不泄露口令哈希」的断言。
    """
    if row is None:
        return None
    return {key: row[key] for key in USER_PUBLIC_FIELDS if key in row}


def create_user(
    username: str, password: str, name: str = "", role: str = config.ROLE_CUSTOMER
) -> Dict[str, Any]:
    """创建用户，返回包含 password_hash 的完整行（调用方自行决定是否暴露）。"""
    password_hash, password_salt = security.hash_password(password)
    now = security.now_ts()

    with db.transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, password_salt, name, role,
                 is_active, failed_attempts, locked_until, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 0, 0, ?)
            """,
            (username, password_hash, password_salt, name, role, now),
        )
        user_id = cursor.lastrowid

    return get_user_by_id(user_id)


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM users WHERE username = ?", (username,))


def count_users() -> int:
    return int(db.query_scalar("SELECT COUNT(*) FROM users") or 0)


def update_login_failure(
    user_id: int, failed_attempts: int, locked_until: int
) -> None:
    """更新连续失败次数与锁定截止时间。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (failed_attempts, locked_until, user_id),
        )


def reset_login_failure(user_id: int) -> None:
    """登录成功后清零失败计数与锁。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = ?",
            (user_id,),
        )


def unlock_user(user_id: int) -> None:
    """测试后门用：立即解锁（把 locked_until 拨到过去）。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = ?",
            (user_id,),
        )


# ===================================================================== 商品


def create_product(
    name: str, description: str, price_cents: int, stock: int
) -> Dict[str, Any]:
    now = security.now_ts()
    with db.transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products
                (name, description, price_cents, stock, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (name, description, price_cents, stock, now, now),
        )
        product_id = cursor.lastrowid

    return get_product(product_id)


def get_product(product_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM products WHERE id = ?", (product_id,))


def get_product_by_name(name: str) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM products WHERE name = ?", (name,))


def list_products(
    page: int, size: int, keyword: Optional[str] = None, sort: str = "id"
) -> Dict[str, Any]:
    """分页查询上架商品。

    返回 {"total": int, "items": [...]}。分页参数的合法性校验在路由层做
    （这样 400 的错误码能落到具体的 code 上）。
    """
    where = ["status = 1"]
    params: List[Any] = []
    if keyword:
        where.append("name LIKE ?")
        params.append("%" + keyword + "%")

    where_sql = "WHERE " + " AND ".join(where)

    # 只允许白名单字段排序，杜绝 SQL 注入
    sort_column = {
        "id": "id",
        "-id": "id DESC",
        "price": "price_cents",
        "-price": "price_cents DESC",
    }.get(sort, "id")

    total = int(
        db.query_scalar("SELECT COUNT(*) FROM products " + where_sql, params) or 0
    )
    offset = (page - 1) * size
    items = db.query_all(
        "SELECT * FROM products {} ORDER BY {} LIMIT ? OFFSET ?".format(
            where_sql, sort_column
        ),
        params + [size, offset],
    )
    return {"total": total, "items": items}


def update_product(
    product_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    price_cents: Optional[int] = None,
    stock: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    fields = []
    params: List[Any] = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if price_cents is not None:
        fields.append("price_cents = ?")
        params.append(price_cents)
    if stock is not None:
        fields.append("stock = ?")
        params.append(stock)

    if fields:
        fields.append("updated_at = ?")
        params.append(security.now_ts())
        params.append(product_id)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE products SET {} WHERE id = ?".format(", ".join(fields)),
                params,
            )

    return get_product(product_id)


def soft_delete_product(product_id: int) -> None:
    """下架（软删）。"""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE products SET status = 0, updated_at = ? WHERE id = ?",
            (security.now_ts(), product_id),
        )


def count_products() -> int:
    return int(db.query_scalar("SELECT COUNT(*) FROM products") or 0)


# ===================================================================== 登录凭证
# 单 token 简化设计：token 落库，靠 expires_at / revoked 判定有效性。
# 这样「过期」「登出失效」「任意 TTL」都能被确定性构造与断言
# （后门 /api/_test/token 直接调用 create_token）。
#
# ★ 已弃用说明：revoked 列保留但当前不使用 —— logout 是纯 no-op 语义
#   （JWT 无状态的诚实做法，见 README「设计简化说明」）。


def create_token(user_id: int, ttl_seconds: Optional[int] = None) -> Dict[str, Any]:
    """为用户签发 token。

    ttl_seconds 允许为负数 —— 后门借此签发「一出生就已过期」的 token，
    用于确定性验证过期分支，测试里不需要 sleep。
    """
    if ttl_seconds is None:
        ttl_seconds = config.TOKEN_TTL_SECONDS

    token = security.generate_token()
    now = security.now_ts()
    expires_at = now + int(ttl_seconds)

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tokens (token, user_id, expires_at, revoked, created_at)"
            " VALUES (?, ?, ?, 0, ?)",
            (token, user_id, expires_at, now),
        )

    return {"token": token, "expires_at": expires_at, "issued_at": now}


def get_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    return db.query_one("SELECT * FROM tokens WHERE token = ?", (token,))


def revoke_token(token: str) -> None:
    """保留能力：把 token 标记为失效。当前 logout 走 no-op 语义，未调用。"""
    with db.transaction() as conn:
        conn.execute("UPDATE tokens SET revoked = 1 WHERE token = ?", (token,))


def count_tokens(user_id: Optional[int] = None) -> int:
    if user_id is None:
        return int(db.query_scalar("SELECT COUNT(*) FROM tokens") or 0)
    return int(
        db.query_scalar(
            "SELECT COUNT(*) FROM tokens WHERE user_id = ?", (user_id,)
        )
        or 0
    )


# ===================================================================== 购物车
# 说明：本模块的校验放在事务内做（而不是路由层先查再写），
# 这样「读当前数量 → 判断是否超库存 → 写回」是一个原子动作，
# 同一用户并发加购同一商品时不会丢失更新、也不会绕过库存上限。


def _cart_rows(conn: Any, user_id: int):
    return conn.execute(
        """
        SELECT c.id, c.product_id, c.quantity, c.created_at, c.updated_at,
               p.name AS product_name, p.price_cents, p.stock, p.status
          FROM cart_items c
          JOIN products p ON p.id = c.product_id
         WHERE c.user_id = ?
         ORDER BY c.id
        """,
        (user_id,),
    ).fetchall()


def get_cart(user_id: int) -> Dict[str, Any]:
    items = db.query_all(
        """
        SELECT c.id, c.product_id, c.quantity,
               p.name AS product_name, p.price_cents, p.stock, p.status,
               (p.price_cents * c.quantity) AS subtotal_cents
          FROM cart_items c
          JOIN products p ON p.id = c.product_id
         WHERE c.user_id = ?
         ORDER BY c.id
        """,
        (user_id,),
    )
    total = sum(int(item["subtotal_cents"]) for item in items)
    return {"items": items, "item_count": len(items), "total_amount_cents": total}


def add_cart_item(user_id: int, product_id: int, quantity: int):
    """加购；同一商品已存在则【合并数量】。返回 (item_id, created)。"""
    now = security.now_ts()
    with db.transaction() as conn:
        product = conn.execute(
            "SELECT id, name, stock, status FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None or product["status"] != 1:
            raise errors.NotFound(
                "商品不存在或已下架",
                code="PRODUCT_NOT_FOUND",
                detail={"product_id": product_id},
            )

        row = conn.execute(
            "SELECT id, quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ).fetchone()

        current = int(row["quantity"]) if row else 0
        new_quantity = current + quantity
        if new_quantity > int(product["stock"]):
            raise errors.Conflict(
                "库存不足",
                code="INSUFFICIENT_STOCK",
                detail={
                    "product_id": product_id,
                    "stock": int(product["stock"]),
                    "in_cart": current,
                    "requested_total": new_quantity,
                },
            )

        if row is None:
            cursor = conn.execute(
                "INSERT INTO cart_items (user_id, product_id, quantity, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (user_id, product_id, quantity, now, now),
            )
            return cursor.lastrowid, True

        conn.execute(
            "UPDATE cart_items SET quantity = ?, updated_at = ? WHERE id = ?",
            (new_quantity, now, row["id"]),
        )
        return row["id"], False


def update_cart_item(user_id: int, item_id: int, quantity: int) -> int:
    """修改购物车某项数量，返回 product_id。"""
    now = security.now_ts()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT c.id, c.product_id, c.user_id, p.stock, p.status"
            "  FROM cart_items c JOIN products p ON p.id = c.product_id"
            " WHERE c.id = ?",
            (item_id,),
        ).fetchone()

        # 不属于当前用户时按「不存在」处理（404），不泄露他人购物车条目的存在性
        if row is None or int(row["user_id"]) != int(user_id):
            raise errors.NotFound("购物车条目不存在", code="CART_ITEM_NOT_FOUND")

        if int(row["status"]) != 1:
            raise errors.NotFound("商品不存在或已下架", code="PRODUCT_NOT_FOUND")

        if quantity > int(row["stock"]):
            raise errors.Conflict(
                "库存不足",
                code="INSUFFICIENT_STOCK",
                detail={"product_id": row["product_id"], "stock": int(row["stock"])},
            )

        conn.execute(
            "UPDATE cart_items SET quantity = ?, updated_at = ? WHERE id = ?",
            (quantity, now, item_id),
        )
        return int(row["product_id"])


def delete_cart_item(user_id: int, item_id: int) -> None:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM cart_items WHERE id = ? AND user_id = ?", (item_id, user_id)
        )
        if cursor.rowcount != 1:
            raise errors.NotFound("购物车条目不存在", code="CART_ITEM_NOT_FOUND")


def clear_cart(user_id: int) -> int:
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        return cursor.rowcount


# ===================================================================== 订单


def _apply_transition(conn: Any, order: Dict[str, Any], to_state: str) -> None:
    """★ 订单状态机校验：非法流转一律 409，并在 detail 里回传 from/to/allowed。"""
    allowed = config.ORDER_STATE_TRANSITIONS.get(order["status"], set())
    if to_state not in allowed:
        raise errors.Conflict(
            "非法的订单状态流转：{} -> {}".format(order["status"], to_state),
            code="INVALID_STATE_TRANSITION",
            detail={
                "from": order["status"],
                "to": to_state,
                "allowed": sorted(allowed),
            },
        )


def create_order_from_cart(
    user_id: int, ttl_seconds: Optional[int] = None
) -> Dict[str, Any]:
    """从购物车下单：★ 事务内条件更新扣库存（防超卖），成功后清空购物车。"""
    if ttl_seconds is None:
        ttl_seconds = config.ORDER_TTL_SECONDS

    now = security.now_ts()
    order_no = security.generate_order_no()
    expire_at = now + int(ttl_seconds)

    with db.transaction() as conn:
        rows = _cart_rows(conn, user_id)
        if not rows:
            raise errors.InvalidParam("购物车为空，无法下单", code="EMPTY_CART")

        total = 0
        snapshot = []
        for row in rows:
            if int(row["status"]) != 1:
                raise errors.Conflict(
                    "购物车中存在已下架商品",
                    code="PRODUCT_UNAVAILABLE",
                    detail={"product_id": row["product_id"]},
                )

            quantity = int(row["quantity"])

            # ★ 防超卖的关键：单条 SQL 条件更新，天然原子；靠 rowcount 判定成功与否。
            #   绝不能写成「先 SELECT stock 再 UPDATE」——那是 check-then-act 竞态。
            cursor = conn.execute(
                "UPDATE products SET stock = stock - ?, updated_at = ?"
                " WHERE id = ? AND stock >= ?",
                (quantity, now, row["product_id"], quantity),
            )
            if cursor.rowcount != 1:
                # 抛错会触发事务回滚，此前已扣减的库存全部还原
                raise errors.Conflict(
                    "库存不足，下单失败",
                    code="INSUFFICIENT_STOCK",
                    detail={"product_id": row["product_id"], "requested": quantity},
                )

            subtotal = int(row["price_cents"]) * quantity
            total += subtotal
            snapshot.append(
                {
                    "product_id": int(row["product_id"]),
                    "product_name": row["product_name"],
                    "unit_price_cents": int(row["price_cents"]),
                    "quantity": quantity,
                    "subtotal_cents": subtotal,
                }
            )

        cursor = conn.execute(
            """
            INSERT INTO orders
                (order_no, user_id, status, total_amount_cents, expire_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_no,
                user_id,
                config.ORDER_STATUS_PENDING,
                total,
                expire_at,
                now,
                now,
            ),
        )
        order_id = cursor.lastrowid

        for item in snapshot:
            conn.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, product_name, unit_price_cents,
                     quantity, subtotal_cents)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item["product_id"],
                    item["product_name"],
                    item["unit_price_cents"],
                    item["quantity"],
                    item["subtotal_cents"],
                ),
            )

        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))

    return get_order(order_no, user_id)


def list_orders(
    user_id: int,
    status: Optional[str] = None,
    page: int = 1,
    size: int = config.DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    where = ["user_id = ?"]
    params: List[Any] = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = "WHERE " + " AND ".join(where)

    total = int(
        db.query_scalar("SELECT COUNT(*) FROM orders " + where_sql, params) or 0
    )
    items = db.query_all(
        "SELECT * FROM orders {} ORDER BY id DESC LIMIT ? OFFSET ?".format(where_sql),
        params + [size, (page - 1) * size],
    )
    return {"total": total, "items": items, "page": page, "size": size}


def get_order(order_no: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """取订单。传 user_id 时按归属过滤 —— 越权访问会自然返回 None，
    调用方据此返回 404（而不是 403：403 会泄露"该订单存在"）。"""
    if user_id is None:
        row = db.query_one("SELECT * FROM orders WHERE order_no = ?", (order_no,))
    else:
        row = db.query_one(
            "SELECT * FROM orders WHERE order_no = ? AND user_id = ?",
            (order_no, user_id),
        )
    if row is None:
        return None
    row["items"] = db.query_all(
        "SELECT product_id, product_name, unit_price_cents, quantity, subtotal_cents"
        "  FROM order_items WHERE order_id = ? ORDER BY id",
        (row["id"],),
    )
    return row


def _transition_order(
    order_no: str, user_id: Optional[int], to_state: str
) -> Dict[str, Any]:
    """流转订单状态。user_id 传 None 表示不按归属过滤（供 admin 发货使用）。"""
    now = security.now_ts()
    with db.transaction() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_no = ?", (order_no,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_no = ? AND user_id = ?",
                (order_no, user_id),
            ).fetchone()
        if row is None:
            raise errors.NotFound("订单不存在", code="ORDER_NOT_FOUND")

        order = dict(row)
        _apply_transition(conn, order, to_state)

        if to_state == config.ORDER_STATUS_CANCELLED:
            # 取消订单要把库存还回去
            for item in conn.execute(
                "SELECT product_id, quantity FROM order_items WHERE order_id = ?",
                (order["id"],),
            ).fetchall():
                conn.execute(
                    "UPDATE products SET stock = stock + ?, updated_at = ? WHERE id = ?",
                    (int(item["quantity"]), now, item["product_id"]),
                )

        column = {
            config.ORDER_STATUS_CANCELLED: "cancelled_at",
            config.ORDER_STATUS_COMPLETED: "completed_at",
            config.ORDER_STATUS_PAID: "paid_at",
        }.get(to_state)

        if column:
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ?, {} = ? WHERE id = ?".format(
                    column
                ),
                (to_state, now, now, order["id"]),
            )
        else:
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                (to_state, now, order["id"]),
            )

    return get_order(order_no, user_id)


def cancel_order(order_no: str, user_id: int) -> Dict[str, Any]:
    """PENDING -> CANCELLED，并回滚库存。"""
    return _transition_order(order_no, user_id, config.ORDER_STATUS_CANCELLED)


def ship_order(order_no: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """PAID -> SHIPPED（发货，admin 触发，不按归属过滤）。

    ★ 这是原设计 6 个订单接口之外的补充：没有它 SHIPPED 状态不可达，
      confirm（SHIPPED -> COMPLETED）就永远无法成功，状态机也就测不完整。
    """
    return _transition_order(order_no, user_id, config.ORDER_STATUS_SHIPPED)


def confirm_order(order_no: str, user_id: int) -> Dict[str, Any]:
    """SHIPPED -> COMPLETED。"""
    return _transition_order(order_no, user_id, config.ORDER_STATUS_COMPLETED)


# ===================================================================== 支付
# 范围收敛：本次只实现「创建支付单 + 幂等回调」，
# 超时补偿与退款按约定跳过（schema 里仍保留 expire_at 字段，订单下单时照常写入）。


def get_payment(pay_no: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if user_id is None:
        return db.query_one("SELECT * FROM payments WHERE pay_no = ?", (pay_no,))
    return db.query_one(
        "SELECT * FROM payments WHERE pay_no = ? AND user_id = ?", (pay_no, user_id)
    )


def create_payment(order_no: str, user_id: int):
    """为订单创建支付单。同一订单重复发起时返回【同一个】支付单（不重复创建）。

    返回 (payment, created)。
    """
    now = security.now_ts()
    with db.transaction() as conn:
        # ★ 先按 user_id 过滤，保证越权请求与「订单不存在」表现一致（都是 404）
        order = conn.execute(
            "SELECT * FROM orders WHERE order_no = ? AND user_id = ?",
            (order_no, user_id),
        ).fetchone()
        if order is None:
            raise errors.NotFound("订单不存在", code="ORDER_NOT_FOUND")

        existing = conn.execute(
            "SELECT * FROM payments WHERE order_no = ?", (order_no,)
        ).fetchone()
        if existing is not None:
            return dict(existing), False

        if order["status"] != config.ORDER_STATUS_PENDING:
            raise errors.Conflict(
                "当前订单状态不可支付：{}".format(order["status"]),
                code="ORDER_NOT_PAYABLE",
                detail={"order_no": order_no, "status": order["status"]},
            )

        pay_no = security.generate_pay_no()
        conn.execute(
            """
            INSERT INTO payments
                (pay_no, order_no, user_id, amount_cents, status,
                 amount_paid_cents, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', 0, ?)
            """,
            (pay_no, order_no, user_id, int(order["total_amount_cents"]), now),
        )

    return get_payment(pay_no, user_id), True


def settle_payment(pay_no: str, notify_id: str, amount_cents: int) -> Dict[str, Any]:
    """处理支付回调（首次成功入账）。

    ★ 幂等靠【数据库唯一约束】而不是应用层先查一遍：
      `INSERT INTO payment_callbacks(notify_id)` 成功 = 首次；
      重复的 notify_id 会抛 sqlite3.IntegrityError，由路由层捕获并返回 duplicated=true。
      check-then-act 在并发下必然漏，唯一约束不会。
    """
    now = security.now_ts()
    with db.transaction() as conn:
        pay = conn.execute(
            "SELECT * FROM payments WHERE pay_no = ?", (pay_no,)
        ).fetchone()
        if pay is None:
            raise errors.NotFound("支付单不存在", code="PAYMENT_NOT_FOUND")

        if int(amount_cents) != int(pay["amount_cents"]):
            raise errors.InvalidParam(
                "回调金额与支付单金额不一致",
                code="AMOUNT_MISMATCH",
                detail={
                    "expected": int(pay["amount_cents"]),
                    "received": int(amount_cents),
                },
            )

        # ★ 幂等闸门：唯一约束冲突会向上抛 IntegrityError
        conn.execute(
            "INSERT INTO payment_callbacks (notify_id, pay_no, created_at)"
            " VALUES (?, ?, ?)",
            (notify_id, pay_no, now),
        )

        order = conn.execute(
            "SELECT * FROM orders WHERE order_no = ?", (pay["order_no"],)
        ).fetchone()
        if order is None:
            raise errors.NotFound("订单不存在", code="ORDER_NOT_FOUND")

        if order["status"] != config.ORDER_STATUS_PENDING:
            raise errors.Conflict(
                "非法的订单状态流转：{} -> {}".format(order["status"], "PAID"),
                code="INVALID_STATE_TRANSITION",
                detail={"from": order["status"], "to": "PAID", "allowed": []},
            )

        conn.execute(
            "UPDATE payments SET status = 'PAID', notify_id = ?,"
            " amount_paid_cents = ?, paid_at = ? WHERE id = ?",
            (notify_id, int(amount_cents), now, pay["id"]),
        )
        conn.execute(
            "UPDATE orders SET status = ?, paid_at = ?, updated_at = ? WHERE id = ?",
            (config.ORDER_STATUS_PAID, now, now, order["id"]),
        )

    return {
        "pay_no": pay_no,
        "order_no": pay["order_no"],
        "duplicated": False,
        "order_status": config.ORDER_STATUS_PAID,
        "amount_paid_cents": int(amount_cents),
        "paid_at": now,
    }


def count_payment_callbacks(pay_no: Optional[str] = None) -> int:
    if pay_no is None:
        return int(db.query_scalar("SELECT COUNT(*) FROM payment_callbacks") or 0)
    return int(
        db.query_scalar(
            "SELECT COUNT(*) FROM payment_callbacks WHERE pay_no = ?", (pay_no,)
        )
        or 0
    )
