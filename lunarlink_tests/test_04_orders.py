# -*- coding: utf-8 -*-
"""订单模块用例：下单扣库存、状态机矩阵、取消回滚库存、列表隔离、越权。

★ 状态机是本文件的重点：用状态流转白名单驱动一个矩阵，合法流转断言 200、
  非法流转断言 409 + from/to/allowed，而不是零散地写几条。
"""

import allure
import pytest

from utils.data import create_product, unique

# 状态机白名单（与 sut/config.py 的 ORDER_STATE_TRANSITIONS 对齐）
# ★ REFUNDED 不在下面：本次改造按约定跳过了退款，该状态不可达；
#   把它写进矩阵会让用例依赖一个不存在的构造路径（那是假断言）。
ALLOWED = {
    "PENDING": {"PAID", "CANCELLED"},
    "PAID": {"SHIPPED"},
    "SHIPPED": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


def _add(client, product_id, quantity):
    return client.post("/api/cart/items", json={"product_id": product_id, "quantity": quantity})


def _new_order(sut_admin, buyer_client, price_cents=1000, stock=10, quantity=1):
    """造一个全新商品 + 下单，返回 (product, order_no)。每个用例独立数据。"""
    product = create_product(sut_admin, price_cents=price_cents, stock=stock)
    assert _add(buyer_client, product["id"], quantity).status_code in (200, 201)
    resp = buyer_client.post("/api/orders", json={})
    assert resp.status_code == 201, resp.text[:300]
    return product, resp.json()["order_no"]


def _pay(buyer_client, order_no):
    """把订单推到 PAID（走真实支付回调）。"""
    order = buyer_client.get("/api/orders/{}".format(order_no)).json()
    created = buyer_client.post("/api/payments", json={"order_no": order_no})
    pay_no = created.json()["payment"]["pay_no"]
    resp = buyer_client.post(
        "/api/payments/{}/callback".format(pay_no),
        json={"notify_id": unique("notify"), "amount_cents": order["total_amount_cents"]},
    )
    assert resp.status_code == 200, resp.text[:300]
    return pay_no


def _advance_to(sut_admin, buyer_client, target):
    """把订单驱动到指定状态，返回 (product, order_no)。"""
    product, order_no = _new_order(sut_admin, buyer_client)
    if target == "PENDING":
        return product, order_no
    if target == "CANCELLED":
        assert buyer_client.post("/api/orders/{}/cancel".format(order_no)).status_code == 200
        return product, order_no
    _pay(buyer_client, order_no)
    if target == "PAID":
        return product, order_no
    assert sut_admin.post("/api/orders/{}/ship".format(order_no)).status_code == 200
    if target == "SHIPPED":
        return product, order_no
    assert buyer_client.post("/api/orders/{}/confirm".format(order_no)).status_code == 200
    return product, order_no


@allure.feature("订单")
@allure.story("下单")
class TestCreateOrder:

    @allure.title("空购物车下单 -> 400 EMPTY_CART")
    @pytest.mark.negative
    def test_empty_cart(self, fresh_buyer):
        resp = fresh_buyer["client"].post("/api/orders", json={})
        assert resp.status_code == 400
        assert resp.json()["code"] == "EMPTY_CART"

    @allure.title("★ 下单成功：201 + 扣库存（落库校验）+ 清空购物车")
    @pytest.mark.smoke
    def test_create_order_deducts_stock(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, price_cents=3900, stock=10)
        client = fresh_buyer["client"]
        _add(client, product["id"], 6)

        resp = client.post("/api/orders", json={})
        assert resp.status_code == 201, resp.text[:300]
        body = resp.json()
        assert body["status"] == "PENDING"
        assert body["total_amount_cents"] == 3900 * 6
        assert body["expire_at"] > 0

        # 落库校验
        assert sut_db.product_stock(product["id"]) == 4
        assert sut_db.order_status(body["order_no"]) == "PENDING"
        assert sut_db.cart_size(fresh_buyer["username"]) == 0

    @allure.title("订单行保留下单时的商品名与单价快照")
    @pytest.mark.regression
    def test_order_items_snapshot(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, price_cents=2500, stock=10)
        client = fresh_buyer["client"]
        _add(client, product["id"], 3)
        order_no = client.post("/api/orders", json={}).json()["order_no"]

        items = sut_db.order_items(order_no)
        assert len(items) == 1
        assert items[0]["product_name"] == product["name"]
        assert items[0]["unit_price_cents"] == 2500
        assert items[0]["subtotal_cents"] == 7500

    @allure.title("多商品下单，总额为各小计之和")
    @pytest.mark.regression
    def test_multi_item_order(self, sut_admin, fresh_buyer):
        p1 = create_product(sut_admin, price_cents=1000, stock=10)
        p2 = create_product(sut_admin, price_cents=2000, stock=10)
        client = fresh_buyer["client"]
        _add(client, p1["id"], 2)
        _add(client, p2["id"], 3)

        body = client.post("/api/orders", json={}).json()
        assert len(body["items"]) == 2
        assert body["total_amount_cents"] == 1000 * 2 + 2000 * 3

    @allure.title("★ 库存不足 -> 409，且【已扣减的部分要回滚】")
    @pytest.mark.boundary
    def test_insufficient_stock_rolls_back(self, sut_admin, fresh_buyer, sut_db):
        enough = create_product(sut_admin, stock=10)
        scarce = create_product(sut_admin, stock=1)
        client = fresh_buyer["client"]
        _add(client, enough["id"], 2)   # 先加一个库存充足的
        _add(client, scarce["id"], 1)   # 再加一个库存刚好 1 的

        # 把 scarce 的库存降到 0（模拟被别人抢走），此时下单必须在它身上失败
        sut_admin.put("/api/products/{}".format(scarce["id"]), json={"stock": 0})

        resp = client.post("/api/orders", json={})
        assert resp.status_code == 409, resp.text[:300]
        assert resp.json()["code"] == "INSUFFICIENT_STOCK"

        # ★ 关键断言：前面那个商品的库存不能被扣掉（事务回滚）
        assert sut_db.product_stock(enough["id"]) == 10, "事务未回滚，库存被扣了"
        assert sut_db.cart_size(fresh_buyer["username"]) == 2, "下单失败不应该清空购物车"

    @allure.title("未登录下单 -> 401")
    @pytest.mark.security
    def test_create_unauthenticated(self, sut):
        assert sut.post("/api/orders", json={}).status_code == 401


@allure.feature("订单")
@allure.story("列表与详情")
class TestOrderQuery:

    @allure.title("我的订单列表包含刚下的单")
    @pytest.mark.smoke
    def test_list_contains_order(self, sut_admin, fresh_buyer):
        _, order_no = _new_order(sut_admin, fresh_buyer["client"])
        body = fresh_buyer["client"].get("/api/orders").json()
        assert any(o["order_no"] == order_no for o in body["items"])

    @allure.title("★ 列表按状态过滤")
    @pytest.mark.regression
    def test_list_filter_by_status(self, sut_admin, fresh_buyer):
        client = fresh_buyer["client"]
        _, pending = _new_order(sut_admin, client)
        _, cancelled = _advance_to(sut_admin, client, "CANCELLED")

        pending_list = client.get("/api/orders", params={"status": "PENDING"}).json()["items"]
        cancelled_list = client.get("/api/orders", params={"status": "CANCELLED"}).json()["items"]

        assert any(o["order_no"] == pending for o in pending_list)
        assert not any(o["order_no"] == cancelled for o in pending_list)
        assert any(o["order_no"] == cancelled for o in cancelled_list)

    @allure.title("非法 status -> 400 INVALID_STATUS")
    @pytest.mark.negative
    def test_list_invalid_status(self, fresh_buyer):
        resp = fresh_buyer["client"].get("/api/orders", params={"status": "NOT_A_STATUS"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_STATUS"

    @allure.title("列表分页参数非法 -> 400")
    @pytest.mark.boundary
    def test_list_invalid_pagination(self, fresh_buyer):
        assert fresh_buyer["client"].get("/api/orders", params={"page": 0}).status_code == 400
        assert fresh_buyer["client"].get("/api/orders", params={"size": 101}).status_code == 400

    @allure.title("详情 -> 200，含订单行")
    @pytest.mark.smoke
    def test_detail(self, sut_admin, fresh_buyer):
        _, order_no = _new_order(sut_admin, fresh_buyer["client"])
        resp = fresh_buyer["client"].get("/api/orders/{}".format(order_no))
        assert resp.status_code == 200
        assert resp.json()["order_no"] == order_no
        assert len(resp.json()["items"]) == 1

    @allure.title("详情不存在 -> 404")
    @pytest.mark.negative
    def test_detail_not_found(self, fresh_buyer):
        resp = fresh_buyer["client"].get("/api/orders/NO_SUCH_ORDER")
        assert resp.status_code == 404
        assert resp.json()["code"] == "ORDER_NOT_FOUND"

    @allure.title("状态查询 -> 200")
    @pytest.mark.smoke
    def test_status_endpoint(self, sut_admin, fresh_buyer):
        _, order_no = _new_order(sut_admin, fresh_buyer["client"])
        resp = fresh_buyer["client"].get("/api/orders/{}/status".format(order_no))
        assert resp.status_code == 200
        assert resp.json()["status"] == "PENDING"

    @allure.title("★ 数据隔离：看不到别人的订单（列表与详情）")
    @pytest.mark.security
    def test_orders_not_visible_to_others(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _new_order(sut_admin, fresh_buyer["client"])
        others = fresh_buyer2["client"]
        assert not any(o["order_no"] == order_no for o in others.get("/api/orders").json()["items"])
        assert others.get("/api/orders/{}".format(order_no)).status_code == 404
        assert others.get("/api/orders/{}/status".format(order_no)).status_code == 404


@allure.feature("订单")
@allure.story("取消与库存回滚")
class TestCancelOrder:

    @allure.title("★ PENDING 取消 -> 200，且库存回滚（落库校验）")
    @pytest.mark.smoke
    def test_cancel_restores_stock(self, sut_admin, fresh_buyer, sut_db):
        product = create_product(sut_admin, stock=10)
        client = fresh_buyer["client"]
        _add(client, product["id"], 4)
        order_no = client.post("/api/orders", json={}).json()["order_no"]
        assert sut_db.product_stock(product["id"]) == 6

        resp = client.post("/api/orders/{}/cancel".format(order_no))
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"
        assert resp.json()["cancelled_at"] is not None

        assert sut_db.product_stock(product["id"]) == 10, "取消后库存没有回滚"
        assert sut_db.order_status(order_no) == "CANCELLED"

    @allure.title("已取消再取消 -> 409 INVALID_STATE_TRANSITION")
    @pytest.mark.negative
    def test_cancel_twice(self, sut_admin, fresh_buyer):
        product, order_no = _advance_to(sut_admin, fresh_buyer["client"], "CANCELLED")
        resp = fresh_buyer["client"].post("/api/orders/{}/cancel".format(order_no))
        assert resp.status_code == 409
        assert resp.json()["code"] == "INVALID_STATE_TRANSITION"

    @allure.title("已支付取消 -> 409")
    @pytest.mark.negative
    def test_cancel_after_paid(self, sut_admin, fresh_buyer):
        _, order_no = _advance_to(sut_admin, fresh_buyer["client"], "PAID")
        assert fresh_buyer["client"].post("/api/orders/{}/cancel".format(order_no)).status_code == 409

    @allure.title("取消不存在的订单 -> 404")
    @pytest.mark.negative
    def test_cancel_not_found(self, fresh_buyer):
        assert fresh_buyer["client"].post("/api/orders/NOPE/cancel").status_code == 404


@allure.feature("订单")
@allure.story("★ 状态机矩阵")
class TestOrderStateMachine:

    @allure.title("合法流转 -> 200")
    @pytest.mark.regression
    @pytest.mark.parametrize(
        "from_state,action",
        [("PENDING", "cancel"), ("PAID", "ship"), ("SHIPPED", "confirm")],
        ids=["PENDING->CANCELLED", "PAID->SHIPPED", "SHIPPED->COMPLETED"],
    )
    def test_legal_transitions(self, sut_admin, fresh_buyer, from_state, action):
        _, order_no = _advance_to(sut_admin, fresh_buyer["client"], from_state)
        client = sut_admin if action == "ship" else fresh_buyer["client"]
        resp = client.post("/api/orders/{}/{}".format(order_no, action))
        assert resp.status_code == 200, resp.text[:250]

    @allure.title("★ 非法流转 -> 409，且 detail 回传 from/to/allowed")
    @pytest.mark.negative
    @pytest.mark.parametrize(
        "from_state,action",
        [
            ("PENDING", "confirm"),
            ("PAID", "confirm"),
            ("COMPLETED", "cancel"),
            ("COMPLETED", "confirm"),
            ("CANCELLED", "confirm"),
            ("CANCELLED", "ship"),
            ("SHIPPED", "cancel"),
        ],
        ids=[
            "PENDING-confirm",
            "PAID-confirm",
            "COMPLETED-cancel",
            "COMPLETED-confirm",
            "CANCELLED-confirm",
            "CANCELLED-ship",
            "SHIPPED-cancel",
        ],
    )
    def test_illegal_transitions(self, sut_admin, fresh_buyer, from_state, action):
        _, order_no = _advance_to(sut_admin, fresh_buyer["client"], from_state)
        client = sut_admin if action == "ship" else fresh_buyer["client"]
        resp = client.post("/api/orders/{}/{}".format(order_no, action))

        assert resp.status_code == 409, resp.text[:250]
        assert resp.json()["code"] == "INVALID_STATE_TRANSITION"
        detail = resp.json()["detail"]
        assert detail["from"] == from_state
        assert "to" in detail and "allowed" in detail, "非法流转应回传 from/to/allowed 便于观测"

    @allure.title("状态机白名单与实现一致（防单测与实现漂移）")
    @pytest.mark.regression
    def test_whitelist_matches_implementation(self, sut_admin, fresh_buyer):
        """把 ALLOWED 这个本地副本与实现对齐：逐条验证 409 与 200。"""
        client = fresh_buyer["client"]
        for from_state, allowed in ALLOWED.items():
            for action, to_state in (("cancel", "CANCELLED"), ("confirm", "COMPLETED"), ("ship", "SHIPPED")):
                _, order_no = _advance_to(sut_admin, client, from_state)
                actor = sut_admin if action == "ship" else client
                resp = actor.post("/api/orders/{}/{}".format(order_no, action))
                if to_state in allowed:
                    assert resp.status_code == 200, "{} -> {} 应合法: {}".format(from_state, to_state, resp.text[:150])
                else:
                    assert resp.status_code == 409, "{} -> {} 应非法: {}".format(from_state, to_state, resp.text[:150])


@allure.feature("订单")
@allure.story("发货与确认收货")
class TestShipAndConfirm:

    @allure.title("普通用户发货 -> 403（垂直越权）")
    @pytest.mark.security
    def test_ship_forbidden_for_customer(self, sut_admin, fresh_buyer):
        _, order_no = _advance_to(sut_admin, fresh_buyer["client"], "PAID")
        resp = fresh_buyer["client"].post("/api/orders/{}/ship".format(order_no))
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"

    @allure.title("admin 发货 -> 200 PAID->SHIPPED；随后确认收货 -> 200 COMPLETED")
    @pytest.mark.smoke
    def test_ship_then_confirm(self, sut_admin, fresh_buyer, sut_db):
        _, order_no = _advance_to(sut_admin, fresh_buyer["client"], "PAID")
        resp = sut_admin.post("/api/orders/{}/ship".format(order_no))
        assert resp.status_code == 200 and resp.json()["status"] == "SHIPPED"

        resp = fresh_buyer["client"].post("/api/orders/{}/confirm".format(order_no))
        assert resp.status_code == 200 and resp.json()["status"] == "COMPLETED"
        assert resp.json()["completed_at"] is not None
        assert sut_db.order_status(order_no) == "COMPLETED"

    @allure.title("发货不存在的订单 -> 404")
    @pytest.mark.negative
    def test_ship_not_found(self, sut_admin):
        assert sut_admin.post("/api/orders/NOPE/ship").status_code == 404

    @allure.title("★ 越权矩阵：他人订单的取消/确认/发货")
    @pytest.mark.security
    def test_cross_user_actions(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no = _new_order(sut_admin, fresh_buyer["client"])
        other = fresh_buyer2["client"]
        assert other.post("/api/orders/{}/cancel".format(order_no)).status_code == 404
        assert other.post("/api/orders/{}/confirm".format(order_no)).status_code == 404
        # 发货是 admin 专属 + 跨归属入口，普通用户先被 403 拦掉
        assert other.post("/api/orders/{}/ship".format(order_no)).status_code == 403
