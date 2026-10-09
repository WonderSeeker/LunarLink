# -*- coding: utf-8 -*-
"""端到端链路用例：把真实业务串起来跑，用 allure.step 让报告能逐步展开。

覆盖三条链路：
    1. 登录 -> 浏览商品 -> 加购 -> 下单 -> 支付 -> 发货 -> 收货 -> 查订单
    2. 登录 -> 加购 -> 下单 -> 取消 -> 库存回滚
    3. 两个用户并发走完整链路互不干扰
"""

import concurrent.futures

import allure
import pytest

from utils.data import create_product, register, unique


@allure.feature("端到端链路")
@allure.story("电商下单主链路")
class TestEcommerceFlow:

    @allure.title("★ 主链路：浏览 -> 加购 -> 下单 -> 支付 -> 发货 -> 收货")
    @pytest.mark.regression
    def test_full_happy_path(self, sut_admin, sut_base_url, sut_db, fresh_buyer):
        client = fresh_buyer["client"]

        with allure.step("1. 浏览商品列表（分页）"):
            listing = client.get("/api/products", params={"size": 5})
            assert listing.status_code == 200
            assert listing.json()["total"] >= 10

        with allure.step("2. 用 admin 上架一个专属商品"):
            product = create_product(sut_admin, price_cents=3990, stock=5)

        with allure.step("3. 查看商品详情与库存"):
            detail = client.get("/api/products/{}".format(product["id"]))
            assert detail.status_code == 200
            assert detail.json()["stock"] == 5

        with allure.step("4. 加入购物车 2 件"):
            added = client.post("/api/cart/items", json={"product_id": product["id"], "quantity": 2})
            assert added.status_code == 201

        with allure.step("5. 核对购物车小计"):
            cart = client.get("/api/cart").json()
            assert cart["total_amount_cents"] == 3990 * 2

        with allure.step("6. 下单（库存应被扣减）"):
            order = client.post("/api/orders", json={})
            assert order.status_code == 201
            order_no = order.json()["order_no"]
            assert order.json()["status"] == "PENDING"
            assert sut_db.product_stock(product["id"]) == 3

        with allure.step("7. 创建支付单并发起支付回调"):
            payment = client.post("/api/payments", json={"order_no": order_no})
            assert payment.status_code == 201
            pay_no = payment.json()["payment"]["pay_no"]
            callback = client.post(
                "/api/payments/{}/callback".format(pay_no),
                json={"notify_id": unique("e2e"), "amount_cents": order.json()["total_amount_cents"]},
            )
            assert callback.status_code == 200
            assert callback.json()["order_status"] == "PAID"

        with allure.step("8. 重复回调（幂等，不改状态）"):
            replay = client.post(
                "/api/payments/{}/callback".format(pay_no),
                json={"notify_id": unique("e2e-replay"), "amount_cents": order.json()["total_amount_cents"]},
            )
            assert replay.status_code == 409

        with allure.step("9. admin 发货"):
            shipped = sut_admin.post("/api/orders/{}/ship".format(order_no))
            assert shipped.status_code == 200
            assert shipped.json()["status"] == "SHIPPED"

        with allure.step("10. 确认收货"):
            completed = client.post("/api/orders/{}/confirm".format(order_no))
            assert completed.status_code == 200
            assert completed.json()["status"] == "COMPLETED"

        with allure.step("11. 查订单状态与落库一致性"):
            status = client.get("/api/orders/{}/status".format(order_no))
            assert status.status_code == 200
            assert status.json()["status"] == "COMPLETED"
            assert sut_db.order_status(order_no) == "COMPLETED"
            assert sut_db.payment(pay_no)["status"] == "PAID"

    @allure.title("★ 取消链路：加购 -> 下单 -> 取消 -> 库存回滚 + 购物车状态")
    @pytest.mark.regression
    def test_cancel_flow(self, sut_admin, sut_db, fresh_buyer):
        client = fresh_buyer["client"]

        with allure.step("上架商品并记录初始库存"):
            product = create_product(sut_admin, price_cents=1000, stock=8)
            initial = sut_db.product_stock(product["id"])

        with allure.step("加购 3 件并下单"):
            client.post("/api/cart/items", json={"product_id": product["id"], "quantity": 3})
            order_no = client.post("/api/orders", json={}).json()["order_no"]
            assert sut_db.product_stock(product["id"]) == initial - 3

        with allure.step("取消订单"):
            cancelled = client.post("/api/orders/{}/cancel".format(order_no))
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "CANCELLED"

        with allure.step("断言库存已回滚到初始值"):
            assert sut_db.product_stock(product["id"]) == initial

        with allure.step("再次取消应被状态机拒绝"):
            assert client.post("/api/orders/{}/cancel".format(order_no)).status_code == 409

    @allure.title("★ 库存不足链路：下单失败不扣任何库存、不清空购物车")
    @pytest.mark.regression
    def test_out_of_stock_flow(self, sut_admin, sut_db, fresh_buyer):
        client = fresh_buyer["client"]
        with allure.step("准备两个商品，其中一个库存随后被清零"):
            plenty = create_product(sut_admin, stock=10)
            scarce = create_product(sut_admin, stock=5)
            client.post("/api/cart/items", json={"product_id": plenty["id"], "quantity": 2})
            client.post("/api/cart/items", json={"product_id": scarce["id"], "quantity": 1})
            sut_admin.put("/api/products/{}".format(scarce["id"]), json={"stock": 0})

        with allure.step("下单应 409"):
            resp = client.post("/api/orders", json={})
            assert resp.status_code == 409
            assert resp.json()["code"] == "INSUFFICIENT_STOCK"

        with allure.step("断言整体回滚：充足商品库存未变、购物车仍在"):
            assert sut_db.product_stock(plenty["id"]) == 10
            assert sut_db.cart_size(fresh_buyer["username"]) == 2

    @allure.title("★ 两个用户并发走完整链路互不干扰")
    @pytest.mark.regression
    def test_two_users_parallel(self, sut_admin, sut_base_url, sut_db):
        from utils.client import ApiClient, TOKEN_STYLE_SUT

        product = create_product(sut_admin, price_cents=1200, stock=10)

        anonymous = ApiClient(sut_base_url, token=None, token_style=TOKEN_STYLE_SUT, name="e2e注册")
        users = [register(anonymous) for _ in range(2)]

        def run(info):
            client = ApiClient(sut_base_url, info["token"], TOKEN_STYLE_SUT, name=info["username"])
            client.post("/api/cart/items", json={"product_id": product["id"], "quantity": 2})
            order = client.post("/api/orders", json={})
            assert order.status_code == 201
            order_no = order.json()["order_no"]
            pay_no = client.post("/api/payments", json={"order_no": order_no}).json()["payment"]["pay_no"]
            cb = client.post(
                "/api/payments/{}/callback".format(pay_no),
                json={"notify_id": unique("e2e2"), "amount_cents": order.json()["total_amount_cents"]},
            )
            assert cb.status_code == 200
            return order_no

        with allure.step("两个用户并发下单并支付"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                order_nos = list(executor.map(run, users))

        with allure.step("断言：两个订单各自独立、库存扣了 4 件"):
            assert len(set(order_nos)) == 2
            for order_no in order_nos:
                assert sut_db.order_status(order_no) == "PAID"
            assert sut_db.product_stock(product["id"]) == 6
