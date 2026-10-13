# 改造前项目档案 —— LunarLink 上游原貌

> **用途**：把「改造前它是什么」一次性钉死，作为后续所有改造结论的参照系。
>
> **范围**：只描述 **fork 之后、动手改造之前** 的状态。
>
> **纪律**：每条结论都带证据（`文件:行`）。凡本档案未亲自核实过的，一律标注「待验证」，不猜。
>
> **配套文档**：[CHANGES.md](../CHANGES.md)（改造记录）、[README.md](../README.md)（改造后体系）

---

## 0. 一句话结论

上游 LunarLink 是一个**跑得起来的**接口自动化测试平台；但它的测试能力被**内置在后端源码里**（HttpRunner），
**只有"网页点一下"这一个出口** —— 这直接决定了它**没法进 CI**。

> 记住这个因果链，它是整个改造的起点：**引擎住进后端 → 用例住进数据库 → 只能从网页触发 → 进不了 CI**。

---

## 1. 它是什么

| 项 | 内容 | 证据 |
|---|---|---|
| 定位 | 基于 HttpRunner + Django + Vue + Element UI 的**接口自动化测试平台**，上游自述「生产可用」 | `README.md:15` |
| 上游仓库 | `tahitimoon/LunarLink` | `README.md:9`；`git remote -v` 的 `upstream` |
| 作者 / 许可 | geekbing / MIT（`Copyright (c) 2023 geekbing`） | `README.md:10`；`LICENSE` |
| 血统 | fork 自 AnotherFasterRunner（上游致谢花菜） | `README.md:17` |
| 前端包名 | `api-web`，描述「一个基于Vue.js的接口自动化测试平台, 提供API用例的录入, 执行和监控」 | `frontend/package.json:2-5` |

---

## 2. 技术栈（含证据）

| 层 | 技术 | 证据（文件:行） |
|---|---|---|
| 后端框架 | Django 3.2.1 | `backend/requirements.txt:33` |
| 接口框架 | Django REST framework 3.14.0 | `backend/requirements.txt:43` |
| 认证 | djangorestframework-jwt 1.11.0 | `backend/requirements.txt:44` |
| 前端框架 | Vue 2.5 + Element UI 2.15.3 | `frontend/package.json:24 / :18` |
| 前端工具链 | **webpack 3** + babel 6 | `frontend/package.json:67 / :35` → 老工具链，**必须 Node 16** |
| **测试引擎** | **HttpRunner 1.5.15（源码内置）** | `backend/extra_apps/httprunner/__about__.py:4` |
| 数据库 | **MySQL 5.7** | `docker-compose.yml:87`；`backend/requirements.txt:81`（mysqlclient） |
| 消息队列 | **RabbitMQ** | `docker-compose.yml:110`；`backend/requirements.txt:2`（amqp） |
| 缓存 / 结果 | Redis 6.2 | `docker-compose.yml:128`；`backend/requirements.txt:110` |
| 异步任务 | Celery 5.2.7 | `backend/requirements.txt:13` |
| 定时调度 | django-celery-beat 2.4.0 | `backend/requirements.txt:35` |
| 生产 WS 服务 | gunicorn / uvicorn | `backend/requirements.txt:53 / :135` |
| 报告渲染 | Jinja2 3.1.2 | `backend/requirements.txt:69` |
| 流量录制 | mitmproxy + ddddocr + opencv | `backend/requirements.txt:77 / :32 / :84` |
| 接口文档 | drf-yasg（Swagger） | `backend/requirements.txt:45` |
| 部署 | Docker Compose + Nginx | `docker-compose.yml` + `deployment/web/nginx.conf` |

> **交叉验证**：上游 README 自述的技术栈（`README.md:31-43`：Django / DRF / Vue / Element UI /
> django-celery-beat / Nginx / **HttpRunner** / RabbitMQ / Redis / MySQL / Docker）与我们从
> `requirements.txt`、`package.json`、`docker-compose.yml` 挖出来的**完全对得上**。

---

## 3. 后端目录结构（关键路径）

```
backend/
├── apps/                     ← Django 业务应用
│   ├── lunarlink/            ← ★ 主业务（63 个 .py）：项目/接口/用例/配置/报告
│   │   ├── models.py         ← 数据模型（API/Case/CaseStep/Config/Variables/Debugtalk/Report/Relation）
│   │   ├── views/            ← 视图（project / api / suite / run / report / debugtalk …）
│   │   └── utils/            ← loader.py / parser.py / prepare.py / runner.py …（执行链路核心）
│   ├── lunaruser/            ← 用户与认证
│   ├── exceptions/           ← 异常定义
│   └── schema/               ← 序列化 / 校验
├── backend/                  ← Django 项目配置（settings / urls / wsgi）+ utils
├── conf/                     ← 配置读取（env.py 读环境变量）
├── crud/                     ← 通用增删改查封装
├── extra_apps/httprunner/    ← ★★ 测试引擎（源码内置，不是 pip 包）
├── record_proxy/ + proxy.py  ← 流量录制（mitmproxy）
├── templates/report_template.html  ← ★ 平台报告模板（查看时渲染用）
├── tempWorkDir/              ← ★ 运行时临时目录（每次执行在此 mkdtemp）
├── logs/
├── manage.py                 ← Django 命令行入口
└── gunicorn_conf.py          ← 生产部署配置
```

**两个要圈起来的目录**：

| 目录 | 为什么重要 |
|---|---|
| `extra_apps/httprunner/` | 测试引擎住在这里 —— **它不是 pip 依赖，是后端代码的一部分** |
| `tempWorkDir/` | 每次执行测试时，后端把「驱动代码」写成 `.py` 放到这里的临时子目录运行（固定开销来源，见第 3 课） |

---

## 4. 运行时形态：`docker-compose.yml` 定义 **7 个服务**

| 服务名 | 端口 | 职责 |
|---|---|---|
| `lunar-link-django` | 8000 | 后端 API（也是调用测试引擎的地方） |
| `lunar-link-web` | 8081 | Nginx + 前端静态 |
| `lunar-link-celery` | — | 异步执行用例的 worker |
| `lunar-link-proxy` | 7778 | mitmproxy **录制代理** |
| `lunar-link-mysql` | 3306 | 数据库 |
| `lunar-link-rabbitmq` | 5672 | Celery 的消息队列（任务排队处） |
| `lunar-link-redis` | 6379 | 缓存 / 结果 |

> `deployment/` 目录按服务分目录（`celery/ django/ mysql/ proxy/ redis/ web/`），与 compose 一一对应。

---

## 5. 功能模块（上游 README 原文 12 项，`README.md:45-58`）

登录 / 项目管理 / 项目概览 / **接口管理** / 接口导入(YApi) / **测试用例** / 流量录制 /
配置管理 / 全局变量 / **驱动代码** / **定时任务** / 历史报告

**与"测试"直接相关的四项**（本次改造的关注点）：

| 模块 | 上游说明（原文摘要） |
|---|---|
| 接口管理 | 一个 API 对应后端一个 HTTP 接口，可**单独运行**，也可作为**用例步骤** |
| 测试用例 | 由多个 API 组成，支持**同步/异步批量执行**，生成测试报告 |
| 驱动代码 | 支持 Python 脚本，可定义辅助函数（签名、加解密等） |
| 定时任务 | 遵循 crontab 表达式，可在线启停，支持串行/并行，完成后推送企业微信 |

---

## 6. 「怎么用」—— 两条路径

### 路径 A：Docker 一键（上游推荐）

```bash
cp .env.example .env        # 准备配置文件（docker-compose 的 env_file 指向它）
docker compose up -d        # 起 7 个服务
# 浏览器访问前端：http://<主机>:8081
```

| | |
|---|---|
| **作用** | 一次性拉起后端/前端/MySQL/Redis/RabbitMQ/proxy/celery |
| **预期** | `docker compose ps` 显示 7 个容器 running |
| **常见错误** | ① 没 `cp .env.example .env` → `env_file: .env` 找不到文件直接报错<br>② Apple Silicon 上 `mysql:5.7` 架构不符（compose 文件第 86 行有注释：改用 `mysql/mysql-server:5.7`）<br>③ 8081 端口被占用 |

### 路径 B：本地手动（我们实际使用的方式）

```powershell
# 后端
cd D:\Projects\LunarLink\backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001 --noreload

# 前端（另一个终端，★ 必须 Node 16）
cd D:\Projects\LunarLink\frontend
npm install
npm run dev        # → http://127.0.0.1:8888
```

| | |
|---|---|
| **作用** | 不起 MySQL/Redis，用 SQLite 跑起来，便于改代码 |
| **预期** | 8001 有响应；8888 打开是登录页 |
| **常见错误** | ① Node 版本不对（webpack 3 在 Node 17+ 上会因 OpenSSL 报错）<br>② 忘了 `migrate` → 表不存在<br>③ 用 `--noreload` 起后端时**改代码不生效**，必须重启 |

---

## 7. 我们本地跑的「简化版」——差异表

| 维度 | 上游标准 | 我们本地 | 原因 |
|---|---|---|---|
| 数据库 | MySQL 5.7 | **SQLite** | 本机未安装 MySQL（`CHANGES.md:50`） |
| Redis / RabbitMQ | 都要 | **都不起**（`REDIS_ON=False` / `PROXY_ON=False`） | 属"异步/定时"能力，与"重构用例层"目标无关 |
| WS 服务器 | gunicorn | `manage.py runserver` | 本地调试不需要生产服务器 |
| 前端 | Nginx(8081) | `npm run dev`(8888) | 开发服务器便于改代码 |
| 端口 | 8000 / 8081 | **8001 / 8888** | 本地端口对齐（`frontend/config/dev.env.js`） |

**为什么要简化**：目标是把**测试层**重构成「能在最少依赖下跑、并能在 CI 里复现」的形式。
简化掉的每一项，都是为了让测试**不依赖生产环境组件**。

---

## 8. ★ 为什么它没法直接进 CI（4 条，逐级推导）

| # | 原因 | 证据 |
|---|---|---|
| 1 | **测试引擎是后端代码的一部分** → 想跑测试，必须先起一整套 Django + 数据库 | `backend/extra_apps/httprunner/`；调用点 `apps/lunarlink/utils/loader.py:285-286` |
| 2 | **用例存在数据库的表里** → CI 里没有这些数据，除非先造/导 | `apps/lunarlink/models.py:267` `CaseStep.body` 为 `TextField` |
| 3 | **没有命令行出口** → 执行入口全是 HTTP 接口，要进 CI 只能"模拟前端调接口 → 等结果 → 读库取报告" | `apps/lunarlink/urls.py:127-132`（`run_testsuite` / `run_suite_tree` / `run_test` / `run_multi_tests`） |
| 4 | **报告是"查看时渲染"** → 没有可归档的产物文件 | `apps/lunarlink/views/report.py:191` → `render(request, template_name="report_template.html", context=summary)`；模板：`backend/templates/report_template.html` |

**一句话**：执行模型 = 「网页点一下 → 后端同步跑 → 结果写回数据库 → 页面渲染」，**没有为"无人值守"设计出口**。

---

## 9. 考古细节：一个被闲置的能力

HttpRunner **自己具备生成 HTML 报告文件的能力**：

```python
# backend/extra_apps/httprunner/report.py:86
def render_html_report(summary, html_report_name=None, html_report_template=None):
    ...
    # 把渲染结果写进文件（report.py:131-135）
```

调用方在 HttpRunner 自己的 API 里：

```python
# backend/extra_apps/httprunner/api.py:260-262
        return report.render_html_report(
            self.summary, html_report_name, html_report_template
        )
```

**但平台从未调用它** —— 在 `backend/apps/` 全目录搜索 `render_html_report`，**0 处匹配**。

| 谁 | 是否用 `render_html_report` |
|---|---|
| HttpRunner 自己的 CLI/API | 用（`api.py:260`） |
| **LunarLink 平台** | **不用**（改用 Django `render()` 在查看时渲染页面） |

> **结论**：「能产出报告文件」这个能力被闲置了。这解释了为什么「报告无法归档」会成为本次改造的一个动因。

---

## 10. 待验证清单（本档案未核实，不猜）

| 项 | 状态 |
|---|---|
| `deployment/README.md` 内容 | 待验证 |
| 根目录 `.env.example` 与 `backend/.env.example` 是否同一份、各自用途 | 待验证 |
| `record_proxy/` 的具体实现细节 | 待验证（仅从依赖与模块名推断为 mitmproxy 录制） |
| `.idea/` 是否被 `.gitignore` 忽略 | 待验证 |

---

## 11. 自测题（附答案）

**Q1. 测试引擎叫什么？住哪个目录？pip 装的还是源码内置？**
A：**HttpRunner 1.5.15**；住 `backend/extra_apps/httprunner/`；**源码内置**，不是 pip 依赖
（证据：`__about__.py:4`；`requirements.txt` 147 行全文无 httprunner）。

**Q2. 标准部署起了几个服务？各职责？**
A：**7 个**：django(8000) 后端 / web(8081) 前端 / celery 异步 worker / proxy(7778) 录制代理 /
mysql(3306) 数据库 / rabbitmq(5672) 队列 / redis(6379) 缓存（证据：`docker-compose.yml`）。

**Q3. 本地简化掉了什么？为什么？**
A：MySQL→SQLite、Redis/RabbitMQ 不起、runserver 代替 gunicorn、npm run dev 代替 Nginx、
端口 8000/8081→8001/8888。因为目标是**改造测试层**，不是搭生产环境；简化后测试能在最少依赖下跑，并能被 CI 复现。

**Q4. 为什么没法直接进 CI？**
A：①引擎是后端代码的一部分，跑它要先起 Django+DB ②用例存在数据库表里 ③只有 HTTP 入口，没有命令行出口
④报告查看时渲染，无可归档产物。本质是**没有为无人值守设计出口**。
