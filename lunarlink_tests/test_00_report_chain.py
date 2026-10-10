# -*- coding: utf-8 -*-
"""报告链路自检：验证 pytest 9 + allure-pytest + pytest-html + pytest-xdist 可用。

★ 这个文件是「工具链体检」，不是业务用例：
   业务用例从 test_01_auth.py 开始。如果这里红了，先修工具链，别往下写。

它验证 4 件事：
   1. pytest 9.1.1 能正常收集与执行
   2. allure-pytest 2.16.2 能产出 allure-results/（含通过/失败/跳过三种状态）
   3. pytest-html 4.2.0 能生成自包含 HTML（含失败与跳过的呈现）
   4. pytest-xdist 3.8.0 能并行执行（阶段 3 要用它产出「并行耗时」这一档数据）
"""

import allure
import pytest
import requests


@allure.feature("工具链自检")
@allure.story("报告链路")
class TestReportChain:

    @allure.title("pytest 能正常执行")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    def test_pytest_runs(self):
        assert 1 + 1 == 2

    @allure.title("allure.step 与 attach 可用")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.smoke
    def test_allure_step_and_attach(self):
        with allure.step("执行一个带步骤的操作"):
            value = 6 * 7
        with allure.step("断言结果"):
            assert value == 42
        allure.attach(
            "step/attach 正常",
            name="自检文本",
            attachment_type=allure.attachment_type.TEXT,
        )

    @allure.title("SUT 可用：GET /health 返回 200 且 db=true")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.smoke
    def test_sut_health(self, sut_base_url, sut_ready):
        with allure.step("请求 GET {}/health".format(sut_base_url)):
            resp = requests.get(sut_base_url + "/health", timeout=10)

        with allure.step("断言状态码为 200"):
            assert resp.status_code == 200

        body = resp.json()
        allure.attach(
            str(body), "health 响应体", allure.attachment_type.JSON
        )

        with allure.step("断言 status=ok 且 db=true"):
            assert body["status"] == "ok"
            assert body["db"] is True

    @allure.title("★ 守卫：SUT 必须开启测试后门（否则整个套件不可信）")
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.smoke
    def test_sut_test_hooks_enabled(self, sut_base_url, sut_ready):
        """把「后门必须开着」做成【机制保证】，而不是靠你记得设环境变量。

        ★ 这条用例是【逃逸检测】，不是功能验证：
          后门关着的时候，sut_seeded 会 skip，于是绝大多数用例跟着 skip ——
          如果唯一信号是「一堆 skip」，整轮回归就会以 exit code 0 收尾，
          变成一种【假的绿色】。所以这里必须【无条件失败】。

        ★ 因此它【刻意不依赖 sut_seeded】（依赖了就一起 skip，守卫失效），
          只用 sut_ready（只探 /health，后门关着它也返回 200，所以这道闸放得过来）。

        实测依据：以后门关闭的方式起 SUT（test_hooks=false）跑全量 →
        exit code = 1，本用例以 404 失败，整轮不会假绿。
        """
        resp = requests.get(sut_base_url + "/api/_test/ping", timeout=10)

        if resp.status_code != 200:
            pytest.fail(
                "SUT 未开启测试后门（GET /api/_test/ping -> HTTP {}）。\n"
                "  影响：sut_seeded 会跳过，绝大多数用例随之 skip —— 这轮回归的结果不可信。\n"
                "  修法：用启动器起服务（它会带上 SUT_ENABLE_TEST_HOOKS=1）：\n"
                "        cd D:\\Projects ; .\\svc.ps1 start sut\n"
                "  不要直接 `python sut/run.py` —— 后门默认是【关闭】的（安全默认）。".format(
                    resp.status_code
                )
            )

        assert resp.json()["ok"] is True

    @allure.title("跳过的用例能被正确呈现（故意 skip）")
    @pytest.mark.smoke
    def test_skip_is_rendered(self):
        pytest.skip("故意跳过：用于验证 skip 在 allure / html 报告里的呈现")

    @allure.title("fixture 链路可用：登录态客户端 / 只读 DB / 造数")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.smoke
    def test_fixture_chain(self, buyer, sut_admin, sut_db):
        # 已登录客户端能用
        resp = buyer.get("/api/auth/profile")
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json()["user"]["role"] == "customer"

        # 只读 DB 能读
        stock = sut_db.product_stock(1)
        assert isinstance(stock, int) and stock >= 0

        # 造数工具能创建 uuid 独立商品（隔离策略的基础）
        from utils.data import create_product

        product = create_product(sut_admin, price_cents=1234, stock=7)
        assert product["id"] > 0
        assert sut_db.product_stock(product["id"]) == 7

    @allure.title("JSON Schema 校验可用（含相对 $ref 解析）")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.smoke
    def test_json_schema_validation(self, sut):
        from utils.schema import validate

        resp = sut.get("/api/products", params={"size": 3})
        assert resp.status_code == 200
        validate(resp.json(), "product_list")

        detail = sut.get("/api/products/1")
        assert detail.status_code == 200
        validate(detail.json(), "product")

        err = sut.get("/api/products/99999")
        assert err.status_code == 404
        validate(err.json(), "error")

    @allure.title("★ 契约锁定：SUT 用 Bearer，LunarLink 用裸 token")
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.smoke
    def test_auth_header_contracts(self, sut, buyer):
        """把两个【实测得出的不同契约】固定成断言，防止有人"顺手修"后端或误改测试。

        将来契约变了，这里会失败并指向 utils/client.py 的两个常量 —— 那是唯一的改动点。
        """
        from utils.client import (
            TOKEN_STYLE_BARE,
            TOKEN_STYLE_BEARER,
            TOKEN_STYLE_LUNARLINK,
            TOKEN_STYLE_SUT,
            auth_headers,
        )

        assert TOKEN_STYLE_SUT == TOKEN_STYLE_BEARER
        assert TOKEN_STYLE_LUNARLINK == TOKEN_STYLE_BARE
        assert auth_headers("T") == {"Authorization": "Bearer T"}
        assert auth_headers("T", TOKEN_STYLE_LUNARLINK) == {"Authorization": "T"}

        # 行为验证：SUT 拒绝裸 token（证明上面的常量不是"写了个寂寞"）
        # 这里刻意用 auth_headers(..., TOKEN_STYLE_LUNARLINK) 生成"错误契约"的头，
        # 而不是手拼字符串 —— 保持单一入口。
        bad = sut.get(
            "/api/auth/profile",
            headers=auth_headers(buyer.token, TOKEN_STYLE_LUNARLINK),
        )
        assert bad.status_code == 401, bad.text[:200]
