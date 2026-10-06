# -*- coding: utf-8 -*-
"""pytest 全局 fixture 与会话前置检查。

本文件当前是最小版本，只为验证「pytest 9 + allure + html 报告链路」可用。
完整的 fixture（登录鉴权、SUT 数据库只读连接、数据隔离工厂等）在阶段 2 补齐。
"""

import os

import allure
import pytest
import requests

DEFAULT_SUT_BASE_URL = "http://127.0.0.1:8021"
DEFAULT_PLATFORM_BASE_URL = "http://127.0.0.1:8001"


def _base_url(env_key: str, default: str) -> str:
    return os.getenv(env_key, default).rstrip("/")


@pytest.fixture(scope="session")
def sut_base_url() -> str:
    """被测系统（mock 电商）地址。"""
    return _base_url("SUT_BASE_URL", DEFAULT_SUT_BASE_URL)


@pytest.fixture(scope="session")
def platform_base_url() -> str:
    """LunarLink 平台自身地址。"""
    return _base_url("LUNARLINK_BASE_URL", DEFAULT_PLATFORM_BASE_URL)


@pytest.fixture(scope="session")
def sut_ready(sut_base_url: str) -> dict:
    """会话级前置检查：SUT 未就绪就直接终止整个会话，而不是让 170 条用例逐个超时。

    用 pytest.exit 而不是 assert —— 环境没起来不属于「用例失败」，
    它应该让整轮回归明确地"没跑成"，避免把环境问题误报成用例失败。
    """
    url = sut_base_url + "/health"
    try:
        resp = requests.get(url, timeout=10)
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            "SUT 不可用（{}）：{}\n请先执行：cd D:\\Projects ; .\\svc.ps1 start sut".format(
                url, exc
            ),
            returncode=1,
        )

    if resp.status_code != 200:
        pytest.exit(
            "SUT 健康检查失败：{} -> HTTP {}\n响应：{}".format(
                url, resp.status_code, resp.text[:300]
            ),
            returncode=1,
        )

    payload = resp.json()
    # 顺手把环境信息挂到 allure 环境面板
    allure.attach(
        str(payload),
        "SUT /health",
        allure.attachment_type.JSON,
    )
    return payload
