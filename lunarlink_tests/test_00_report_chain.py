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

    @allure.title("SUT 测试后门已开启：GET /api/_test/ping 返回 200")
    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.smoke
    def test_sut_test_hooks_enabled(self, sut_base_url, sut_ready):
        resp = requests.get(sut_base_url + "/api/_test/ping", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @allure.title("跳过的用例能被正确呈现（故意 skip）")
    @pytest.mark.smoke
    def test_skip_is_rendered(self):
        pytest.skip("故意跳过：用于验证 skip 在 allure / html 报告里的呈现")
