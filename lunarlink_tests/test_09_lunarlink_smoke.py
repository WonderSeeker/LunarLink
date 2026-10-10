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
# 七、已知缺陷：SQLite 模式下必定 500
#
# ★★ 这一组的原因我一开始判错了，这里记下纠正过程（避免后人再踩）★★
#
#   初判：这三个端点都 500，我在日志里看到 prepare.py / VisitView 里有
#         DATE_FORMAT、YEARWEEK 这类 MySQL 专有 SQL，就归因为「同一个可移植性缺陷」。
#
#   实测纠正（抓真实异常 + traceback 后）：
#     1. /dashboard、/visit  → django.db.utils.OperationalError: no such function: DATE_FORMAT
#                              ← 确实【是】可移植性缺陷，命中 2 处代码
#                              （prepare.py 的 aggregate_reports_or_case_bydate、VisitView.list）
#     2. /reports            → ValueError: Cannot use None as a query value
#                              ← 【不是】SQL 问题：report.py:157 的
#                                `search = request.query_params.get("search")` 拿到 None，
#                                而守卫写的是 `if search != ""`（None != "" 为真）→
#                                `filter(name__contains=None)` 直接报错。
#                                这个缺陷在 MySQL 下同样会 500，与数据库无关。
#     3. 三个端点的 500 页面显示的都是 MissingSchema（不是上面两个真实异常）——
#        因为 backend/utils/middleware.py 的 process_exception 会去调
#        企业微信告警 qy_message.send()，而 QY_WEB_HOOK 是空串
#        → requests.post("") → MissingSchema。
#        ★ 于是【错误告警把真实异常掩盖了】，排障时会被引到完全错误的方向。
#        我本人就是先被它误导，看到 MissingSchema 才发现要看 traceback 链。
#
#   教训：把「多个端点都 500」直接归因为「同一个原因」是危险的捷径。
#        必须逐个端点拿到真实异常，尤其是当错误处理路径本身有问题时。
# ============================================================================


@allure.feature("已知平台缺陷")
class TestKnownPlatformDefectsPortability:
    @allure.title("可移植性缺陷：{path} 在 SQLite 下 500（上游使用 MySQL 专有 SQL）")
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "【可移植性缺陷】平台声明支持 SQLite（settings.py 的 DATABASE_ENGINE=sqlite 开关），"
            "但以下 2 处代码使用了 MySQL 专有 SQL：\n"
            "  * apps/lunarlink/utils/prepare.py 的 aggregate_reports_or_case_bydate → DATE_FORMAT/YEARWEEK\n"
            "  * apps/lunarlink/views/project.py 的 VisitView.list → DATE_FORMAT\n"
            "SQLite 无对应函数 → django.db.utils.OperationalError: no such function: DATE_FORMAT。\n"
            "即：这是 SQLite 模式下的可移植性缺陷，不是「平台功能全坏」。\n"
            "★ 用 strict=True：上游一旦补齐 SQLite 兼容，这条会 XPASS 失败，提醒我们撤掉标记。"
        ),
    )
    @pytest.mark.parametrize(
        "path",
        [
            "/api/lunarlink/dashboard",
            "/api/lunarlink/visit?project=1",
        ],
    )
    def test_mysql_only_sql_on_sqlite(self, platform_user, path):
        """按【正确行为】断言：这些只读端点应当返回 200。

        ★ 断言必须写成「期望 200」，不能写成「期望 500」。
          xfail 的语义是「本用例预期会失败」，所以用例体要表达正确行为：
            缺陷存在 -> 断言失败 -> XFAIL（绿，符合预期）
            上游修好 -> 断言通过 -> XPASS(strict) 失败 -> 提醒我们撤掉标记
          若反过来写成断言 500，就等于把「平台有缺陷」说成「平台应该 500」，
          而且会 XPASS(strict) 报错 —— 实测确实如此，这个方向我一开始写反了。
        """
        resp = platform_user.get(path)
        assert resp.status_code == 200, (
            "{} 实际 HTTP {}（可移植性缺陷：上游 MySQL 专有 SQL 在 SQLite 下不可用）".format(
                path, resp.status_code
            )
        )


@allure.feature("已知平台缺陷")
class TestKnownPlatformDefectsNoneHandling:
    @allure.title("缺陷：/reports 未传 search 时 500（None 被 `!= \"\"` 放行）")
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "apps/lunarlink/views/report.py:157 —— `search = request.query_params.get(\"search\")` "
            "在未传参时得到 None，而守卫写的是 `if search != \"\"`（None != \"\" 成立），"
            "于是执行 `filter(name__contains=None)` → ValueError: Cannot use None as a query value。\n"
            "★ 这与数据库无关（MySQL 下同样会 500），是独立的 None 处理缺陷，"
            "不应与上面的「可移植性缺陷」混为一谈。\n"
            "正确写法类似 `if search:` 或 `if search not in (None, \"\")`。\n"
            "★ strict=True：上游修好后会 XPASS 失败，提醒撤标记。"
        ),
    )
    def test_reports_list_without_search(self, platform_user):
        """按【正确行为】断言：不传 search 也应该正常返回 200。"""
        resp = platform_user.get("/api/lunarlink/reports")
        assert resp.status_code == 200, (
            "/api/lunarlink/reports 实际 HTTP {}（None 处理缺陷）".format(resp.status_code)
        )
