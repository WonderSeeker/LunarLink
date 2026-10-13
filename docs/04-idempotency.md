# 支付幂等实测 —— 用数据库的唯一约束当闸门

> **用途**：把「★ 支付幂等」讲透：概念 → 实现 → 真实场景实测 → 原理对照实验 → **反直觉结论** → 诚实口径。
>
> **纪律**：每条结论带证据（`文件:行`）；实测输出均为本机真实运行结果；推断显式标注。
>
> **配套**：[改造前项目档案](./00-before-refactor.md) · [执行链路考古](./01-execution-chain.md) · [架构对照](./02-architecture-after.md) · [防超卖实测](./03-oversell.md)

---

## 0. 一句话结论

**幂等的第一道防线是「表结构上的唯一约束」，不是「应用层的判断」。**

应用层那些"先查一下有没有处理过"的代码，只是**看起来**在做幂等；
真正兜底的是数据库的 `UNIQUE` 约束 —— 约束一旦缺失，"先查"在并发下完全靠不住。

---

## 1. 概念

### 幂等

**同一个操作执行 N 次，效果与执行 1 次相同。**

| 生活场景 | 幂等吗 |
|---|---|
| 按电梯按钮 5 次 | ✅ 幂等（还是只来一趟） |
| 转账 5 次 | ❌ 不幂等（扣 5 次钱） |

### 为什么支付回调最怕不幂等

第三方支付平台在你没及时回 `success` 时会**重试**通知（超时、网络抖动都会触发）。
不幂等 → **对方重试几次，你就加几次钱 / 发几次货**。

### `notify_id`：天然的"去重钥匙"

支付平台**每次通知都带唯一编号** → 同一个 `notify_id` 出现第二次，就说明是重试。

### 两种实现思路

| 思路 | 做法 | 并发下 |
|---|---|---|
| **A. 应用层先查** | `SELECT` 有没有处理过 → 没有就入账 | ❌ 两个回调可能同时查到"没处理过" → 都入账 |
| **B. 数据库约束** | 直接 `INSERT`，靠 `UNIQUE` 拦重复 | ✅ 数据库级原子保证，只有一个能成功 |

> **与第 6 课的关系**：同一个道理的两个应用 ——
> **判断必须落在数据库的原子操作里**。防超卖用「条件更新」表达判断；幂等用「唯一约束」表达判断。

---

## 2. 项目里的实现

### 2.1 闸门在表结构上（★ 最关键的一行）

```120:129:sut/schema.sql
-- ---------------------------------------------------------------- 支付回调流水
-- ★ 幂等闸门：notify_id 上建 UNIQUE 约束。
-- 并发重复回调时由数据库抛 IntegrityError，应用层据此判定「重复回调」并直接返回
-- 首次结果。绝不能靠应用层「先查再写」——那在并发下必然漏（check-then-act 竞态）。
CREATE TABLE IF NOT EXISTS payment_callbacks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    notify_id  TEXT    NOT NULL UNIQUE,
    pay_no     TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);
```

### 2.2 首次入账：先占闸门，再入账

```764:769:sut/store.py
        # ★ 幂等闸门：唯一约束冲突会向上抛 IntegrityError
        conn.execute(
            "INSERT INTO payment_callbacks (notify_id, pay_no, created_at)"
            " VALUES (?, ?, ?)",
            (notify_id, pay_no, now),
        )
```

**顺序很重要**：**先占闸门（INSERT），再入账（UPDATE payments / orders）**。
占到 = 首次；占不到（抛 `IntegrityError`）= 重复 → 整个事务回滚，不留脏数据。

### 2.3 重复回调：只读，绝不改任何字段

```87:92:sut/routers/payments.py
    try:
        return store.settle_payment(pay_no, payload.notify_id, payload.amount_cents)
    except sqlite3.IntegrityError:
        # ★ 幂等命中：unique(payment_callbacks.notify_id) 拦下的重复回调。
        #   这里只读取当前状态返回，绝不重复入账。
        payment = store.get_payment(pay_no)
```

返回 `duplicated: true` + 当前字段。**重点在它"没做什么"**：不更新 `payments`、不动 `orders`、不改 `paid_at`。

---

## 3. 实测 A：真实场景（20 线程同一 `notify_id`）

`tools/idempotency_demo.py` A 段真实输出：

```
准备：商品 id=20 / 订单 SO17918703171D3C84F3 / 支付单 PAY1791870317881ABD51 / 金额 800 分
准备：同一个 notify_id = demo_notify_PAY1791870317881ABD51
---- 现在同时发起 20 个回调 ----
★ 首次入账(duplicated=false) = 1
★ 幂等命中(duplicated=true)  = 19
  异常(非 200)               = []   ← 期望空
★ payment_callbacks 落库行数 = 1   ← 期望 1（唯一约束闸门）
★ payments.amount_paid_cents = 800
★ orders.status              = PAID

---- 再补一次【串行】回调（同一个 notify_id）----
   响应 = HTTP 200 duplicated=True
   paid_at 是否变化 = False   ← 期望 False（绝不重复改字段）
判定：幂等成立 ✓
```

> `paid_at 不变` 这一步是**幂等的直接证据** —— 口头说"我做了幂等"没有说服力，"字段没变"才有。

---

## 4. ★★ 实测 B：原理对照实验

**两组唯一的差别是「表上有没有 UNIQUE 约束」**：

```
❌ 先查再入账（check-then-act）
   表上是否有 UNIQUE 约束 = 无
   回调线程 = 20   单笔金额 = 100 分
   实际入账次数 = 16   判为幂等 = 4   ← 期望 入账 1
   账户余额(落库) = 1600   ← 期望 100
   → ★ 重复入账 15 次（这正是要演示的：错误写法真的会重复入账）

✅ 唯一约束闸门（UNIQUE + IntegrityError）
   表上是否有 UNIQUE 约束 = 有
   回调线程 = 20   单笔金额 = 100 分
   实际入账次数 = 1   判为幂等 = 19   ← 期望 入账 1
   账户余额(落库) = 100   ← 期望 100
   → 正确：只入账一次
```

> 两次运行里"重复入账 15 次 / 17 次"**数字会变**（竞态本质非确定），但**结论稳定**：不幂等就是会重复。

---

## 5. ★★★ 反直觉结论（做这个实验时才发现的）

第一版对照实验里，我给对照组表**也加了 UNIQUE 约束**，结果"先查再入账"那段直接抛了：

```
sqlite3.IntegrityError: UNIQUE constraint failed: callbacks.notify_id
```

**含义**：只要表上有 `UNIQUE` 约束，**即使应用层写得很糙，数据库也会替你拦住重复**。

所以真正的结论是：

> **幂等的第一道防线是「表结构上的约束」，而不是「应用层的判断」。**
> 应用层那些"先查一下"的代码只是**看起来**在做幂等；真正兜底的是约束。
> 反过来，**约束一旦缺失，"先查"就完全靠不住**（实测：20 个线程里 16~18 个都入账了）。
>
> ★ 推论：**这类需求应该先从表结构设计入手**（约束 / 唯一索引），再写应用逻辑 ——
> 而不是反过来"用应用层判断拟补表结构缺失"。

---

## 6. ★ 诚实口径（面试必背，`test_07_concurrency.py:5-13`）

> 这两个测试**【不能】**说成「证明了数据库级幂等」。SQLite 是单写者模型，写入本身被串行化，
> **测不出 MVCC 数据库（MySQL InnoDB / PG）下的竞争表现**。
>
> 它们真正证明的是：**并发访问下不崩**（无死锁、无 `database is locked`、无 5xx）+ **计数守恒**。
> 「幂等由数据库唯一约束保证」是【**设计保证**】，测试只是把它在并发路径上验证了一遍。

---

## 7. 演示工具

**`tools/idempotency_demo.py`**（独立于 pytest 套件）

| 特性 | 说明 |
|---|---|
| A 段 | 真实场景：20 线程同一 `notify_id` 并发回调（走真实 HTTP 打 SUT）+ 串行重放验证 `paid_at` 不变 |
| B 段 | 原理对照：**表上有/无 UNIQUE 约束** → `先查再入账` vs `唯一约束闸门` |
| 关键设计 | 两组**唯一差别**就是 "有没有 `UNIQUE` 约束"，其余代码结构一致（公平对照） |
| 路径推导 | `Path(__file__).resolve().parents[1]`，任意目录可调用 |
| 友好失败 | 连不上 SUT 时提示启动命令 |
| 可重复执行 | 商品名/用户名用 `unique()` 加 uuid 后缀 |
| 退出码 | `0` = 符合预期（含"对照组确实重复入账"）；`1` = 异常或未复现 |

---

## 8. 操作命令

### 命令 ① 跑真实用例

```powershell
cd D:\Projects\LunarLink\lunarlink_tests
..\backend\.venv\Scripts\python.exe -m pytest test_07_concurrency.py -v
```

| | |
|---|---|
| **作用** | 6 条并发与幂等用例（含 20 线程幂等回调） |
| **预期**（实测） | `6 passed in 3.25s` |
| **常见错误** | 没起 SUT → `sut_ready` 让整轮**明确退出** |

### 命令 ② 跑幂等演示工具

```powershell
cd D:\Projects\LunarLink
& '.\backend\.venv\Scripts\python.exe' .\tools\idempotency_demo.py
```

| | |
|---|---|
| **作用** | A 真实场景 + B 原理对照（有/无 UNIQUE 约束） |
| **预期**（实测，连跑两次） | A：`1 / 19 / [] / 1 行 / paid_at 不变`；B：无约束 → 重复入账 15~17 次，有约束 → 只入账 1 次；`exit code = 0` |
| **常见错误** | SUT 没起 → 给出启动命令提示（不是 traceback） |

---

## 9. 证据表

| 结论 | 文件:行 |
|---|---|
| **闸门在表结构上**（`notify_id UNIQUE`） | `sut/schema.sql:126`（表定义 `:124-129`，注释 `:121-123`） |
| 首次：先占闸门再入账 | `sut/store.py:764-769`，后续 `:784-792` |
| 重复：只读返回，不改字段 | `sut/routers/payments.py:87-92`、`:99-107` |
| 金额校验（不一致 → `AMOUNT_MISMATCH`） | `sut/store.py:754-762` |
| 创建支付单幂等复用（同订单返回同一张） | `sut/routers/payments.py:51-62` |
| 真实用例（20 线程同一 notify_id） | `lunarlink_tests/test_07_concurrency.py:190-222` |
| 6 条断言（含落库 1 行） | `test_07_concurrency.py:237-242` |
| 串行重放仍幂等（另一条用例） | `test_07_concurrency.py:244-263` |
| ★ 诚实口径 | `test_07_concurrency.py:5-13` |
| 演示工具 | `tools/idempotency_demo.py` |

**真实用例的断言原文**（面试可引用）：

```237:242:lunarlink_tests/test_07_concurrency.py
        assert others == [], "出现非 200 响应（可能是 5xx / database is locked）：{}".format(others)
        assert len(settled) == 1, "★ 应恰好 1 次真实入账，实际 {}".format(len(settled))
        assert len(duplicated) == THREADS_CALLBACK - 1, "★ 其余应为幂等命中，实际 {}".format(len(duplicated))
        assert callback_rows == 1, "★ payment_callbacks 应恰好 1 行（唯一约束闸门），实际 {}".format(callback_rows)
        assert sut_db.payment(pay_no)["amount_paid_cents"] == amount
        assert sut_db.order_status(order["order_no"]) == "PAID"
```

---

## 10. 自测题 + 标准答案

**Q1. 幂等是什么？举一个"不幂等会出事"的场景。**
A：同一操作执行 N 次效果与 1 次相同。场景：**支付平台重试通知** —— 不幂等就会重复加钱/发货。

**Q2. 我们的幂等闸门建在哪里？**
A：**建在表结构上** —— `sut/schema.sql:126` 的 `notify_id TEXT NOT NULL UNIQUE`。
不是 Python 里的判断语句。配套实现：`store.py:764-769`（先占闸门）、`payments.py:89`（捕获 `IntegrityError`）。

**Q3. 重复回调进来时，代码做了什么、没做什么？为什么强调"没做什么"？**
A：**做了**：只读当前状态，返回 `duplicated: true`。
**没做**：不 UPDATE `payments` / `orders` / `paid_at`。
**强调"没做"**是因为"字段没变"才是幂等的证据 —— 演示里用串行重放断言 `paid_at 不变`。

**Q4. 为什么说"幂等的第一道防线是表结构上的约束"？**
A：实测发现：**表上有 UNIQUE 约束时，"先查再入账"这种糙写法也不会重复**（数据库替你拦住了，
第一版对照实验直接抛 `IntegrityError`）。去掉约束后，"先查"在 20 线程下重复入账 15~17 次。
→ 应用层的"先查"只是**看起来**在做幂等，真正兜底的是约束。

---

## 11. 附：做这个演示工具时踩的坑（真实记录）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 对照组抛 `IntegrityError`，脚本崩 | **对照组表也加了 UNIQUE 约束** → 数据库替应用层拦住了重复，"先查"的失败演示不出来 | 给对照组表**去掉约束**（`reset(with_unique)`），两组**只在这一点上不同** —— 反而让结论更硬 |

> ★ 这个坑本身产出了本课最有价值的结论（见第 5 节）：
> **"约束在不在"就是决定性的** —— 错误的设计往往是"表结构缺约束 + 应用层拼命补"。

---

## 12. 待验证项

| 项 | 状态 |
|---|---|
| 在 MySQL InnoDB / PostgreSQL 下的并发表现 | **待验证**（当前仅在 SQLite 上验证，口径已声明） |
| 若 `payment_callbacks` 的约束被误删，是否有其它防线（如业务层状态检查） | **待验证**（本课未做该实验） |
