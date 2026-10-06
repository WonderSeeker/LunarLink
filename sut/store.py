# -*- coding: utf-8 -*-
"""数据访问层（手写 SQL，不引入 ORM）。

按领域分节，随改造推进逐节补充：
    [本阶段] 用户、商品
    [后续]   token、购物车、订单、支付

所有写操作走 db.transaction()（BEGIN IMMEDIATE + 等锁退避重试），
所有读操作走 db.query_*()。
"""

from typing import Any, Dict, List, Optional

from . import config, db, security

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
