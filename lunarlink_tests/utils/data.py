# -*- coding: utf-8 -*-
"""造数与隔离工具。

★ 隔离策略（见 CHANGES.md）：
  用例之间的数据隔离靠 **uuid 后缀造独立数据**，不靠 reset。
  reset 只在会话开始时调用一次（见 conftest.py 的 sut_seeded fixture），
  并发用例自带独立 SKU 与用户，不与其他用例共享资源。
  这样用例可以任意顺序、任意并行执行，互不干扰。
"""

import uuid
from typing import Any, Dict, Optional

DEFAULT_PASSWORD = "pw123456"


def unique(prefix: str) -> str:
    """生成带 uuid 后缀的唯一名字，避免用例之间撞名（也便于失败时定位来源）。"""
    return "{}_{}".format(prefix, uuid.uuid4().hex[:10])


def register(client, username: Optional[str] = None, password: str = DEFAULT_PASSWORD,
             name: str = "自动化账号") -> Dict[str, Any]:
    """注册一个用户并返回 {username, password, id, token}。

    ★ 这里刻意走真实注册 + 真实登录，而不是用测试后门直接签 token ——
      让 fixture 准备阶段也覆盖一遍正常链路，顺带发现注册/登录的回归。
      （需要绕过锁定时才用后门，见 conftest 的 sut_admin_token。）
    """
    username = username or unique("u")
    resp = client.post("/api/auth/register",
                       json={"username": username, "password": password, "name": name})
    assert resp.status_code == 201, "注册失败: HTTP {} {}".format(resp.status_code, resp.text[:300])

    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, "登录失败: HTTP {} {}".format(login.status_code, login.text[:300])

    return {
        "username": username,
        "password": password,
        "id": resp.json()["user"]["id"],
        "token": login.json()["token"],
    }


def create_product(client, price_cents: int = 1000, stock: int = 10,
                   name: Optional[str] = None, description: str = "") -> Dict[str, Any]:
    """用 admin 客户端创建一个商品，返回商品 dict。"""
    name = name or unique("商品")
    resp = client.post("/api/products",
                       json={"name": name, "description": description,
                             "price_cents": price_cents, "stock": stock})
    assert resp.status_code == 201, "创建商品失败: HTTP {} {}".format(resp.status_code, resp.text[:300])
    return resp.json()
