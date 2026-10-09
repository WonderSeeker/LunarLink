# -*- coding: utf-8 -*-
"""购物车模块用例：数量与库存校验、同商品合并、越权、清空。

每个用例都用 fresh_buyer（全新 uuid 用户）——购物车是 per-user 有状态资源，
用 session 级买家会互相污染。这正是「靠造数不靠 reset」的落地。
"""

import allure
import pytest

from utils.data import create_product, unique


def _add(client, product_id, quantity):
    return client.post("/api/cart/items", json={"product_id": product_id, "quantity": quantity})


@allure.feature("购物车")
@allure.story("加购与校验")
class TestAddToCart:

    @allure.title("首次加购 -> 201 created=true，落库可见")
    @pytest.mark.smoke
    def test_first_add(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, price_cents=1000, stock=10)
        resp = _add(fresh_buyer["client"], product["id"], 2)
        assert resp.status_code == 201
        assert resp.json()["created"] is True
        assert resp.json()["item"]["quantity"] == 2
        assert sut_db.cart_size(fresh_buyer["username"]) == 1

    @allure.title("★ 重复加购同一商品 -> 200 且数量合并")
    @pytest.mark.regression
    def test_merge_same_product(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, price_cents=3900, stock=10)
        client = fresh_buyer["client"]

        assert _add(client, product["id"], 2).status_code == 201
        resp = _add(client, product["id"], 3)
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json()["created"] is False
        assert resp.json()["item"]["quantity"] == 5

        # 落库校验：只有一行，且数量是 5
        assert sut_db.cart_size(fresh_buyer["username"]) == 1
        rows = sut_db.query(
            "SELECT c.quantity FROM cart_items c JOIN users u ON u.id = c.user_id WHERE u.username = ?",
            (fresh_buyer["username"],),
        )
        assert rows[0]["quantity"] == 5

    @allure.title("明细与小计正确")
    @pytest.mark.regression
    def test_cart_totals(self, sut_admin, fresh_buyer):
        p1 = create_product(sut_admin, price_cents=1000, stock=10)
        p2 = create_product(sut_admin, price_cents=2500, stock=10)
        client = fresh_buyer["client"]
        _add(client, p1["id"], 2)
        _add(client, p2["id"], 3)

        body = client.get("/api/cart").json()
        assert body["item_count"] == 2
        assert body["total_amount_cents"] == 1000 * 2 + 2500 * 3
        for item in body["items"]:
            assert item["subtotal_cents"] == item["price_cents"] * item["quantity"]

    @allure.title("数量边界：0 / 负数 -> 400 INVALID_QUANTITY")
    @pytest.mark.boundary
    @pytest.mark.parametrize("quantity", [0, -1, -100], ids=["zero", "neg1", "neg100"])
    def test_invalid_quantity(self, sut_admin, fresh_buyer, quantity):
        product = create_product(sut_admin, stock=10)
        resp = _add(fresh_buyer["client"], product["id"], quantity)
        assert resp.status_code == 400, resp.text[:200]
        assert resp.json()["code"] == "INVALID_QUANTITY"

    @allure.title("加购不存在的商品 -> 404")
    @pytest.mark.negative
    def test_add_unknown_product(self, fresh_buyer):
        resp = _add(fresh_buyer["client"], 999999, 1)
        assert resp.status_code == 404
        assert resp.json()["code"] == "PRODUCT_NOT_FOUND"

    @allure.title("★ 数量超过库存 -> 409 INSUFFICIENT_STOCK")
    @pytest.mark.boundary
    def test_add_over_stock(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin, stock=3)
        resp = _add(fresh_buyer["client"], product["id"], 4)
        assert resp.status_code == 409, resp.text[:200]
        assert resp.json()["code"] == "INSUFFICIENT_STOCK"

    @allure.title("★ 恰好等于库存 -> 201；合并后超过库存 -> 409")
    @pytest.mark.boundary
    def test_stock_boundary_and_merge_overflow(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin, stock=3)
        client = fresh_buyer["client"]
        assert _add(client, product["id"], 3).status_code == 201  # 恰好等于库存

        resp = _add(client, product["id"], 1)  # 3+1=4 > 3
        assert resp.status_code == 409
        assert resp.json()["code"] == "INSUFFICIENT_STOCK"
        assert resp.json()["detail"]["in_cart"] == 3

    @allure.title("未登录加购 -> 401")
    @pytest.mark.security
    def test_add_unauthenticated(self, sut, sut_admin):
        product = create_product(sut_admin)
        assert _add(sut, product["id"], 1).status_code == 401

    @allure.title("数量类型不对 -> 422")
    @pytest.mark.negative
    def test_quantity_type_error(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin)
        resp = fresh_buyer["client"].post(
            "/api/cart/items", json={"product_id": product["id"], "quantity": "abc"}
        )
        assert resp.status_code == 422


@allure.feature("购物车")
@allure.story("修改、删除与越权")
class TestModifyCart:

    @allure.title("修改数量 -> 200 且生效")
    @pytest.mark.smoke
    def test_update_quantity(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin, stock=10)
        client = fresh_buyer["client"]
        item_id = _add(client, product["id"], 1).json()["item"]["id"]

        resp = client.put("/api/cart/items/{}".format(item_id), json={"quantity": 7})
        assert resp.status_code == 200
        assert resp.json()["item"]["quantity"] == 7

    @allure.title("修改数量为 0 / 超库存 -> 400 / 409")
    @pytest.mark.boundary
    def test_update_invalid_quantity(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin, stock=5)
        client = fresh_buyer["client"]
        item_id = _add(client, product["id"], 1).json()["item"]["id"]

        assert client.put("/api/cart/items/{}".format(item_id), json={"quantity": 0}).status_code == 400
        resp = client.put("/api/cart/items/{}".format(item_id), json={"quantity": 6})
        assert resp.status_code == 409

    @allure.title("★ 修改【他人】购物车条目 -> 404（水平越权）")
    @pytest.mark.security
    def test_update_others_item(self, sut_admin, fresh_buyer, fresh_buyer2):
        product = create_product(sut_admin, stock=10)
        item_id = _add(fresh_buyer["client"], product["id"], 1).json()["item"]["id"]

        resp = fresh_buyer2["client"].put("/api/cart/items/{}".format(item_id), json={"quantity": 9})
        assert resp.status_code == 404
        assert resp.json()["code"] == "CART_ITEM_NOT_FOUND"

    @allure.title("★ 删除【他人】购物车条目 -> 404")
    @pytest.mark.security
    def test_delete_others_item(self, sut_admin, fresh_buyer, fresh_buyer2):
        product = create_product(sut_admin, stock=10)
        item_id = _add(fresh_buyer["client"], product["id"], 1).json()["item"]["id"]
        assert fresh_buyer2["client"].delete("/api/cart/items/{}".format(item_id)).status_code == 404

    @allure.title("删除自己的条目 -> 204，重复删除 -> 404")
    @pytest.mark.regression
    def test_delete_item(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, stock=10)
        client = fresh_buyer["client"]
        item_id = _add(client, product["id"], 1).json()["item"]["id"]

        assert client.delete("/api/cart/items/{}".format(item_id)).status_code == 204
        assert sut_db.cart_size(fresh_buyer["username"]) == 0
        assert client.delete("/api/cart/items/{}".format(item_id)).status_code == 404

    @allure.title("清空购物车 -> 204，之后为空数组")
    @pytest.mark.regression
    def test_clear_cart(self, sut_admin, fresh_buyer, sut_db):
        client = fresh_buyer["client"]
        for _ in range(3):
            _add(client, create_product(sut_admin)["id"], 1)
        assert client.get("/api/cart").json()["item_count"] == 3

        assert client.delete("/api/cart").status_code == 204
        body = client.get("/api/cart").json()
        assert body["items"] == []
        assert body["total_amount_cents"] == 0
        assert sut_db.cart_size(fresh_buyer["username"]) == 0

    @allure.title("空购物车查询 -> 200 空结构")
    def test_empty_cart(self, fresh_buyer):
        body = fresh_buyer["client"].get("/api/cart").json()
        assert body == {"items": [], "item_count": 0, "total_amount_cents": 0}

    @allure.title("下架商品仍在购物车里时，购物车仍可查看（只是不能被下单）")
    @pytest.mark.regression
    def test_cart_with_deleted_product(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin, stock=5)
        client = fresh_buyer["client"]
        _add(client, product["id"], 1)
        sut_admin.delete("/api/products/{}".format(product["id"]))

        resp = client.get("/api/cart")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["status"] == 0

        order = client.post("/api/orders", json={})
        assert order.status_code == 409
        assert order.json()["code"] == "PRODUCT_UNAVAILABLE"

    @allure.title("两个用户购物车互不可见")
    @pytest.mark.security
    def test_carts_are_isolated(self, sut_admin, fresh_buyer, fresh_buyer2):
        product = create_product(sut_admin, stock=10)
        _add(fresh_buyer["client"], product["id"], 5)
        assert fresh_buyer["client"].get("/api/cart").json()["item_count"] == 1
        assert fresh_buyer2["client"].get("/api/cart").json()["item_count"] == 0
