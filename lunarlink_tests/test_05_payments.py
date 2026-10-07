# -*- coding: utf-8 -*-
"""支付模块用例：创建支付单（幂等复用）+ ★ 幂等回调 + 金额校验 + 越权。

★ 幂等的证明方式（不只是断言返回值）：
    * 重复回调后 payments.paid_at / amount_paid_cents **不变**（落库对比）
    * payment_callbacks 只有 1 行
    * 不同 notify_id 的二次回调 -> 409，且不产生第 2 行回调记录
"""

import allure
import pytest

from utils.data import create_product, unique


def _ordered(sut_admin, buyer_client, price_cents=1000, stock=10, quantity=1):
    product = create_product(sut_admin, price_cents=price_cents, stock=stock)
    buyer_client.post("/api/cart/items", json={"product_id": product["id"], "quantity": quantity})
    order = buyer_client.post("/api/orders", json={}).json()
    return product, order["order_no"], order["total_amount_cents"]


@allure.feature("支付")
@allure.story("创建支付单")
class TestCreatePayment:

    @allure.title("创建支付单 -> 201，金额等于订单总额")
    @pytest.mark.smoke
    def test_create(self, sut_admin, fresh_buyer):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        resp = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no})
        assert resp.status_code == 201, resp.text[:250]
        assert resp.json()["created"] is True
        assert resp.json()["payment"]["amount_cents"] == amount
        assert resp.json()["payment"]["status"] == "PENDING"

    @allure.title("★ 同一订单重复创建 -> 200 且返回同一个支付单（不重复建单）")
    @pytest.mark.regression
    def test_create_is_idempotent_per_order(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, _ = _ordered(sut_admin, fresh_buyer["client"])
        client = fresh_buyer["client"]
        first = client.post("/api/payments", json={"order_no": order_no})
        second = client.post("/api/payments", json={"order_no": order_no})

        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["payment"]["pay_no"] == first.json()["payment"]["pay_no"]
        count = sut_db.scalar("SELECT COUNT(*) FROM payments WHERE order_no = ?", (order_no,))
        assert count == 1

    @allure.title("★ 为【他人】订单创建支付单 -> 404（水平越权）")
    @pytest.mark.security
    def test_create_for_others_order(self, sut_admin, fresh_buyer, fresh_buyer2):
        _, order_no, _ = _ordered(sut_admin, fresh_buyer["client"])
        resp = fresh_buyer2["client"].post("/api/payments", json={"order_no": order_no})
        assert resp.status_code == 404
        assert resp.json()["code"] == "ORDER_NOT_FOUND"

    @allure.title("订单不存在 -> 404")
    @pytest.mark.negative
    def test_create_for_unknown_order(self, fresh_buyer):
        assert fresh_buyer["client"].post("/api/payments", json={"order_no": "NOPE"}).status_code == 404

    @allure.title("已取消订单不可支付 -> 409 ORDER_NOT_PAYABLE")
    @pytest.mark.negative
    def test_create_for_cancelled_order(self, sut_admin, fresh_buyer):
        _, order_no, _ = _ordered(sut_admin, fresh_buyer["client"])
        client = fresh_buyer["client"]
        client.post("/api/orders/{}/cancel".format(order_no))
        resp = client.post("/api/payments", json={"order_no": order_no})
        assert resp.status_code == 409
        assert resp.json()["code"] == "ORDER_NOT_PAYABLE"

    @allure.title("查询支付单：本人 200 / 他人 404 / 不存在 404")
    @pytest.mark.security
    def test_query_payment(self, sut_admin, fresh_buyer, fresh_buyer2, sut_db):
        _, order_no, _ = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]

        mine = fresh_buyer["client"].get("/api/payments/{}".format(pay_no))
        assert mine.status_code == 200
        assert mine.json()["payment"]["pay_no"] == pay_no

        assert fresh_buyer2["client"].get("/api/payments/{}".format(pay_no)).status_code == 404
        assert fresh_buyer["client"].get("/api/payments/NOPE").status_code == 404
        assert sut_db.payment(pay_no)["status"] == "PENDING"


@allure.feature("支付")
@allure.story("★ 幂等回调")
class TestPaymentCallback:

    @allure.title("金额不匹配 -> 400 AMOUNT_MISMATCH，支付单保持 PENDING")
    @pytest.mark.negative
    def test_amount_mismatch(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]

        resp = fresh_buyer["client"].post(
            "/api/payments/{}/callback".format(pay_no),
            json={"notify_id": unique("notify"), "amount_cents": amount - 1},
        )
        assert resp.status_code == 400, resp.text[:250]
        assert resp.json()["code"] == "AMOUNT_MISMATCH"
        assert sut_db.payment(pay_no)["status"] == "PENDING"
        assert sut_db.payment_callback_count(pay_no) == 0

    @allure.title("notify_id 为空 -> 400")
    @pytest.mark.negative
    def test_empty_notify_id(self, sut_admin, fresh_buyer):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
        resp = fresh_buyer["client"].post(
            "/api/payments/{}/callback".format(pay_no),
            json={"notify_id": "  ", "amount_cents": amount},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_NOTIFY_ID"

    @allure.title("支付单不存在 -> 404")
    @pytest.mark.negative
    def test_callback_unknown_payment(self, fresh_buyer):
        resp = fresh_buyer["client"].post(
            "/api/payments/NOPE/callback", json={"notify_id": unique("n"), "amount_cents": 100}
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "PAYMENT_NOT_FOUND"

    @allure.title("首次回调 -> 200 duplicated=false，订单变 PAID（落库校验）")
    @pytest.mark.smoke
    def test_first_callback_settles(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]

        resp = fresh_buyer["client"].post(
            "/api/payments/{}/callback".format(pay_no),
            json={"notify_id": unique("notify"), "amount_cents": amount},
        )
        assert resp.status_code == 200, resp.text[:250]
        assert resp.json()["duplicated"] is False
        assert resp.json()["order_status"] == "PAID"

        assert sut_db.payment(pay_no)["status"] == "PAID"
        assert sut_db.payment(pay_no)["amount_paid_cents"] == amount
        assert sut_db.payment(pay_no)["paid_at"] is not None
        assert sut_db.order_status(order_no) == "PAID"
        assert sut_db.payment_callback_count(pay_no) == 1

    @allure.title("★ 同 notify_id 重复回调：duplicated=true，且【任何字段都不变】")
    @pytest.mark.regression
    def test_same_notify_id_is_idempotent(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
        notify_id = unique("notify")
        client = fresh_buyer["client"]

        first = client.post("/api/payments/{}/callback".format(pay_no),
                            json={"notify_id": notify_id, "amount_cents": amount})
        assert first.status_code == 200 and first.json()["duplicated"] is False

        paid_at_before = sut_db.payment(pay_no)["paid_at"]
        amount_before = sut_db.payment(pay_no)["amount_paid_cents"]

        second = client.post("/api/payments/{}/callback".format(pay_no),
                             json={"notify_id": notify_id, "amount_cents": amount})
        assert second.status_code == 200, second.text[:250]
        assert second.json()["duplicated"] is True

        # ★ 三项落库断言：不重复入账
        assert sut_db.payment(pay_no)["paid_at"] == paid_at_before, "重复回调改了 paid_at"
        assert sut_db.payment(pay_no)["amount_paid_cents"] == amount_before
        assert sut_db.payment_callback_count(pay_no) == 1, "重复回调写入了第 2 行回调记录"

        # 订单也只被置为 PAID（不会重复推进）
        assert sut_db.order_status(order_no) == "PAID"

    @allure.title("★ 不同 notify_id 二次回调 -> 409，且不再入账、不留第 2 行记录")
    @pytest.mark.negative
    def test_different_notify_id_rejected(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
        client = fresh_buyer["client"]

        client.post("/api/payments/{}/callback".format(pay_no),
                    json={"notify_id": unique("notify"), "amount_cents": amount})
        paid_at_before = sut_db.payment(pay_no)["paid_at"]

        resp = client.post("/api/payments/{}/callback".format(pay_no),
                           json={"notify_id": unique("notify"), "amount_cents": amount})
        assert resp.status_code == 409, resp.text[:250]
        assert resp.json()["code"] == "INVALID_STATE_TRANSITION"

        assert sut_db.payment(pay_no)["paid_at"] == paid_at_before
        assert sut_db.payment_callback_count(pay_no) == 1, "被拒的回调不应留下记录"

    @allure.title("回调后订单可继续流转：发货 -> 确认收货")
    @pytest.mark.regression
    def test_flow_after_payment(self, sut_admin, fresh_buyer, sut_db):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        client = fresh_buyer["client"]
        pay_no = client.post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
        client.post("/api/payments/{}/callback".format(pay_no),
                    json={"notify_id": unique("notify"), "amount_cents": amount})

        assert sut_admin.post("/api/orders/{}/ship".format(order_no)).status_code == 200
        assert client.post("/api/orders/{}/confirm".format(order_no)).status_code == 200
        assert sut_db.order_status(order_no) == "COMPLETED"

    @allure.title("回调不需要鉴权（网关调用），但支付单不存在仍 404")
    def test_callback_needs_no_auth(self, sut_admin, fresh_buyer):
        _, order_no, amount = _ordered(sut_admin, fresh_buyer["client"])
        pay_no = fresh_buyer["client"].post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
        import requests

        resp = requests.post(
            fresh_buyer["client"].base_url + "/api/payments/{}/callback".format(pay_no),
            json={"notify_id": unique("notify"), "amount_cents": amount},
            timeout=20,
        )
        assert resp.status_code == 200, resp.text[:250]
