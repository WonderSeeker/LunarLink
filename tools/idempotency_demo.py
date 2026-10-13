# -*- coding: utf-8 -*-
"""支付回调幂等演示工具（教学 / 面试现场演示用，不属于 pytest 套件）。

用法：
    # 1) 先起被测系统
    cd D:\\Projects ; .\\svc.ps1 start sut
    # 2) 再跑本脚本
    & 'D:\\Projects\\LunarLink\\backend\\.venv\\Scripts\\python.exe' D:\\Projects\\LunarLink\\tools\\idempotency_demo.py

它做两件事：

  A. 真实场景：20 个线程用【同一个 notify_id】并发回调同一张支付单，验证
     首次入账=1 / 幂等命中=19 / 异常=[] / payment_callbacks 落库=1 行；
     并再补一次【串行】回调，确认 paid_at 不变（证明"重复回调绝不改任何字段"）。

  B. 原理对照实验：在同一个临时 SQLite 库、同一个并发压力下，对比
       ❌ 「先查有没有处理过，再入账」（check-then-act） → 会重复入账
       ✅ 「唯一约束当闸门」（INSERT 抛 IntegrityError 即幂等命中） → 只入账一次
     把「为什么不能那样写」从经验之谈变成可复现的实证。

★ 口径提醒（不要拔高）：SQLite 是单写者模型，写入被串行化，因此 A 段【测不出】
  MVCC 数据库（MySQL InnoDB / PG）下的竞争表现。它真正验证的是「并发访问下不崩
  （无死锁 / 无 database is locked / 无 5xx）」+「计数守恒」；「幂等由数据库唯一约束
  保证」是【设计保证】。详见 lunarlink_tests/test_07_concurrency.py 头部说明。
"""

import concurrent.futures
import io
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

# ---- 让脚本无论从哪里调用都能找到仓库 --------------------------------------
REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO / "lunarlink_tests"
SUT_DB = REPO / "sut" / "sut.sqlite3"
SUT_BASE_URL = os.getenv("SUT_BASE_URL", "http://127.0.0.1:8021").rstrip("/")

sys.path.insert(0, str(TESTS_DIR))

import requests  # noqa: E402

from utils.data import unique  # noqa: E402
from utils.db import SutDatabase  # noqa: E402

THREADS = 20          # 并发回调线程数（与 test_07_concurrency.py 的 THREADS_CALLBACK 一致）
AMOUNT = 100          # 对照实验里的单笔金额（单位：分）

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def run_concurrently(fns):
    """用 Barrier 让所有线程尽量同一瞬间发起，最大化真实竞争。"""
    barrier = threading.Barrier(len(fns))
    lock = threading.Lock()
    results = []

    def wrapper(fn):
        barrier.wait()
        value = fn()
        with lock:
            results.append(value)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(fns)) as ex:
        list(ex.map(wrapper, fns))
    return results


def auth(token):
    return {"Authorization": "Bearer " + token}


def part_a(session):
    print("=" * 76)
    print("A. 真实场景：{} 个线程用【同一 notify_id】并发回调同一张支付单".format(THREADS))
    print("=" * 76)

    admin = session.post(
        SUT_BASE_URL + "/api/_test/token",
        json={"username": "admin", "ttl_seconds": 600},
        timeout=10,
    ).json()["token"]

    # 建商品（★ uuid 后缀，可重复运行 —— 本项目"靠造数不靠 reset"原则）
    resp = session.post(
        SUT_BASE_URL + "/api/products",
        headers=auth(admin),
        json={"name": unique("幂等商品"), "description": "", "price_cents": 800, "stock": 5},
        timeout=10,
    )
    if resp.status_code != 201:
        print("  ★ 建商品失败：HTTP {} {}".format(resp.status_code, resp.text[:200]))
        return False
    product = resp.json()

    # 注册一个买家并登录
    username = unique("buyer")
    session.post(
        SUT_BASE_URL + "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": "幂等买家"},
        timeout=10,
    )
    token = session.post(
        SUT_BASE_URL + "/api/auth/login",
        json={"username": username, "password": "pw123456"},
        timeout=10,
    ).json()["token"]
    headers = auth(token)

    # 加购 → 下单 → 建支付单
    session.post(
        SUT_BASE_URL + "/api/cart/items",
        headers=headers,
        json={"product_id": product["id"], "quantity": 1},
        timeout=10,
    )
    order = session.post(SUT_BASE_URL + "/api/orders", headers=headers, json={}, timeout=30).json()
    order_no = order["order_no"]
    amount = int(order["total_amount_cents"])
    created = session.post(
        SUT_BASE_URL + "/api/payments", headers=headers, json={"order_no": order_no}, timeout=30
    ).json()
    pay_no = created["payment"]["pay_no"]
    notify_id = "demo_notify_{}".format(pay_no)

    print("  准备：商品 id={} / 订单 {} / 支付单 {} / 金额 {} 分".format(
        product["id"], order_no, pay_no, amount))
    print("  准备：同一个 notify_id = {}".format(notify_id))
    print("  ---- 现在同时发起 {} 个回调 ----".format(THREADS))

    def callback():
        r = requests.post(
            SUT_BASE_URL + "/api/payments/{}/callback".format(pay_no),
            json={"notify_id": notify_id, "amount_cents": amount},
            timeout=60,
        )
        try:
            return r.status_code, r.json().get("duplicated")
        except ValueError:
            return r.status_code, None

    results = run_concurrently([callback] * THREADS)
    settled = [r for r in results if r[0] == 200 and r[1] is False]
    duplicated = [r for r in results if r[0] == 200 and r[1] is True]
    others = [r for r in results if r[0] != 200]

    db = SutDatabase(str(SUT_DB))
    rows = db.payment_callback_count(pay_no)
    paid_at_first = db.payment(pay_no)["paid_at"]

    print("  ★ 首次入账(duplicated=false) = {}".format(len(settled)))
    print("  ★ 幂等命中(duplicated=true)  = {}".format(len(duplicated)))
    print("    异常(非 200)               = {}   ← 期望空".format(others))
    print("  ★ payment_callbacks 落库行数 = {}   ← 期望 1（唯一约束闸门）".format(rows))
    print("  ★ payments.amount_paid_cents = {}".format(db.payment(pay_no)["amount_paid_cents"]))
    print("  ★ orders.status              = {}".format(db.order_status(order_no)))

    # ★ 补一发【串行】回调：验证"重复回调绝不改任何字段"
    print()
    print("  ---- 再补一次【串行】回调（同一个 notify_id）----")
    replay = callback()
    paid_at_after = db.payment(pay_no)["paid_at"]
    print("     响应 = HTTP {} duplicated={}".format(replay[0], replay[1]))
    print("     paid_at 是否变化 = {}   ← 期望 False（绝不重复改字段）".format(
        paid_at_first != paid_at_after))

    good = (
        len(settled) == 1
        and len(duplicated) == THREADS - 1
        and not others
        and rows == 1
        and db.payment(pay_no)["amount_paid_cents"] == amount
        and db.order_status(order_no) == "PAID"
        and paid_at_first == paid_at_after
    )
    print("  判定：{}".format("幂等成立 ✓" if good else "★ 幂等被破坏 ✗"))
    return good


def part_b():
    print()
    print("=" * 76)
    print("B. 原理对照实验：同一并发压力下，「先查再入账」vs「唯一约束闸门」")
    print("=" * 76)

    tmp = os.path.join(tempfile.mkdtemp(prefix="idem_"), "demo.sqlite3")

    def reset(with_unique):
        """★ with_unique 决定表上有没有 UNIQUE 约束 —— 这正是两组对照的唯一差别。

        为什么要这么设计：如果表上【有】UNIQUE 约束，那么即便应用层"先查再写"写得很糙，
        数据库也会替你拦住重复（实测第一版就撞了 IntegrityError）。
        所以要让"先查"这条路暴露问题，对照组必须【不带约束】——
        它模拟的是"把幂等完全托付给应用层"这种做法。
        """
        ddl = (
            "DROP TABLE IF EXISTS callbacks;"
            "DROP TABLE IF EXISTS accounts;"
            "CREATE TABLE callbacks ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  notify_id TEXT NOT NULL {u}"
            ");"
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER);"
            "INSERT INTO accounts (id, balance) VALUES (1, 0);"
        ).format(u="UNIQUE" if with_unique else "")
        conn = sqlite3.connect(tmp)
        conn.executescript(ddl)
        conn.commit()
        conn.close()

    def conn():
        c = sqlite3.connect(tmp, timeout=30, isolation_level=None)  # autocommit
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def balance():
        c = sqlite3.connect(tmp)
        v = c.execute("SELECT balance FROM accounts WHERE id=1").fetchone()[0]
        c.close()
        return v

    NOTIFY = "n-001"

    def naive_callback():
        """❌ 先查有没有处理过，再入账 —— check-then-act。"""
        c = conn()
        try:
            seen = c.execute(
                "SELECT COUNT(*) FROM callbacks WHERE notify_id = ?", (NOTIFY,)
            ).fetchone()[0]
            if seen > 0:
                return "duplicated"
            time.sleep(0.005)  # 模拟"查到没处理"到"真正写库"之间的耗时
            c.execute("INSERT INTO callbacks (notify_id) VALUES (?)", (NOTIFY,))
            c.execute("UPDATE accounts SET balance = balance + ? WHERE id=1", (AMOUNT,))
            return "credited"
        finally:
            c.close()

    def atomic_callback():
        """✅ 唯一约束当闸门：INSERT 抛 IntegrityError 即幂等命中。"""
        c = conn()
        try:
            c.execute("INSERT INTO callbacks (notify_id) VALUES (?)", (NOTIFY,))
            c.execute("UPDATE accounts SET balance = balance + ? WHERE id=1", (AMOUNT,))
            return "credited"
        except sqlite3.IntegrityError:
            return "duplicated"
        finally:
            c.close()

    all_good = True
    # should_double：对照组期望它【重复入账】—— 重复了才说明原理被演示出来
    # with_unique ：两组的唯一差别 —— 表上有没有 UNIQUE 约束
    for label, fn, should_double, with_unique in [
        ("❌ 先查再入账（check-then-act）", naive_callback, True, False),
        ("✅ 唯一约束闸门（UNIQUE + IntegrityError）", atomic_callback, False, True),
    ]:
        reset(with_unique)
        res = run_concurrently([fn] * THREADS)
        credited = res.count("credited")
        duplicated = res.count("duplicated")
        final_balance = balance()
        print()
        print("  {}".format(label))
        print("     表上是否有 UNIQUE 约束 = {}".format("有" if with_unique else "无"))
        print("     回调线程 = {}   单笔金额 = {} 分".format(THREADS, AMOUNT))
        print("     实际入账次数 = {}   判为幂等 = {}   ← 期望 入账 1".format(credited, duplicated))
        print("     账户余额(落库) = {}   ← 期望 {}".format(final_balance, AMOUNT))
        doubled = credited > 1
        if should_double:
            if doubled:
                print("     → ★ 重复入账 {} 次（这正是要演示的：错误写法真的会重复入账）".format(
                    credited - 1))
            else:
                all_good = False
                print("     → ⚠ 未复现重复入账（机器太快/太慢所致，可多跑几次）")
        else:
            if not doubled:
                print("     → 正确：只入账一次")
            else:
                all_good = False
                print("     → ★ 不该重复却重复入账 {} 次！".format(credited - 1))
    return all_good


def main():
    session = requests.Session()
    try:
        session.get(SUT_BASE_URL + "/health", timeout=5)
    except requests.RequestException as exc:
        print("★ 连不上被测系统 {}：{}".format(SUT_BASE_URL, exc))
        print("  请先执行： cd D:\\Projects ; .\\svc.ps1 start sut")
        return 2

    a_ok = part_a(session)
    b_ok = part_b()

    print()
    print("=" * 76)
    print("结论：幂等不能靠「先查一遍」—— 判断同样必须落在数据库的原子操作里。")
    print("=" * 76)
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
