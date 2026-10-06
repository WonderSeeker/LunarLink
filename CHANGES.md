# CHANGES.md — LunarLink 用例层重构记录

> 本文档记录：**原项目是什么 / 我改了什么 / 量化结果**。
> 从改造第一天开始维护，每个阶段结束时更新。

---

## 一、原项目是什么

**tahitimoon/LunarLink** —— 一个前后端分离的**接口自动化测试平台**（MIT 许可）。

| 维度 | 原项目情况 |
|---|---|
| 用例内核 | **HttpRunner 1.5.15**（以 `extra_apps/httprunner` 形式**内置**在仓库里，不是 pip 依赖） |
| 后端 | Django 3.2.1 + DRF 3.14.0 + DRF-JWT，Python 3.10 |
| 前端 | Vue 2 + Element UI + webpack 3（老 webpack，必须 Node 16） |
| 数据库 | 源码默认 **MySQL**（本项目本地改用 SQLite） |
| 异步/定时 | Celery + Redis + django-celery-beat |
| 用例形态 | 平台内可视化录入，存成 HttpRunner 的 YAML/JSON 结构，存在 DB 的 `TextField` 里（`literal_eval` 读取） |
| 执行方式 | 平台内点击运行 → `HttpRunner` + `unittest.TextTestRunner` → 同步串行执行 → 报告落库 |
| 报告 | 查看时用 Jinja2 模板 `templates/report_template.html` 二次渲染（不是落 HTML 文件） |

**原项目的核心局限**（本次改造的动因）：

1. 用例只能用平台界面录入，**无法进版本控制、无法 Code Review、无法被 CI 原生执行**；
2. HttpRunner 1.5.15 内核的断言能力弱（`eq`/`len_eq` 等少数 comparator），**没有 JSON Schema 校验、没有落库校验**；
3. 执行是**串行**的（并行分支 `debug_suite_parallel` 需显式开启，且 `os.chdir` 导致进程级并发不安全）；
4. 每次执行都要 `mkdtemp` + `os.chdir` + `importlib.reload` 装载 `debugtalk.py`，**存在固定开销**；
5. 无可视化测试报告产物，回归结果只能看平台内的 HTML 页。

---

## 二、改造目标

把**用例层**从「HttpRunner 平台内录入」重构为「**pytest + Requests + Allure**」体系，并新增：

- 一套**真实业务语义**的测试套件（自建 mock 电商 SUT，覆盖登录 → 加购 → 下单 → 支付 → 查订单全链路）；
- **CI 每日回归**（GitHub Actions：定时 + 手动触发，上传 Allure 报告 artifact）；
- 可量化的**回归效率提升**（耗时对比必须有实测口径，见第五节）。

---

## 三、改造前：已有的本地适配改动（非本次改造产出）

以下 4 个文件在原仓库上做了本地跑通适配，**属于改造前的基线状态**，本次一并纳入版本控制
（原因：`settings.py` 的 SQLite 开关是 CI 能跑起来的前提，不提交会导致 GitHub Actions 失败）。

| 文件 | 改动 | 原因 |
|---|---|---|
| `backend/backend/settings.py` | 新增 `DATABASE_ENGINE` 环境变量开关（`sqlite` 用 SQLite，否则 MySQL） | 本机未安装 MySQL 服务 |
| `backend/extra_apps/httprunner/loader.py` | `collections.Hashable` → `collections.abc.Hashable` | Python 3.10 已移除旧入口 |
| `backend/extra_apps/httprunner/report.py` | `collections.Iterable` → `collections.abc.Iterable` | 同上 |
| `frontend/config/dev.env.js` | `VUE_APP_BASE_URL` 由 `8000` 改为 `8001` | 本机后端实际端口为 8001 |

另外本地存在（**不入库**，已加 `.gitignore`）：`backend/.venv`（Python 3.10.11 虚拟环境）、`backend/lunarlink.sqlite3`。

---

## 四、改造内容清单

### 4.1 阶段进度表

| 阶段 | 内容 | 状态 |
|---|---|---|
| 准备 | 切 `feat/pytest-refactor` 分支、修 `.gitignore`、建 `CHANGES.md` | ✅ 已完成 |
| 阶段 1 | 摸清骨架：读 Django app 结构与 httprunner 内核，跑通「新建项目 → 新建用例 → 执行 → 看报告」，记录性能基线 | 🚧 进行中 |
| 阶段 2 | 用例层重构为 pytest + Requests + Allure（`lunarlink_tests/`） | ⏳ 未开始 |
| 阶段 3 | 业务测试套件（`sut/` mock 电商 + 100+ 用例，正常/异常/边界/并发/幂等/越权） | ⏳ 未开始 |
| 阶段 4 | GitHub Actions 每日回归 + README（架构图 / 用例清单 / 耗时对比） | ⏳ 未开始 |

### 4.2 文件变更

> 随改造推进实时补充。

**新增**

| 文件 | 说明 |
|---|---|
| `CHANGES.md` | 本文档 |
| `backend/requirements-test.txt` | 测试依赖清单，与 `backend/requirements.txt` 完全隔离 |
| `sut/__init__.py` | SUT 包与版本号 |
| `sut/config.py` | SUT 配置中心（端口 8021 / SQLite 并发参数 / 鉴权与业务参数 / 订单状态机白名单） |
| `sut/schema.sql` | SUT 库结构：8 张表（users / tokens / products / cart_items / orders / order_items / payments / payment_callbacks） |
| `sut/db.py` | SQLite 连接与事务封装（WAL + `busy_timeout=15000` + 等锁退避重试 + `BEGIN IMMEDIATE`） |
| `sut/errors.py` | 统一错误体 + 异常处理器（含 4 条状态码细则与「越权返 404」的安全设计说明） |
| `sut/security.py` | 口令哈希（pbkdf2，标准库）与 token / 单号生成 |
| `sut/store.py` | 数据访问层（手写 SQL）；当前含「用户」「商品」两节 |
| `sut/app.py` | FastAPI 装配（顶部含「路由必须同步 def，禁止 async def」强制约束） |
| `sut/run.py` | SUT 启动入口（8021） |
| `sut/run-sut.ps1` | 本地启动器：为测试环境打开测试后门（`SUT_ENABLE_TEST_HOOKS=1`） |
| `sut/seed.py` | 种子数据脚本（`--reset` 一键重建；商品库存刻意覆盖 0 / 1 / 3 等并发边界） |
| `sut/routers/__init__.py` | 路由包 |
| `sut/routers/system.py` | `/health` 健康检查（数据库不可用返回 503） |
| `sut/routers/test_hooks.py` | 测试后门（仅 `SUT_ENABLE_TEST_HOOKS=1` 时挂载）；当前仅 `/api/_test/ping` |

**修改**

| 文件 | 改动 | 原因 |
|---|---|---|
| `.gitignore` | 新增 `backend/.venv/`、`backend/lunarlink.sqlite3`、`sut/*.sqlite3`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`allure-results/`、`allure-report/`、`report.html` | ★ 修正原规则 `/backend/venv` 与实际目录名 `backend/.venv` **不符导致虚拟环境未被忽略**的 Bug；并排除测试产物 |
| `backend/backend/settings.py` | 纳入版本控制（改动内容见第三节） | CI 需要 SQLite 开关 |
| `backend/extra_apps/httprunner/loader.py` | 纳入版本控制 | Python 3.10 兼容 |
| `backend/extra_apps/httprunner/report.py` | 纳入版本控制 | Python 3.10 兼容 |
| `frontend/config/dev.env.js` | 纳入版本控制 | 本地端口对齐 |
| `D:\Projects\svc.ps1`（不在本仓库内） | 新增短名 `sut`（端口 8021，工作目录 `D:\Projects\LunarLink\sut`，探测 `/health`） | 让 SUT 与其他项目统一启停；文件保持 UTF-8 with BOM |

---

## 五、量化结果

### 5.1 回归耗时口径（★ 必须实测，不估算）

三段对比，**统一口径 =「从开始执行到报告产出的墙钟时间」**：

| 组别 | 执行方式 | 说明 |
|---|---|---|
| 基线 | LunarLink 平台原生 HttpRunner 执行 | 从触发到出报告的墙钟时间 |
| 改造后（串行） | `pytest` 串行执行同规模用例 | 含 fixture 准备与报告生成 |
| 改造后（并行） | `pytest -n auto`（pytest-xdist） | 同上 |

加速比 = 并行耗时 / 串行耗时。README 中需注明各口径**包含哪些环节**（是否含 SUT 启动、数据重置、报告渲染）。

| 指标 | 基线（HttpRunner） | 改造后（pytest 串行） | 改造后（pytest 并行） | 加速比 |
|---|---|---|---|---|
| 待实测 | — | — | — | — |

### 5.2 用例规模

| 指标 | 数值 |
|---|---|
| 用例总数 | 待统计 |
| 覆盖接口数 | 待统计 |
| 场景分布（正常/异常/边界/并发/幂等/越权） | 待统计 |

---

## 六、设计决策速查（面试可讲点）

| 决策 | 结论 | 理由 |
|---|---|---|
| 越权返回 **404 而非 403** | 越权访问他人资源统一返回 404 | **这是安全设计，不是疏漏**：403 会泄露「该资源存在」，404 不泄露资源是否存在性。业界先例：GitHub 对无权访问的私有仓库即返回 404 |
| 金额单位 | 全部用**分（int）** | 规避浮点精度问题；`schemas/*.json` 中约束 `"type": "integer"`，让 JSON Schema 断言能自动抓住意外引入的浮点/字符串金额 |
| 错误码风格 | 语义化 HTTP 状态码（非全 200 + code） | 本项目第 1 层断言即「状态码 + 错误码」双层；全用 200 会让状态码断言失效 |
| 防超卖 | `BEGIN IMMEDIATE` 事务 + 条件更新 `UPDATE ... WHERE stock >= ?`，靠 `rowcount` 判定 | 单条 SQL 条件更新天然原子，避免「先查再减」的 TOCTOU 竞态 |
| 支付幂等 | DB **唯一约束** 做幂等闸门（`payment_callbacks.notify_id UNIQUE`） | 并发下「应用层先查一遍」必然漏，幂等必须由数据库保证 |
| 测试确定性 | 提供**测试后门**（`/api/_test/*`，仅 `SUT_ENABLE_TEST_HOOKS=1` 时挂载） | 「token 过期 / 订单超时 / 账号锁定」可确定性构造，测试套件里零 `sleep(60)` |
| SUT 路由全部用**同步 `def`** | 不用 `async def` | `sqlite3` 是阻塞式：同步 `def` 会被 FastAPI 丢进 anyio 线程池（真并发）；`async def` 会在事件循环里阻塞 sqlite3，导致并发请求排队甚至超时，使超卖/幂等并发用例**假失败** |

---

## 七、设计简化说明（已知取舍，避免面试被问倒）

| 简化点 | 说明 |
|---|---|
| **SUT 采用单 token 设计** | 真实系统应为「access token 短命 + refresh token 长命」双 token。为聚焦**测试场景本身**，SUT 简化为单 token（`/api/auth/refresh` 直接续签当前 token）。这是**有意简化**，不是遗漏 |
| SUT 用 `sqlite3` 标准库 + 手写 SQL | 不引入 SQLAlchemy，减少依赖与版本风险；SUT 的目的是提供**稳定的被测对象**，而非演示 ORM |
| SUT 测试后门 | 仅测试环境开启，生产默认关闭（`SUT_ENABLE_TEST_HOOKS` 未设时不挂载路由） |
| Allure HTML 本地渲染 | 本机无 JDK、无 allure CLI，且 `allure-commandline` 不在 PyPI、GitHub Release 通道受限。本地产出 `allure-results/` + `pytest-html` 兜底报告；Allure HTML 由 CI 生成并上传 artifact。**后续可选路径**：`allure-commandline` 发行包在 Maven Central 上（`io.qameta.allure:allure-commandline`），可用阿里云 Maven 镜像拉取，JDK 用清华/华为镜像 |
| SUT 并发用例线程数 | 先用 10 线程跑通（SQLite 写串行，`busy_timeout=15000` + 退避重试），不追求高并发压测指标 |

---

## 八、本地环境说明

| 项 | 值 |
|---|---|
| 后端 venv | `backend/.venv`（Python 3.10.11） |
| 测试依赖 | `backend/requirements-test.txt`（**新建，不污染 `backend/requirements.txt`**） |
| 测试依赖安装 | `& 'D:\Projects\LunarLink\backend\.venv\Scripts\python.exe' -m pip install -r backend/requirements-test.txt` |
| 后端服务 | `lunar-be` → `http://127.0.0.1:8001/swagger/` |
| 前端服务 | `lunar-fe` → `http://127.0.0.1:8888`（**必须 Node 16**） |
| SUT 服务 | `sut` → `http://127.0.0.1:8021/health` |
| 服务启停 | `cd D:\Projects ; .\svc.ps1 start lunar-be,sut` |
| 本地跑测试 | `cd lunarlink_tests ; ..\backend\.venv\Scripts\python.exe -m pytest --alluredir=allure-results --html=report.html --self-contained-html` |
