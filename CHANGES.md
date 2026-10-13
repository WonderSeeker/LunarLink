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
| 阶段 1 | 摸清骨架：读 Django app 结构与 httprunner 内核，跑通「新建项目 → 新建用例 → 执行 → 看报告」，记录性能基线 | ✅ 链路已摸清（报告产出与基线数字待补） |
| 阶段 2 | 用例层重构为 pytest + Requests + Allure（`lunarlink_tests/`） | ✅ 工程与报告链路完成（工具链体检 8 条全绿） |
| 阶段 3 | 业务测试套件（`sut/` mock 电商 + 100+ 用例，正常/异常/边界/并发/幂等/越权） | ✅ SUT 完整（26 路径 / 32 端点，含 4 个测试后门）+ **175 条用例全绿**（174 passed / 1 故意 skip） |
| 阶段 4 | GitHub Actions 每日回归 + README（架构图 / 用例清单 / 耗时口径） | ✅ **CI 已上线并全绿（run #5 success）**；平台冒烟 26 条已完成（本地套件，原因见 9.15③）；浅克隆与 requests 漏声明两条缺陷已修（9.15①②） |

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
| `sut/routers/test_hooks.py` | 测试后门（仅 `SUT_ENABLE_TEST_HOOKS=1` 时挂载）：`/api/_test/ping`、`/api/_test/token`（签发任意 TTL，**等价认证绕过**）、`/api/_test/user/{username}/unlock`、`/api/_test/user/{username}/expire_lock`（只拨锁定时间、保留失败计数） |
| `.github/workflows/daily-regression.yml` | 每日回归：定时 + 手动触发，起 SUT + 起平台 → pytest（201 条）→ Allure HTML → 上传 artifact<br>★【现状以 README 为准】见本表下方注 |
| `lunarlink_tests/test_09_lunarlink_smoke.py` | 平台冒烟 26 条（`platform` marker）；含认证头契约锁定、业务错误码、权限边界、轻量写闭环、3 条已知缺陷 xfail<br>★【现状以 README 为准】见本表下方注 |
| `backend/apps/lunaruser/management/commands/ensure_smoke_user.py` | 扩展为幂等创建两个账号：非管理员（主力）+ 管理员（admin-only 端点） |
| `README.md` | 追加 12 节测试体系说明（架构图 / 用法 / 用例清单 / 分层耗时实测 / 设计简化说明） |
| `sut/routers/auth.py` | 认证模块：注册 / 登录（连续失败 5 次锁定 423 + Retry-After）/ 登出（纯 no-op）/ profile / refresh（旧换新） |
| `sut/routers/products.py` | 商品模块：分页列表（边界校验）/ 详情 / 创建·更新·下架（admin）/ 库存查询 |
| `sut/routers/cart.py` | 购物车模块：查看 / 加购（同商品数量合并，首次 201、合并 200）/ 改数量 / 移除 / 清空 |
| `sut/routers/orders.py` | 订单模块：下单（★ 事务内条件更新防超卖）/ 列表 / 详情 / 状态 / 取消（回滚库存）/ 发货（admin）/ 确认收货 |
| `sut/routers/payments.py` | 支付模块（范围收敛版）：创建支付单（同订单幂等复用）+ ★ 幂等回调 |
| `sut/routers/system.py` | `/health`（含 `inflight` 字段）+ `POST /api/system/reset`（仅 admin，带并发保护） |
| `lunarlink_tests/pytest.ini` | pytest 配置：显式钉 rootdir + markers 定义 |
| `lunarlink_tests/conftest.py` | 分层 fixture：session 级（地址 / 就绪 / **会话内一次性 reset** / 预置账号与客户端 / 只读 DB）+ function 级由用例自造 uuid 数据 |
| `lunarlink_tests/utils/client.py` | `ApiClient` + **认证头契约单一入口** `auth_headers()`（SUT=Bearer、LunarLink=裸 token，实测得出） |
| `lunarlink_tests/utils/db.py` | SUT 数据库**只读**断言助手（URI `mode=ro`，禁止测试绕过 API 改库） |
| `lunarlink_tests/utils/schema.py` | JSON Schema 校验（`referencing.Registry` 解析相对 `$ref`），失败抛带路径的可读断言 |
| `lunarlink_tests/utils/data.py` | 造数与隔离：`unique()` / `register()` / `create_product()`，一律 uuid 后缀 |
| `lunarlink_tests/schemas/*.json` | `product` / `product_list` / `error`（金额字段一律约束 `"type":"integer"`） |
| `lunarlink_tests/test_00_report_chain.py` | 工具链体检用例（8 条：报告链路 + fixture 链路 + Schema + ★ 契约锁定） |
| `lunarlink_tests/test_01_auth.py` | 认证用例 33 条：注册边界 / 登录 / ★ 连续失败锁定边界 / token 矩阵 / 登出与续签 |
| `lunarlink_tests/test_02_products.py` | 商品用例 39 条：分页·关键字·排序边界 / 详情 / 库存 / admin 权限与 CRUD |
| `lunarlink_tests/test_03_cart.py` | 购物车用例 20 条：数量与库存校验 / ★ 同商品合并 / 越权 / 清空 / 下架商品处理 |
| `lunarlink_tests/test_04_orders.py` | 订单用例 33 条：下单扣库存 / ★ 状态机矩阵 / 取消回滚库存 / 列表隔离 / 越权 |
| `lunarlink_tests/test_05_payments.py` | 支付用例 14 条：创建支付单幂等复用 / ★ 幂等回调（落库三项断言）/ 金额校验 / 越权 |
| `lunarlink_tests/test_06_flow_e2e.py` | 端到端 4 条：主链路 / 取消链路 / 库存不足回滚链路 / 双用户并发链路（均带 `allure.step`） |
| `lunarlink_tests/test_07_concurrency.py` | ★ 并发与幂等 6 条：防超卖 / 同 notify_id 并发回调 / 并发加购合并 / 库存上限保护 |
| `lunarlink_tests/test_08_security.py` | 安全用例 18 条：水平越权(404) / 垂直越权(403) / token 矩阵 / 信息泄露 / 参数注入 |
| `sut/runtime.py` | 在途请求计数（供 reset 做并发保护） |
| `backend/apps/lunaruser/management/commands/ensure_smoke_user.py` | Django 管理命令：幂等创建回归冒烟账号，让套件能在干净 DB 上自举 |

> 【现状以 README 为准】上表 `.github/workflows/daily-regression.yml` 与
> `lunarlink_tests/test_09_lunarlink_smoke.py` 两栏是**当时**的记录，后来都有变化：
>
> 1. **CI 范围收窄为「只起 SUT、跑 175 条业务用例」**（不再起平台、不再跑 201 条）。
>    平台冒烟 26 条改为**本地套件**（`-m platform`）。原因是 `backend/requirements-test.txt`
>    与 `backend/requirements.txt` 依赖集冲突，实测合并后连 `sut/seed.py --reset` 都会失败，
>    而 CI 只装前者。命令与原因见 workflow 头部注释与 `README.md` 第 10 节。
>    → CI 上的权威计数（可匿名核实）：`总计 175 条 passed 174 skipped 1`。
> 2. **那 3 条 xfail 已不存在**：对应缺陷修好后，`xfail(strict=True)` 如设计般触发
>    `XPASS(strict)` 报警，随即被改造成 3 条**正向回归守卫**（总数仍 26，当前 `26 passed`）。
>    详见 9.20（缺陷修复 + 守卫改造）与 9.14（归因更正）、`README.md` 第 11 节。
>
> 此处保留原文不改写，以维持变更记录的时间顺序。

**修改**

| 文件 | 改动 | 原因 |
|---|---|---|
| `.gitignore` | 新增 `backend/.venv/`、`backend/lunarlink.sqlite3`、`sut/*.sqlite3`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`allure-results/`、`allure-report/`、`report.html` | ★ 修正原规则 `/backend/venv` 与实际目录名 `backend/.venv` **不符导致虚拟环境未被忽略**的 Bug；并排除测试产物 |
| `backend/backend/settings.py` | 纳入版本控制（改动内容见第三节） | CI 需要 SQLite 开关 |
| `backend/extra_apps/httprunner/loader.py` | 纳入版本控制 | Python 3.10 兼容 |
| `backend/extra_apps/httprunner/report.py` | 纳入版本控制 | Python 3.10 兼容 |
| `frontend/config/dev.env.js` | 纳入版本控制 | 本地端口对齐 |
| `D:\Projects\svc.ps1`（不在本仓库内） | 新增短名 `sut`（端口 8021，工作目录 `D:\Projects\LunarLink\sut`，探测 `/health`）；`Get-Procs` 新增可选 `M`（命令行匹配串，支持数组 OR，定义后不再用 `D`）；为 `lunar-be` / `rag` / `kbapi` / `wht-be` / `wht-worker` / `llm` / `afa` 共 7 个服务设置 `M`（合计 9 条模式）；`kbapi` 的启动参数由 `server.py` 改为 `server.py --port 8020`；顶部注释补齐「`-like` 不是正则」「复用他人 venv 必须给被复用方设 `M`」两条约定 | 让 SUT 与其他项目统一启停；**消灭「同 D 服务互相误杀 / 漏杀」这一类 bug**（共 3 组，详见第九节 9.5、9.6）。文件保持 UTF-8 with BOM |

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
| **SUT 采用单 token 设计** | 真实系统应为「access token 短命 + refresh token 长命」双 token。为聚焦**测试场景本身**，SUT 简化为单 token。这是**有意简化**，不是遗漏 |
| **`logout` 是纯 no-op** | JWT 式无状态鉴权下，服务端不做主动失效才是诚实的。响应体明确返回 `token_revoked: false` 并附提示「请客户端自行丢弃本地 token」，**不假装服务端已失效**。代价：登出后旧 token 在自身过期前仍可用（测试已断言这一行为，避免把"错误预期"写成断言） |
| **`refresh` 语义 = 旧换新** | `POST /api/auth/refresh` 用仍然有效的旧 token 换一个新 token，**不引入 refresh token** —— 后者会带出过期、轮换、重放等一连串问题，超出 mock 服务的目标。旧 token 不做轮换失效（已文档化并测试） |
| **测试后门关闭时返回 404 而非 403** | 后门由 `SUT_ENABLE_TEST_HOOKS`（默认 off）控制是否**注册路由**。关闭时 `/api/_test/*` 返回 404 —— 用 403 会暴露"这里存在一个后门"，404 不泄露存在性（与"越权返 404"同一原则） |
| **SUT 用 `Bearer`，LunarLink 用裸 token** | 两个契约**不同**，测试里必须区分：SUT 为 RFC 6750 风格 `Authorization: Bearer <token>`；LunarLink 的 `MyJWTAuthentication` 直接读 `HTTP_AUTHORIZATION` 原文，必须发裸 token（详见第 8 节）。这也是 `conftest.py` 要收一个 `auth_headers()` 单一入口的原因 |
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
| 并行跑（产出"并行耗时"档） | 追加 `-n auto`（pytest-xdist） |
| 回归冒烟账号 | 由 `python manage.py ensure_smoke_user` 幂等创建（非超管，供阶段 2 平台冒烟套件自举用）。**口令见该命令源码 / `SMOKE_PASSWORD` 环境变量，本文档不写明文** |
| 后端认证头格式 | ★ `Authorization: <裸 token>`（**不带 `JWT ` 前缀**）—— `backend/utils/auth.py:MyJWTAuthentication` 直接把 `HTTP_AUTHORIZATION` 原文交给 `jwt_decode_handler`，实测带前缀会 401「签名解析失败」 |

---

## 九、踩坑与约定记录（★ 防复发）

### 9.1 文本文件编码：★ 三分法（同一个 quirk 会从两个相反方向咬人）

> 本节在 9.19 之后由「两类文件两套规则」**升级为三分法** —— 因为原来只写了
> 「`.ps1` 必须有 BOM」，却漏了**反向**的那一半（有些文件必须**没有** BOM），
> 而漏掉的那一半正好造成了 9.19 那次事故。**歧义规则等于没有规则**。
> README 第 12 节也放了同一张表（那是面向使用者的规范位置）。

| 谁读这个文件 | 要求 | 原因 |
|---|---|---|
| **PowerShell 5.1**（`.ps1` 脚本） | ★ **必须有 BOM** | PS 5.1 不认 PEP263，只会按 ANSI（中文系统 = GBK）读，非 ASCII 字符会导致乱码甚至语法错误。`D:\Projects\svc.ps1` 与 `sut\run-sut.ps1` 均已校验 `BOM=True` |
| **Python**（`requirements-*.txt`、`.py` 源码等） | PEP263 coding 声明 或 UTF-8 BOM 都行 | pip 23.0.1 的 `auto_decode()` 在**既无 BOM 又无 coding 声明**时退回 `locale.getpreferredencoding()`；中文 Windows 下 locale 是 GBK，于是含中文注释的依赖文件直接 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`。已在 `backend/requirements-test.txt` 第 1 行加 `# -*- coding: utf-8 -*-` 解决 |
| **其它一切文本**（commit message / `.md` / `.json` / `.yaml` / `.csv`…） | ★ **必须没有 BOM** | BOM 会被当成**内容的第一个字符**（不是"元数据"）。实例：`git commit -F <文件>` 把 BOM 当成标题首字符 → `\ufeffdocs: …`，见 9.19 |

**★ 写文件的 API 必须显式选（永远别用 `Set-Content -Encoding UTF8`）**：
PS 5.1 的这个取值**总是写 BOM**，这正是 9.19 事故的根因。

```powershell
# 要 BOM（.ps1）：
[IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($true)))
# 不要 BOM（commit message / .md / .json …）：
[IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($false)))
# PowerShell 7 也可：Set-Content -Path $p -Value $t -Encoding utf8NoBOM
```

> 记法：**`.ps1` 要 BOM，其它都不要**；而且**两边的默认值都是错的**
> （PS 5.1 `Set-Content -Encoding UTF8` 默认**加** BOM，Python `open()` 默认**不加**），
> 所以不能靠默认，必须显式指定。

### 9.2 `svc.ps1` 的服务复用约定（连带影响已修）

服务应使用**自己项目目录内**的解释器。若某服务复用别的项目的 venv（例：`sut` 复用 LunarLink 的 `backend\.venv`），必须给**被复用的那个服务**设置 `M`（匹配串），否则 `stop` 会误伤。

实测证据（`sut` + `lunar-be` 同时运行时）：

```
lunar-be 的 M 命中: pid 6404（venv python，svc.ps1 启动） + pid 4132（其子进程，持有 8001）
旧 D 匹配会命中  : pid 14992（★ 就是 SUT 进程，误伤源头） + pid 6404
```

附带发现：`runserver` 在 venv 下会派生一个以 **base 解释器**（`D:\Python310\python.exe`）运行的子进程并持有端口，其命令行**不含** `LunarLink\backend` 路径 —— 旧的 `D` 匹配根本找不到它（只靠端口兜住），`M` 把这个漏杀隐患一并解决。

双向回归结论（原始输出见提交记录）：`stop lunar-be` 后 `sut` 存活；`stop sut` 后 `lunar-be` 存活；`stop-all` 两者都停干净且无残留。

### 9.3 查看 `pytest-html` 报告时不要用 grep 搜中文

`pytest-html 4.x` 把全部数据塞进 `<div id="data-container" data-jsonblob="...">`，且 `json.dumps` 默认 `ensure_ascii=True`，所以**中文以 `\uXXXX` 形式存储**（浏览器渲染正常）。用 `Select-String '中文'` 会误判为"报告里没有中文"。要看真实内容需先 `HtmlDecode` 再 `ConvertFrom-Json` 解 `data-jsonblob`。

### 9.4 工具链体检结果（pytest 9.1.1 兼容性）

`report.html` 的 `data-jsonblob.environment.Plugins` 实测输出：

```
allure-pytest 2.16.2 / html 4.2.0 / metadata 3.1.1 / xdist 3.8.0 / anyio 3.7.1 / Faker 7.0.1
```

**结论：不需要降级 pytest。** 5 条自检用例 `4 passed, 1 skipped`，`allure-results/` 产出 11 个文件，`report.html` 35.4 KB（`charset=utf-8`），`-n 2` 并行亦通过。

> ⚠️ 口径提醒：`-n 2` 通过只证明 xdist **能加载**，**不能**当作「并行能提速」的证据（3 条用例 0.37 秒，串行并行无差别）。阶段 3 讲并行耗时必须用真实规模用例集，并同时给出串行基线，否则该数字没有信息量。

### 9.5 `svc.ps1` 的「同 D 互相误杀」全表排查（第 5 个提交）

`Get-Procs` 的进程匹配有两路：**端口** + **命令行含 `D`**。当两个服务的 `D` 相同时，后一路会把对方的进程一并算进来 → `stop A` 连带杀掉 B；反之若某服务的子进程命令行里不含 `D`，则**漏杀**。

对全部 18 个服务的 `D` 做了碰撞分析，共 **3 组**问题，均已用 `M` 修复：

| 组 | 服务对 | 问题 | 修复（M 模式） |
|---|---|---|---|
| 1 | `lunar-be` × `sut` | `sut` 复用 `backend\.venv`，其命令行带 backend 路径 | `lunar-be → 'manage.py runserver 127.0.0.1:8001'` |
| 2 | `rag` × `kbapi` | 同 `D:\Projects\KnowledgeBase-RAG-LLM-System` | `rag → 'app_chat.py'`；`kbapi → 'server.py --port 8020'` |
| 3 | `wht-be` × `wht-worker` | 同 `D:\Projects\WHartTest\WHartTest_Django` | `wht-be → ['run-backend.ps1','manage.py runserver 127.0.0.1:8012']`；`wht-worker → ['run-celery-worker.ps1','-A wharttest_django worker']` |

**实测证据（碰撞对 2，最能说明问题）**

```
wht-be  M1 'run-backend.ps1'              -> 1 个 [2904]        （powershell wrapper）
wht-be  M2 'manage.py runserver ...:8012' -> 2 个 [22288,35148] （venv python + base python 子进程）
wht-wk  M1 'run-celery-worker.ps1'        -> 1 个 [18100]       （powershell wrapper）
wht-wk  M2 '-A wharttest_django worker'   -> 3 个 [12628,32256,27544]
两者共用的旧 D 'WHartTest_Django'          -> 6 个 [两组全混在一起]  ← 旧行为：互杀
```

`stop wht-be` → `已停止 (3 个进程)`（只有 wht-be，wht-worker 的 4 个存活）；
`stop wht-worker` → `已停止 (4 个进程)`（只有 wht-worker，wht-be 3 个存活 + 8012 在听 + HTTP 200）。
`M` 的**数组 OR 分支由此变成真被测**：powershell wrapper 只能被 M1 命中、python 子进程只能被 M2 命中，两者必须 OR 才拼得齐。

**排查中发现的 4 个反直觉事实（都是"想当然"会踩的坑）**

1. **`-like` 不是正则**：反斜杠没有特殊含义。写成 `'run-backend\.ps1'` 要求前面真有一个反斜杠 → 永远匹配不到 `run-backend.ps1`。
2. **`rag` 的进程树是 3 层**：setuptools 的 `streamlit.exe` launcher 会再 exec 一个 **venv python** 子进程，后者又 exec 一个 **base python**（`D:\Python311`）孙进程并持有端口：
   ```
   pid=12456 streamlit.exe  → "...\.venv\Scripts\streamlit.exe" run app_chat.py ...
   pid=18984 python.exe     → "...\.venv\Scripts\python.exe" "...\streamlit.exe" run app_chat.py ...   ← 含 venv python 的绝对路径！
   pid=25696 D:\Python311.. → "D:\Python311\python.exe" "...\streamlit.exe" run app_chat.py ...
   ```
   所以 `kbapi` 的 M **不能**用「该 venv 的 `python.exe` 绝对路径」—— 实测被 rag 的 `pid 18984` 命中（`svc.ps1 start kbapi` 曾因此误报"已在运行"）。
3. **`kbapi` 与 `llm` 的子进程命令行形状完全一样**（都是 `"D:\Python3xx\python.exe" server.py`），只靠 `server.py` 无法区分。最终解法：**把默认端口显式写进启动参数**（`server.py --port 8020`，`server.py` 默认就是 8020，行为不变），用端口做唯一特征。
4. **`wht-worker` 是最危险的**：`Port = 0`（无端口兜底），且 celery 子进程命令行用相对路径 `.\venv\Scripts\celery.exe`，**不含项目绝对路径** → 旧 `D` 匹配既误杀 wht-be、又漏杀自己。`M` 的两模式正好覆盖 powershell wrapper + celery 子进程。

**遗留的同类隐患（本次未改，等你决定）**

`llm` 未设 `M`，用 `D` 匹配。实测它的 `D`（`D:\Projects\WHartTest\llm-server`）**只命中 1 个进程**（父），派生出的子进程 `"D:\Python312\python.exe" server.py --port 11434 --dtype bfloat16` 命令行里不含该路径 → **漏杀**。目前靠 `Port = 11434` 兜住，暂无实际故障。若要与 wht-worker 同样稳妥，可加 `llm → 'server.py --port 11434'`（已验证该串在服务表内唯一）。本次按「减少改动面」原则未动。

### 9.6 补齐 `llm` / `afa`，并实测 `flowfile`（第 6 个提交）

**① `llm` 补 M —— 目的是「规则单一化」而非「怕出事」**

实测进程树与匹配集合：

```
pid=34640  "...\llm-server\.venv\Scripts\python.exe" server.py --port 11434 --dtype bfloat16
pid=33444  "D:\Python312\python.exe"                server.py --port 11434 --dtype bfloat16

候选 M 'server.py --port 11434' -> 2 个 [34640, 33444]   ✓ 父子都覆盖
旧 D 'WHartTest\llm-server'       -> 1 个 [34640]         ✗ 漏子进程
```

`llm` 原本是**唯一**同时靠 D 和 Port 两条规则、且 D 对子进程失效的服务 —— 「一个服务有一部分进程只被端口规则兜住」正是这类 bug 的温床。补 M 后父子两层同源。

⚠️ 口径诚实性：`stop llm` 修复前后**都报 2 个进程**（修复前 D 命中 1 + 端口兜 1，修复后 M 命中 2），所以**计数不能作为 M 生效的证据**，真正证据是上面的模式命中矩阵。

**② `afa` 补 M —— 确认是 P2 潜伏缺陷，不是活跃故障**

实测进程树（3 层，与 `rag` 同形状）：

```
pid=15016 streamlit.exe  "…\Automated-Fundamental-Analysis\.venv\Scripts\streamlit.exe" run app.py --server.port 8503 …
pid=23864 python.exe     "…\.venv\Scripts\python.exe" "…\streamlit.exe" run app.py …
pid=9992  python.exe     "D:\Python310\python.exe" "…\streamlit.exe" run app.py …
```

- `afa` 旧 `D`（`…\Automated-Fundamental-Analysis\WebApp`）命中 **0 个** —— `D` 指向 `\WebApp`，而 `Exe` 在 `\.venv`，两者不相交
- ✅ 但 `stop afa` 在修复前**也没有残留**：杀掉端口持有者（孙进程）后整条 launcher 链**级联自己退出**。所以它"看起来正常"靠的是**进程拓扑的巧合**，不是设计保证 → 定级 **P2 潜伏缺陷**
- 修复后：`stop afa` → `已停止 (3 个进程)`（修复前只声明 1 个），8503 释放、残留 0

**③ `flowfile` 实测 —— 无缺陷（不是第二个 wht-worker）**

全表唯一 `Port = 0` 且原本无 `M` 的服务（零兜底）。实测进程树也是 3 层：

```
pid=16752 flowfile.exe  "D:\Projects\Flowfile\.venv\Scripts\flowfile.exe" run ui --no-browser
pid=21388 python.exe    "…\.venv\Scripts\python.exe" "…\flowfile.exe" run ui --no-browser
pid=8136  python.exe    "D:\Python312\python.exe" "…\flowfile.exe" run ui --no-browser
```

`D`（`D:\Projects\Flowfile`）命中 **3/3** —— 因为它的 `Exe` 就在项目根目录下，三层命令行都含完整 Exe 路径。`stop flowfile` → `已停止 (3 个进程)`，残留 0。**结论：不需要 `M`。**

**全表最终状态（18 个服务）**：`M` 已设 7 个（`lunar-be`、`rag`、`kbapi`、`wht-be`、`wht-worker`、`llm`、`afa`，共 9 条模式），其中 `wht-be` / `wht-worker` 是唯二的 2 模式数组（数组 OR 分支被真测）。每条 `M` 在「服务表区」内只命中所属服务的条目。

### 9.7 SUT auth 模块的实测发现（第 7 个提交）

验证方式：44 项断言的临时脚本，全部通过（脚本已删除，其断言将在阶段 2 落成 `test_01_auth.py`）。

**★ pydantic v1 的宽松转换会让「类型不对 → 422」失效（真实踩到）**

写 `password: str` 时，`POST /api/auth/register {"password": 123456}`（int）**被静默转成 `"123456"` 并通过校验，返回 201**。这与「422 = 参数格式错误（类型不对）」的状态码约定直接冲突。
修复：请求模型改用 `StrictStr` / `StrictInt`，之后 int 类型入参稳定返回 **422 `VALIDATION_ERROR`**。

**LunarLink 平台侧的对照实测（供阶段 2 写用例时对齐预期）**

| 场景 | SUT（新） | LunarLink（平台） |
|---|---|---|
| 错误密码 | `401 BAD_CREDENTIALS` | **`400`**（`rest_framework_jwt` 默认行为，不是 401） |
| 连续失败锁定 | 第 5 次 → `423 ACCOUNT_LOCKED` + `Retry-After` | **无任何锁定机制** |
| 认证头 | `Authorization: Bearer <token>` | `Authorization: <裸 token>` |
| 越权访问他人资源 | 计划返回 `404` | 待阶段 2 实测 |

**锁定边界实测输出（逐条断言）**

```
第 1 次错误密码  HTTP 401  BAD_CREDENTIALS  failed_attempts=1
第 2 次错误密码  HTTP 401  BAD_CREDENTIALS  failed_attempts=2
第 3 次错误密码  HTTP 401  BAD_CREDENTIALS  failed_attempts=3
第 4 次错误密码  HTTP 401  BAD_CREDENTIALS  failed_attempts=4
第 5 次错误密码  HTTP 423  ACCOUNT_LOCKED   Retry-After=60     ← 此时才写 locked_until
锁定期内用正确密码 HTTP 423  ACCOUNT_LOCKED   Retry-After=60     ← 密码对也不放行
后门解锁 → 登录   HTTP 200
先错 3 次→正确登录 HTTP 200；随后再错 4 次仍是 401（计数确实归零）
锁定到期（拨库）→ 正确密码 200；随后第 1 次错误 → 401（计数归零，不是立刻 423）
```

**token 矩阵实测**：无头 `401` / 垃圾 token `401` / **裸 token（无 Bearer）`401`** / **篡改签名末字符 `401`（不是 500）** / 过期 token（后门 `ttl=-10`）`401 TOKEN_EXPIRED` / 后门签发的有效 token `200`。

**其它**：`log_out` no-op 语义与 `refresh` 旧换新均已实测（见第七节）；`ensure_smoke_user` 幂等（连跑两次均为 `updated id=2`）、`SMOKE_PASSWORD` 环境变量优先生效、账号可真实登录成功（`POST /login/ → 200`，裸 token 访问 `/api/lunarlink/project → 200`）。

### 9.8 商品 / 购物车 / 订单 / 支付幂等 实测（第 8 个提交）

范围为**商品 + 购物车 + 订单**全量，支付只做「创建支付单 + 幂等回调」（按约定跳过超时补偿与退款）。
一次性验证脚本 **82 项断言全部通过，0 失败**（脚本已删除，断言将在阶段 2/3 落成用例）。

**SUT 最终接口规模**：24 条路径 / 30 个端点 = 26 业务 + 3 测试后门 + 1 健康检查。

**★★ 并发实测（最核心的两条）**

> ★ **口径（务必按这个说法，不要拔高）**
> 这两条**不能**说成「证明了数据库级防超卖 / 证明了并发正确性」。原因：SQLite 是**单写者**模型，
> 写入本来就被串行化，因此**测不出 MVCC 数据库（MySQL InnoDB / PG）下的竞争表现**。
> 它们真正证明的是：**并发访问下不崩** —— 无死锁、无 `database is locked`、无 5xx，
> 且**计数守恒**（5 成功 / 5 失败 / 最终 `stock=0`）。
> 而「条件更新本身是原子的」这一点是**设计保证**，不是靠这两个测试证明的；
> 测试只是把它在并发路径上验证了一遍、并暴露了锁竞争问题。
> 表述模板：**「并发下无 5xx + 计数守恒；防超卖由单条原子条件更新（`UPDATE ... WHERE stock >= ?`）保证」**。

```
5. 10 线程抢 5 件库存（同 SKU，各自购物车各 1 件，Barrier 同时发起下单）
   成功(201) = 5    失败(409) = 5    其他 = []
   最终库存（落库校验） = 0                 ← 无超卖、无多卖、无 5xx

6. 20 线程同一 notify_id 并发回调
   首次入账(duplicated=False) = 1    幂等命中(duplicated=True) = 19    异常 = []
   payment_callbacks 行数（落库校验） = 1   ← 唯一约束闸门生效，只入账一次
```

**关键设计（均已被断言覆盖）**

| 点 | 实现 | 断言 |
|---|---|---|
| 防超卖 | 事务内单条 `UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?`，靠 `rowcount` 判定；抛错则整个事务回滚。**原子性由这条 SQL 保证**（不是由并发测试证明） | 并发下**无 5xx + 计数守恒**：恰好 5 成功 / 5 失败 / 库存归零 |
| 取消回滚库存 | 取消时按 `order_items` 反向 `stock = stock + qty` | 取消后库存回到下单前 |
| 订单状态机 | 白名单 `config.ORDER_STATE_TRANSITIONS`；非法流转 409 + `detail.from/to/allowed` | `PENDING→COMPLETED` 409、`CANCELLED→CANCELLED` 409、`COMPLETED→CANCELLED` 409、`PAID→SHIPPED→COMPLETED` 200 |
| 水平越权 | 订单/支付单/购物车条目一律先按 `user_id` 过滤，查不到即 404 | 查/取消/确认/发货/创建支付单他人订单 → 404 |
| 垂直越权 | `require_admin` → 403 | 普通用户创建商品 / 发货 → 403 |
| 支付幂等 | `payment_callbacks.notify_id` **UNIQUE** 做闸门；重复回调捕获 `IntegrityError` → 返回首次结果且**不改任何字段** | 重复回调后 `paid_at` / `amount_paid_cents` 不变、`payment_callbacks` 仍 1 行 |
| 分页边界 | `page<1` / `size<1` / `size>100` → 400；`page` 超大 → 200 空列表 | 区分「参数非法」与「无数据」 |
| 加购合并 | `cart_items` 的 `UNIQUE(user_id, product_id)` + 事务内读-判-写 | 首次 201、重复 200 且数量合并；合并后超库存 409 |

### 9.9 第 9 个提交：并发口径修正 + 订单状态流转的安全显式化 + 一个工具坑

**① 并发口径修正（已同步到 9.8 的引用块）**

表述从「证明了数据库级防超卖」收为
**「并发下无 5xx + 计数守恒（5 成功 / 5 失败 / stock=0）；防超卖由单条原子条件更新保证」**。
原因：SQLite 是单写者模型，写入本身被串行化，**测不出 MVCC 数据库（MySQL InnoDB / PG）下的竞争**；
这两个测试的价值在于证明「并发访问不崩」（无死锁 / 无 `database is locked` / 无 5xx），
而原子性是设计保证、不是被测试证明的。

**② 订单状态流转：把隐式旁路改成显式入口（方案 A）**

原实现 `_transition_order(order_no, user_id=None, ...)` 用「可空参数」表达跨归属旁路：
今天安全（只有 `ship_order` 传 `None`，而它挂在 `require_admin` 后面），
但将来任何人漏传 `user_id` 就会**静默变成跨用户管理员操作**。

改为三层显式结构（`sut/store.py`）：

```
_transition(order_no, to_state, *, scope, user_id)     # 内部实现，scope 为必填关键字参数
    scope="user"  -> 必须提供 user_id，SQL 强制按归属过滤（越权自然 404）
    scope="admin" -> 不由 user_id 表达，调用处显式传 None
    非法 scope、或 scope="user" 却不给 user_id -> 直接 ValueError（宁可炸掉也不静默越权）

_transition_order(order_no, user_id, to_state)          # 按归属（user_id 必填）
_transition_order_as_admin(order_no, to_state)          # ★ 全项目唯一跨归属入口：
                                                        #   函数名带 as_admin 标记、签名里没有 user_id
```

`ship_order(order_no)` 现在只走 `_transition_order_as_admin`。

**为什么值得改**（面试口径）：阶段 3 的 `test_08_security` 要测越权；
如果实现里存在隐式旁路，用例通过可能是巧合 —— **显式实现 + 越权用例一起看才是有效证据**。

**重验证**：聚焦脚本 16 项断言全通过（脚本已删），含

```
scope="user" + user_id=None  -> ValueError（拒绝隐式跨归属）      ← 单元级直接验证
非法 scope                    -> ValueError
越权矩阵：查/查状态/取消/确认他人订单 -> 404；普通用户发货 -> 403
状态机：PAID -> SHIPPED（admin 显式入口）-> COMPLETED；终态再流转 -> 409；不存在订单发货 -> 404
★ 并发回归：8 线程抢 3 件 -> 成功 3 / 失败(409) 5 / 库存 0 / 其他状态码 []（无 5xx）
```

**③ 一个会静默吃掉脚本的工具坑（★ 我踩了）**

`python seed.py --reset | Select-Object -First 1` —— 我用它「防止输出刷屏」，
结果 PowerShell 在取到第一个对象后就关闭了管道，Python 在打印第一行后即被中止，
**`seed()` 根本没执行**（数据库连表都没建）。对照实验：

```
seed.py --reset | Select-Object -First 1  ->  sqlite3.OperationalError: no such table: users
seed.py --reset（不加管道）                ->  用户数 = 3
```

**纪律：不要用 `| Select-Object -First N` 去截短原生命令的输出**（它截断的是管道，不是显示）。
要么改用 `Select-Object -Last N`、要么用 `Out-String` 全量取回后自行裁剪、
要么把输出重定向到文件再读。

### 9.10 阶段 2 测试工程收尾（第 10 个提交）

一次性把 `conftest.py`（分层 fixture）、`utils/{client,db,schema,data}.py`、
`schemas/*.json` 与 SUT 的 `POST /api/system/reset` 一起设计落地。

**① `reset` 的并发保护（可被真实触发，不只是代码）**

`reset` 要删除数据库文件，而 Windows 在文件被其它请求持有时会拒绝删除；
若不保护可能「删到一半失败」留下坏库。实现：`sut/runtime.py` 在途计数 +
`app.py` 的 HTTP 中间件，`inflight > 1` 时直接 `409 RESET_BUSY`。

实测（3 次并发 reset + 120 次并发读同时进行）：

```
普通用户 reset                 -> HTTP 403 FORBIDDEN
匿名 reset                     -> HTTP 401 UNAUTHORIZED
造脏数据后 admin reset          -> HTTP 200；users=3 products=10 脏数据残留=0（回到种子状态）
3 次并发 reset                 -> [(409,'RESET_BUSY'), (409,'RESET_BUSY'), (200,'OK')]
120 次并发读状态码分布          -> {200: 120}   ← 无 5xx
/health                        -> 含 "inflight": 1（证明中间件在工作）
```

**② fixture 分层与数据隔离口径**

| 层级 | 内容 | 理由 |
|---|---|---|
| session | 地址、`sut_ready`（未就绪 `pytest.exit` 终止整轮）、`sut_seeded`（**整会话只 reset 一次**）、`admin/buyer/buyer2` 客户端、`sut_db` | 只做一次的事不要每用例重做 |
| function | 用例自己用 `utils/data.unique()` / `create_product()` 造 uuid 独立数据 | 隔离靠造数不靠 reset，用例可任意顺序/并行 |

`buyer2` 的存在意义就是**水平越权**用例：用它访问 `buyer` 的资源，断言 404。

**③ 认证头契约被固定成断言（防止有人"顺手修"后端）**

`utils/client.py` 里 `TOKEN_STYLE_SUT = "bearer"` / `TOKEN_STYLE_LUNARLINK = "bare"` 是唯一改动点；
`test_00` 里有一条 **★ 契约锁定**用例，既断言常量、也**行为验证**（用裸 token 打 SUT 必须 401）。
将来契约变了会在这里失败并指向那两个常量，而不是散落在一堆用例里。

**④ 工具链体检扩到 8 条**（`test_00_report_chain.py`）

新增：fixture 链路（登录态客户端 / 只读 DB / 造数）、JSON Schema 校验（含相对 `$ref` 解析）、
★ 契约锁定。实测 `7 passed, 1 skipped in 0.95s`，`report.html` 与 `allure-results/` 正常产出。

### 9.11 阶段 3 业务测试套件落地（第 11 个提交）

**规模**：139 个用例函数（含 10 处 `parametrize`）展开为 **175 条用例**。

```
$ cd lunarlink_tests
$ ..\backend\.venv\Scripts\python.exe -m pytest --alluredir=allure-results --html=report.html --self-contained-html
174 passed, 1 skipped in 30.61s
产物：allure-results/ 2097 个文件，report.html 147 KB
（唯一 skip 是 test_00 里那条【故意】跳过的用例，用于验证 skip 在报告里的呈现）
```

**各文件用例数**

| 文件 | 条数 | 文件 | 条数 |
|---|---|---|---|
| `test_00_report_chain.py` | 8 | `test_05_payments.py` | 14 |
| `test_01_auth.py` | 33 | `test_06_flow_e2e.py` | 4 |
| `test_02_products.py` | 39 | `test_07_concurrency.py` | 6 |
| `test_03_cart.py` | 20 | `test_08_security.py` | 18 |
| `test_04_orders.py` | 33 | **合计** | **175** |

**场景类型分布**（源码级标记数，可重叠）：`security` 35 / `negative` 29 / `smoke` 26 /
`regression` 23 / `boundary` 13 / `concurrency` 6。

**★ 两条并发断言已落成用例，并从 allure 附件取回实证**

```
test_oversell            -> 并发下单结果 => 成功(201)=5  失败(409)=5  其他=[]  最终库存=0
test_concurrent_same_notify_id -> 并发回调结果 => 首次入账=1  幂等命中=19  异常=[]  payment_callbacks 行数=1
```

断言写法刻意不是「看返回码对不对」，而是：
`others == []`（无 5xx / 无死锁 / 无 database is locked）+ **计数守恒** + **落库校验**。

**并发用例为什么用裸 requests 而不是 ApiClient**：并发线程里共享一个 `requests.Session`
有额外风险，而这里要验证的是**服务端行为**；用裸 requests + 显式 `auth_headers()` 更干净、更可归因。
准备阶段（批量注册、加购）仍串行做，避免把「注册」也卷进并发而污染归因。

**★ 防 xdist 并行守卫（阶段 4 会踩的坑，提前挡住）**

`conftest.py` 顶部：检测到 `PYTEST_XDIST_WORKER` 就 `pytest.exit`。
（★ 该守卫的位置在第 12 个提交里又改了一次 —— 挪到 `pytest_configure` 并区分主进程/worker，
原因见 9.12 第 ② 条：裸写会让 11 个 worker 各报一次。）
原因：`sut_seeded` 是 session 级 + 整会话只 reset 一次，隐含「所有用例共享同一个 SUT 进程」。
一旦 `pytest -n 2`，每个 worker 独立进程 → 各自 reset → 互相重置对方数据，
而且 `RESET_BUSY` 守卫会把另一个 worker 的 reset 打成 409 → fixture 直接报错。
**不加守卫的失败方式是「间歇性 flaky」（最难查）**，加了就是显式报错 + 说明。

实测 `pytest -n 2` 的输出（被 pytest 包成 `ConftestImportFailure`，说明文字完整可见）：

```
_pytest.config.ConftestImportFailure: Exit: 本套件依赖「共享同一个 SUT 进程 + 会话级 reset」，
不支持 xdist 并行。去掉 -n/--dist 参数，或为每个 worker 提供独立的 SUT 实例（不同端口 + 不同 DB）。
```

> 附注：`pytest.exit` 在 conftest 导入阶段抛出的异常会被 pytest 包成 `ConftestImportFailure`
> 并附带一段 traceback。若希望输出更干净，可改在 `pytest_configure` 钩子里做同样的检查。
> 当前保留顶部检查（最早拦截、最简单），行为已满足「显式报错 + 说明」。

**一处防漂移设计**：`test_04_orders.py` 里维护了一份状态机白名单副本 `ALLOWED`，
并有一条用例 `test_whitelist_matches_implementation` **逐条对着实现验证**（合法的断言 200、
非法的断言 409）。这样本地副本与 `sut/config.py` 的漂移会立刻暴露，而不是让矩阵变成装饰。
`REFUNDED` 刻意不写进矩阵 —— 本次跳过了退款，该状态不可达，写进去就是假断言。

### 9.12 阶段 4：CI + README，以及测量过程中揪出的 3 个问题（第 12 个提交）

#### ① 我报错的一条事实：`llm` 的「环境已复原」

上一轮汇报我写「只保留你原本在跑的 `llm`（HTTP 200）」，核对时发现 11434 无监听。
我最后一次检查时它确实是 `YES/HTTP 200`，之后掉线 —— 但 `_logs/llm.err` 已被重启覆盖，
**我无法证明死因，所以不编**。

真正的教训不是"llm 为什么挂"，而是**"环境已复原"这句话必须是汇报时刻的实测值**，
不能拿几分钟前的快照当结论。本轮起改为：**所有操作做完后、写汇报之前，重新实测一次环境**，
并在汇报里写清"实测时刻"。

#### ② xdist 守卫按建议挪到 `pytest_configure` —— 实测比原来更糟，于是改了第二版

你批准「挪到 `pytest_configure`，避免 `ConftestImportFailure` + traceback 污染 CI 日志」。
挪完实测 `pytest -n 2`：

```
  bringing up nodes...
  Exit: 本套件依赖「共享同一个 SUT 进程 + 会话级 reset」，不支持 xdist 并行。   × 11 次
  [gw0] node down: Not properly terminated                                  × 10 行
```

**原因**：`pytest_configure` 在 xdist 下**每个 worker 都会执行**，所以报错被打印了 11 次，
还多了 node down 噪音 —— 比原来的单次 `ConftestImportFailure` 更差。

第二版显式区分主进程与 worker：

```python
def pytest_configure(config):
    if hasattr(config, "workerinput"):
        return  # 本进程是 xdist worker，不重复报错
    if config.getoption("numprocesses", None) or os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.exit("…", returncode=1)
```

**实测结果**：输出 8 行、**只有 1 条 Exit 信息**、无 traceback、无 node down、exit code = 1 ✅。

> 教训：你给的判断方向是对的（顶层报错确实脏），但**"哪个钩子里报"必须实测**——
> xdist 下钩子的执行次数与进程模型强相关，靠推理会得出错误结论。

#### ③ 「锁定自然到期」用例会随启动方式静默 skip —— 已改为后门驱动

测量全量耗时的时候发现：

```
SKIPPED [1] test_01_auth.py:204: 锁定时长 60s 过长，无法在用例内等待。
                                 请以 AUTH_LOCK_SECONDS=2 启动 SUT 后重跑
```

即这道用例**是否执行取决于 SUT 怎么启动的**（我在测量时没带 `AUTH_LOCK_SECONDS`）。
这是一种「环境相关的绿色」，与我们早已立下的「用后门消除 sleep」原则相矛盾。

**修法**：新增后门 `POST /api/_test/user/{username}/expire_lock` ——
只把 `locked_until` 拨到过去、**故意保留 `failed_attempts=5`**，正好命中
「锁定已到期 → 计数归零 → 重新开窗口」这段业务逻辑。

> 为什么不复用手边的 `/unlock`：它会把失败计数一起清零，等于**绕过**了要验证的那段逻辑；
> 用它来测「到期后计数归零」是假断言。

改完后 `test_01_auth.py` 独立跑：**33 passed, 0 skipped**，不再依赖任何环境变量。
`AUTH_LOCK_SECONDS` 配置项保留（业务参数仍可调），但**测试套件不再依赖它**。

#### ④ 耗时对比：按你的要求**不做** HttpRunner 对比，改为同引擎分层标记

采纳你的三条理由（引擎不同导致差值无法归因 / 建等量用例成本高 / 素材已经更强）。
README 里明确写了「**不用这个数字去和上游比快了多少倍**」，并说明原因是口径不可比。

替代口径（同一台机器、同一条命令形态，各采样 3 次取中位数）：

| 命令 | 条数 | 3 次采样（秒） | 中位数 |
|---|---|---|---|
| `pytest -m smoke --alluredir --html` | 26 | 6.57 / 6.66 / 7.14 | **6.7s** |
| `pytest --alluredir --html`（全量） | 175 | 29.20 / 30.39 / 34.23 | **30.4s** |

口径：含**会话级 reset 一次**与 **Allure 附件写入**，不含 SUT 启动时间。
结论表述为「分层标记提供分级反馈通道（提交前跑 26 条 ≈ 7 秒，全量交给 CI）」，
并加了一句防止过度解读：这个价值**随套件规模增长而放大**，冒烟集大小是有意收敛的。

#### ⑤ 阶段 4 产出

| 文件 | 内容 |
|---|---|
| `.github/workflows/daily-regression.yml` | 每日 UTC 18:00 + 手动触发；起 SUT（带后门）→ 轮询 `/health` → pytest → Allure HTML → 上传 artifact |
| `README.md`（追加 12 节） | 改造动机 / 架构图 / 目录结构 / 快速开始 / 用例清单 / 4 层断言体系 / 分层耗时实测 / 四个重点机制 / 设计简化说明 / CI / 平台冒烟（标注未完成）/ 编码约定 |

**CI 的三个刻意选择**（都写进了 workflow 注释）：

1. 生成 Allure HTML **不加 `|| true`** —— 报告流水线坏掉本来就该是红的，
   加了会出现「测试全绿但报告悄悄没了」这种最坏情况。
2. marker 为空时**不传 `-m`** —— `-m ""` 在部分 pytest 版本会被当成非法表达式。
3. 未使用 `simple-elf/allure-report-action`（虽然你建议用现成 Action）——
   已核实它是 `using: docker`、9 个必填入参、且以「部署 gh-pages + 保留历史」为目标；
   我们要的只是一份可下载 HTML。改用官方 `allure-commandline`（runner 预装 Node/Java），
   每一行都可在本地推理验证，避免引入无法在本地验证的第三方依赖。
   Action 的确切入参已写在注释里，你想换回来只需替换 5 行。

> ⚠️ 诚实声明：workflow 的 YAML 语法、`seed.py --reset`、`run.py` 就绪探测、
> `--junitxml` 与 pytest-html 同时使用，**都在本地逐条实跑验证过**；
> 但**尚未在 GitHub Actions 上真实触发**。首次请手动 Run workflow 确认。
>
> 【后续已解决】这一条仅记录当时状态：CI 已于后续上线并**全绿（run #5 success）**，
> 详见 9.15。此处保留原文不改写，以维持变更记录的时间顺序。

### 9.13 平台冒烟套件（test_09，26 条）与平台侧实测发现（第 14 个提交）

#### ① 先合并到 main（13 提交 + 1 合并提交 = 领先 origin/main 14 个）

按你的提醒执行 —— 你指出的前提是对的，我核实并记录：

- **远端默认分支确实是 `main`**（`git symbolic-ref refs/remotes/origin/HEAD` → `origin/main`），
  所以合并到 main 才能让 `on: schedule` 与 Actions UI 的入口生效
- `main` 与 `origin/main` 当时同为 `f4d99ab`，无分叉，不需要先 pull
- 用 `git merge --no-ff` 保留分支结构，合并提交 `b37f279`

#### ② ⚠️ 推送失败，且发现一个更根本的问题（需要你决定）

```
$ git push --dry-run origin main
fatal: unable to access 'https://github.com/tahitimoon/LunarLink.git/':
Failed to connect to github.com:443 after 21083 ms: Could not connect to server
```

网络探测：`github.com:443` 不可达，`ghproxy.net:443` 可达。**所以现在推不上去，
Actions 也就无法触发。**

更根本的问题：`remote.origin.url` 指向 **`tahitimoon/LunarLink`（上游原作者的仓库）**，
而不是你自己的 fork。`git branch -a` 里也没有其它远端分支。这意味着即使网络恢复，
推送是否成功取决于你对那个仓库有没有写权限 —— 这一点我无法替你确认。

待你决定：把 origin 换成你的 fork，还是你确实对该仓库有写权限。

#### ③ test_09 平台冒烟（26 条）完成

| 组 | 条数 | 内容 |
|---|---|---|
| 登录与令牌 | 3 | 登录成功、密码错 400、缺 password 400 |
| 认证头契约 | 4 | **★ 加 `Bearer ` 前缀反而 401**（锁定裸 token 契约）、无头 401、垃圾 token 401、匿名写 401 |
| 只读端点 | 9 | 5 个列表端点的分页信封（含 cursor 分页**无 count** 的差异）、用户列表含冒烟账号且不泄露 password、3 个「不存在」的业务错误码 |
| 参数校验 | 2 | `/api`、`/test` 缺 `project` → 400 |
| 权限边界 | 2 | 非管理员删项目 403、非管理员访问 gitlab-ci 403 |
| 轻量写闭环 | 3 | 创建→列表可见→删除→消失（自清理）、重名 0101、缺必填字段 9999 |
| 已知缺陷 | 3 | `/dashboard`、`/visit`、`/reports` → `xfail(strict=True)` |

**实测结果**：`23 passed, 3 xfailed in 1.25s`，且**单跑 test_09 不需要 SUT**
（8021 未监听时照样跑完，与你的核实一致）。

#### ④ ★ xfail 方向我写反了（实测才发现）

第一版把断言写成「期望 500」，结果 `XPASS(strict)` 报失败。
**原因是 `xfail` 的语义是「本用例预期会失败」，所以用例体必须断言【正确行为】。**
写成断言 500 等于把「平台有缺陷」说成「平台应该 500」，语义是错的。
改成断言 200 后：缺陷在 → 断言失败 → XFAIL（绿）；上游修好 → 断言通过 → XPASS(strict) 失败
→ 提醒撤掉标记。

#### ⑤ 平台侧实测发现 6 条（都已固定成断言或写入 README）

| # | 发现 | 处理 |
|---|---|---|
| 1 | 认证头是**裸 token**，`Bearer ` 前缀 → 401 | 锁成断言（契约一变就报错） |
| 2 | 业务错误用 **HTTP 200** + `{"code":"0102"}` 表达 | 双层断言，避免"只断状态码"的假通过 |
| 3 | `POST /project` 的 `desc`/`responsible` 必填（模型 `null=False` 且无 default） | 按正确请求体写用例 |
| 4 | 缺必填字段只回 `9999 System Error`，不说是哪个字段 | 断言写成"当前确实是 9999"，并注明这不是预期行为，上游改进后失败提醒 |
| 5 | **可移植性缺陷**：`/dashboard`、`/visit` 在 SQLite 下 500（`prepare.py`/`VisitView` 用 `DATE_FORMAT`/`YEARWEEK`） | `xfail(strict=True)`，归因修正见 9.14 |
| 6 | **`ENABLE_LOGIN_ANALYSIS_LOG` 是死配置** —— `conf/env.py` 硬编码 `True`，不读环境变量。它让 `/api/user/login` 每次登录都调外部 IP 归属地 API（timeout 5s） | 本套件统一走 `/login/`（DRF-JWT），不经过该调用；已记录 |

#### ⑥ conftest 修掉一个 order-dependent 隐患 + 新增管理员账号

- **隐患**：原 `platform_admin` 会改写 session 级 `platform` 的 token（`platform.token = ...`），
  于是「platform 到底带不带 token」取决于 fixture 实例化顺序 →
  「未登录访问受保护接口 → 401」这类用例会时对时错。现在 `platform` **永不持有 token**，
  每个身份各自新建客户端。
- **`ensure_smoke_user` 扩展为建两个账号**：非管理员（主力，原设计意图不变）+
  **管理员**（`is_staff=True`，仅供 admin-only 端点）。有了它，写用例能自己清理现场，
  而不是每次留一条脏数据。

> 实测确认：套件跑完平台库 `project` 表**存活行数 = 0**（历史行都是 `is_deleted=1` 的软删行，
> 那是平台的设计）。之前看到的"残留 9 条"是我**手工探针**留下的，已清理。

#### ⑦ CI 增加平台侧：三个必设项（都是实测踩出来的）

| 设置 | 为什么 |
|---|---|
| `cp .env.example .env` | `.env` 被 gitignore，CI 里不存在，而 `conf/env.py` 在 **import 期**执行 `PROXY_PORT = int(os.getenv("PROXY_PORT"))`（**无默认值**）→ 缺它 Django 直接崩 |
| `DATABASE_ENGINE=sqlite` | `settings.py` 里默认是 `mysql` |
| `REDIS_ON=False` / `PROXY_ON=False` | `.env.example` 里是 `True`（本地 `.env` 是 `False`），不覆盖就会去连不存在的 Redis/MQ |

**`load_dotenv() 默认不覆盖已有环境变量`这一点我实测确认过**（不是照文档推断）：
进程环境给 `DATABASE_PORT=9999`、`REDIS_ON=True`，`conf.env` 取到的就是环境变量的值。

#### ⑧ 一个我自己的统计方法错误 + 一个待观察异常

- **`allure-results/` 是累积写入的**。我一度用它数出「1076 条」，其实那是历史上所有运行的累加。
  正确做法是先 `Remove-Item allure-results -Recurse -Force` 再数（已写进 README）。
- **待观察**：有一次全量运行出现 **999.70s 与 1 条 broken**，之后连续复跑均正常
  （最长单测 1.23s，全量 27~34s）。SUT 日志里没有 `database is locked`、
  没有 Traceback、没有 5xx。**我无法解释那一次，不编原因**，作为待观察项留在这里；
  CI 侧由 `timeout-minutes: 15` 兜底。

#### ⑨ 数字更新

| 项 | 值 |
|---|---|
| 用例总数 | **201**（业务套件 175 + 平台冒烟 26）= 197 passed + 1 故意 skip + 3 xfailed |
| 平台冒烟耗时 | 1.25s（26 条） |
| 全量耗时 | 中位 31.4s（27.38 / 31.42 / 34.23） |

### 9.14 纠正一个归因错误 + 平台冒烟合入 main（第 15 个提交）

#### ① ★ 我把「三个端点都 500」错误地归因为「同一个原因」

你的口径提醒（"这是**可移植性缺陷**，不是平台有严重 bug"）让我去复核，
结果发现我原先写进 README、CHANGES 和 `xfail(reason=...)` 的**根因是错的**。

**我原来的说法**：这三个端点都 500，原因是上游 `prepare.py` / `VisitView` 用了
`DATE_FORMAT`、`YEARWEEK` 等 MySQL 专有 SQL。

**抓真实异常后的真相**（三个独立原因）：

| 端点 | 真实异常 | 性质 |
|---|---|---|
| `/dashboard` | `django.db.utils.OperationalError: no such function: DATE_FORMAT`（`prepare.py:166`） | **可移植性缺陷** |
| `/visit?project=1` | 同上（`views/project.py:338` VisitView） | **可移植性缺陷** |
| `/reports` | `ValueError: Cannot use None as a query value`（`report.py:157`） | **独立的 None 处理 bug，与数据库无关** |

你说的是「**2 处**接口使用 MySQL 专有 SQL」—— **你是对的**，
只有 `/dashboard` 与 `/visit` 属于可移植性缺陷。

`/reports` 的真实原因：`search = request.query_params.get("search")` 未传参时是 `None`，
而守卫写的是 `if search != ""`（`None != ""` 为真）→ `filter(name__contains=None)` → ValueError。
**这个缺陷在 MySQL 下同样会 500**，所以它不是可移植性问题。

#### ② 顺带挖出第三个缺陷：**错误告警中间件把真实异常掩盖了**

我一开始**被 500 页面误导**了：三个端点的页面显示的都是
`MissingSchema: Invalid URL '': No scheme supplied.`，而不是上面两个真实异常。原因：

```
真实异常 → backend/utils/middleware.py:121  process_exception
        → apps/lunarlink/utils/qy_message.py:67  send（企业微信告警）
        → requests.post("")   ← QY_WEB_HOOK 是空字符串
        → MissingSchema，把原始异常顶掉
```

**排障时这会把方向带偏**（我本人就是先看到 MissingSchema 才去看 traceback 链的）。

> 教训（已写进 README 与用例注释）：**「多个端点都 500」不等于「同一个原因」**，
> 尤其当错误处理路径本身有毛病时。必须逐个端点拿真实异常。

#### ③ 用例按真实根因拆成两组

原来是一个 `parametrize` 覆盖 3 个端点、共用一条 reason。现在拆成：

- `TestKnownPlatformDefectsPortability`：`/dashboard`、`/visit`（2 条，reason 说明可移植性缺陷 + CI 用 SQLite 会持续暴露它）
- `TestKnownPlatformDefectsNoneHandling`：`/reports`（1 条，reason 说明 None 处理缺陷，并明确指出**这与数据库无关**）

实测：`23 passed, 3 xfailed in 1.37s`（总数不变）。

#### ④ 采纳你的口径修正

README 里改为「平台声明支持 SQLite（`DATABASE_ENGINE=sqlite`），但 2 处代码使用了
MySQL 专有 SQL，导致 SQLite 模式下返回 500」，不再写成"平台有严重 bug"；
并补上你指出的正面副作用：**CI 用 SQLite 跑，正好每天把这个可移植性缺陷暴露一次**
（若 CI 用 MySQL 则会被完全掩盖）。

#### ⑤ 推送问题（Q1）：你的判断我接受，这步留给你手动做

你厘清了这是**两个独立问题**：问题 A（origin 指向上游作者仓库，归属/权限）+
问题 B（github.com HTTPS 被 SNI 层拦截）→ 所以**换 remote 不解决网络问题**。
你说的「换一次网络推上去，而且只需要推一次，之后 Actions 在云端跑」是投入产出比最高的解。

**我没有擅自改 remote**（改了也没用，而且会破坏现在的 ghproxy 拉取配置）。
等你切换网络后，按下面执行（这批动作要在**同一次换网络**里做完）：

```powershell
# 1) 在 github.com 上 fork（或新建）自己的仓库 —— 这一步也需要 github.com 可达
# 2) 把 push 目标换成自己的仓库，保留 upstream 做归属
cd D:\Projects\LunarLink
git remote set-url origin https://github.com/WonderSeeker/LunarLink.git
git remote add upstream https://ghproxy.net/https://github.com/tahitimoon/LunarLink.git

# 3) 推送（main 已含全部改造提交）
git push -u origin main
# 注：两个特性分支（feat/pytest-refactor、feat/platform-smoke）都已并入 main，
#     不必单独推送 —— 「PR 叙事」要靠「先开 PR 再合并」才能留痕，推死分支做不到。

# 4) 推送后在 GitHub 的 Actions 页面手动触发一次 Run workflow 确认
```

#### ⑥ Q2：platform-smoke 合入 main

按你的判断执行 —— 本地合并没有成本，而且 `main` 应该是**完整状态**；
我原先"不想堆推不出去的提交"的顾虑只对**继续开发新功能**成立，这批本来就是同一批。

### 9.15 CI 上线（run #5 全绿）与两条由 CI 暴露的真实缺陷（第 17 个提交）

**状态**：CI 已上线并**全绿** —— `run #5, conclusion=success`
（仓库：https://github.com/WonderSeeker/LunarLink，本地 main 与 origin/main 完全同步，116 个提交）。

> 以下 ①②两项的修复由仓库所有者完成，这里按事实归档；③④的文档同步由本轮补上。

#### ① ★ 浅克隆导致无法推送（网络之外的第二个拦路虎）

**现象**：`git push` 报 `remote: fatal: did not receive expected object <sha>`，
而那个 sha **本地不存在**，`git fsck` 又报**干净**。

**根因**：`D:\Projects\LunarLink` 是上游的 `--depth 1` **浅克隆**
（`.git/shallow` 存在、`git rev-parse --is-shallow-repository` = true，
可追溯提交只有 21 个）。在浅克隆里"缺失的历史"属于**预期缺失**，
所以 `fsck` 查不出来 —— **这两个事实同时成立，就是定位它的唯一线索**。
（我此前把 push 失败**只**归因于网络封锁，漏了这一层；浅克隆这件事我没查。）

**修法**：`git fetch --unshallow upstream` —— 走 ghproxy 即可，**不需要 github.com 可达**。

**量化**：21 → 112 提交；pack 体积只从 **12.22 MiB 涨到 13.46 MiB**
（delta 压缩把历史版本吃掉了，代价极小）。

**已写入 README 快速开始**：用 `--depth 1` 克隆的仓库，push 前先 `git fetch --unshallow`。

#### ② ★ `requirements-test.txt` 漏声明 `requests`（CI 才暴露的依赖缺口）

**现象**：CI 里 `conftest.py:23` **导入期** `ModuleNotFoundError: No module named 'requests'`，
整个套件在**收集期**就崩；而本地 175 条全绿。

**根因**：`conftest.py` 与 `utils/client.py` 直接 `import requests`，
但 `requirements-test.txt` **没有声明它**。本地 venv 里恰好有
`backend/requirements.txt` 装进来的 `requests==2.28.2`，把缺口掩盖了；
CI 只装测试依赖，于是暴露。

**修法**：在 `requirements-test.txt` 显式加 `requests`。

**系统性核对结论**（逐个比对测试与 sut 代码的全部 import 与依赖声明）：
只有 `requests` 是**真缺失**；`allure` / `referencing` / `starlette` 分别是
`allure-pytest` / `jsonschema` / `fastapi` 的**传递依赖**，`sut` 是项目自身包。

**教训（已写进文件注释）**：**测试代码直接 import 的包必须在测试依赖里显式声明，
不能指望传递依赖** —— 因为"本地恰好装过"和"CI 真的装了"是两回事。

> 这条对本地全绿、CI 全红的现象特别有解释力：本地开发环境**从来不是**干净环境。
> 这也解释了为什么"本地 201 条全绿"不能替代 CI 的一次真实运行。

#### ③ CI / 部署说明的补充（两处）

**a) 平台冒烟（test_09，26 条）不在 CI 运行**

原因：跑它必须启动 `backend/`（Django），也就必须装 `backend/requirements.txt`；
而 `requirements-test.txt` 规定**两者完全隔离**。
**实测**：把两者合并装进同一环境后，`sut/seed.py --reset` 直接失败（依赖集冲突）。

所以当前它定位为**【本地套件】**：`cd lunarlink_tests; ..\backend\.venv\Scripts\python.exe -m pytest -m platform`

要放进 CI 的**正确做法**：新增**独立 job + 独立虚拟环境**（不能与本 job 共用），
在该 job 装 `backend/requirements.txt`、启动平台、再跑 `-m platform`。
已知坑：`mysqlclient` 需要系统 `libmysqlclient-dev`（或用 SQLite 时先剔除该行）。
**已写进 workflow 文件末尾的注释**。

**b) workflow 新增两种能力**

- **push 触发器**（`branches: [main]`，`paths-ignore` 掉 `**.md` 与 `screenshots/**`）——
  纯文档改动不浪费 Actions 配额。
- **失败时把 pytest 尾部 40 行发成 GitHub annotation** —— 因为 workflow 运行日志的
  **下载接口需要认证**（匿名请求 403），而 annotation 在公开仓库上**可匿名读取**，
  便于排查。这个设计很实用：把"需要权限才能看的日志"转成了"匿名可见的注解"。

#### ④ 顺带修掉两处我留下的文档不一致（本轮）

1. **workflow 头部注释与正文不符**：头部仍写「CI 要起**两个服务**」并描述平台启动的两个坑，
   但 `b2cede4`/`ef4a5db` 已把平台步骤移除。已改为「本 job 只跑 sut/」，
   并把平台启动的两个坑**标注为"将来新增 platform job 时才需要处理"**（知识保留，但不再误导）。
2. **README 里一句已被证伪的论断**：我原先写「CI 默认用 SQLite 跑，于是这个可移植性缺陷
   **每天都会被自动暴露一次**」。平台冒烟移出 CI 后，CI **根本不碰**那些端点，
   这句话就不成立了。已更正为：该缺陷是**本地平台冒烟套件在 SQLite 上跑**时暴露的，
   并显式标注「CI 目前不跑平台冒烟」。
   > 这是一类很典型的**文档腐化**：结论曾经成立，前提变了却没人回头改。
   > 记在这里提醒自己：**改动某个机制时，要回头搜一遍依赖该机制的表述**。

### 9.16 CI 运行史确认 + 步进摘要 + 修掉 test_09 的「后端未启动却硬失败」缺陷（第 18 个提交）

#### ① 状态：推送成功，CI 运行史已由独立证据确认

```
git push:  4d2fb1e..e069adb  main -> main            ✅  ahead/behind = 0 0
run #6:    success（47 秒，sha=e069adb，event=push）  ← 这次推送触发的，pytest 真跑了 18s
运行史:    #1~#4 failure（调试过程）→ #5 success → #6 success
```

> ★ 我把这条记下来的原因：**运行史是我拿不到的**（我看不到 Actions 页面，
> 且运行日志下载需认证）。此前我只能说"按记录采信"，是仓库所有者用 API 独立查证的。
> 「记录属实」这个结论不该由我自己下 —— 这一点值得在变更记录里写清楚。
>
> 另外，run #6 由 `push` 事件触发这件事，**反向验证了 `paths-ignore` 只忽略 `**.md`** 的判断：
> 该次提交同时改了 `.yml` 与 `.md`，而 workflow 仍然触发 → `.yml` 不在忽略范围内。

#### ② 采纳建议：用 `GITHUB_STEP_SUMMARY` 让绿色运行也能看到用例统计

**动机**：原先「输出用例统计」只 `print`，而运行日志下载接口需认证（匿名 403）——
失败那一半已有 annotation 兜底，**成功那一半什么都看不到**。
`GITHUB_STEP_SUMMARY` 在 public 仓库上匿名可读且直接渲染在 run 页面，正好补上。

**顺带一个附加价值**：它把「**跳过了多少**」变成公开事实。
skipped 数量本身就是警示信号 —— 例如后门没开会让绝大多数用例 skip，
那正是"假的绿色"的典型形态（见下面第 ④ 条）。

**实现后我做了真实预演，而不是"看着对就提交"**：

| 检查 | 结论 |
|---|---|
| YAML dedent 后 Python 缩进 | 打印实际执行的脚本文本逐行核对，无意外缩进 |
| ★ **行尾风险** | 工作区是 CRLF，而 bash 的 heredoc 终止符 `PY\r` **匹配不上 `PY`** → heredoc 不会终止。<br>查 `git ls-files --eol` 得到 **`i/lf w/crlf`** → 索引存的是 LF，runner 拿到 LF，所以没问题。<br>（这也解释了之前的 heredoc 为什么在 CI 上能跑） |
| ★ **真实执行** | 本机有 git 自带的 bash，于是把脚本按 LF 抽出来**真的跑了一遍**：<br>输出 `总计 201 条 / passed 197 / skipped 4 / pytest-html: 180261 bytes`，exit code 0 |
| ★ **中文乱码** | 预演时 summary 里的中文**真的乱码了**（本机 bash 继承了 GBK 控制台）。<br>虽然 runner 上按 PEP 540 一般也是 UTF-8，但这条 summary 是要公开可读的，**不赌"理论上没问题"**：<br>已在 job 级加 `PYTHONIOENCODING: utf-8`，复跑后乱码消失（已验证） |

#### ③ ★ 修掉 test_09 的「后端未启动却硬失败」缺陷（我自己引入的）

**怎么发现的**：做上面第 ④ 条的守卫验证时，我以后门关闭的方式起 SUT 跑了一次全量，
结果除了守卫该失败的那 1 条之外，**还有 8 条 test_09 硬失败**：

```
FAILED test_09_lunarlink_smoke.py::TestLoginAndToken::test_login_success
FAILED test_09_lunarlink_smoke.py::TestLoginAndToken::test_login_wrong_password
FAILED test_09_lunarlink_smoke.py::TestLoginAndToken::test_login_missing_password
FAILED test_09_lunarlink_smoke.py::TestAuthContract::test_bearer_prefix_is_rejected
FAILED test_09_lunarlink_smoke.py::TestAuthContract::test_missing_authorization
FAILED test_09_lunarlink_smoke.py::TestAuthContract::test_garbage_token
FAILED test_09_lunarlink_smoke.py::TestAuthContract::test_anonymous_cannot_write
```

**根因**：只有走 `platform_user` / `platform_admin` 的用例会在平台挂掉时 skip
（`_platform_client` 捕获了 `RequestException`），而用**匿名 `platform` fixture**
或直接 `requests` 的那 8 条会抛 `ConnectionError` 硬失败。

**这意味着我此前在 README 与 CHANGES 里写的「平台没起来时整组 skip」对这 8 条是假的。**
（典型的"文档描述了意图，但实现只覆盖了一部分"。）

**修法**：新增 session 级 `platform_ready` 探测 fixture，并在 test_09 用
`pytestmark = [pytest.mark.platform, pytest.mark.usefixtures("platform_ready")]` 挂成模块级闸门
→ 模块内**每条**用例都先过这道闸。

**踩到的小坑**：我第一版写成 `pytest.usefixtures(...)`，**pytest 9 里这个属性已不存在**
（收集期 `AttributeError`）。必须用 `pytest.mark.usefixtures`。

**修复后实测**（后端关闭时单跑 test_09）：

```
exit code = 0      allure: {'skipped': 26}  总计: 26
```

即 26 条**全部干净 skip**，与文档声称的行为一致。

#### ④ 关于「后门守卫」：实测确认**它已经存在**，所以我没有再加一条重复的

原计划是"新增一条无条件失败的守卫，把后门开着从依赖 env 变成机制保证"。
我先做了负向验证（**以后门关闭的方式起 SUT**，`/health` 返回 `test_hooks:false`，跑全量）：

```
exit code = 1
FAILED test_00_report_chain.py::TestReportChain::test_sut_test_hooks_enabled
```

**结论：这套机制已经生效** —— `test_00` 里的 `test_sut_test_hooks_enabled` 刻意**不依赖
`sut_seeded`**（依赖了就一起 skip，守卫会失效），只依赖 `sut_ready`（它只探 `/health`，
后门关着也返回 200，所以放得过来）。所以后门关闭时整轮**不会假绿**，是红的。

**因此我只做了一件不重复的事**：把它的失败信息改成**可操作**的。
原来只报 `assert 404 == 200`，看的人不知道该做什么；现在直接说明影响与修法：

```
SUT 未开启测试后门（GET /api/_test/ping -> HTTP 404）。
  影响：sut_seeded 会跳过，绝大多数用例随之 skip —— 这轮回归的结果不可信。
  修法：用启动器起服务（它会带上 SUT_ENABLE_TEST_HOOKS=1）：
        cd D:\Projects ; .\svc.ps1 start sut
  不要直接 `python sut/run.py` —— 后门默认是【关闭】的（安全默认）。
```

#### ⑤ 修掉 README 里一处数字腐化

`（--unshallow 的代价很小：实测 21 → 112 提交…）` —— 实际已经是 **117** 个提交。
已加「（当时）」并补一句：**这类计数会随开发增长，以 `git rev-list --count HEAD` 为准，
别信任何文档里冻结的数字**。这是与 9.15④ 同一类腐化，只是这次是数字。

#### ⑥ 数字更新

| 项 | 值 |
|---|---|
| 用例总数 | 201（业务 175 + 平台冒烟 26） |
| 全量（两个服务都在） | 197 passed + 4 skipped，33.51s |
| test_09 单跑 / 平台未启动 | 26 skipped，exit 0 |
| test_09 单跑 / 平台已启动 | 23 passed + 3 xfailed |
| 全量 / 后门关闭 | exit 1，且**只有**守卫 1 条 FAILED |
| 平台库存活项目 | 0（写用例自清理有效） |

### 9.17 让 CI 结论可匿名核实（notice annotation）+ 记录两个决定（第 19 个提交）

#### ① 状态：全部核实通过

仓库所有者独立复核：`origin/main = b7980f6`（0/0、工作区干净）、`run #7 = success`
（由 API 自己读到，不靠转述）、`PYTHONIOENCODING`(L67)、`GITHUB_STEP_SUMMARY`(L215-221)、
`platform_ready` 闸门（conftest + test_09 的 `pytestmark`）、后门守卫的 docstring 与实测依据、
README 的「（当时）」与「别信文档里冻结的数字」。

#### ② 决定：**不做**「独立 platform job」

理由（采纳仓库所有者的判断，记下来避免以后重复讨论）：

1. **成本高、收益低**：要处理 `mysqlclient` / `libmysqlclient-dev` / 完整 Django 运行时 /
   两个 env 坑，而收益只是把**公开可见**的覆盖率从 175 扩到 201 ——
   没有人看着这个数字做决定。
2. **它已经不是问题了**：26 条本地跑通 + step summary 已声明它不在 CI + 两个坑写进 workflow 注释。
3. **它是"可选加分项"，不是缺陷**：加它只会让 CI 更复杂，不会让项目更强。

#### ③ ★ 我发现的一个真实缺口：「summary 无法被第三方核实」

仓库所有者请我打开 run #7 页面确认 `### 用例统计` 区块。**我做不到**，而且这次是**实测**了三条路径：

| 尝试 | 结果 |
|---|---|
| `web_fetch` run #7 页面 | `Request timed out after 10000ms`（与仓库所有者遇到的一致） |
| step summary 走 API | **不存在该端点**；`check-runs.output.summary` 为空字符串 |
| 产物 / 运行日志下载 | 401（需认证） |

**含义**：step summary 虽然"公开可读"，却**只对人眼可读** —— 任何程序化/第三方核实都拿不到它。
这与我们一直在追求的"让结论可被独立核实"是**不一致的**。

**修法（11 行，互补而非替代）**：把统计同时发成一条 **notice annotation**。
依据是这次读注解时的实测发现：**`level=notice` 的注解同样能匿名读取**
（我就是用 `GET /check-runs/{id}/annotations` 读到 GitHub 那条 ubuntu 迁移提示的）。

**本地预演**（bash 按 LF 抽出脚本真实执行）：

```
总计 201 条
  passed   197
  skipped  4
pytest-html: 180261 bytes
::notice::CI 用例统计 —— 总计 201 条 passed 197 skipped 4 pytest-html: 180261 bytes
bash exit code = 0
```

**同时修掉我上一轮的一处不准确表述**：我在 README 里写过
「annotation 与 step summary 在公开仓库上**可匿名读取**」——
但 summary 的**内容走 API 根本读不到**（这一步刚被实测证伪）。
已改写为「**两者互补而非替代**：summary 好看但只能人眼看；annotation 不好看但可被程序与第三方核实」。

#### ④ 顺带发现（**未处理**，仅记录）

读注解时读到一条 GitHub 平台提示：

```
warning: Node.js 20 is deprecated. The following actions target Node.js 20 but are being
forced to run on Node.js 24: actions/checkout@v4, actions/setup-python@v5, actions/upload-artifact@v4
```

**严重度低**（GitHub 会自动把它们跑在 Node 24 上，CI 目前全绿），**我没有动版本号**：
`@v5`/`@v6` 这些标签具体存在与否我没有核实，靠猜去改很可能直接把 CI 改红 ——
那比留着一条 warning 更糟。要处理的话，正确顺序是**先查这些 action 的可用大版本，再改**。

### 9.18 处理两条外部环境变更警告：钉住 runner + 升 action 版本（第 20 个提交）

两条警告都是从 **annotation** 里读到的（它们是 warning/notice 级，页面上不显眼）——
这也算第 9.17 条那个 notice 设计的额外收益。

#### ① 时间敏感：`ubuntu-latest` 将在 2026-10-19 迁到 Ubuntu 26.04

**处理**：`runs-on: ubuntu-latest` → **`ubuntu-24.04`**。

**先核实再改**：用 contents API 取 `actions/runner-images` 的 README，
确认 `ubuntu-24.04` 仍是有效 label（README 里明确列着
`Ubuntu 24.04 | x64 | ubuntu-latest or ubuntu-24.04`），同时看到 `ubuntu-26.04` 已存在。

**为什么必须钉**：runner 换镜像后系统依赖可能变化 —— 一个**跟代码无关的外部变更**
就能让 CI 突然变红。钉住后，镜像升级由我们主动选时机。

#### ② 长期：Node.js 20 弃用（checkout@v4 / setup-python@v5 / upload-artifact@v4 被强制跑在 Node 24）

**先查真实可用大版本**（没有按"建议的版本号"直接写）：

| action | 最新 release | 可用大版本 | 建议值 | **我采用** |
|---|---|---|---|---|
| `actions/checkout` | v7.0.1 | v1…v7 | v5 | **v6** |
| `actions/setup-python` | v7.0.0 | v1…v7 | v6 | **v6** |
| `actions/upload-artifact` | v7.0.2（3 天前） | v1…v7 | v5 | **v6** |

**采用「够用的最小大版本」v6，而不是直接跳最新 v7，两个理由都是具体的**：

1. **`checkout` 取 v6 而不是建议的 v5**：声明 Node 24 的是 **v6**（v6.0.0 release note
   "Node.js 24 support details and requirements"）。**用 v5 很可能清不掉这条警告** ——
   目标是"脱离 Node 20"，不是"数字变大"。
2. **`setup-python` 特意停在 v6、不上 v7**：v6.0.0 的破坏性变更就是"升级到 node 24"（正好达成目标）；
   而 **v7.0.0 的 release note 里有一条 `Remove EOL Python versions`**，
   而 **Python 3.10 恰好在这个月（2026-10）EOL** → 上 v7 有让 `python-version: "3.10"`
   直接装不上的风险，而后端 venv 就是 3.10.11，CI 要对齐它。
   > 「升到最新」在这里**不是**更优解。若将来要上 v7，应先确认 3.10 仍在
   > `actions/python-versions` 的 manifest 里。
3. `upload-artifact` 取 v6 而不是 v7.0.2（发布仅 3 天）：v6.0.0 已明确声明
   `runs.using: node24` 且要求 runner ≥ 2.327.1（托管 runner 远高于此）。
   跳最新**无收益、多一份新版本风险**。

**证据缺口：已缩小，但未完全关闭**（如实记录，供将来升 v7 时决策）

想直接确认 3.10 是否还在 `actions/python-versions` 的 **manifest** 里（setup-python 的版本来源），
四条路径都试过：

| 尝试 | 结果 |
|---|---|
| `Invoke-WebRequest` 取 manifest | 操作超时（60s） |
| `web_fetch` 取 manifest | `Request timed out after 10000ms` |
| 本机 `curl` | exit 35（SSL 证书链问题）—— 仓库所有者尝试 |
| ★ **releases API**（比 manifest 小得多） | **成功**：最近 100 个 release 里有 **9 个 3.10.x**，<br>最新 **3.10.22 发布于 2026-10-01**（约 9 天前） |

**由此可得的结论 —— 分两层，不混说**：

- ✅ **已证实**：`actions/python-versions` **仍在为 3.10 构建并发布**（含本月一次）。
  这大幅削弱了"3.10 已被移除"的担心。
- ❌ **仍未证实**：setup-python **v7** 是否仍能安装 3.10。
  v7 那条 "Remove EOL Python versions" 改的究竟是 manifest 还是仓库内部的测试夹具，
  我没有读到对应 PR；而 manifest 本体用上述四条路径都拿不到。
- 所以缺口从**「未知」缩小到「很可能没问题，但没验」**。
  **结论不变**（当前停在 v6，不影响任何事）。将来真要升 v7 时，
  先按上表第 4 条路径确认 3.10 仍在构建，再决定是升 v7 还是把 CI 与 venv 一起迁到 3.11/3.12。

### 9.19 把 BOM 事故升级成规则 + 两处表述修正（第 22 个提交）

#### ① 事故本身：提交标题带了 BOM

第 21 个提交（`1e12d5a`）的标题首字符是 `U+FEFF`：

```
repr = b'\xef\xbb\xbfdocs: 缩小 9.18 的证…'      # 239,187,191 = UTF-8 BOM
```

**根因是我自己偏离了流程**：前面 20 个提交都用文件写入工具生成消息文件（无 BOM），
这次图省事换成了 PowerShell 的 `Set-Content -Encoding UTF8` ——
而 **PS 5.1 的这个取值总是写 BOM**，`git commit -F` 就把它当标题第一个字符。

> 仓库所有者独立复核：扫最近 25 个提交，**BOM 只有 1 个**（就是 HEAD 那个），
> 并实测 `Set-Content -Encoding UTF8` 写出的文件首 3 字节 = `239,187,191` → 根因成立。

**实际影响（唯一的）**：锚定搜索会漏 —— `git log --grep='^docs:'` 匹配不上；
不带 `^` 的搜索正常。**功能、CI、内容零影响。**

#### ② ★ 决定不修：不是「危险」，而是「收益为零」

**先说结论**：不改写已发布的 `main`、不强推。

仓库所有者给了一处**更精确的表述**，我采纳并记在这里：
**单人仓库用 `--force-with-lease` 其实是安全的**（没人拉过那段历史），
所以这**不是"危险"而是"收益为零"**。两个理由都成立，且第二个更准确 ——
以后遇到同类判断，应该用"收益 vs 成本"而不是一律用"危险"来表述。

#### ③ 升级成规则：同一个 quirk 从**两个相反方向**咬人

本次最有价值的产出不是"修好这个 BOM"，而是**发现原来的约定有歧义**：
`CHANGES` 9.1 与 `README` 第 12 节原先都只写了「`.ps1` **必须**有 BOM」，
**却漏了反向的那一半**（有些文件**必须没有** BOM）—— 而漏掉的一半正好造成了这次事故。

**已把两处都改成三分法**（`CHANGES` 9.1 / `README` §12），并补上"写文件的 API 必须显式选"：

| 文件 | 要求 |
|---|---|
| `.ps1`（PS 5.1 读） | ★ **必须有** BOM |
| `requirements-*.txt` / `.py`（Python 读） | PEP263 声明或 BOM 均可 |
| commit message / `.md` / `.json` / `.yaml` … | ★ **必须没有** BOM |

```powershell
# 要 BOM：       [IO.File]::WriteAllText($p,$t,(New-Object Text.UTF8Encoding($true)))
# 不要 BOM：     [IO.File]::WriteAllText($p,$t,(New-Object Text.UTF8Encoding($false)))
# PS 7 也可：    Set-Content -Path $p -Value $t -Encoding utf8NoBOM
```

**★ 永远不要用 `Set-Content -Encoding UTF8`** —— 它就是这次事故的根因。

> 一句话记法：**`.ps1` 要 BOM，其它都不要**；且**两边的默认值都是错的**
> （PS 5.1 默认**加** BOM，Python `open()` 默认**不加**），所以不能靠默认，必须显式指定。

#### ④ 修正上一轮我对「证据缺口」的方法论（仓库所有者建议，采纳）

原记录只说了"失败的四条路径"，但没提炼出**下次该走哪条**。已补进 9.18②：

> **要确认某个 Python 版本是否还在维护，先查 `actions/python-versions` 的 releases API，
> 不要去拉 `manifest`。**
> 依据：manifest 体积大，`Invoke-WebRequest` 60s 超时、`web_fetch` 10s 硬超时、`curl` SSL 报错，
> 三条路径全撞墙；而 **releases API 一次就成了**（100 个 release 里有 9 个 3.10.x）。

#### ⑤ 归档

```
1e12d5a docs: 缩小 9.18② 的证据缺口 —— python-versions 仍在构建 3.10（最新 3.10.22，2026-10-01）
```
（本行即那个带 BOM 的标题。**我不改写历史**，所以它保持原样留在这里作为记录。）

### 9.20 修复 SQLite 可移植性缺陷（让平台在 SQLite 下真正可用）+ 一处自我更正

#### ① 起因：在界面上撞墙

仓库所有者按我的指引打开前端，走到「进入项目 → 接口管理」时报 **服务器异常**。
查日志得到两个 500：

```
GET /api/lunarlink/project/22    → 500      ← 进项目时的项目详情/概览
GET /api/lunarlink/visit?project=22 → 500   ← 项目看板的访问统计
```

根因链（日志原文）：

```
ProjectView.single
  → prepare.get_project_detail_v2  (prepare.py:322)
    → prepare.get_project_daily_create (prepare.py:308)
      → prepare.get_daily_count        (prepare.py:293)
        → sqlite3.OperationalError: no such function: DATE_FORMAT
```

即**原平台只考虑了 MySQL**，在统计"最近 6 天每天创建了多少"时写死了 `DATE_FORMAT`。
本仓库按 `DATABASE_ENGINE=sqlite` 运行 → **进项目就必 500**，平台的**核心页面根本打不开**。

> 注意这与 9.17 发现的那批是**同一类缺陷的新实例**：
> 之前发现的是 `/dashboard`、`/visit`（以及被我误归因的 `/reports`），
> 这次是 `/project/<pk>`。**同一类缺陷会有多个实例**，修的时候要一起看。

#### ② 顺带更正我自己的一个错误：`/tree` 没坏

我在 9.17 后的沟通中把 `/tree/<pk>` 也列进了"500 端点"。**那是我探测时传错了参数**：

```
GET /api/lunarlink/tree/22?type=1     → 200 ✓   ← 前端实际用的是 type=1
GET /api/lunarlink/tree/22?type=api   → 500     ← 我用了字符串，pydantic 校验失败
pydantic.error_wrappers.ValidationError: 1 validation error for TreeUniqueIn
```

`type` 要的是整数。**`/tree` 一直是好的。**
（好消息：这个错误只出现在对话里，**没有写进 README/CHANGES** —— 我核对过。
 这条记在这里，是为了让"我错在哪"也留痕，而不是悄悄带过。）

#### ③ 修复方案：SQL 按数据库方言生成

**新增两个辅助函数**（`apps/lunarlink/utils/prepare.py`）：

| 函数 | MySQL（原） | SQLite（新） |
|---|---|---|
| `date_format_sql(col, fmt)` | `DATE_FORMAT(col, '%%m-%%d')` | `strftime('%%m-%%d', col)` |
| `date_week_sql(col)` | `CAST(YEARWEEK(col, 1) AS CHAR)` | `strftime('%%Y%%W', col)` |

用 `connection.vendor == "sqlite"` 判断方言。**改动共 5 处表达式 / 2 个文件**：

| 文件 | 改动 |
|---|---|
| `utils/prepare.py` | `get_sql_dateformat()`（周/月/日三处）改用上面两个函数 |
| `utils/prepare.py` | `get_daily_count()` 里那处写死的 `DATE_FORMAT`（**就是本次 500 的那条**） |
| `views/project.py` | `VisitView.list` 里那处写死的 `DATE_FORMAT` |
| `views/report.py` | 顺带修独立缺陷：`if search != ""` → `if search:`（未传参时是 `None`） |

> `%%` 的疑问我先按原代码写法保持一致，然后**实测确认**：Django 的 SQLite 后端
> （`SQLiteCursorWrapper.convert_query` 会做 `.replace("%%", "%")`）会把它还原成 `%`，
> 所以两种数据库都写 `%%` 即可，**不需要**为方言单独做转义 —— 我一开始加了个"还原"逻辑，
> 想清楚后删掉了（不引入无依据的分支）。

#### ④ ★ 验证：重点不是"不报 500"，而是"算出来的值对不对"

这次有一个**比 500 更危险的失败模式**：如果 SQLite 分支的转义写错，
`strftime` 会把 `'%%m-%%d'` 当字面量输出 `"%m-%d"`，标签与 `recent_days` 对不上 →
统计数**静默全 0**：不报错、但结果是错的。

所以验证分两层（用一张**有数据**的表 `project` 走同一条 `extra()` 路径）：

```
① 日期标签正确性
   day   分组 → label='10-11'   counts=1      ← 真日期，不是字面量
   month 分组 → label='202610'  counts=1
② 之前 500 的 5 个请求全部恢复 200
   /project/22 ✓  /visit?project=22 ✓  /dashboard ✓  /reports?project=22 ✓  /reports ✓
③ 那 3 条 xfail(strict=True) 如设计般变成 XPASS 失败 → 提醒撤标记（见 ⑤）
④ 全量回归：200 passed + 1 skipped（此前是 197 passed + 4 skipped）
```

> **如实标注一处局限**：标签正确性是用一次性脚本验证的，**没有做成自动化用例**。
> 原因：要断言"当天创建的条数 ≥ 1"就得先造数据（创建 API/用例/报告），
> 而 test_09 目前只做只读 + 轻量写（项目级）。这条已作为**将来可加的守卫**记在这里。

#### ⑤ 把"记录缺陷"的用例改造成"修复的回归守卫"

`strict=True` 按设计报警（3 条 XPASS → 失败）后，把它们改成正向用例：

| 原 | 现 |
|---|---|
| `TestKnownPlatformDefectsPortability`（2 条 xfail） | `TestSqliteCompatibility`（2 条：断言 200，并在断言消息里写明"改回 MySQL 写法就会红"） |
| `TestKnownPlatformDefectsNoneHandling`（1 条 xfail） | `TestReportListParamHandling`（1 条：**两条分支都走**——不传 search 与传 search，确认修好 None 分支且没写坏筛选分支） |

用例总数**仍是 26**（3 条 → 3 条），所以 README / CI 里所有"26 条"的数字无需改动。
> 刻意用"一条用例走两条分支"而不是 `parametrize` 拆两条：两者在语义上是同一件事
> （这个视图的参数处理），且断言消息里带 `params`，失败时能定位是哪条。

#### ⑥ 一个刻意**没有**做的改动（避免无意义的"统一"）

顺手发现 `views/api.py` 与 `views/suite.py` 里也有同样的 `if xxx != "":` 写法，
**但它们不是 bug**：那些值来自序列化器的 `validated_data`（字段有默认空串），
永远不会是 `None`。而 `report.py` 用的是原始 `request.query_params.get()` 且无默认值 → 才是 bug。

**没有为了"看起来统一"去改没有问题的代码** —— 那只会增加回归面。

#### ⑦ 数字变化

| 项 | 之前 | 现在 |
|---|---|---|
| 全量 | 197 passed + 4 skipped | **200 passed + 1 skipped** |
| `/dashboard` `/visit` `/reports` `/project/<pk>` | 500 | **200** |
| 平台可用性 | 进项目就打不开 | 项目概览/看板/报告页都能用 |

**两处对原设计的偏差（需知悉）**

1. **新增了 `POST /api/orders/{order_no}/ship`（admin 发货）** —— 原设计 6 个订单接口里没有它，
   但 `SHIPPED` 状态将不可达，`confirm`（`SHIPPED→COMPLETED`）永远无法成功、状态机测不完整。
   这是必要的补充，不是擅自扩范围。
2. **支付模块只实现了 `POST /api/payments`、`GET /api/payments/{pay_no}`、`POST /api/payments/{pay_no}/callback`** ——
   按约定跳过超时补偿与退款；`GET` 是为了能断言越权 404 与简化断言而保留的。
