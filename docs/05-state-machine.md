# 订单状态机实测 —— 规则写成数据，校验只有一个入口

> **用途**：把「★ 订单状态机」讲透：概念 → 实现 → 三次实测（统一入口 / 内联校验 / 合法链路）
> → **用一处真实的不一致证明"统一入口"的价值**。
>
> **纪律**：每条结论带证据（`文件:行`）；实测输出均为本机真实运行结果；推断显式标注。
>
> **配套**：[改造前项目档案](./00-before-refactor.md) · [执行链路考古](./01-execution-chain.md) · [架构对照](./02-architecture-after.md) · [防超卖实测](./03-oversell.md) · [支付幂等实测](./04-idempotency.md)

---

## 0. 一句话结论

**同一个规则只能有一份副本 —— 有 2 处，就有 2 处会漂移。**

状态机的正确做法是：**规则写成一张数据表（白名单）** + **校验只有一个入口**。

---

## 1. 概念：什么是状态机

**一组「状态」+ 一组「允许的流转」。**

**生活比喻：报销单审批**

```
草稿 ──提交──► 审批中 ──通过──► 已通过
                  │
                  └──驳回──► 已驳回
```

**关键规则**：「已驳回」不能直接变成「已通过」—— 必须先重新提交。
没有这条规则，数据会自相矛盾（一张单子既"被驳回"又"已通过"）。

### 核心设计选择：规则**写在哪里**

| 写法 | 问题 |
|---|---|
| ❌ `if-else` 散落在各处 | 新增状态要改 **N 处**；改漏一处就漏一处 |
| ✅ **写成数据表（白名单）** | 规则只有 **1 份**，校验只有 **1 个入口** |

> **规则是数据，不是代码。** 新增状态**零改动业务逻辑**。

---

## 2. 项目里的实现

### 2.1 白名单（规则本体）

```73:80:sut/config.py
ORDER_STATE_TRANSITIONS = {
    "PENDING": {"PAID", "CANCELLED"},
    "PAID": {"SHIPPED", "REFUNDED"},
    "SHIPPED": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "REFUNDED": set(),
}
```

读法：`"PENDING": {"PAID","CANCELLED"}` = 待支付时**只允许**变已支付或已取消；`set()` = 终态。

### 2.2 统一校验入口（唯一的"守门人"）

```421:433:sut/store.py
def _apply_transition(conn: Any, order: Dict[str, Any], to_state: str) -> None:
    """★ 订单状态机校验：非法流转一律 409，并在 detail 里回传 from/to/allowed。"""
    allowed = config.ORDER_STATE_TRANSITIONS.get(order["status"], set())
    if to_state not in allowed:
        raise errors.Conflict(
            "非法的订单状态流转：{} -> {}".format(order["status"], to_state),
            code="INVALID_STATE_TRANSITION",
            detail={
                "from": order["status"],
                "to": to_state,
                "allowed": sorted(allowed),      # ← ★ 动态算出"你现在能去哪"
            },
        )
```

`allowed` 是**从白名单现算的**，不是写死的 —— 好错误信息的标准：调用方不用翻文档就知道当前能去哪。

### 2.3 统一流转函数 + 两个包装（危险设计成做不到）

```649:661:sut/store.py
def _transition_order(order_no: str, user_id: int, to_state: str) -> Dict[str, Any]:
    """【按归属】流转订单状态：user_id 必填，他人的订单一律 404。"""
    return _transition(order_no, to_state, scope="user", user_id=user_id)


def _transition_order_as_admin(order_no: str, to_state: str) -> Dict[str, Any]:
    """【管理员跨归属】流转订单状态。

    ★ 全项目唯一的跨归属旁路入口：函数名自带 as_admin 标记，签名里根本没有 user_id，
      因此不存在「漏传 user_id 就静默变成管理员操作」的可能。
    """
    return _transition(order_no, to_state, scope="admin", user_id=None)
```

| 设计 | 为什么 |
|---|---|
| `scope="user"` 时**必须**给 `user_id`，否则抛 `ValueError`（`:594-597`） | ★ **「宁可炸掉也不要静默越权」** —— 漏传参数立刻报错，而不是悄悄放行成跨用户操作 |
| 跨归属入口**函数名带 `as_admin`**、**签名里没有 `user_id`** | 把危险**设计成做不到** |

### 2.4 三个业务动作

| 函数 | 流转 | 特殊之处 |
|---|---|---|
| `cancel_order`（`:664-666`） | PENDING → CANCELLED | ★ 事务内**把库存还回去**（`:616-625`） |
| `ship_order`（`:669-675`） | PAID → SHIPPED | **admin 专属**（走 `as_admin`） |
| `confirm_order`（`:678-680`） | SHIPPED → COMPLETED | 用户确认收货 |

> `ship_order` 的注释还解释了一件事：**没有它 SHIPPED 不可达、confirm 就永远成功不了** —— 状态机必须**可达**才有意义。
> 同理 `REFUNDED` 目前没有收款接口能走到（不可达），所以状态机矩阵用例里**故意不测它**，避免假断言。

---

## 3. 实测（工具：`tools/state_machine_demo.py`）

### A. 走【统一入口】的非法流转（PENDING 订单去 `confirm`）

```
POST /api/orders/.../confirm                 HTTP 409
    {"code":"INVALID_STATE_TRANSITION",
     "message":"非法的订单状态流转：PENDING -> COMPLETED",
     "detail":{"from":"PENDING","to":"COMPLETED","allowed":["CANCELLED","PAID"]}}
detail.allowed = ['CANCELLED', 'PAID']
判定：动态算出，正确（调用方一眼知道现在能去哪）✓
```

### ★★ B. 走【`settle_payment` 内联校验】的非法流转（已 PAID 再回调）

```
回调 #1（notify_A，首次入账）    HTTP 200
    {"pay_no":"PAY...","order_status":"PAID","duplicated":false,"amount_paid_cents":500,...}
现在订单已是 PAID。再发一个【不同 notify_id】的回调：
回调 #2（notify_B，订单已 PAID）  HTTP 409
    {"code":"INVALID_STATE_TRANSITION",
     "message":"非法的订单状态流转：PAID -> PAID",
     "detail":{"from":"PAID","to":"PAID","allowed":[]}}
detail.allowed = []
判定：★ 硬编码空数组，与白名单不符（本课要证明的不一致）
```

**根因（代码原文）**：

```777:782:sut/store.py
        if order["status"] != config.ORDER_STATUS_PENDING:
            raise errors.Conflict(
                "非法的订单状态流转：{} -> {}".format(order["status"], "PAID"),
                code="INVALID_STATE_TRANSITION",
                detail={"from": order["status"], "to": "PAID", "allowed": []},
            )
```

`settle_payment` **没调 `_apply_transition`**，自己写了一份校验：

| 差异 | 后果 |
|---|---|
| 硬编码 `!= PENDING`，**不查白名单** | 白名单以后改了这里**不会跟着变** → **两处规则会漂移** |
| `allowed` 写死 `[]` | 错误信息质量下降（白名单里 `PAID` 允许 `SHIPPED`/`REFUNDED`，**本应返回 `["REFUNDED","SHIPPED"]`**） |

### C. 合法链路（证明状态机**可达**）

```
POST /api/payments/.../callback  （支付）        HTTP 200   order_status=PAID
POST /api/orders/.../ship        （发货，admin）  HTTP 200   status=SHIPPED
POST /api/orders/.../confirm     （确认收货）     HTTP 200   status=COMPLETED
POST /api/orders/.../cancel      （终态再取消）   HTTP 409
    {"message":"非法的订单状态流转：COMPLETED -> CANCELLED",
     "detail":{"from":"COMPLETED","to":"CANCELLED","allowed":[]}}

链路：PENDING --支付--> PAID --发货--> SHIPPED --确认收货--> COMPLETED
判定：合法链路全部走通，且终态拒绝任何流转 ✓
```

> 注意 C 里的 `allowed: []` 是 **正确的**（`COMPLETED` 的允许集合就是空集，与 A 一样是动态算的）。

---

## 4. ★ 结论：为什么"统一入口"值钱

A 与 B 的差异就是答案：

| | A（统一入口） | B（内联校验） |
|---|---|---|
| `allowed` | **动态**：`["CANCELLED","PAID"]` | **硬编码**：`[]` |
| 规则来源 | 白名单（唯一事实源） | 复制了一份 `!= PENDING` |
| 白名单改动后会怎样 | 自动跟着变 | **静默漂移** |

> **"统一校验入口"的价值 = 消灭"同一个规则的多份副本"。**
> **有 2 处规则，就有 2 处会漂移；只有 1 处，才谈得上"改一次就对"。**

**与前几课的联系**（同一个道理的不同表现）：

| 课 | "事实源"被复制成多份 → 后果 |
|---|---|
| 本课 | 状态机规则被复制 → 错误信息漂移、规则可能不一致 |
| 改造前的平台 | 用例被复制进 `case_step` → 改接口不会同步到用例 |
| 改接口 → 用例 | 靠手动同步 → 忘了同步就"接口对、用例跑旧的" |
| 幂等 | 约束缺失时应用层补 → 补不住 |

### 处置边界（按既定原则）

这是一个**真实存在的上游缺陷**（`sut/store.py:777-782`）。按本项目既定边界：

> **上游平台/SUT 的缺陷：只有当它【挡住我们的测试】时才修；否则记录，不修。**

该缺陷不影响任何现有用例（`test_04_orders.py` / `test_05_payments.py` 断言的是状态码与业务结果，
不是 `detail.allowed` 的内容），因此**记录在案，不做修改**。若将来要修，正确做法是：
让 `settle_payment` 复用 `_apply_transition`，而不是再写一份判断。

---

## 5. 演示工具

**`tools/state_machine_demo.py`**（独立于 pytest 套件）

| 特性 | 说明 |
|---|---|
| A 段 | 统一入口的非法流转 → 动态 `allowed` |
| B 段 | 内联校验的非法流转 → 硬编码 `allowed`（复现不一致） |
| C 段 | 合法链路 + 终态拒绝（证明**可达性**） |
| 路径推导 | `Path(__file__).resolve().parents[1]`，任意目录可调用 |
| 友好失败 | 连不上 SUT 时提示启动命令 |
| 可重复执行 | 商品名/用户名用 `unique()` 加 uuid 后缀 |
| 退出码 | `0` = 三段结果均符合预期；`1` = 有一项不符（也可能是实现已改动，需重新核对） |

---

## 6. 操作命令

### 命令 ① 一眼看清状态机结构

```powershell
cd D:\Projects\LunarLink\sut
Select-String -Path config.py -Pattern 'ORDER_STATE_TRANSITIONS' -Encoding UTF8 | ForEach-Object { "  L{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }
Select-String -Path store.py -Pattern '_apply_transition\(|def _transition|scope=' -Encoding UTF8 | ForEach-Object { "  L{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }
```

| | |
|---|---|
| **作用** | 看白名单位置 + 统一入口被哪些地方调用 |
| **预期**（实测） | `config.py:73` 白名单；`store.py:421` 定义；**唯一一处调用在 `store.py:614`**；`:651/:661` 两个 scope 包装 |
| **常见错误** | 忘 `-Encoding UTF8` |

### 命令 ② 跑状态机演示工具

```powershell
cd D:\Projects\LunarLink
& '.\backend\.venv\Scripts\python.exe' .\tools\state_machine_demo.py
```

| | |
|---|---|
| **作用** | A 统一入口 / B 内联校验 / C 合法链路 |
| **预期**（实测） | A：`allowed=['CANCELLED','PAID']`；B：`allowed=[]`（与白名单不符）；C：`PAID → SHIPPED → COMPLETED` 全 200，终态 409；`exit code = 0` |
| **常见错误** | SUT 没起 → 会给出启动命令提示 |

### 命令 ③ 跑真实用例

```powershell
cd D:\Projects\LunarLink\lunarlink_tests
..\backend\.venv\Scripts\python.exe -m pytest test_04_orders.py -v
```

| | |
|---|---|
| **作用** | 订单模块 33 条（含状态机矩阵） |
| **预期** | 全部通过 |
| **常见错误** | 没起 SUT → `sut_ready` 让整轮**明确退出** |

---

## 7. 证据表

| 结论 | 文件:行 |
|---|---|
| 白名单（规则本体） | `sut/config.py:73-80` |
| 统一校验入口（动态算 `allowed`） | `sut/store.py:421-433` |
| 统一流转函数（scope = user / admin） | `sut/store.py:594-646` |
| 「宁可炸掉也不要静默越权」 | `sut/store.py:594-597` |
| 按归属包装 / 跨归属包装（唯一旁路） | `sut/store.py:649-651 / :654-661` |
| 三个业务动作 | `sut/store.py:664-666 / :669-675 / :678-680` |
| 取消订单回滚库存 | `sut/store.py:616-625` |
| ★ **漏网之处**（内联校验 + 硬编码 `allowed`） | `sut/store.py:777-782` |
| 路由层暴露 | `sut/routers/orders.py:109-123`（cancel / ship / confirm） |
| 状态机矩阵用例 | `lunarlink_tests/test_04_orders.py` |
| 演示工具 | `tools/state_machine_demo.py` |

---

## 8. 自测题 + 标准答案

**Q1. 状态机是什么？没有它会出现什么"自相矛盾的数据"？**
A：一组状态 + 一组允许的流转。没有它，会出现"已取消的订单又变成已支付""已完成的订单又被取消"这类矛盾数据。

**Q2. 为什么流转规则写成字典（数据），而不是 `if-else`？**
A：① 规则只有**一份**，新增状态**零改动业务代码**；② 校验只有**一个入口**，不会出现"改一处漏一处"；
③ 错误信息能**动态**给出 `allowed`，调用方一眼看懂。

**Q3. 非法流转返回什么状态码？`detail` 回传哪三个字段？为什么 `allowed` 必须动态算？**
A：**409** `INVALID_STATE_TRANSITION`；`detail` = `from` / `to` / `allowed`（`store.py:421-433`）。
必须动态算，否则会像 `settle_payment` 那样写死 `[]`，**与白名单脱节**（实测 B 段）。

**Q4. A/B 两次实测证明了什么？"统一校验入口"的价值是什么？**
A：证明了**同一规则有两份副本时会漂移** —— A 走统一入口 → `allowed` 动态正确；
B 内联校验 → `allowed` 硬编码空数组，与白名单不符。
价值：**消灭规则的副本，让"改一次就对"成立**。

---

## 9. 待验证项

| 项 | 状态 |
|---|---|
| `REFUNDED` 状态是否可达 | **待验证**（当前无退款接口；状态机矩阵用例故意不测它） |
| 若修复 `settle_payment` 复用 `_apply_transition`，是否有用例会受影响 | **待验证**（现有用例断言状态码与业务结果，未断言 `allowed` 内容） |
| `_apply_transition` 的第一个参数 `conn` 是否被使用 | **待验证**（函数体内只用到 `order` 与 `to_state`） |
