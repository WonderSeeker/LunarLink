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
| 阶段 4 | GitHub Actions 每日回归 + README（架构图 / 用例清单 / 耗时口径） | 🚧 workflow（含 SUT + 平台两个服务）与 README 已完成并本地逐条验证；平台冒烟 26 条已完成；**⚠️ 推送被阻断（github.com:443 不可达）且 origin 指向上游仓库，Actions 尚未真实触发 —— 需你先解决远端** |

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
| `.github/workflows/daily-regression.yml` | 每日回归：定时 + 手动触发，起 SUT + 起平台 → pytest（201 条）→ Allure HTML → 上传 artifact |
| `lunarlink_tests/test_09_lunarlink_smoke.py` | 平台冒烟 26 条（`platform` marker）；含认证头契约锁定、业务错误码、权限边界、轻量写闭环、3 条已知缺陷 xfail |
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

### 9.1 文本文件编码：两类文件两套规则

| 谁读这个文件 | 要求 | 原因 |
|---|---|---|
| **Python**（`requirements-*.txt`、`.py` 源码等） | PEP263 coding 声明 或 UTF-8 BOM 都行 | pip 23.0.1 的 `auto_decode()` 在**既无 BOM 又无 coding 声明**时退回 `locale.getpreferredencoding()`；中文 Windows 下 locale 是 GBK，于是含中文注释的依赖文件直接 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80`。已在 `backend/requirements-test.txt` 第 1 行加 `# -*- coding: utf-8 -*-` 解决 |
| **PowerShell 5.1**（`.ps1` 脚本） | ★ **必须 BOM** | PS 5.1 不认 PEP263，只会按 ANSI（中文系统 = GBK）读，非 ASCII 字符会导致乱码甚至语法错误。`D:\Projects\svc.ps1` 与 `sut\run-sut.ps1` 均已校验 `BOM=True` |

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
| 5 | **3 个端点在 SQLite 下必定 500**（`prepare.py`/`VisitView` 用 `DATE_FORMAT`/`YEARWEEK`） | `xfail(strict=True)` |
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

**两处对原设计的偏差（需知悉）**

1. **新增了 `POST /api/orders/{order_no}/ship`（admin 发货）** —— 原设计 6 个订单接口里没有它，
   但 `SHIPPED` 状态将不可达，`confirm`（`SHIPPED→COMPLETED`）永远无法成功、状态机测不完整。
   这是必要的补充，不是擅自扩范围。
2. **支付模块只实现了 `POST /api/payments`、`GET /api/payments/{pay_no}`、`POST /api/payments/{pay_no}/callback`** ——
   按约定跳过超时补偿与退款；`GET` 是为了能断言越权 404 与简化断言而保留的。
