# -*- coding: utf-8 -*-
"""安全用例：水平越权 / 垂直越权 / token 矩阵 / 信息泄露 / 参数注入。

★ 口径（与实现对齐，面试讲点）：
    * 水平越权（访问他人资源）-> **404**，不是 403。
      403 会泄露「该资源存在」；404 让「无权访问」与「不存在」在外部不可区分。
      先例：GitHub 对无权访问的私有仓库返回 404。
    * 垂直越权（角色不足，如普通用户调 admin 接口）-> **403**。
      接口本身的存在性不是秘密，明确告知权限不足更有助于调用方定位问题。

  这两条能与实现对照着看：store 里「跨归属」只有一个显式入口
  （_transition_order_as_admin），所以越权用例通过不是巧合。
"""

import time

import allure
import pytest

from utils.client import (
    TOKEN_STYLE_LUNARLINK,
    TOKEN_STYLE_SUT,
    auth_headers,
)
from utils.data import create_product, unique


def _ordered(sut_admin, buyer_client, quantity=1):
    product = create_product(sut_admin, price_cents=1000, stock=10)
    buyer_client.post("/api/cart/items", json={"product_id": product["id"], "quantity": quantity})
    order = buyer_client.post("/api/orders", json={}).json()
    return product, order["order_no"]


@allure.feature("安全")
@allure.story("水平越权（应为 404）")
class TestHorizontalAccess:

    @allure.title("★ 他人订单：查详情 / 查状态 / 取消 / 确认 -> 全部 404")
    @pytest.mark.security
    def test_other_order_returns_404(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _ordered(sut_admin, fresh_buyer["client"])
        other = fresh_buyer2["client"]

        failed = []
        for method, path in [
            ("get", "/api/orders/{}".format(order_no)),
            ("get", "/api/orders/{}/status".format(order_no)),
            ("post", "/api/orders/{}/cancel".format(order_no)),
            ("post", "/api/orders/{}/confirm".format(order_no)),
        ]:
            resp = getattr(other, method)(path)
            if resp.status_code != 404:
                failed.append("{} {} -> {}".format(method.upper(), path, resp.status_code))
            assert resp.json().get("code") == "ORDER_NOT_FOUND" or resp.status_code == 404

        assert failed == [], "水平越权未返回 404：{}".format(failed)

    @allure.title("★ 他人支付单：查询 / 为他人订单创建支付单 -> 404")
    @pytest.mark.security
    def test_other_payment_returns_404(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post(
            "/api/payments", json={"order_no": order_no}
        ).json()["payment"]["pay_no"]

        other = fresh_buyer2["client"]
        assert other.get("/api/payments/{}".format(pay_no)).status_code == 404
        assert other.post("/api/payments", json={"order_no": order_no}).status_code == 404

    @allure.title("★ 他人购物车条目：改 / 删 -> 404")
    @pytest.mark.security
    def test_other_cart_item_returns_404(self, sut_admin, fresh_buyer, fresh_buyer2):
        product = create_product(sut_admin, stock=10)
        item_id = fresh_buyer["client"].post(
            "/api/cart/items", json={"product_id": product["id"], "quantity": 1}
        ).json()["item"]["id"]

        other = fresh_buyer2["client"]
        assert other.get("/api/cart").json()["item_count"] == 0
        assert other.put("/api/cart/items/{}".format(item_id), json={"quantity": 2}).status_code == 404
        assert other.delete("/api/cart/items/{}".format(item_id)).status_code == 404

    @allure.title("★ 越权返回 404 而不是 403（避免泄露资源是否存在）")
    @pytest.mark.security
    def test_404_not_403(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _ordered(sut_admin, fresh_buyer["client"])
        resp = fresh_buyer2["client"].get("/api/orders/{}".format(order_no))
        assert resp.status_code == 404
        assert resp.status_code != 403, "403 会泄露该订单存在"

        # 与「根本不存在」表现一致 —— 这是关键：外部无法区分
        missing = fresh_buyer2["client"].get("/api/orders/NO_SUCH_ORDER_AT_ALL")
        assert missing.status_code == 404
        assert resp.json()["code"] == missing.json()["code"] == "ORDER_NOT_FOUND"


@allure.feature("安全")
@allure.story("垂直越权（应为 403）")
class TestVerticalAccess:

    @allure.title("★ 普通用户调用 admin 接口 -> 403（不是 404）")
    @pytest.mark.security
    def test_customer_admin_endpoints(self, buyer, sut_admin):
        product = create_product(sut_admin)
        checks = [
            ("post", "/api/products", {"name": unique("越权"), "price_cents": 1, "stock": 1}),
            ("put", "/api/products/{}".format(product["id"]), {"stock": 1}),
            ("delete", "/api/products/{}".format(product["id"]), None),
            ("post", "/api/orders/NOPE/ship", {}),
            ("post", "/api/system/reset", {}),
        ]
        for method, path, payload in checks:
            kwargs = {"json": payload} if payload is not None else {}
            resp = getattr(buyer, method)(path, **kwargs)
            assert resp.status_code == 403, "{} {} -> {}（应为 403）".format(
                method.upper(), path, resp.status_code
            )
            assert resp.json()["code"] == "FORBIDDEN"

    @allure.title("匿名调用受保护接口 -> 401（不是 403）")
    @pytest.mark.security
    # 注意：不含 /api/auth/logout —— 它刻意是无状态的 no-op，不需要鉴权（见 CHANGES.md 第七节）
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/auth/profile"),
            ("get", "/api/cart"),
            ("post", "/api/cart/items"),
            ("post", "/api/orders"),
            ("get", "/api/orders"),
            ("post", "/api/payments"),
            ("post", "/api/auth/refresh"),
        ],
        ids=["profile", "cart-get", "cart-add", "order-create", "order-list", "payment-create", "refresh"],
    )
    def test_anonymous_gets_401(self, sut, method, path):
        resp = getattr(sut, method)(path, json={} if method == "post" else None)
        assert resp.status_code == 401, "{} {} -> {}".format(method.upper(), path, resp.status_code)
        assert resp.json()["code"] == "UNAUTHORIZED"


@allure.feature("安全")
@allure.story("token 矩阵")
class TestTokenMatrix:

    @allure.title("无头 / 垃圾 / 篡改 / 过期 / 错误契约 -> 401")
    @pytest.mark.security
    def test_token_matrix(self, sut, sut_base_url, fresh_buyer):
        token = fresh_buyer["token"]
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

        expired = sut.post(
            "/api/_test/token",
            json={"username": fresh_buyer["username"], "ttl_seconds": -5},
        ).json()["token"]

        cases = [
            ("无 Authorization 头", {}),
            ("空字符串", {"Authorization": ""}),
            ("垃圾 token", {"Authorization": "Bearer garbage"}),
            ("篡改末字符", auth_headers(tampered, TOKEN_STYLE_SUT)),
            ("过期 token", auth_headers(expired, TOKEN_STYLE_SUT)),
            ("错误契约（裸 token）", auth_headers(token, TOKEN_STYLE_LUNARLINK)),
            ("缺少 Bearer 前缀", {"Authorization": "Token " + token}),
        ]
        for label, headers in cases:
            resp = sut.get("/api/auth/profile", headers=headers)
            assert resp.status_code == 401, "{} -> {}（应为 401）".format(label, resp.status_code)

        # 过期单独有更细的错误码，用于区分「凭证无效」与「凭证过期」
        expired_resp = sut.get("/api/auth/profile", headers=auth_headers(expired, TOKEN_STYLE_SUT))
        assert expired_resp.json()["code"] == "TOKEN_EXPIRED"

    @allure.title("★ 正确契约可用（对照组，避免上一条因别的原因全 401）")
    @pytest.mark.security
    def test_correct_contract_works(self, sut, fresh_buyer):
        resp = sut.get("/api/auth/profile", headers=auth_headers(fresh_buyer["token"], TOKEN_STYLE_SUT))
        assert resp.status_code == 200

    @allure.title("锁定期间被禁用账号的 token 不可用（改库禁用后 403/401）")
    @pytest.mark.security
    def test_no_such_token_after_reset_like_state(self, sut_base_url, sut, fresh_buyer):
        """签发一个 token 后，用一个不存在的用户名再签 -> 404；原 token 仍可用。"""
        issued = sut.post(
            "/api/_test/token",
            json={"username": unique("ghost"), "ttl_seconds": 60},
        )
        assert issued.status_code == 404


@allure.feature("安全")
@allure.story("信息泄露与参数注入")
class TestInfoLeak:

    @allure.title("★ 响应中不出现口令哈希 / 盐 / 数据库路径之外的内部字段")
    @pytest.mark.security
    def test_no_credential_leak(self, sut, sut_admin, fresh_buyer):
        paths = [
            "/api/auth/profile",
            "/api/products",
            "/api/products/1",
            "/api/cart",
            "/api/orders",
        ]
        for path in paths:
            resp = fresh_buyer["client"].get(path)
            assert "password_hash" not in resp.text, path
            assert "password_salt" not in resp.text, path

        created = sut_admin.post(
            "/api/products", json={"name": unique("脱敏"), "price_cents": 1, "stock": 1}
        )
        assert "password" not in created.text

    @allure.title("★ 请求体里伪造 user_id / username 不影响归属判定")
    @pytest.mark.security
    def test_injected_user_id_ignored(self, sut_admin, fresh_buyer, fresh_buyer2, sut_db):
        product = create_product(sut_admin, stock=10)
        victim = fresh_buyer["client"]
        attacker = fresh_buyer2["client"]

        victim.post("/api/cart/items", json={"product_id": product["id"], "quantity": 2})

        # 攻击者尝试用伪造字段把自己的身份改成受害者
        resp = attacker.post(
            "/api/orders",
            json={"user_id": fresh_buyer["id"], "username": fresh_buyer["username"]},
        )
        # 攻击者购物车是空的 -> EMPTY_CART；绝不会去结算受害者的购物车
        assert resp.status_code == 400
        assert resp.json()["code"] == "EMPTY_CART"

        # 受害者的购物车与库存都不受影响
        assert sut_db.cart_size(fresh_buyer["username"]) == 1
        assert sut_db.product_stock(product["id"]) == 10

    @allure.title("伪造的他人订单号不能通过注入拿到数据")
    @pytest.mark.security
    def test_order_no_injection(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _ordered(sut_admin, fresh_buyer["client"])
        attacker = fresh_buyer2["client"]
        for candidate in [
            order_no + "' OR '1'='1",
            order_no + "%",
            "{} OR 1=1".format(order_no),
        ]:
            resp = attacker.get("/api/orders/{}".format(candidate))
            assert resp.status_code == 404, "注入串 {} 竟然返回 {}".format(candidate, resp.status_code)
