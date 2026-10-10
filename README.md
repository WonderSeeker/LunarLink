# LunarLink

[![每日回归（CI）](https://github.com/WonderSeeker/LunarLink/actions/workflows/daily-regression.yml/badge.svg)](https://github.com/WonderSeeker/LunarLink/actions/workflows/daily-regression.yml)
[![MIT License](./LICENSE)](./LICENSE)
[![Python Version](https://img.shields.io/badge/Python-%3E%3D3.9.5-green)](https://python.org/)
[![Django Version](https://img.shields.io/badge/Django-3.2-blue)](https://docs.djangoproject.com/zh-hans/3.2/)
[![Static Badge](https://img.shields.io/badge/Node-%3E%3D16.0.0-brightgreen)](https://nodejs.org/en)

> **上游与授权**：本项目 fork 自 [tahitimoon/LunarLink](https://github.com/tahitimoon/LunarLink)，
> 遵循 MIT License（`Copyright (c) 2023 geekbing`）。上游的平台主体**未做改动**；
> 本次改造的产出集中在**测试工程部分** —— 见下方「接口自动化测试体系」一节与 [CHANGES.md](./CHANGES.md)。

## 平台简介

基于HttpRunner + Django + Vue + Element UI 的接口自动化测试平台，生产可用。

此外，非常感谢 [花菜](https://github.com/lihuacai168)。没有 AnotherFasterRunner 就不会有 LunarLink :)

---

> ### 📌 关于本仓库的测试体系改造（fork 后的新增内容）
>
> 在保留上游平台功能的前提下，本次 fork 做了一件事：**把用例层从 HttpRunner 重构为
> pytest + Requests + Allure，并新增一套 175 条的真实业务回归套件（自带被测系统）**。
>
> - 改造后的测试体系说明 → [接口自动化测试体系](#接口自动化测试体系pytest--requests--allure)
> - 完整变更记录、设计取舍与量化口径 → [CHANGES.md](CHANGES.md)
>
> 上游平台自身的介绍、部署方式全部保留在下方，未做改动。

## 技术栈

- [x]  🎨 Django
- [x]  🎶 Django Rest framework
- [x]  🎉 Vue.js
- [x]  🎃 Element UI
- [x] 🏐 django-celery-beat（定时任务）
- [x]  🎲 Nginx（反向代理，https配置等）
- [x] 👟 HttpRunner（测试用例执行引擎）
- [x]  🔒 RabbitMQ
- [x]  🚚 Redis
- [x]  💎 MySQL
- [x] ⛏ Docker

## 功能模块

- 登录：账号认证、用户管理、权限管理
- 项目管理：新增项目、列表展示及相关操作，支持项目看板，显示项目每日、每周、每月不同维度数据
- 项目概览：项目基本信息，API、测试用例、任务执行总览，每日明细
- 接口管理：一个API对应后端一个HTTP接口，API可以单独运行，也可以作为一个用例步骤，支持API分组、参数提取、数据断言、变量引用、前后置钩子函数、接口调试、日志显示
- 接口导入：支持同步YApi （以YApi 做媒介间接支持Swagger、Postman、Har），无需手动录入接口
- 测试用例：支持分组管理，由多个API组成，支持关联接口的动态参数传递，不区分场景用例和单接口用例，支持同步、异步批量执行，生成测试报告
- 流量录制：为了适配复杂流程的接口测试场景，打开浏览器，进行一系列操作，就可以得到一个测试用例，不同接口之间的参数自动提取并关联
- 配置管理：自定义不同配置的请求base_url地址，公共请求头参数、局部变量、前后置钩子函数、参数化内容，API和用例可以选择不同配置执行
- 全局变量：定义变量供API使用，变量值也可以引用驱动代码中的函数，通用账号、秘钥可以定义在此处
- 驱动代码：支持Python脚本，定义前后置钩子函数、辅助函数可以轻松实现请求参数签名，加密和解密响应等功能
- 定时任务：可设置定时任务，遵循crontab表达式，可在线开启、关闭，用例执行支持串行、并行，完成后测试报告推送企业微信
- 历史报告：保存定时、调试、异步等不同类型的测试报告，可在线查看、筛选、删除，还可查看他人测试报告

## 准备工作

```
Python >= 3.9.5 (推荐3.9.x版本)
nodejs >= 16.0 (推荐最新)
Mysql >= 5.7.0 (推荐5.7.x版本)
RabbitMQ >= 3.x-management(默认需要，推荐最新版)
Redis >= 6.2.6
```

## 前端 ♝

建议使用yarn，项目提供了`yarn.lock`，使用其他包管理器，容易出现版本依赖问题。

```bash
# 克隆项目
git clone https://github.com/WonderSeeker/LunarLink.git

# 进入项目目录
cd LunarLink/frontend

# 安装依赖
yarn install --registry=https://registry.npmmirror.com

# 启动服务
yarn start
# 浏览器访问 http://127.0.0.1:8888
# config/index.js 文件可配置启动端口等参数
# config/dev.env.js 文件可配置后端接口地址
# config/prod.env.js 文件保持不变
# 构建生产环境
# yarn build
```

## 后端 💈

```bash
# 克隆项目
git clone https://github.com/WonderSeeker/LunarLink.git

# 进入项目目录
cd LunarLink/backend

# 将.env.example文件重命名为.env 并配置相关参数
mv .env.example .env

# 安装依赖环境
pip3 install -r requirements.txt

# 执行迁移命令：
python3 manage.py makemigrations
python3 manage.py migrate

# 创建管理员用户
python3 manage.py createsuperuser

# 启动项目
python3 manage.py runserver

# 开启流量录制代理
python3 proxy.py
```

## 访问项目

```bash
# 登录管理后台，设置账号姓名
http://127.0.0.1:8000/admin/

# 浏览器打开
http://127.0.0.1:8888
用户/密码:管理员用户/密码

# 接口文档地址
http://127.0.0.1:8000/swagger/
```

## 开启定时/异步任务

```bash
# 启动celery，在控制台执行
celery -A backend worker -B --loglevel=info
```

## 演示图 ✅

![](screenshots/1.png)

![](screenshots/2.png)

![](screenshots/3.png)

![](screenshots/4.png)

![](screenshots/5.gif)

## Docker构建

请参考文档[Docker构建](deployment/README.md)

## 鸣谢

感谢 [Chat2Report](https://ch2report.com/)、[Gold Price Per Gram](https://gold-price-per-gram.net/) 友情赞助

## 版权信息

本项目签署了MIT 授权协议，详情请参阅 [LICENSE](LICENSE)

---

# 接口自动化测试体系（pytest + Requests + Allure）

> 本节是本仓库 fork 后的**改造产出**，与上游平台功能无关。变更记录见 [CHANGES.md](CHANGES.md)。

## 1. 为什么把用例层换掉

上游用 HttpRunner 作为用例内核。它的定位是"接口测试引擎"，平台把它包成了「在网页上录接口 → 组用例 → 点执行 → 看报告」。这条路能做起来，但作为**工程化的回归体系**有几处硬伤：

| 上游方式 | 问题 | 改造后 |
|---|---|---|
| 用例是「DB 里的 JSON + 每步一个 unittest 方法」 | 用例不可 code review、不可 diff、不可参数化 | 用例是 `.py`，走 Git 评审与 CI |
| 断言能力受限于内置 comparator | 想断言「库存真的扣了、库存真的回滚了」做不到 | 直连 SQLite **落库断言** |
| 报告由平台模板二次渲染 | 报告无法脱离平台存在、无法作为 artifact 归档 | Allure + pytest-html |
| 无分层标记 | 165 条只能整体跑，无法"提交前快速自检" | `-m smoke` 等 6 类标记 |
| 没有"被测系统" | 平台自身的接口只有 50 条 path，且没有业务语义 | 自带 `sut/`（27 个业务端点，含 4 个 ★ 重点场景） |

## 2. 整体架构

```
        ┌──────────────────────────── CI / 本地 ────────────────────────────┐
        │   .\svc.ps1 start sut          GitHub Actions（每日 + 手动）        │
        └───────────────────────────────┬───────────────────────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │   sut/  —— 自带被测系统 :8021    │
                        │   FastAPI + 标准库 sqlite3       │
                        │   WAL / busy_timeout=15000      │
                        │   21 条业务路径 / 27 个端点       │
                        │   + 4 个测试后门（默认关闭）      │
                        └───────────────▲────────────────┘
                                        │ HTTP（纯黑盒，不 import 被测代码）
   ┌────────────────────────────────────┴────────────────────────────────────┐
   │                        lunarlink_tests/  —— 175 条                       │
   │  conftest.py      分层 fixture：地址 / 就绪检查 / 会话级 reset 只做一次     │
   │                   / admin·buyer·buyer2 客户端 / SQLite 只读视图           │
   │  utils/client.py  ApiClient + auth_headers()  ★ 认证头契约单一入口         │
   │  utils/db.py      SQLite 只读断言（第 4 层断言：关键数据落库）              │
   │  utils/schema.py  JSON Schema（referencing.Registry 解析相对 $ref）       │
   │  utils/data.py    uuid 后缀造数 —— 用例之间零耦合，可任意顺序/独立运行      │
   └────────────────────────────────────┬────────────────────────────────────┘
                                        │
                ┌───────────────────────▼───────────────────────┐
                │  allure-results/  +  report.html（自包含）      │
                │  本地双报告；CI 额外生成 Allure HTML 并上传      │
                └───────────────────────────────────────────────┘
```

## 3. 目录结构

```
LunarLink/
├── sut/                          # 被测系统（FastAPI + SQLite，零外部依赖）
│   ├── app.py                    # FastAPI 装配（顶部写明"路由必须同步 def"的强制约束）
│   ├── config.py                 # 配置中心（全部可用环境变量覆盖）
│   ├── db.py                     # SQLite 封装：WAL + busy_timeout=15000 + 等锁退避重试
│   ├── schema.sql                # 8 张表（tokens / orders / payment_callbacks …）
│   ├── store.py                  # 手写 SQL 数据访问层（不用 ORM）
│   ├── security.py               # pbkdf2 口令哈希 + token/单号生成（标准库）
│   ├── errors.py                 # 统一错误体 + 4 条状态码细则
│   ├── seed.py                   # 种子数据：--reset 一键重建（CI 自举用）
│   ├── run.py / run-sut.ps1      # 启动入口（绑定 127.0.0.1）
│   └── routers/                  # auth / products / cart / orders / payments / system / test_hooks
├── lunarlink_tests/              # pytest 用例工程
│   ├── pytest.ini                # 显式钉 rootdir + 6 类 markers
│   ├── conftest.py               # 分层 fixture + 防 xdist 并行守卫
│   ├── utils/                    # client / db / schema / data
│   ├── schemas/                  # 响应体 JSON Schema（金额一律约束为 integer）
│   ├── test_00…test_08*.py       # 业务套件 175 条（测 sut/）
│   └── test_09_lunarlink_smoke.py  # 平台冒烟 26 条（测 backend/，platform marker）
├── backend/requirements-test.txt # 测试依赖（与后端 requirements.txt 完全隔离）
└── .github/workflows/daily-regression.yml
```

## 4. 快速开始

```powershell
# 1) 起两个被测对象（sut 会自动打开测试后门；都只绑 127.0.0.1）
cd D:\Projects ; .\svc.ps1 start sut,lunar-be

# 1b) 仅平台冒烟（test_09）需要：准备平台数据库与冒烟账号（幂等，可重复执行）
cd D:\Projects\LunarLink\backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py ensure_smoke_user

# 2) 跑全量并产出两份报告
cd D:\Projects\LunarLink\lunarlink_tests
..\backend\.venv\Scripts\python.exe -m pytest --alluredir=allure-results --html=report.html --self-contained-html

#    → 人眼看 report.html（自包含，双击即可）
#    → allure-results/ 可在 CI 或装了 allure CLI 的机器上生成可视化报告
#    ★ allure-results/ 是【累积】写入的，想知道本次跑了多少条要先清空它：
#      Remove-Item allure-results -Recurse -Force

# 3) 只跑冒烟（提交前自检）
..\backend\.venv\Scripts\python.exe -m pytest -m smoke

# 3b) 只跑平台冒烟 / 只跑业务套件
..\backend\.venv\Scripts\python.exe -m pytest -m platform
..\backend\.venv\Scripts\python.exe -m pytest -m "not platform"

# 4) 用完停掉
cd D:\Projects ; .\svc.ps1 stop sut,lunar-be
```

★ 必须用 `python -m pytest`（而不是裸 `pytest`），确保用的是 `backend/.venv` 的解释器。

★ **浅克隆陷阱（推代码前必看，真踩过）**：若你用 `git clone --depth 1` 克隆本仓库（或任何
用来当 push 源的仓库），**push 之前必须先补全历史**：

```powershell
git fetch --unshallow upstream     # 走 ghproxy 即可，不需要 github.com 可达
```

否则 push 会报 `remote: fatal: did not receive expected object <sha>`，而**那个 sha 本地不存在、
`git fsck` 又报干净** —— 因为浅克隆里"缺失的历史"属于**预期缺失**，fsck 查不出来。
这两个事实同时成立，就是定位它的唯一线索。
（`--unshallow` 的代价很小：实测 **21 → 112 提交（当时）**，pack 只从 12.22 MiB 涨到 13.46 MiB，
delta 压缩把历史版本吃掉了。仓库现在已有 117 个提交 —— **这类数字会随开发增长，
以 `git rev-list --count HEAD` 为准，别信任何文档里冻结的计数**。）

## 5. 用例清单（201 条 = 业务套件 175 + 平台冒烟 26）

| 文件 | 条数 | 覆盖内容 |
|---|---|---|
| `test_00_report_chain.py` | 8 | 工具链体检：报告链路、fixture 链路、JSON Schema、★ 认证头契约锁定 |
| `test_01_auth.py` | 33 | 注册边界、登录、**★ 连续失败锁定边界**、token 矩阵、登出与续签 |
| `test_02_products.py` | 39 | 分页/关键字/排序边界、详情、库存、admin 权限与 CRUD |
| `test_03_cart.py` | 20 | 数量与库存校验、同商品合并、越权、清空 |
| `test_04_orders.py` | 33 | 下单扣库存、**★ 状态机矩阵**、取消回滚库存、列表隔离、越权 |
| `test_05_payments.py` | 14 | 支付单幂等复用、**★ 幂等回调**、金额校验、越权 |
| `test_06_flow_e2e.py` | 4 | 主链路 / 取消链路 / 库存不足回滚链路 / 双用户并发链路（带 `allure.step`） |
| `test_07_concurrency.py` | 6 | **★ 防超卖**、**★ 支付幂等并发**、并发加购合并不丢更新、库存上限保护 |
| `test_08_security.py` | 18 | 水平越权 404、垂直越权 403、token 矩阵、信息泄露、参数注入 |
| **业务套件小计** | **175** | 174 passed + 1 skipped（唯一 skip 是刻意用来验证 skip 呈现的用例） |
| `test_09_lunarlink_smoke.py` | 26 | **平台冒烟**（另一个被测对象）：认证头契约、只读端点信封、业务错误码、权限边界、轻量写闭环、3 条已知缺陷 xfail |
| **合计** | **201** | 197 passed + 1 skipped + 3 xfailed |

**场景类型分布**（按 marker 统计，一条用例可带多个标记）：`security` 40 / `negative` 36 /
`smoke` 33 / `regression` 23 / `boundary` 13 / `concurrency` 6 / `platform` 26。

> `test_09` 被测的是 **LunarLink 平台自身**（8001），与前 9 个文件（测 `sut/`）是**两个不同对象**，
> 因此单独用 `platform` marker 统计，`-m platform` / `-m "not platform"` 可分别执行。

## 6. 断言体系（4 层递进）

1. **状态码 + 业务错误码**：`assert r.status_code == 409` **且** `r.json()["code"] == "INSUFFICIENT_STOCK"`。
   只断状态码不够 —— 同一个 409 在业务上有 4 种原因。
2. **JSON Schema**：校验响应结构（字段名/类型/必填）。金额字段一律约束为 `"type": "integer"`（单位：分），
   任何意外引入的浮点或字符串金额都会被结构校验抓住。
3. **业务字段**：累计金额、状态流转、分页 `total/page/size` 的一致性。
4. **★ 关键数据落库校验**：直连 `sut/sut.sqlite3`（只读打开）断言 `products.stock`、
   `orders.status`、`payments.amount_paid/paid_at`、`payment_callbacks` 行数。

> 第 4 层才是把「接口返回对了」升级成「数据真的对了」。**超卖、幂等、库存回滚这三个场景
> 必须靠落库断言才能证明**，只看 HTTP 响应是证明不了的。

## 7. 分层标记与耗时（实测）

同一台机器、同一条命令口径（`--alluredir + --html`），各采样 3 次取中位数：

| 命令 | 条数 | 墙钟时间（3 次采样） | 中位数 |
|---|---|---|---|
| `pytest -m smoke` | 26 | 6.57s / 6.66s / 7.14s | **6.7s** |
| `pytest`（全量，含平台冒烟） | 201 | 27.38s / 31.42s / 34.23s | **31.4s** |
| `pytest -m "not platform"`（仅业务套件） | 175 | 29.20s / 30.39s / 34.23s | **30.4s** |

> ⚠️ 诚实记录：其中有一次全量运行出现 **999.70s 与 1 条 broken**，之后连续复跑均正常
> （最长单测 1.23s）。SUT 日志里没有任何 `database is locked` / Traceback / 5xx 证据，
> **我无法解释那一次，也不编原因**；已作为待观察项记录在本仓库的 CHANGES.md。
> CI 侧靠 `timeout-minutes: 15` 兜住这类挂起。

口径说明：计时包含 **会话级数据重置**（`POST /api/system/reset`，整个会话只做一次）
与 **Allure 附件写入**（每次请求/响应都 attach，便于失败排障）。不含 SUT 启动时间。

**这个数字想说明什么、以及不想说明什么**：

- ✅ 想说明：分层标记提供了**分级反馈通道** —— 提交前跑 26 条冒烟（约 7 秒）拿到快速反馈，
  完整回归交给 CI 夜里跑。这个价值会**随套件规模增长而放大**：冒烟集大小是有意收敛的，
  全量集会一直涨。
- ❌ 不想说明：**不用这个数字去和上游 HttpRunner 比"快了多少倍"**。
  两者引擎、链路、统计口径都不同（平台侧还包含 Django 请求、DB 写入、任务调度、
  报告模板渲染），差值无法归因，那种对比是"像结论但站不住"的数字。
  本套件的价值在于**断言深度与工程化程度**（落库校验 / 并发幂等 / 契约锁定 / 防漂移），不在耗时。

## 8. 四个重点机制怎么实现的

| 机制 | 实现 | 为什么这么做 |
|---|---|---|
| **★ 防超卖** | 下单在 `BEGIN IMMEDIATE` 事务内执行单条 `UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?`，靠 `rowcount` 判定成败；抛错则整个事务回滚 | 条件更新天然原子，避免「先查再减」的 TOCTOU 竞态；这是超卖问题的标准解 |
| **★ 订单状态机** | 白名单 `ORDER_STATE_TRANSITIONS` 驱动，非法流转一律 409 + 回传 `from/to/allowed` | 状态机写成数据而非 if-else，新增状态零改动、错误可观测 |
| **★ 支付幂等** | `payment_callbacks.notify_id` 的 **UNIQUE 约束**做闸门：重复回调触发 `IntegrityError`，捕获后返回首次结果且**不改任何字段** | 用数据库唯一索引做幂等，而不是应用层先查一遍 —— check-then-act 在并发下必然漏 |
| **★ 越权隔离** | 所有订单/支付/购物车的查询 SQL 强制带 `AND user_id = ?`，查不到统一 404 | 见下方「越权为什么返回 404」 |

**并发实测结果**（`test_07_concurrency.py`，断言写在用例里）：

```
10 线程抢 5 件库存  -> 成功(201)=5  失败(409)=5  其他=[]  最终库存(落库)=0（无超卖）
20 线程同一 notify_id 并发回调 -> 首次入账=1  幂等命中=19  异常=[]  回调表(落库)=1 行
```

★ **口径（不拔高）**：SQLite 是单写者模型，写入本身被串行化，因此这两个测试**测不出
MVCC 数据库（MySQL InnoDB / PostgreSQL）下的竞争表现**。它们真正证明的是
**「并发访问不崩」——无死锁、无 `database is locked`、无 5xx，且计数守恒**；
而「防超卖由单条原子条件更新保证」是**设计保证**，不是被这两个测试证明的。
断言写法也据此设计：`others == []` + 计数守恒 + 落库校验，而不是只看返回码。

## 9. 设计简化说明（有意为之，不是遗漏）

| 项 | 说明 |
|---|---|
| **越权返回 404 而不是 403** | 这是**安全设计**：403 会泄露「该资源存在」，404 让「无权访问」与「不存在」在外部不可区分。参考先例：GitHub 对无权访问的私有仓库同样返回 404。与之相对，**垂直越权**（角色不足，如普通用户调 admin 接口）返回 **403** —— 接口本身的存在性不是秘密，明确告知权限不足更有助于调用方定位问题 |
| **SUT 采用单 token 简化设计** | 真实系统应是「access token 短命 + refresh token 长命」双 token。为聚焦测试场景本身，`/api/auth/refresh` 简化为「用仍有效的旧 token 换一个新 token」，不引入 refresh token 轮换/重放那一串问题 |
| **logout 是纯 no-op** | JWT 式无状态鉴权下服务端不主动失效才是诚实的。响应明确返回 `token_revoked: false` 并提示「客户端自行丢弃 token」，不假装服务端已失效。测试也据此断言「登出后旧 token 仍可用」，而不是写一个错误的预期 |
| **测试后门默认关闭，关闭时返回 404（不是 403）** | 后门 `/api/_test/token` 能签发**任意 TTL 的合法 token**，等价于认证绕过。它由 `SUT_ENABLE_TEST_HOOKS` 控制是否**注册路由**（默认关闭），关闭时一律 404 —— 用 403 会暴露「这里存在一个后门」。服务同时强制绑定 `127.0.0.1`，并有红线注释禁止改成 `0.0.0.0` |
| **错误码风格** | 语义化 HTTP 状态码：`422` 参数格式错误（缺字段/类型不对）／`400` 业务参数非法（`page=0`、`keyword` 超长）／`409` 状态冲突（库存不足、非法流转）／`401` 未认证／`403` 垂直越权／`404` 资源不存在或水平越权／`423` 账号锁定 |
| **不引入 pytest-django** | 本套件是**走 HTTP 的黑盒接口测试**，不需要 Django 测试客户端；且后端 Django 3.2.1 较老，引入 pytest-django 容易踩版本兼容问题 |

## 10. CI：每日回归

`.github/workflows/daily-regression.yml`：**每日 UTC 18:00（北京 02:00）自动跑 + 支持手动触发 +
push 到 main 时跑**（纯文档与 `screenshots/**` 改动不触发）。

> ★ **CI 跑的是 175 条业务套件，不是 201 条。** 平台冒烟（26 条）目前是**本地套件**，
> 原因见下方「若要给平台冒烟也上 CI」。

流程：检出 → 装 Python 3.10 + 测试依赖（只装 `backend/requirements-test.txt`）→
`python sut/seed.py --reset` 重建 SUT 数据 → 后台起 SUT（带 `SUT_ENABLE_TEST_HOOKS=1`）→
轮询 `/health` 就绪 → `pytest -m "not platform"` → 生成 Allure HTML →
上传 artifact（Allure 报告 + pytest-html + `junit.xml` + SUT 日志）。

三个刻意的选择：

- **测试依赖与后端运行依赖分文件**（CI 只装 `requirements-test.txt`）。这不是图省事：
  该文件头部明确写着「与 `backend/requirements.txt` 完全隔离、禁止合并」，
  实测把两者装进同一环境后连 `sut/seed.py --reset` 都会失败（依赖集冲突）。
- **生成 Allure HTML 后不加 `|| true`**：报告流水线坏掉本来就该是红的；
  加了会出现「测试全绿但报告悄悄没了」这种最坏情况。
- **让 CI 的结论对匿名读者可核实**（刻意设计，不是顺手加的）：
  workflow 运行日志与产物的下载接口**都需要认证**（匿名请求 403），所以：
  - **失败时** → 把 pytest 尾部 40 行发成 **annotation**：annotation 可匿名读取
    （`GET /repos/{owner}/{repo}/check-runs/{id}/annotations`）。
  - **成功时** → 把用例统计**同时**写进 **`GITHUB_STEP_SUMMARY`**（渲染在 run 页面，人眼看）
    和一条 **notice annotation**（可匿名 API 读取）。
    ★ 两者**互补而非替代**：summary 好看但只能人眼看；annotation 不好看但可被程序与第三方核实。
    （实测依据：`level=notice` 的注解同样能匿名读到；而 step summary 的内容**不在任何 API 里**，
    `check-runs.output.summary` 是空的。）
  - 另一半考虑：**把「跳过了多少」变成公开事实**。skipped 数量本身就是警示信号 ——
    后门没开会让绝大多数用例 skip，那正是"假的绿色"的典型形态。

> ★ 已实测通过：**run #5 `conclusion=success`**（仓库：https://github.com/WonderSeeker/LunarLink）。
> 该次运行由仓库所有者触发。

### 若要给平台冒烟也上 CI

正确做法是**新增一个独立 job + 独立虚拟环境**（不能与本 job 共用环境，否则依赖集冲突），
在那个 job 里装 `backend/requirements.txt`、启动平台、再跑 `-m platform`。
三个已知的坑（都已写在 workflow 头部注释里）：

| 坑 | 说明 |
|---|---|
| `cp .env.example .env` | `.env` 被 gitignore（CI 里不存在），而 `conf/env.py` 在 import 期执行 `PROXY_PORT = int(os.getenv("PROXY_PORT"))`，**无默认值**，缺它 Django 根本起不来 |
| `DATABASE_ENGINE=sqlite` | `settings.py` 里默认是 `mysql`；CI 没有 MySQL |
| `REDIS_ON=False` / `PROXY_ON=False` | `.env.example` 里是 `True`（本地 `.env` 是 `False`），不覆盖就会去连不存在的 Redis / 录制代理。另外 `mysqlclient` 需要系统的 `libmysqlclient-dev`，用 SQLite 时应先剔除该行 |

> `load_dotenv()` 默认**不覆盖**已存在的环境变量，所以 job 级 `env:` 的优先级高于 `.env`
> —— 这一点已在本机实测确认，不是照文档推断的。

## 11. 平台冒烟套件（Platform Smoke）

`test_09_lunarlink_smoke.py`，26 条，用 `platform` marker 单独统计 ——
它测的是**平台自身**（8001），与测 `sut/` 的业务套件是两个被测对象。

**范围纪律**：只做**只读 + 轻量写**，**不碰 `run_api` / `run_testsuite` / `run_suite_tree` 这类
会真正拉起 HttpRunner 执行用例的接口**（慢且不稳定，会把冒烟套件拖垮）。
平台没起来时整组 **skip 而不是 fail** —— 它是辅助套件，不该阻断业务回归。

**执行前需要**：`lunar-be` 在 8001 运行 + `DATABASE_ENGINE=sqlite` + 冒烟账号

```powershell
cd backend
python manage.py migrate
python manage.py ensure_smoke_user      # 幂等：建非管理员账号 + 管理员账号
```
> 单跑 `test_09` **不需要 SUT**（已实测：8021 未监听时 26 条照样跑完，1.25s）。

### 顺手实测出来的平台问题（都已固定成断言）

| 现象 | 结论 |
|---|---|
| **认证头是裸 token** | 加 `Bearer ` 前缀 → 401 `签名解析失败`。与 RFC 6750 不符，但这是实际契约（前端也这么发），见 `utils/client.py` 注释。已锁成断言，实现一变就报错 |
| **业务错误用 HTTP 200 表达** | `GET /project/999999` → **200** + `{"code":"0102","success":false}`。所以只断状态码的用例在这里会"假通过"，必须双层断言 |
| **`POST /project` 要求 `desc` 与 `responsible`** | 二者是 `CharField(null=False)` 且无 `default`/`blank` → ModelSerializer 里成为必填。只传 `name` 会得到 `{"code":"9999","msg":"System Error"}` |
| ★ **缺字段时报 `9999 System Error`** | 不说是哪个字段、也不回 400，调用方只能翻源码。已写成断言并注明"这不是预期行为"，上游改进后会失败提醒 |
| ★ **可移植性缺陷**：`/dashboard`、`/visit` 在 SQLite 下 500 | 平台声明支持 SQLite（`settings.py` 有 `DATABASE_ENGINE=sqlite` 开关），但 **2 处代码**使用了 MySQL 专有 SQL，导致 SQLite 模式下返回 500：`utils/prepare.py` 的 `aggregate_reports_or_case_bydate`（`DATE_FORMAT`/`YEARWEEK`）与 `views/project.py` 的 `VisitView.list`（`DATE_FORMAT`）→ `OperationalError: no such function: DATE_FORMAT`。用 `xfail(strict=True)` 显式记录 |
| ★ **独立的 None 处理缺陷**：`/reports` 不传 `search` 就 500 | **与数据库无关**（MySQL 下同样会坏）：`report.py` 的 `search = request.query_params.get("search")` 未传参时是 `None`，而守卫写的是 `if search != ""`（`None != ""` 成立）→ `filter(name__contains=None)` → `ValueError: Cannot use None as a query value` |
| ★ **错误告警中间件掩盖真实异常** | 上面三个端点的 500 页面显示的**都不是**真实异常，而是 `MissingSchema: Invalid URL ''` —— 因为 `backend/utils/middleware.py` 的 `process_exception` 会去发企业微信告警（`qy_message.send`），而 `QY_WEB_HOOK` 是空串 → `requests.post("")` 直接抛错，把原始异常顶掉。排障时会被引向完全错误的方向 |
| **`ENABLE_LOGIN_ANALYSIS_LOG` 是死配置** | `conf/env.py` 把它**硬编码为 `True`**，不读环境变量，`.env` 里写 `False` 无效。它会让 `/api/user/login` 每次登录都去调外部 IP 归属地 API（timeout 5s）。本套件统一走 `/login/`（DRF-JWT），**不经过那段调用**，所以不受影响 |

> 关于 `xfail(strict=True)` 的写法：用例体断言的是**正确行为（200）**，不是"应该 500"。
> 方向写反会变成 `XPASS(strict)` 报错 —— 我第一版就写反了，实测才发现。

> ★ **这个缺陷为什么能被发现**：它是**本地平台冒烟套件在 SQLite 上跑**时暴露的。
> 若只在 MySQL 上验证，这个可移植性缺陷会被完全掩盖 —— 这正是"在更弱的依赖环境上
> 再跑一遍"的价值。
> ⚠️ 注意：**CI 目前不跑平台冒烟**（见第 10 节），所以它**不是**由 CI 每日暴露的
> —— 这一条我原先写反过（当时计划把平台放进 CI，后来因依赖集冲突移出），特此更正。

> ★ **一次我自己犯的归因错误**：初判时我看到这三个端点都 500，又在 `prepare.py`/`VisitView`
> 里看到 MySQL 专有 SQL，就把它们归为**同一个原因**。抓真实 traceback 后才发现是
> **两个不同缺陷 + 一个掩盖问题**（`/reports` 与 SQL 毫无关系）。教训：
> **"多个端点都 500" ≠ "同一个原因"**，尤其是当错误处理路径本身有毛病时。

## 12. 本地环境与编码约定

```powershell
# 安装/更新测试依赖（★ 用 backend/.venv 的 python，不要用全局 pip）
& 'D:\Projects\LunarLink\backend\.venv\Scripts\python.exe' -m pip install -r backend/requirements-test.txt
```

两个容易踩的编码坑（都已踩过并记录）：

| 谁读该文件 | 要求 | 原因 |
|---|---|---|
| **Python**（`requirements-*.txt`、`.py`） | PEP263 coding 声明 `# -*- coding: utf-8 -*-` 或 UTF-8 BOM 均可 | pip 23.0.1 的 `auto_decode()` 在既无 BOM 又无 coding 声明时，会用系统 locale（中文 Windows = GBK）解码依赖文件，中文注释会触发 `UnicodeDecodeError` |
| **PowerShell 5.1**（`.ps1`） | ★ **必须 UTF-8 with BOM** | PS 5.1 不认 PEP263，会按 GBK 读，非 ASCII 字符直接导致语法错误 |
