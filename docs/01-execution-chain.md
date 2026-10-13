# 执行链路考古 —— 改造前「点一次运行」背后发生了什么

> **用途**：把改造前的执行链路钉死，并从中推出**改造的 3 个理由**。
>
> **范围**：仅描述 **改造前**（fork 后、动手前）的行为。
>
> **纪律**：每条结论带证据（`文件:行`）。推断会显式标注「推断」；未核实标「待验证」。
>
> **配套**：[改造前项目档案](./00-before-refactor.md) · [CHANGES.md](../CHANGES.md)

---

## 0. 一句话结论

改造前的执行链路是：**HTTP 入口 → 从数据库取用例文本 → 每次把驱动代码落盘并 import → 切工作目录 →
交给 HttpRunner（内部用 unittest）执行 → 报告落两张表 → 页面查看时再渲染**。

由此产生三个后果：**用例进不了版本控制、每次执行有固定开销、因 `os.chdir` 而不能安全并发**。

---

## 1. 完整链路图（7 跳）

```
【第 1 跳】前端点「运行」
     │  POST /api/lunarlink/run_test
     ▼
【第 2 跳】apps/lunarlink/views/run.py:261  def run_test(request)
     │   :276-281  前端传了 config → 从数据库取配置
     │             （取不到 → 返回「指定的配置不存在」）
     │   :284      loader.load_test(test=body)
     │             ★ 从【数据库】把用例/接口内容取出来
     ▼
【第 3 跳】apps/lunarlink/utils/loader.py:214  def debug_api(...)
     │   :269   load_debugtalk(project=project)
     │          ★ 把【驱动代码】从数据库取出 → 写成真实 .py → import
     │            ↳ :136 mkdtemp 建临时目录
     │              :140 os.chdir 进去
     │              :142 dump_python_file 写文件
     │              :143 importlib 导入模块（先删 sys.modules 缓存）
     │   :272   os.chdir(临时目录)        ← ★★ 改【进程级】工作目录
     │   :274-282  parse_tests(...)       组装成引擎要的 testcase
     │              （注入 refs.debugtalk + 全局变量）
     │   :285-286  HttpRunner().run(...)  ← ★ 交给测试引擎
     ▼
【第 4 跳】backend/extra_apps/httprunner/（引擎内部）
     │   api.py:82-103   每个 teststep 变成一个 unittest 方法，塞进 TestSuite
     │   api.py:105-124  _run_suite() → self.unittest_runner.run(testcase)
     │                   ★ 引擎内部【用 unittest 在跑】
     │   → runner.py / client.py  真正发 HTTP
     │   api.py:126+     _aggregate() → self.summary
     ▼
【第 5 跳】loader.py:287  parse_summary(...)
     │   loader.py:291  save_summary(...)   ← ★ 报告落库
     │      ↳ :558-570  models.Report.objects.create(...)        → report 表
     │        :572-575  models.ReportDetail.objects.create(...)  → report_detail 表
     ▼
【第 6 跳】loader.py:311-312  finally: chdir 回 BASE_DIR + rmtree 临时目录
     ▼
【第 7 跳】前端拿到 summary 直接渲染；
         之后查看历史报告：views/report.py:191
            render(request, "report_template.html", context=summary)   ← 查看时渲染
```

---

## 2. 逐跳证据表

| 环节 | 文件:行 | 作用 |
|---|---|---|
| HTTP 入口注册 | `apps/lunarlink/urls.py:127-132` | `run_testsuite` / `run_suite_tree` / `run_test` / `run_multi_tests` |
| 用例调试视图 | `apps/lunarlink/views/run.py:261` | `def run_test(request)` |
| 取配置 | `apps/lunarlink/views/run.py:276-281` | 从 DB 取 config；取不到返回「指定的配置不存在」 |
| **从 DB 取用例内容** | `apps/lunarlink/views/run.py:284` | `loader.load_test(test=body)` |
| 用例存储形态 | `apps/lunarlink/models.py:277` | `CaseStep.body` 是 **`TextField`**（文本，不是文件） |
| 组装 testcase | `apps/lunarlink/utils/loader.py:151-211` | `parse_tests()`：注入 `refs.debugtalk` + 全局变量 |
| 装载驱动代码 | `apps/lunarlink/utils/loader.py:127-148` | `load_debugtalk()`：取 code → mkdtemp → 写 `.py` → import |
| 删模块缓存 | `apps/lunarlink/utils/loader.py:106-107` | 切项目后必须删 `sys.modules["debugtalk"]` 才能 reload |
| **切工作目录** | `apps/lunarlink/utils/loader.py:272` | `os.chdir(临时目录)` |
| 交给引擎 | `apps/lunarlink/utils/loader.py:285-286` | `HttpRunner(**kwargs)` + `runner.run(...)` |
| 引擎内部用 unittest | `backend/extra_apps/httprunner/api.py:105-124` | `_run_suite()` → `self.unittest_runner.run()` |
| 汇总结果 | `backend/extra_apps/httprunner/api.py:126-149` | `_aggregate()` → `self.summary` |
| **报告落库（汇总）** | `apps/lunarlink/utils/loader.py:558-570` | `Report.objects.create(...)` |
| **报告落库（明细）** | `apps/lunarlink/utils/loader.py:572-575` | `ReportDetail.objects.create(...)` |
| 恢复 + 清理 | `apps/lunarlink/utils/loader.py:311-312` | `finally: os.chdir(BASE_DIR)` + `shutil.rmtree(...)` |
| 报告渲染 | `apps/lunarlink/views/report.py:191` | `render(request, "report_template.html", context=summary)` |
| 引擎有生成 HTML 的能力但未被平台调用 | `extra_apps/httprunner/report.py:86` / `api.py:260` | `render_html_report(...)` → 全项目搜 `apps/` 目录 **0 处调用** |

---

## 3. 三条取证命令（含作用 / 预期 / 常见错误）

### 命令 ① 一次看到链路上的关键行

```powershell
cd D:\Projects\LunarLink\backend
Select-String -Path apps\lunarlink\utils\loader.py -Encoding UTF8 -Pattern `
  'load_debugtalk\(project=|os\.chdir\(|runner = HttpRunner|runner\.run\(|save_summary\(|shutil\.rmtree' |
  ForEach-Object { "  L{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }
```

| | |
|---|---|
| **作用** | 把执行链路的 6 个关键动作一次列出来，不必通读 600 行 |
| **预期输出**（实测） | `L269 load_debugtalk` / `L272 os.chdir` / `L285 runner = HttpRunner` / `L286 runner.run` / `L291 save_summary` / `L311-312 finally chdir+rmtree` |
| **常见错误** | 忘转义 `(`（正则分组符）；忘 `-Encoding UTF8`（中文匹配不到） |

### 命令 ② 证明「报告落库」

```powershell
cd D:\Projects\LunarLink\backend
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('lunarlink.sqlite3'); print('report =', c.execute('select count(*) from report').fetchone()[0]); print('report_detail =', c.execute('select count(*) from report_detail').fetchone()[0])"
```

| | |
|---|---|
| **作用** | 数两张报告表的行数 |
| **预期输出**（实测） | `report = 14`、`report_detail = 14` —— 一一对应 |
| **常见错误** | 在错误目录执行 → 找不到 `lunarlink.sqlite3` |

### 命令 ③ 证明「临时目录被清理」

```powershell
cd D:\Projects\LunarLink\backend
(Get-ChildItem tempWorkDir -Directory -Force).Count
```

| | |
|---|---|
| **作用** | 看运行时临时目录有无残留 |
| **预期输出**（实测） | **子目录数 = 0**（只剩 `.gitkeep`） |
| **常见错误** | 无（只读检查） |

> 若这个数字长期不为 0 → `finally` 的清理没生效（泄漏）。

---

## 4. ★ 从链路推出的 3 个结构性缺陷（= 改造理由）

| 链路事实 | 缺陷 | 证据 |
|---|---|---|
| 用例内容是**数据库里的一段文本**，执行时才取出 | **无法 git 管理 / diff / code review**；CI 里也没有这些数据 | `views/run.py:284`；`models.py:277`（`TextField`） |
| 驱动代码**每次执行都要**建目录 → 落盘 → import | **每次执行有固定开销**（`mkdtemp` + 磁盘 IO + `importlib.reload`） | `loader.py:136-143`；`:269`、`:342`（debug_api / debug_suite 各走一遍） |
| ★ `os.chdir` 改的是**进程级** cwd，`finally` 还会删掉刚用过的目录 | **不能安全并发** | `loader.py:272` + `:311-312`；`CHANGES.md:27` 亦记为「进程级并发不安全」 |
| 报告先落两张表，**查看时**才渲染 | **没有可归档的报告产物** | `loader.py:558-575`；`views/report.py:191` |

> **诚实标注**：前 3 条是代码事实；第 3 条的**后果**（两个请求互相干扰）是**推断**，
> 我未实测「并发两个请求同时跑」的场景。事实部分（`os.chdir` 是进程级 + `finally` 删目录）已在代码中确认。

### 面试可直接用的一段话

> 「改造前的链路是：HTTP 入口 → 从数据库取用例文本 → 每次把驱动代码落盘并 import → 切工作目录 →
> 交给 HttpRunner（内部用 unittest）执行 → 报告落两张表 → 页面查看时再渲染。
> 这条链路带来三个后果：**用例进不了版本控制、每次执行有固定开销、而且因为 `os.chdir` 不能安全并发**。
> 这就是我要改它的理由。」

---

## 5. 自测题 + 标准答案

**Q1. 用例的内容存在哪里？引擎怎么拿到它？**
A：存在**数据库表字段**里，是一段文本（`CaseStep.body` 为 `TextField`，`models.py:277`）。执行时用 `literal_eval()`
临时取出来转成字典（`views/run.py:284` → `loader.load_test`）。含义：**用例是数据，不是代码** → 不能 diff/review/进 git。

**Q2. 为什么每次执行都要"建临时目录 + 写文件 + import"？能只做一次吗？**
A：不能。驱动代码存在**数据库**里，而 Python **只能 import 磁盘上的 `.py` 文件**，所以每次执行都要
`mkdtemp`（`:136`）→ 写文件（`:142`）→ import（`:143`），并先删 `sys.modules` 缓存（`:106-107`）。
这是**每次执行的固定开销**，清理靠 `finally`（`:311-312`）。

**Q3. `os.chdir` 为什么会导致"不能安全并发"？**
A：因为 **cwd 是进程级全局状态**，而一个进程同时处理多个请求。执行前切到临时目录（`:272`），
执行后切回并删掉该目录（`:311-312`）。**推断**：两个请求并发时会互相改 cwd、甚至删掉对方正在用的目录。
旁证：`CHANGES.md:27` 记为「进程级并发不安全」。这也解释了平台为何需要 Celery 独立进程做异步。

**Q4. 报告存在哪几张表？为什么分两张？**
A：`report`（汇总，`:558-570`）+ `report_detail`（明细，`:572-575`）。理由：汇总小、明细大，
分开存避免列表页把大明细一起查出（冷热分离）。实测 14 : 14 一一对应。

---

## 6. 待验证项

| 项 | 状态 |
|---|---|
| 两个请求并发执行时实际会不会互相干扰 | **待验证**（需实跑并发触发实验；当前只有代码级推断 + 文档旁证） |
| `debug_suite_parallel` 分支的实现细节 | 待验证 |
| `loader.py:466-467` 那条执行路径（第三个 `HttpRunner().run()`）对应哪个视图 | 待验证 |
