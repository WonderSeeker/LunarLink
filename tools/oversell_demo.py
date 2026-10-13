# -*- coding: utf-8 -*-
"""防超卖演示工具（教学 / 面试现场演示用，不属于 pytest 套件）。

用法：
    # 1) 先起被测系统
    cd D:\\Projects ; .\\svc.ps1 start sut
    # 2) 再跑本脚本
    & 'D:\\Projects\\LunarLink\\backend\\.venv\\Scripts\\python.exe' D:\\Projects\\LunarLink\\tools\\oversell_demo.py

它做两件事：

  A. 真实场景：10 个用户同时抢 5 件库存（走真实 HTTP 打 SUT），验证
     成功(201)=5 / 失败(409)=5 / 其他=[] / 最终库存(落库)=0。

  B. 原理对照实验：在同一个临时 SQLite 库、同一个并发压力下，对比
       ❌ 「先查再减」（check-then-act）      → 会超卖（卖出 10、库存 -5）
       ✅ 「条件更新」（UPDATE ... WHERE stock >= ?） → 正确（卖出 5、库存 0）
     把「为什么不能那样写」从经验之谈变成可复现的实证。

★ 口径提醒（不要拔高）：SQLite 是单写者模型，写入被串行化，因此本脚本
  A 段【测不出】MVCC 数据库（MySQL InnoDB / PG）下的竞争表现。它真正验证的是
  「并发访问下不崩（无死锁 / 无 database is locked / 无 5xx）」+「计数守恒」；
  「防超卖由单条原子条件更新保证」是【设计保证】。详见 lunarlink_tests/test_07_concurrency.py 头部说明。
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

import requests  # noqa: E402  (必须在 sys.path 设置之后)

from utils.data import unique  # noqa: E402
from utils.db import SutDatabase  # noqa: E402

THREADS = 10
STOCK = 5

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def run_concurrently(fns):
    """用 Barrier 让所有线程尽量同一瞬间发起，最大化真实竞争。

    不加 Barrier 的话线程会"排队"跑，测不出并发问题。
    """
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
    print("A. 真实场景：{} 个用户同时抢 {} 件库存（走 HTTP，打真实 SUT）".format(THREADS, STOCK))
    print("=" * 76)

    admin = session.post(
        SUT_BASE_URL + "/api/_test/token",
        json={"username": "admin", "ttl_seconds": 600},
        timeout=10,
    ).json()["token"]

    resp = session.post(
        SUT_BASE_URL + "/api/products",
        headers=auth(admin),
        # ★ 用 unique() 加 uuid 后缀 —— 本项目原则：靠造数不靠 reset。
        #   写死名字会在第二次运行时撞重名（本人已踩过）。
        json={
            "name": unique("抢购商品"),
            "description": "",
            "price_cents": 1000,
            "stock": STOCK,
        },
        timeout=10,
    )
    if resp.status_code != 201:
        print("  ★ 建商品失败：HTTP {} {}".format(resp.status_code, resp.text[:200]))
        return False
    pid = resp.json()["id"]
    db = SutDatabase(str(SUT_DB))
    print("  准备：建商品 id={} 初始库存={}".format(pid, db.product_stock(pid)))

    tokens = []
    for i in range(THREADS):
        username = "buyer_{}_{}".format(i, os.urandom(3).hex())
        session.post(
            SUT_BASE_URL + "/api/auth/register",
            json={"username": username, "password": "pw123456", "name": "并发买家"},
            timeout=10,
        )
        token = session.post(
            SUT_BASE_URL + "/api/auth/login",
            json={"username": username, "password": "pw123456"},
            timeout=10,
        ).json()["token"]
        tokens.append(token)
        # 串行把购物车放好 1 件 —— 避免把"加购"也卷进并发，导致失败原因不可归因
        assert session.post(
            SUT_BASE_URL + "/api/cart/items",
            headers=auth(token),
            json={"product_id": pid, "quantity": 1},
            timeout=10,
        ).status_code in (200, 201)
    print("  准备：{} 个用户各自购物车放好 1 件（串行完成）".format(THREADS))
    print("  ---- 现在同时发起 {} 个下单请求 ----".format(THREADS))

    def place_order(token):
        resp = requests.post(
            SUT_BASE_URL + "/api/orders", headers=auth(token), json={}, timeout=60
        )
        return resp.status_code

    codes = run_concurrently([(lambda t=t: place_order(t)) for t in tokens])
    ok = codes.count(201)
    conflict = codes.count(409)
    others = sorted(c for c in codes if c not in (201, 409))
    final = db.product_stock(pid)

    print("  ★ 成功(201) = {}".format(ok))
    print("  ★ 失败(409) = {}".format(conflict))
    print("    其他状态码 = {}   ← 期望空（空 = 无 5xx / 无 database is locked）".format(others))
    print("  ★ 最终库存(落库) = {}   ← 期望 0".format(final))
    good = ok == STOCK and final == 0 and not others
    print("  判定：{}".format("无超卖 ✓" if good else "★ 出现超卖或异常 ✗"))
    return good


def part_b():
    print()
    print("=" * 76)
    print("B. 原理对照实验：同一并发压力下，「先查再减」vs 「条件更新」")
    print("=" * 76)

    tmp = os.path.join(tempfile.mkdtemp(prefix="oversell_"), "demo.sqlite3")

    def reset(stock):
        conn = sqlite3.connect(tmp)
        conn.execute("DROP TABLE IF EXISTS products")
        conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, stock INTEGER)")
        conn.execute("INSERT INTO products (id, stock) VALUES (1, ?)", (stock,))
        conn.commit()
        conn.close()

    def conn():
        # isolation_level=None -> autocommit，贴近真实业务；busy_timeout 避免瞬时锁冲突
        c = sqlite3.connect(tmp, timeout=30, isolation_level=None)
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def stock_now():
        c = sqlite3.connect(tmp)
        value = c.execute("SELECT stock FROM products WHERE id=1").fetchone()[0]
        c.close()
        return value

    def naive_buy():
        """❌ 先查再减：读到 stock 后判断，再 UPDATE。两步之间有缝。"""
        c = conn()
        try:
            current = c.execute("SELECT stock FROM products WHERE id=1").fetchone()[0]
            if current >= 1:
                time.sleep(0.005)  # 模拟"读到之后、写回之前"的耗时（校验/打折/写日志）
                c.execute("UPDATE products SET stock = stock - 1 WHERE id=1")
                return "success"
            return "reject"
        finally:
            c.close()

    def atomic_buy():
        """✅ 条件更新：把"够不够"塞进 WHERE，靠 rowcount 判定。"""
        c = conn()
        try:
            cur = c.execute(
                "UPDATE products SET stock = stock - 1 WHERE id=1 AND stock >= 1"
            )
            return "success" if cur.rowcount == 1 else "reject"
        finally:
            c.close()

    all_good = True
    # should_oversell：这是【对照组】，我们期望它超卖 —— 超卖了才说明原理被演示出来
    for label, fn, should_oversell in [
        ("❌ 先查再减（check-then-act）", naive_buy, True),
        ("✅ 条件更新（UPDATE ... WHERE stock >= ?）", atomic_buy, False),
    ]:
        reset(STOCK)
        res = run_concurrently([fn] * THREADS)
        ok = res.count("success")
        final = stock_now()
        oversold = ok > STOCK
        print()
        print("  {}".format(label))
        print("     初始库存 = {}   并发线程 = {}".format(STOCK, THREADS))
        print("     卖出（返回 success） = {}   ← 期望 {}".format(ok, STOCK))
        print("     最终库存(落库)      = {}   ← 期望 0".format(final))
        if should_oversell:
            if oversold:
                print("     → ★ 超卖 {} 件（这正是要演示的：错误写法真的会超卖）".format(ok - STOCK))
            else:
                all_good = False
                print("     → ⚠ 未复现超卖（机器太快/太慢所致，可多跑几次）")
        else:
            if not oversold:
                print("     → 正确：卖出数不超过库存")
            else:
                all_good = False
                print("     → ★ 不该超卖却超卖了 {} 件！".format(ok - STOCK))
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
    print("结论：并发安全不是「小心」出来的 —— 它取决于「判断」有没有落在原子操作里。")
    print("=" * 76)
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
