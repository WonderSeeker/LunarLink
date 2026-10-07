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
| 阶段 2 | 用例层重构为 pytest + Requests + Allure（`lunarlink_tests/`） | 🚧 工程骨架 + 报告链路已通；业务用例待写 |
| 阶段 3 | 业务测试套件（`sut/` mock 电商 + 100+ 用例，正常/异常/边界/并发/幂等/越权） | 🚧 SUT 骨架 + auth 模块已完成；商品/购物车/订单/支付待做 |
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
| `sut/routers/test_hooks.py` | 测试后门（仅 `SUT_ENABLE_TEST_HOOKS=1` 时挂载）：`/api/_test/ping`、`/api/_test/token`（签发任意 TTL，**等价认证绕过**）、`/api/_test/user/{username}/unlock` |
| `sut/routers/auth.py` | 认证模块：注册 / 登录（连续失败 5 次锁定 423 + Retry-After）/ 登出（纯 no-op）/ profile / refresh（旧换新） |
| `lunarlink_tests/pytest.ini` | pytest 配置：显式钉 rootdir + markers 定义 |
| `lunarlink_tests/conftest.py` | 最小 fixture：`sut_base_url` / `platform_base_url` / 会话级 `sut_ready`（未就绪用 `pytest.exit` 终止整轮） |
| `lunarlink_tests/test_00_report_chain.py` | 工具链体检用例（5 条；业务用例从 `test_01_auth.py` 开始） |
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
