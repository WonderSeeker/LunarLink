# 架构对照 —— 改造前 vs 改造后（执行链路）

> **用途**：把改造前后的链路并排钉住，逐点对应；说清「改造的本质是什么」。
>
> **纪律**：每条结论带证据（`文件:行`）。推断显式标注；未核实标「待验证」。
>
> **配套**：[改造前项目档案](./00-before-refactor.md) · [执行链路考古](./01-execution-chain.md) · [CHANGES.md](../CHANGES.md)

---

## 0. 一句话结论

**改造的本质：把「用例」从「数据」变成了「代码」。**

这一个变化牵动其他所有变化：代码能进 git → 能被 pytest 收集 → 能在命令行跑 → **CI 才有可能**；
同时「发请求 / 断言 / 造数 / 报告」这些原本由平台代劳的事，**必须变成我们自己的代码**。

---

## 1. 比喻：菜谱

| | 改造前 | 改造后 |
|---|---|---|
| 用例 | **写在厨师脑子里**（数据库里的文本） | **写在纸上**（`.py` 文件） |
| 谁能读 | 只有平台 | 任何人 / 任何工具 |
| 能改吗 | 只能在平台上点 | 编辑器改，**能 diff** |
| 能自动化跑吗 | 只能在平台点 | 命令行 / **CI** |

### 「平台代劳」变成「自己写」——这是改造工作量的真实分布

| 原平台代劳 | 改造后自己写 | 我们写在哪 |
|---|---|---|
| 发请求 | HTTP 客户端 | `utils/client.py` |
| 断言 | 断言逻辑 | 原生 `assert` + `utils/schema.py` + `utils/db.py` |
| 造数 | 造数工具 | `utils/data.py` |
| 前置准备 | fixture | `conftest.py` |
| 报告 | 报告产物 | `--alluredir` / `--html` |

> 所以改造的主要工作量在**基础设施（conftest + utils）**，而不是"写 175 条用例"。

---

## 2. 改造后的链路图（8 跳）

```
【第 1 跳】命令行触发
     │  python -m pytest -m smoke
     ▼
【第 2 跳】pytest 自动收集
     │  lunarlink_tests/pytest.ini:19   python_files = test_*.py
     ▼
【第 3 跳】fixture 依赖注入（conftest.py）
     │  session 级（整轮只做一次）：
     │    :115 sut_ready        就绪检查（未就绪→明确退出，而非诡异失败）
     │    :146 sut_seeded       会话级数据重置（只做一次）
     │    :185 sut              匿名客户端
     │    :219 sut_buyer_token  走【真实登录】拿 token
     │    :285 sut_db           SUT 数据库【只读】视图
     │  function 级（每个用例一份）：
     │    :253 fresh_buyer      注册全新买家 ← 用例间隔离靠它
     ▼
【第 4 跳】发请求
     │  utils/client.py:77   ApiClient.request()
     │    :36     auth_headers()   ← ★ 认证头契约【单一入口】（两种契约）
     │    :90-104 allure.attach()  ← 自动把请求/响应挂进报告
     │  → requests → HTTP
     ▼
【第 5 跳】被测系统（两个之一）
     │  A：sut:8021       （FastAPI，175 条用例）
     │  B：backend:8001   （Django，26 条平台冒烟）
     ▼
【第 6 跳】断言（4 层）
     │  ① 状态码 + 业务错误码
     │  ② JSON Schema      utils/schema.py
     │  ③ 业务字段
     │  ④ ★ 落库断言       utils/db.py:24（URI mode=ro，只读打开）
     ▼
【第 7 跳】报告
     │  allure-results/ + report.html（自包含）
     ▼
【第 8 跳】触发
     .github/workflows/daily-regression.yml（定时 / push / 手动）
```

---

## 3. ★ 逐点对照表

| 环节 | 改造前 | 改造后 | 证据（前 → 后） |
|---|---|---|---|
| 用例存在哪 | 数据库 `TextField` | **`.py` 文件** | `apps/lunarlink/models.py:277` → `lunarlink_tests/pytest.ini:19` |
| 谁组织用例 | 平台页面 | **pytest 收集** | — → `pytest.ini:19-21` |
| 前置准备 | 视图里现取现用 | **fixture 分层注入** | `utils/loader.py:269` → `conftest.py:115/146/185/285` |
| 发请求 | HttpRunner 内部 client | **requests（`ApiClient`）** | `extra_apps/httprunner/client.py` → `utils/client.py:77` |
| 认证头 | 平台内部处理 | ★ **`auth_headers()` 单一入口** | — → `utils/client.py:36-49` |
| 断言 | 少数 comparator | **原生 assert + Schema + 直连 SQL** | — → `utils/schema.py`；`utils/db.py:24` |
| 造数 | 手工 / 录制 | **uuid 后缀造数** | — → `utils/data.py:17-19` |
| 报告 | 落 2 表，查看时渲染 | **`allure-results/` + `report.html`** | `apps/lunarlink/views/report.py:191` → 命令行产物 |
| 触发 | 网页 / Celery 定时 | **pytest / GitHub Actions** | `docker-compose.yml:45` → `.github/workflows/daily-regression.yml` |
| 代码复用 | `debugtalk`（DB→落盘→import） | **`utils/` 普通模块** | `utils/loader.py:127-148` → `utils/` |
| 工作目录 | `os.chdir` 反复切换 | ★ **不切** | `utils/loader.py:272` → 无对应 |
| 并发 | 进程级不安全 | ★ **显式禁用 xdist**（有意） | — → `conftest.py:59-81` 守卫 |

> **最后两行必须能解释**：改造后我们**没有解决**"并发"，而是**主动禁止**（`pytest -n 2` 会明确报错退出）。
> 原因：套件依赖「所有用例共享同一个 SUT 进程 + 会话级 reset 只做一次」。
> **这是有意的取舍，不是遗漏。**

---

## 4. 操作命令（含 作用 / 预期 / 常见错误）

### 命令 ① 一眼确认三块骨架在位

```powershell
cd D:\Projects\LunarLink\lunarlink_tests
Select-String -Path pytest.ini -Pattern 'python_files|markers' -Encoding UTF8
Select-String -Path conftest.py -Pattern '^def sut_ready|^def sut_seeded|^def sut\(|^def sut_db|^def fresh_buyer' -Encoding UTF8
Get-ChildItem utils\*.py | ForEach-Object { $_.Name }
```

| | |
|---|---|
| **作用** | 不开文件就确认「配置 / fixture / 工具」三块在位 |
| **预期** | `python_files = test_*.py`；5 个 fixture 定义行；`client.py / data.py / db.py / schema.py` |
| **常见错误** | 不在 `lunarlink_tests` 目录下执行 → `conftest.py` 找不到 |

### 命令 ② 跑冒烟（提交前自检）

```powershell
cd D:\Projects\LunarLink\lunarlink_tests
..\backend\.venv\Scripts\python.exe -m pytest -m smoke -q
```

| | |
|---|---|
| **作用** | 跑 38 条冒烟用例 |
| **预期**（实测） | ≈4.7 秒，通过，1 条刻意 skip |
| **常见错误** | ① 忘了先 `.\svc.ps1 start sut` → `sut_ready` 让整轮**明确退出** ② 用全局 `python` 而非 venv 里的 → 缺依赖 |

### 命令 ③ 出报告

```powershell
cd D:\Projects\LunarLink\lunarlink_tests
Remove-Item allure-results -Recurse -Force -EA SilentlyContinue   # ★ 必须先清空
..\backend\.venv\Scripts\python.exe -m pytest --alluredir=allure-results --html=report.html --self-contained-html -q
```

| | |
|---|---|
| **作用** | 全量跑 + 两份报告 |
| **预期** | 201 条、≈30 秒；`report.html` ≈180KB |
| **常见错误** | ★ 不清空 `allure-results/` → 数出的条数是**历史累加**（已踩过，README 记录） |

### ★ 踩坑记录：`cd` 撞上可执行文件

```
PS> cd D:\Projects\LunarLink\backend\.venv\Scripts\python.exe
cd : 找不到路径"...python.exe"，因为该路径不存在。
```

| | |
|---|---|
| **原因** | `cd`（change **directory**）的对象必须是**目录**；这里给的是**文件**。报错文案有误导性 —— 文件其实存在（`Test-Path` = True） |
| **正确写法 A** | `& 'D:\Projects\LunarLink\backend\.venv\Scripts\python.exe' -m pytest -m smoke -q` |
| **正确写法 B** | 相对路径直接执行：`..\backend\.venv\Scripts\python.exe -m pytest -m smoke -q` |
| **记住** | **`cd` 进目录；执行程序 → 直接写路径（有空格时前面加 `&`）** |

---

## 5. 证据表

| 结论 | 文件:行 |
|---|---|
| 用例是文件、由 pytest 收集 | `lunarlink_tests/pytest.ini:17-21` |
| marker 分级（7 类） | `lunarlink_tests/pytest.ini:26-33` |
| 就绪 / 会话重置 / 匿名客户端 / 只读 DB | `lunarlink_tests/conftest.py:115 / :146 / :185 / :285` |
| 用例级隔离（全新买家） | `lunarlink_tests/conftest.py:253` |
| 认证头单一入口 | `lunarlink_tests/utils/client.py:36-49` |
| 统一发请求 + 自动 attach | `lunarlink_tests/utils/client.py:77-105` |
| 落库断言只读打开 | `lunarlink_tests/utils/db.py:24` |
| 造数 uuid 后缀 | `lunarlink_tests/utils/data.py:17-19` |
| 并发被显式禁用（有意） | `lunarlink_tests/conftest.py:59-81` |
| CI 触发方式 | `.github/workflows/daily-regression.yml` |

---

## 6. 自测题 + 标准答案

**Q1. 用一句话说：这次改造的**本质变化**是什么？**
A：**把「用例」从数据库里的数据，变成了代码文件（`.py`）**。由此解锁了 git 管理、pytest 收集、命令行执行与 CI；
同时"发请求/断言/造数/报告"由平台代劳变成自己实现（`utils/` + `conftest.py`）。

**Q2. 改造后"谁帮你发请求"变成了什么？包在哪个文件里？**
A：变成 **`requests`**，被包在 `lunarlink_tests/utils/client.py` 的 `ApiClient` 里
（`:77` `request()` 统一发请求；`:36` `auth_headers()` 统一认证头；`:90-104` 自动把请求/响应 attach 进报告）。

**Q3. 改造后"前置准备"从"视图里现取现用"变成了什么机制？**
A：**fixture 依赖注入**。例：`sut_ready`（`:115` 就绪检查）、`sut_seeded`（`:146` 会话级重置）、
`sut_db`（`:285` 只读 DB 视图）、`fresh_buyer`（`:253` 每个用例一个全新买家，实现用例间隔离）。

**Q4. 改造后我们没有解决"并发"，做了什么？为什么这是有意的？**
A：**显式禁用** —— `conftest.py:59-81` 的守卫检测到 `-n`（xdist）就报错退出。
因为套件依赖「所有用例共享同一个 SUT 进程 + 会话级 reset 只做一次」，
强行并行会互相重置数据、并触发 SUT 的 `RESET_BUSY` 守卫 → 变成最难查的**间歇性 flaky**。
**所以这是有意的取舍：宁可不并行，也不要不可复现的绿。**

---

## 7. 待验证项

| 项 | 状态 |
|---|---|
| 改造后"并发被禁用"是否会在未来随 SUT 拆分（每 worker 独立实例）而放开 | 待验证（属未来规划，非现状） |
| `pytest.ini` 中 `-ra -q` 的实际输出效果差异 | 待验证（仅读过配置，未逐项对比） |
