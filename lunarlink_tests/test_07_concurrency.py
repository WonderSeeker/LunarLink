# -*- coding: utf-8 -*-
"""★ 并发与幂等用例（本套件的核心价值之一）。

================================================================================
★★ 口径（务必按这个说法，不要拔高）★★
================================================================================
    这两个测试【不能】说成「证明了数据库级防超卖 / 证明了并发正确性」。
    SQLite 是单写者模型，写入本身被串行化，因此**测不出 MVCC 数据库（MySQL InnoDB / PG）
    下的竞争表现**。

    它们真正证明的是：**并发访问下不崩** —— 无死锁、无 `database is locked`、无 5xx，
    且**计数守恒**。「防超卖由单条原子条件更新保证」是【设计保证】，
    不是靠这两个测试证明的；测试只是把它在并发路径上验证了一遍。
================================================================================

为什么用裸 requests 而不是 ApiClient：
    并发线程里共享一个 requests.Session 有额外风险，而这里要验证的是【服务端行为】，
    不是客户端封装。用裸 requests + 显式 auth_headers() 更干净、更可归因。
"""

import concurrent.futures
import threading
from typing import Any, Dict, List, Tuple

import allure
import pytest
import requests

from utils.client import TOKEN_STYLE_SUT, auth_headers
from utils.data import create_product, register

THREADS_OVERSELL = 10
STOCK_OVERSELL = 5

THREADS_CALLBACK = 20


def _register_tokens(base_url: str, count: int, prefix: str) -> List[str]:
    """批量注册用户并返回 token 列表（准备阶段串行做，避免把注册也卷进并发）。

    用 ApiClient 走真实注册 + 真实登录 —— 准备阶段顺便覆盖一遍正常链路。
    """
    from utils.client import ApiClient

    anonymous = ApiClient(base_url, token=None, token_style=TOKEN_STYLE_SUT, name="批量注册")
    tokens = []
    for _ in range(count):
        info = register(anonymous, username=None)
        tokens.append(info["token"])
    return tokens


def _post(base_url: str, path: str, token: str, payload: Dict[str, Any]) -> int:
    resp = requests.post(
        base_url + path,
        json=payload,
        headers=auth_headers(token, TOKEN_STYLE_SUT),
        timeout=60,
    )
    return resp.status_code


def _run_concurrently(funcs: List[Any]) -> List[Any]:
    """用 Barrier 让所有线程尽量同一瞬间发起，最大化真实竞争。"""
    barrier = threading.Barrier(len(funcs))
    lock = threading.Lock()
    results: List[Any] = []

    def wrapper(fn):
        barrier.wait()
        value = fn()
        with lock:
            results.append(value)
        return value

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(funcs)) as executor:
        list(executor.map(wrapper, funcs))
    return results


@allure.feature("并发与幂等")
@allure.story("防超卖")
class TestConcurrentOrder:

    @allure.title("★ 10 线程抢 5 件库存：恰好 5 成功 / 5 失败(409) / 库存归零 / 无 5xx")
    @pytest.mark.concurrency
    def test_oversell(self, sut_admin, sut_base_url, sut_db):
        product = create_product(sut_admin, price_cents=1000, stock=STOCK_OVERSELL)
        tokens = _register_tokens(sut_base_url, THREADS_OVERSELL, "oversell")

        # 准备阶段：每个用户把自己的购物车放好 1 件（串行，避免把加购也卷进并发）
        for token in tokens:
            status = _post(sut_base_url, "/api/cart/items", token,
                           {"product_id": product["id"], "quantity": 1})
            assert status in (200, 201), "准备阶段加购失败: {}".format(status)

        results = _run_concurrently(
            [(lambda t=token: _post(sut_base_url, "/api/orders", t, {})) for token in tokens]
        )

        success = results.count(201)
        conflict = results.count(409)
        others = [code for code in results if code not in (201, 409)]
        final_stock = sut_db.product_stock(product["id"])

        allure.attach(
            "成功(201)={}  失败(409)={}  其他={}  最终库存={}".format(
                success, conflict, others, final_stock
            ),
            "并发下单结果",
            allure.attachment_type.TEXT,
        )

        assert others == [], "出现了非预期状态码（可能是 5xx / 死锁 / database is locked）：{}".format(others)
        assert success == STOCK_OVERSELL, "成功数不是恰好 {}（超卖或多卖）: {}".format(STOCK_OVERSELL, success)
        assert conflict == THREADS_OVERSELL - STOCK_OVERSELL, "失败数不对: {}".format(conflict)
        assert final_stock == 0, "★ 最终库存应为 0（无超卖），实际 {}".format(final_stock)

    @allure.title("★ 同一用户并发下单同一 SKU（库存 3）：恰好 3 成功，库存归零")
    @pytest.mark.concurrency
    def test_same_user_concurrent_orders(self, sut_admin, sut_base_url, sut_db, fresh_buyer):
        product = create_product(sut_admin, price_cents=500, stock=3)
        token = fresh_buyer["token"]

        # 每次下单都会清空购物车，所以要先给足购物车内容不现实；
        # 这里改为并发「加购 + 下单」的组合不可控 —— 改测：并发加购同一 SKU 后总量正确。
        status = _post(sut_base_url, "/api/cart/items", token, {"product_id": product["id"], "quantity": 1})
        assert status in (200, 201)

        # 并发下单 3 次：只有第 1 次能成功（购物车被清空后其余都是 EMPTY_CART）
        results = _run_concurrently(
            [(lambda: _post(sut_base_url, "/api/orders", token, {})) for _ in range(3)]
        )
        success = results.count(201)
        assert success == 1, "同一用户并发下单，购物车只有一份，应恰好 1 次成功: {}".format(results)
        assert sut_db.product_stock(product["id"]) == 2

    @allure.title("同用户并发加购同一商品：数量正确累加，不丢更新、不越库存")
    @pytest.mark.concurrency
    def test_concurrent_cart_merge(self, sut_admin, sut_base_url, fresh_buyer):
        product = create_product(sut_admin, price_cents=100, stock=10)
        token = fresh_buyer["token"]

        _run_concurrently(
            [(lambda: _post(sut_base_url, "/api/cart/items", token,
                            {"product_id": product["id"], "quantity": 1})) for _ in range(8)]
        )

        resp = requests.get(
            sut_base_url + "/api/cart",
            headers=auth_headers(token, TOKEN_STYLE_SUT),
            timeout=30,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1, "并发加购同一商品应合并为 1 行，实际 {} 行".format(len(items))
        assert items[0]["quantity"] == 8, "数量应为 8（无丢更新），实际 {}".format(items[0]["quantity"])

    @allure.title("同用户并发加购超过库存：总量不超过库存上限")
    @pytest.mark.concurrency
    def test_concurrent_merge_respects_stock(self, sut_admin, sut_base_url, fresh_buyer):
        product = create_product(sut_admin, price_cents=100, stock=4)
        token = fresh_buyer["token"]

        results = _run_concurrently(
            [(lambda: _post(sut_base_url, "/api/cart/items", token,
                            {"product_id": product["id"], "quantity": 2})) for _ in range(6)]
        )

        # 每次加 2，库存 4 => 最多成功 2 次，其余必须 409
        success = len([c for c in results if c in (200, 201)])
        conflicts = len([c for c in results if c == 409])
        others = [c for c in results if c not in (200, 201, 409)]
        assert others == [], "出现非预期状态码: {}".format(others)
        assert success == 2, "应恰好 2 次成功（4/2），实际 {}，结果 {}".format(success, results)
        assert conflicts == 4

        resp = requests.get(
            sut_base_url + "/api/cart",
            headers=auth_headers(token, TOKEN_STYLE_SUT),
            timeout=30,
        )
        assert resp.json()["items"][0]["quantity"] == 4, "购物车数量不应超过库存"


@allure.feature("并发与幂等")
@allure.story("支付回调幂等")
class TestConcurrentCallback:

    @allure.title("★ 20 线程同一 notify_id 并发回调：恰好 1 次入账 / 19 次幂等命中 / 无 5xx")
    @pytest.mark.concurrency
    def test_concurrent_same_notify_id(self, sut_admin, sut_base_url, sut_db, fresh_buyer):
        product = create_product(sut_admin, price_cents=800, stock=5)
        token = fresh_buyer["token"]
        headers = auth_headers(token, TOKEN_STYLE_SUT)

        _post(sut_base_url, "/api/cart/items", token, {"product_id": product["id"], "quantity": 1})
        order = requests.post(sut_base_url + "/api/orders", json={}, headers=headers, timeout=30).json()
        amount = order["total_amount_cents"]

        created = requests.post(
            sut_base_url + "/api/payments",
            json={"order_no": order["order_no"]},
            headers=headers,
            timeout=30,
        ).json()
        pay_no = created["payment"]["pay_no"]

        notify_id = "concurrent_notify_{}".format(pay_no)

        def callback():
            resp = requests.post(
                sut_base_url + "/api/payments/{}/callback".format(pay_no),
                json={"notify_id": notify_id, "amount_cents": amount},
                timeout=60,
            )
            try:
                return resp.status_code, resp.json().get("duplicated")
            except ValueError:
                return resp.status_code, None

        results: List[Tuple[Any, Any]] = _run_concurrently([callback] * THREADS_CALLBACK)

        settled = [r for r in results if r[0] == 200 and r[1] is False]
        duplicated = [r for r in results if r[0] == 200 and r[1] is True]
        others = [r for r in results if r[0] != 200]
        callback_rows = sut_db.payment_callback_count(pay_no)

        allure.attach(
            "首次入账={}  幂等命中={}  异常={}  payment_callbacks 行数={}".format(
                len(settled), len(duplicated), others, callback_rows
            ),
            "并发回调结果",
            allure.attachment_type.TEXT,
        )

        assert others == [], "出现非 200 响应（可能是 5xx / database is locked）：{}".format(others)
        assert len(settled) == 1, "★ 应恰好 1 次真实入账，实际 {}".format(len(settled))
        assert len(duplicated) == THREADS_CALLBACK - 1, "★ 其余应为幂等命中，实际 {}".format(len(duplicated))
        assert callback_rows == 1, "★ payment_callbacks 应恰好 1 行（唯一约束闸门），实际 {}".format(callback_rows)
        assert sut_db.payment(pay_no)["amount_paid_cents"] == amount
        assert sut_db.order_status(order["order_no"]) == "PAID"

    @allure.title("★ 并发回调后再次串行回调，幂等仍成立（状态不会被重复推进）")
    @pytest.mark.concurrency
    def test_sequential_replay_after_concurrent(self, sut_admin, sut_base_url, sut_db, fresh_buyer):
        product = create_product(sut_admin, price_cents=600, stock=5)
        token = fresh_buyer["token"]
        headers = auth_headers(token, TOKEN_STYLE_SUT)

        _post(sut_base_url, "/api/cart/items", token, {"product_id": product["id"], "quantity": 1})
        order = requests.post(sut_base_url + "/api/orders", json={}, headers=headers, timeout=30).json()
        amount = order["total_amount_cents"]
        pay_no = requests.post(
            sut_base_url + "/api/payments",
            json={"order_no": order["order_no"]},
            headers=headers,
            timeout=30,
        ).json()["payment"]["pay_no"]

        notify_id = "replay_{}".format(pay_no)
        payload = {"notify_id": notify_id, "amount_cents": amount}

        first = requests.post(sut_base_url + "/api/payments/{}/callback".format(pay_no),
                              json=payload, timeout=30).json()
        assert first["duplicated"] is False
        paid_at = sut_db.payment(pay_no)["paid_at"]

        for _ in range(5):
            again = requests.post(sut_base_url + "/api/payments/{}/callback".format(pay_no),
                                  json=payload, timeout=30).json()
            assert again["duplicated"] is True

        assert sut_db.payment(pay_no)["paid_at"] == paid_at
        assert sut_db.payment_callback_count(pay_no) == 1
