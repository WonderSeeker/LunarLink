# -*- coding: utf-8 -*-
"""订单状态机演示工具（教学 / 面试现场演示用，不属于 pytest 套件）。

用法：
    # 1) 先起被测系统
    cd D:\\Projects ; .\\svc.ps1 start sut
    # 2) 再跑本脚本
    & 'D:\\Projects\\LunarLink\\backend\\.venv\\Scripts\\python.exe' D:\\Projects\\LunarLink\\tools\\state_machine_demo.py

它做三件事：

  A. 走【统一校验入口 _apply_transition】的非法流转：
     PENDING 订单去 confirm -> 409，且 detail.allowed 是【从白名单动态算出来的】
     （PENDING 只允许 PAID / CANCELLED）

  B. 走【settle_payment 内联校验】的非法流转：
     已 PAID 的订单再用一个新 notify_id 回调 -> 409，但 detail.allowed 是【硬编码的空数组】
     而白名单里 PAID -> {SHIPPED, REFUNDED}，本该返回 ['REFUNDED','SHIPPED']。

     ★ A 与 B 的差异，就是本课要证明的事：同一个规则有两份副本，就会漂移。

  C. 合法链路（证明状态机是【可达】的）：
     PENDING --支付--> PAID --发货(admin)--> SHIPPED --确认收货--> COMPLETED
     再到终态上试一次取消 -> 409，allowed 为空集合（正确）。

★ 口径提醒：本脚本验证的是"状态机规则本身"（确定性逻辑），不是并发。见
  lunarlink_tests/test_04_orders.py 的状态机矩阵用例。
"""

import io
import os
import sys
from pathlib import Path

# ---- 让脚本无论从哪里调用都能找到仓库 --------------------------------------
REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO / "lunarlink_tests"
SUT_BASE_URL = os.getenv("SUT_BASE_URL", "http://127.0.0.1:8021").rstrip("/")

sys.path.insert(0, str(TESTS_DIR))

import requests  # noqa: E402

from utils.data import unique  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

session = requests.Session()


def auth(token):
    return {"Authorization": "Bearer " + token}


def admin_token():
    return session.post(
        SUT_BASE_URL + "/api/_test/token",
        json={"username": "admin", "ttl_seconds": 600},
        timeout=10,
    ).json()["token"]


def new_buyer():
    """注册一个全新买家并登录，返回 token。"""
    username = unique("sm_buyer")
    session.post(
        SUT_BASE_URL + "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": "状态机买家"},
        timeout=10,
    )
    return session.post(
        SUT_BASE_URL + "/api/auth/login",
        json={"username": username, "password": "pw123456"},
        timeout=10,
    ).json()["token"]


def new_order(buyer_token):
    """建商品 -> 加购 -> 下单，返回 (order_no, amount_cents, product_id)。"""
    resp = session.post(
        SUT_BASE_URL + "/api/products",
        headers=auth(admin_token()),
        json={"name": unique("状态机商品"), "description": "", "price_cents": 500, "stock": 10},
        timeout=10,
    )
    if resp.status_code != 201:
        raise RuntimeError("建商品失败：HTTP {} {}".format(resp.status_code, resp.text[:200]))
    pid = resp.json()["id"]
    session.post(
        SUT_BASE_URL + "/api/cart/items",
        headers=auth(buyer_token),
        json={"product_id": pid, "quantity": 1},
        timeout=10,
    )
    order = session.post(
        SUT_BASE_URL + "/api/orders", headers=auth(buyer_token), json={}, timeout=30
    ).json()
    return order["order_no"], int(order["total_amount_cents"]), pid


def show(label, resp):
    body = resp.text.replace("\n", " ")
    print("  {:<44} HTTP {}".format(label, resp.status_code))
    print("      {}".format(body[:260]))


def detail_of(resp):
    try:
        return resp.json().get("detail") or {}
    except ValueError:
        return {}


def part_a():
    print("=" * 78)
    print("A. 走【统一入口 _apply_transition】的非法流转（订单处于 PENDING）")
    print("=" * 78)
    print("  白名单 config.py  ->  PENDING: {'PAID', 'CANCELLED'}")
    print("  尝试 PENDING -> COMPLETED（非法）：")

    buyer = new_buyer()
    order_no, _, _ = new_order(buyer)
    resp = session.post(
        SUT_BASE_URL + "/api/orders/{}/confirm".format(order_no), headers=auth(buyer), timeout=15
    )
    show("POST /api/orders/.../confirm", resp)

    allowed = detail_of(resp).get("allowed")
    print("  detail.allowed = {}".format(allowed))
    good = resp.status_code == 409 and allowed == ["CANCELLED", "PAID"]
    print("  判定：{}".format(
        "动态算出，正确（调用方一眼知道现在能去哪）✓" if good else "★ 与预期不符 ✗"))
    return good


def part_b():
    print()
    print("=" * 78)
    print("B. 走【settle_payment 内联校验】的非法流转（订单已 PAID）")
    print("=" * 78)
    print("  白名单 config.py  ->  PAID: {'SHIPPED', 'REFUNDED'}")

    buyer = new_buyer()
    order_no, amount, _ = new_order(buyer)
    pay_no = session.post(
        SUT_BASE_URL + "/api/payments",
        headers=auth(buyer),
        json={"order_no": order_no},
        timeout=30,
    ).json()["payment"]["pay_no"]

    first = session.post(
        SUT_BASE_URL + "/api/payments/{}/callback".format(pay_no),
        json={"notify_id": "sm_A_" + pay_no, "amount_cents": amount},
        timeout=15,
    )
    show("回调 #1（notify_A，首次入账）", first)
    print("  现在订单已是 PAID。再发一个【不同 notify_id】的回调：")

    second = session.post(
        SUT_BASE_URL + "/api/payments/{}/callback".format(pay_no),
        json={"notify_id": "sm_B_" + pay_no, "amount_cents": amount},
        timeout=15,
    )
    show("回调 #2（notify_B，订单已 PAID）", second)

    allowed = detail_of(second).get("allowed")
    print("  detail.allowed = {}".format(allowed))
    # 期望观察到的"不一致"：硬编码空数组，而白名单里 PAID 允许 SHIPPED/REFUNDED
    inconsistent = allowed == [] and second.status_code == 409
    print("  判定：{}".format(
        "★ 硬编码空数组，与白名单不符（本课要证明的不一致）" if inconsistent
        else "未复现该不一致（实现可能已改动，需重新核对代码）"))
    return inconsistent


def part_c():
    print()
    print("=" * 78)
    print("C. 合法链路（证明状态机是【可达】的）")
    print("=" * 78)
    buyer = new_buyer()
    order_no, amount, _ = new_order(buyer)
    pay_no = session.post(
        SUT_BASE_URL + "/api/payments",
        headers=auth(buyer),
        json={"order_no": order_no},
        timeout=30,
    ).json()["payment"]["pay_no"]

    steps = []
    r = session.post(
        SUT_BASE_URL + "/api/payments/{}/callback".format(pay_no),
        json={"notify_id": "sm_C_" + pay_no, "amount_cents": amount},
        timeout=15,
    )
    steps.append(("PENDING --支付--> PAID", r.status_code, r.json().get("order_status")))
    show("POST /api/payments/.../callback  （支付）", r)

    r = session.post(
        SUT_BASE_URL + "/api/orders/{}/ship".format(order_no),
        headers=auth(admin_token()),
        timeout=15,
    )
    steps.append(("PAID --发货(admin)--> SHIPPED", r.status_code, r.json().get("status")))
    show("POST /api/orders/.../ship      （发货，admin）", r)

    r = session.post(
        SUT_BASE_URL + "/api/orders/{}/confirm".format(order_no),
        headers=auth(buyer),
        timeout=15,
    )
    steps.append(("SHIPPED --确认收货--> COMPLETED", r.status_code, r.json().get("status")))
    show("POST /api/orders/.../confirm   （确认收货）", r)

    r = session.post(
        SUT_BASE_URL + "/api/orders/{}/cancel".format(order_no),
        headers=auth(buyer),
        timeout=15,
    )
    show("POST /api/orders/.../cancel    （终态再取消）", r)

    good = (
        all(code == 200 for _, code, _ in steps)
        and steps[0][2] == "PAID"
        and steps[1][2] == "SHIPPED"
        and steps[2][2] == "COMPLETED"
        and r.status_code == 409
    )
    print("  链路：{}".format(" -> ".join(s[0].split("-->")[-1] for s in steps)))
    print("  判定：{}".format("合法链路全部走通，且终态拒绝任何流转 ✓" if good else "★ 链路不符预期 ✗"))
    return good


def main():
    try:
        session.get(SUT_BASE_URL + "/health", timeout=5)
    except requests.RequestException as exc:
        print("★ 连不上被测系统 {}：{}".format(SUT_BASE_URL, exc))
        print("  请先执行： cd D:\\Projects ; .\\svc.ps1 start sut")
        return 2

    a_ok = part_a()
    b_ok = part_b()
    c_ok = part_c()

    print()
    print("=" * 78)
    print("结论：同一个规则只能有一份副本 —— 有 2 处，就有 2 处会漂移。")
    print("=" * 78)
    return 0 if (a_ok and b_ok and c_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
