# -*- coding: utf-8 -*-
"""LunarLink 平台自身 API 的冒烟套件（Platform Smoke）。

============================================================================
这个文件与 test_00 ~ test_08 是【两个不同的被测对象】：
    test_00 ~ test_08  → sut/（自建 mock 电商，8021）
    test_09（本文件）  → LunarLink 平台自身（backend，8001）
所以它用 `platform` marker 单独统计，README 里也单独列一栏。
============================================================================

范围纪律（有意为之）：
    * 只做【只读 + 轻量写】，且写用例自己清理现场
    * ★ 不碰 run_api / run_testsuite / run_test / run_suite_tree / run_multi_tests
      这类会真正拉起 HttpRunner 执行用例的接口 —— 它们慢且不稳定，
      会把一个本该秒级跑完的冒烟套件拖垮。
    * 平台没起来时整组 skip（不是 fail）：它是辅助套件，
      不应该阻断业务套件的回归。

本文件同时把【实测发现的平台契约与缺陷】固定成断言，而不是只写"能通就行"：
    1. 认证头是【裸 token】，加 "Bearer " 前缀一律 401（契约锁定）
    2. 平台用 HTTP 200 + {"code":"0102"} 表达「资源不存在」这类业务错误 ——
       所以只断状态码是没有意义的，必须双层断言
    3. POST /project 的 desc / responsible 是必填（模型 null=False 且无 default）
    4. 平台有 3 个端点在 SQLite 下必定 500（上游 prepare.py 用了
       DATE_FORMAT / YEARWEEK 等 MySQL 专有 SQL）—— 用 xfail 显式记录，
       而不是悄悄跳过（见下方 TestKnownPlatformDefects）
"""

import uuid

import allure
import pytest

# ★ platform_ready 是「平台不可达就整组 skip」的唯一闸门。
#   不加它的话，用匿名 platform / 直接 requests 的那些用例会在平台挂掉时硬失败，
#   而走 platform_user / platform_admin 的用例却会 skip —— 同一模块行为不一致（实测踩过）。
#   注意：`pytest.usefixtures` 这个写法在 pytest 9 里已不存在（收集期 AttributeError），
#   必须用 `pytest.mark.usefixtures`。
pytestmark = [pytest.mark.platform, pytest.mark.usefixtures("platform_ready")]


def _unique_name(prefix: str = "smoke") -> str:
    """uuid 后缀造数：用例之间零耦合，可任意顺序执行。"""
    return "{}-{}".format(prefix, uuid.uuid4().hex[:10])


def _project_body(name: str):
    """POST /project 的最小合法请求体。

    ★ desc / responsible 必填（Project.desc、Project.responsible 是
      CharField(null=False) 且既无 default 也无 blank=True）—— 实测结论，
      只传 name 会得到 {"code":"9999","msg":"System Error"}。
    """
    return {"name": name, "desc": "自动化冒烟创建", "responsible": "smoke"}


# ============================================================================
# 一、登录与令牌
# ============================================================================


@allure.feature("平台冒烟")
class TestLoginAndToken:
    @allure.title("冒烟账号可正常登录并拿到 token")
    @pytest.mark.smoke
    def test_login_success(self, platform):
        resp = platform.post(
            "/login/",
            json={"username": "smoke_regression", "password": "smoke123456"},
        )
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body.get("token"), "登录成功却没有返回 token: {}".format(body)
        # 顺带锁定：平台上还有另一个登录入口 /api/user/login（LoginView），
        # 本套件统一只走 /login/（DRF-JWT 的 obtain_jwt_token）。
        allure.attach(str(list(body.keys())), "登录响应字段", allure.attachment_type.TEXT)

    @allure.title("密码错误 -> 400 且带 non_field_errors")
    @pytest.mark.smoke
    @pytest.mark.negative
    def test_login_wrong_password(self, platform):
        resp = platform.post(
            "/login/", json={"username": "smoke_regression", "password": "definitely-wrong"}
        )
        assert resp.status_code == 400, resp.text[:300]
        assert "non_field_errors" in resp.json()

    @allure.title("登录缺 password 字段 -> 400 且明确指出缺哪个字段")
    @pytest.mark.negative
    def test_login_missing_password(self, platform):
        resp = platform.post("/login/", json={"username": "smoke_regression"})
        assert resp.status_code == 400, resp.text[:300]
        assert "password" in resp.json()


# ============================================================================
# 二、认证头契约（锁定「裸 token」这个非 RFC 风格的实际契约）
# ============================================================================


@allure.feature("平台冒烟")
class TestAuthContract:
    @allure.title("★ 加 Bearer 前缀反而被拒 -> 锁定平台使用裸 token 的契约")
    @pytest.mark.smoke
    @pytest.mark.security
    def test_bearer_prefix_is_rejected(self, platform_base_url):
        import requests

        token = requests.post(
            platform_base_url + "/login/",
            json={"username": "smoke_regression", "password": "smoke123456"},
            timeout=20,
        ).json()["token"]

        resp = requests.get(
            platform_base_url + "/api/lunarlink/project",
            headers={"Authorization": "Bearer " + token},
            timeout=20,
        )
        assert resp.status_code == 401, (
            "平台竟然接受了 Bearer 前缀？说明认证实现变了，"
            "需同步更新 utils/client.py 的 TOKEN_STYLE_LUNARLINK。响应：{}".format(
                resp.text[:200]
            )
        )

    @allure.title("无 Authorization 头 -> 401")
    @pytest.mark.smoke
    @pytest.mark.security
    def test_missing_authorization(self, platform):
        resp = platform.get("/api/lunarlink/project")
        assert resp.status_code == 401, resp.text[:300]

    @allure.title("垃圾 token -> 401 且不泄露内部信息")
    @pytest.mark.security
    def test_garbage_token(self, platform):
        resp = platform.get(
            "/api/lunarlink/project", headers={"Authorization": "not-a-jwt-at-all"}
        )
        assert resp.status_code == 401, resp.text[:300]
        # 只回一句通用文案，不回栈信息 / 不回"token 解析失败于第 N 字节"
        detail = str(resp.json().get("detail", ""))
        assert "Traceback" not in detail and "File " not in detail

    @allure.title("匿名写操作 -> 401（鉴权在权限之前生效）")
    @pytest.mark.security
    def test_anonymous_cannot_write(self, platform):
        resp = platform.post("/api/lunarlink/project", json=_project_body(_unique_name()))
        assert resp.status_code == 401, resp.text[:300]


# ============================================================================
# 三、只读端点可用性（平台「活着」的最基本证据）
# ============================================================================


@allure.feature("平台冒烟")
class TestReadOnlyEndpoints:
    @allure.title("列表端点 {path} 返回 200 + 分页信封")
    @pytest.mark.smoke
    @pytest.mark.parametrize(
        "path,has_count",
        [
            ("/api/lunarlink/project", False),  # MyCursorPagination：无 count
            ("/api/lunarlink/config", True),
            ("/api/lunarlink/variables", True),
            ("/api/lunarlink/schedule", True),
            ("/api/user/login_log", True),
        ],
    )
    def test_list_envelope(self, platform_user, path, has_count):
        resp = platform_user.get(path)
        assert resp.status_code == 200, "{} -> {} {}".format(path, resp.status_code, resp.text[:200])
        body = resp.json()
        assert isinstance(body, dict), "分页信封应是对象，实际 {}".format(type(body))
        assert isinstance(body.get("results"), list), "缺少 results 数组: {}".format(body)
        assert "next" in body and "previous" in body, "缺少分页游标字段: {}".format(body)
        if has_count:
            assert "count" in body, "该端点应带 count: {}".format(body)

    @allure.title("用户列表返回数组，且含两个冒烟账号")
    @pytest.mark.smoke
    def test_user_list(self, platform_user):
        resp = platform_user.get("/api/user/list")
        assert resp.status_code == 200, resp.text[:300]
        users = resp.json()
        assert isinstance(users, list), "该端点返回裸数组，不分页：{}".format(type(users))
        usernames = {u.get("username") for u in users}
        assert "smoke_regression" in usernames, "冒烟账号不在用户列表里: {}".format(usernames)
        # 顺带断言：列表不能泄露口令字段
        for user in users:
            assert "password" not in user, "用户列表泄露了 password 字段"

    @allure.title("★ 不存在的资源返回 HTTP 200 + code=0102（平台业务错误契约）")
    @pytest.mark.negative
    @pytest.mark.parametrize(
        "path,expected_code",
        [
            ("/api/lunarlink/project/999999", "0102"),
            ("/api/lunarlink/api/999999", "0102"),
            ("/api/lunarlink/debugtalk/999999", "0102"),
        ],
    )
    def test_not_found_is_business_error(self, platform_user, path, expected_code):
        resp = platform_user.get(path)
        # ★ 这一条是本文件最重要的契约断言：
        #   平台把「资源不存在」表达为 HTTP 200 + success=false，
        #   所以只断 status_code 的用例在这里会「假通过」。
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        assert body.get("success") is False, "应表达为业务失败: {}".format(body)
        assert body.get("code") == expected_code, "错误码变了: {}".format(body)


# ============================================================================
# 四、参数校验
# ============================================================================


@allure.feature("平台冒烟")
class TestParameterValidation:
    @allure.title("{path} 缺 project 参数 -> 400 且指出该字段必填")
    @pytest.mark.negative
    @pytest.mark.parametrize("path", ["/api/lunarlink/api", "/api/lunarlink/test"])
    def test_requires_project_param(self, platform_user, path):
        resp = platform_user.get(path)
        assert resp.status_code == 400, "{} -> {} {}".format(path, resp.status_code, resp.text[:200])
        assert "project" in resp.json(), "应指出 project 必填: {}".format(resp.json())


# ============================================================================
# 五、权限边界（非管理员 vs 管理员）
# ============================================================================


@allure.feature("平台冒烟")
class TestPermissionBoundary:
    @allure.title("非管理员删除项目 -> 403（CustomIsAdminUser 查 is_staff）")
    @pytest.mark.security
    def test_non_admin_cannot_delete_project(self, platform_user):
        resp = platform_user.request(
            "DELETE", "/api/lunarlink/project", json={"id": 1}
        )
        assert resp.status_code == 403, resp.text[:300]

    @allure.title("非管理员访问 gitlab-ci 管理端点 -> 403")
    @pytest.mark.security
    def test_non_admin_cannot_read_gitlab_ci(self, platform_user):
        resp = platform_user.get("/api/lunarlink/gitlab-ci/")
        assert resp.status_code == 403, resp.text[:300]


# ============================================================================
# 六、轻量写闭环（自己创建、自己清理）
# ============================================================================


@allure.feature("平台冒烟")
class TestProjectWriteCycle:
    @allure.title("★ 创建 -> 在列表可见 -> 删除 -> 列表消失（完整闭环）")
    def test_create_visible_delete(self, platform_admin):
        name = _unique_name("smoke-cycle")

        created = platform_admin.post("/api/lunarlink/project", json=_project_body(name))
        assert created.status_code == 200, created.text[:300]
        assert created.json().get("code") == "0001", "项目创建失败: {}".format(created.json())

        try:
            # 管理员是超管 -> get_queryset 返回全部项目，能在这里找到它
            found = None
            listing = platform_admin.get("/api/lunarlink/project")
            assert listing.status_code == 200, listing.text[:300]
            for item in listing.json()["results"]:
                if item.get("name") == name:
                    found = item
                    break
            assert found is not None, "刚创建的项目在列表里找不到（说明写没落库）"
            assert found.get("desc") == "自动化冒烟创建"

            removed = platform_admin.request(
                "DELETE", "/api/lunarlink/project", json={"id": found["id"]}
            )
            assert removed.status_code == 200, removed.text[:300]
            assert removed.json().get("code") == "0003", "删除失败: {}".format(removed.json())
        finally:
            # 兜底清理：即便中间断言失败，也不给数据库留垃圾
            listing = platform_admin.get("/api/lunarlink/project")
            if listing.status_code == 200:
                for item in listing.json()["results"]:
                    if item.get("name") == name:
                        platform_admin.request(
                            "DELETE", "/api/lunarlink/project", json={"id": item["id"]}
                        )

        # 删除是软删（is_deleted=True），默认管理器会过滤掉它
        after = platform_admin.get("/api/lunarlink/project")
        assert after.status_code == 200
        names = {item.get("name") for item in after.json()["results"]}
        assert name not in names, "删除后项目仍在列表里（软删未生效）"

    @allure.title("★ 同名项目重复创建 -> code=0101（顺带证明数据真的落库了）")
    @pytest.mark.negative
    def test_duplicate_name_rejected(self, platform_admin):
        name = _unique_name("smoke-dup")
        first = platform_admin.post("/api/lunarlink/project", json=_project_body(name))
        assert first.json().get("code") == "0001", first.text[:300]

        try:
            second = platform_admin.post("/api/lunarlink/project", json=_project_body(name))
            assert second.status_code == 200, second.text[:200]
            body = second.json()
            # 用「重名检测」来证明上一条写入真的持久化了 ——
            # 不需要绕去查数据库或猜主键，纯 HTTP 就能验证。
            assert body.get("success") is False, "重名竟然还能建成: {}".format(body)
            assert body.get("code") == "0101", "重名错误码变了: {}".format(body)
        finally:
            listing = platform_admin.get("/api/lunarlink/project")
            for item in listing.json().get("results", []):
                if item.get("name") == name:
                    platform_admin.request(
                        "DELETE", "/api/lunarlink/project", json={"id": item["id"]}
                    )

    @allure.title("★ 缺必填字段时平台只回 code=9999 System Error（记录缺陷，非预期行为）")
    @pytest.mark.negative
    def test_missing_required_field_gives_unhelpful_error(self, platform_admin):
        """这条不是在"认可"平台的行为，而是把缺陷固定下来。

        `Project.desc` / `Project.responsible` 是 CharField(null=False) 且无 default，
        在 ModelSerializer 里成为必填；但 `ProjectView.add` 在 serializer 不合法时
        统一返回 SYSTEM_ERROR，既不说是哪个字段、也不回 400。
        调用方只能靠翻源码才知道少传了什么。

        断言写成「当前确实是 9999」，这样上游一旦改成 400 + 字段级提示，
        这条用例会失败并提醒我们更新它（而不是继续假装一切正常）。
        """
        resp = platform_admin.post(
            "/api/lunarlink/project", json={"name": _unique_name("smoke-bad")}
        )
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        assert body.get("success") is False
        assert body.get("code") == "9999", "平台行为已变: {}".format(body)
        # 本次创建必然失败，所以无需清理


# ============================================================================
# 七、曾经的缺陷 → 已修复，这里保留为【回归守卫】
#
# ★ 这段历史值得留着（含一次我自己的归因错误）：
#
#   初判：/dashboard、/visit、/reports 三个端点都 500，我在日志里看到
#         prepare.py / VisitView 里有 DATE_FORMAT、YEARWEEK 这类 MySQL 专有 SQL，
#         就把三者归因为「同一个可移植性缺陷」。
#
#   实测纠正（抓真实异常 + traceback 后）——其实是【两个不同的缺陷】：
#     1. /dashboard、/visit  → OperationalError: no such function: DATE_FORMAT
#                              ← 确实是可移植性缺陷（两处写死了 MySQL 函数）
#     2. /reports            → ValueError: Cannot use None as a query value
#                              ← 【与数据库无关】：report.py 用 `search != ""` 判空，
#                                而未传参时得到的是 None（None != "" 成立）
#                                → filter(name__contains=None)
#     3. 而且三个端点的 500 页面显示的都是 MissingSchema —— 因为错误告警中间件
#        （middleware.process_exception → qy_message.send → requests.post(空 URL)）
#        自己抛了错，把真实异常【掩盖】了。排障时会被引到完全错误的方向。
#
#   教训一：把「多个端点都 500」归因为「同一个原因」，是危险的捷径 —— 必须逐个拿真实异常。
#   教训二：错误处理路径本身有毛病时，它会把诊断信息也一起毁掉。
#
#   ★ 修复（见 CHANGES）：把写死的 DATE_FORMAT 改成【按数据库方言生成】
#     （SQLite 用 strftime），report.py 的判空改成真值判断。
#   修完这三条 xfail 如设计般变成 XPASS(strict) 失败 → 于是改造成下面的【正向守卫】。
#   （这正是当初给 xfail 加 strict=True 的目的：缺陷一被修好就主动喊人。)
#
#   为什么保留成守卫而不是删掉：写死 MySQL 函数在本地会立刻 500，容易被"顺手改到能跑"；
#   而真正危险的是**改回去**（本地若换了 MySQL 就测不出来）。有这两条守卫，
#   谁把日期格式化改回 MySQL 专有写法，CI 立刻红。
# ============================================================================


@allure.feature("SQLite 兼容性")
class TestSqliteCompatibility:
    @allure.title("{path} 在 SQLite 下可用（曾经的 500 · 回归守卫）")
    @pytest.mark.smoke
    @pytest.mark.parametrize(
        "path",
        [
            "/api/lunarlink/dashboard",
            "/api/lunarlink/visit?project=1",
        ],
    )
    def test_mysql_only_sql_no_longer_breaks(self, platform_user, path):
        """这两个端点曾在 SQLite 下必定 500，因为写死了 MySQL 专有的 DATE_FORMAT/YEARWEEK。

        修法：`prepare.date_format_sql()` / `date_week_sql()` 按 `connection.vendor`
        生成对应方言的 SQL。
        """
        resp = platform_user.get(path)
        assert resp.status_code == 200, (
            "{} 返回 HTTP {}。若日志里是 'no such function: DATE_FORMAT'，"
            "说明有人把日期格式化改回了 MySQL 专有写法 —— 请改用 "
            "prepare.date_format_sql() / prepare.date_week_sql()。".format(
                path, resp.status_code
            )
        )


@allure.feature("SQLite 兼容性")
class TestReportListParamHandling:
    @allure.title("报告列表：不传 search 与传 search 都要正常返回（None 处理守卫）")
    def test_reports_list_param_handling(self, platform_user):
        """曾经：未传 search 时 `search` 是 None，而守卫写的是 `search != ""`
        → `filter(name__contains=None)` → ValueError → 整个报告列表 500。

        已改为真值判断。这里把【两条分支都走一遍】，确认既修好了 None 分支、
        也没有把"传了 search 时的筛选分支"写坏：
          ① 完全不传 search（就是曾经 500 的那条路径）
          ② 传一个 search（确认筛选分支仍然工作）
        （两条分支写在同一条用例里，而不是拆成两个 parametrize —— 它们在语义上
          是同一件事"这个视图的参数处理"；断言消息里带上 params，失败时能定位是哪条。）
        """
        for params in (None, {"search": "x"}):
            resp = platform_user.get("/api/lunarlink/reports", params=params)
            assert resp.status_code == 200, "params={} 时返回 HTTP {}".format(
                params, resp.status_code
            )
            body = resp.json()
            assert "results" in body, "params={} 的分页信封缺 results: {}".format(
                params, body
            )
