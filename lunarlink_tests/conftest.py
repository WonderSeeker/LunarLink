# -*- coding: utf-8 -*-
"""pytest 全局 fixture。

分层约定（重要）：
    * session 级：地址、就绪检查、SUT 数据重置（只做一次）、预置账号与客户端
    * function 级：任何会被用例改动的东西（商品、购物车、订单）一律由用例自己
      用 utils.data 造 uuid 独立数据 —— 用例可任意顺序 / 并行执行

★ 数据隔离策略（见 CHANGES.md）：
    隔离靠 uuid 后缀造独立数据，**不靠 reset**。
    reset 只在会话开始时调用一次，且要求后门开启、当前无并发请求。

★ 后门未开启时：直接 `pytest.skip` 整个依赖后门的用例组，
  而不是让它们以 401/404 之类的方式「诡异地失败」。
"""

import os
import sys

import allure
import pytest
import requests

# 让 `from utils.xxx import ...` 在两种执行方式下都能工作：
#   cd lunarlink_tests && python -m pytest
#   仓库根 && python -m pytest lunarlink_tests
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from utils.client import (  # noqa: E402
    TOKEN_STYLE_LUNARLINK,
    TOKEN_STYLE_SUT,
    ApiClient,
)
from utils.db import SutDatabase  # noqa: E402

DEFAULT_SUT_BASE_URL = "http://127.0.0.1:8021"
DEFAULT_PLATFORM_BASE_URL = "http://127.0.0.1:8001"

SUT_DB_PATH = os.getenv(
    "SUT_DB_PATH", os.path.join(_TESTS_DIR, "..", "sut", "sut.sqlite3")
)

# 预置账号（由 sut/seed.py 灌入）
SEED_ADMIN = "admin"
SEED_BUYER = "buyer"
SEED_BUYER2 = "buyer2"
SEED_BUYER_PASSWORD = "buyer123"

# 平台冒烟账号（由 backend manage.py ensure_smoke_user 保证存在）
PLATFORM_SMOKE_USER = os.getenv("SMOKE_USERNAME", "smoke_regression")
PLATFORM_SMOKE_PASSWORD = os.getenv("SMOKE_PASSWORD", "smoke123456")


def _base_url(env_key: str, default: str) -> str:
    return os.getenv(env_key, default).rstrip("/")


# ===================================================================== 地址


@pytest.fixture(scope="session")
def sut_base_url() -> str:
    """被测系统（mock 电商）地址。"""
    return _base_url("SUT_BASE_URL", DEFAULT_SUT_BASE_URL)


@pytest.fixture(scope="session")
def platform_base_url() -> str:
    """LunarLink 平台自身地址。"""
    return _base_url("LUNARLINK_BASE_URL", DEFAULT_PLATFORM_BASE_URL)


# ===================================================================== 就绪与重置


@pytest.fixture(scope="session")
def sut_ready(sut_base_url: str) -> dict:
    """会话级前置检查：SUT 未就绪直接终止整个会话。

    用 pytest.exit 而不是 assert —— 环境没起来不属于「用例失败」，
    它应该让整轮回归明确地「没跑成」，避免把环境问题误报成大量用例失败。
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
    allure.attach(str(payload), "SUT /health", allure.attachment_type.JSON)
    return payload


@pytest.fixture(scope="session")
def sut_seeded(sut_base_url: str, sut_ready: dict) -> bool:
    """会话开始时把 SUT 数据重置到种子状态（**整个会话只做一次**）。

    ★ 后门没开就 skip 整组依赖它的用例，而不是让它们诡异失败。
    """
    if not sut_ready.get("test_hooks"):
        pytest.skip(
            "SUT 未开启测试后门（SUT_ENABLE_TEST_HOOKS=1）。"
            "请用 svc.ps1 start sut 启动（run-sut.ps1 会打开后门）。"
        )

    # 先把 admin 的 token 签出来（reset 会清空 tokens 表，所以重置后要重新签）
    boot = requests.post(
        sut_base_url + "/api/_test/token",
        json={"username": SEED_ADMIN, "ttl_seconds": 3600},
        timeout=15,
    )
    assert boot.status_code == 200, "后门签发 admin token 失败: {}".format(boot.text[:200])

    resp = requests.post(
        sut_base_url + "/api/system/reset",
        headers={"Authorization": "Bearer " + boot.json()["token"]},
        timeout=60,
    )
    assert resp.status_code == 200, (
        "重置 SUT 数据失败: HTTP {} {}\n"
        "（若为 409 RESET_BUSY，说明有并发请求在跑，等它结束后重试）".format(
            resp.status_code, resp.text[:300]
        )
    )
    allure.attach(str(resp.json()), "SUT 数据已重置", allure.attachment_type.JSON)
    return True


# ===================================================================== 客户端


@pytest.fixture(scope="session")
def sut(sut_base_url: str, sut_seeded: bool) -> ApiClient:
    """未登录的 SUT 客户端（用于 401 / 越权等匿名场景）。"""
    return ApiClient(sut_base_url, token=None, token_style=TOKEN_STYLE_SUT, name="sut-匿名")


def _login_sut(sut_base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        sut_base_url + "/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    assert resp.status_code == 200, "SUT 登录失败 {}: HTTP {} {}".format(
        username, resp.status_code, resp.text[:200]
    )
    return resp.json()["token"]


@pytest.fixture(scope="session")
def sut_admin_token(sut_base_url: str, sut_seeded: bool) -> str:
    """admin 的 token。

    走测试后门签发（而不是登录）：seed 里的 admin 口令不是本套件该关心的事，
    而且后门不受登录锁定影响，适合做 fixture。
    """
    resp = requests.post(
        sut_base_url + "/api/_test/token",
        json={"username": SEED_ADMIN, "ttl_seconds": 3600},
        timeout=15,
    )
    assert resp.status_code == 200, "后门签发 admin token 失败: {}".format(resp.text[:200])
    return resp.json()["token"]


@pytest.fixture(scope="session")
def sut_buyer_token(sut_base_url: str, sut_seeded: bool) -> str:
    # 走真实登录：让 fixture 准备阶段也覆盖一遍正常链路
    return _login_sut(sut_base_url, SEED_BUYER, SEED_BUYER_PASSWORD)


@pytest.fixture(scope="session")
def sut_buyer2_token(sut_base_url: str, sut_seeded: bool) -> str:
    return _login_sut(sut_base_url, SEED_BUYER2, SEED_BUYER_PASSWORD)


@pytest.fixture(scope="session")
def sut_admin(sut_base_url: str, sut_admin_token: str) -> ApiClient:
    """admin 身份的 SUT 客户端（商品 CRUD、发货等需要管理员权限）。"""
    return ApiClient(sut_base_url, sut_admin_token, TOKEN_STYLE_SUT, name="sut-admin")


@pytest.fixture(scope="session")
def buyer(sut_base_url: str, sut_buyer_token: str) -> ApiClient:
    """买家一号（下单主流程用它）。"""
    return ApiClient(sut_base_url, sut_buyer_token, TOKEN_STYLE_SUT, name="sut-buyer")


@pytest.fixture(scope="session")
def buyer2(sut_base_url: str, sut_buyer2_token: str) -> ApiClient:
    """买家二号 —— 专供【水平越权】用例：用它去访问 buyer 的资源，断言 404。"""
    return ApiClient(sut_base_url, sut_buyer2_token, TOKEN_STYLE_SUT, name="sut-buyer2")


# ===================================================================== 数据库断言


@pytest.fixture(scope="session")
def sut_db(sut_ready: dict) -> SutDatabase:
    """SUT 数据库只读视图（断言体系第 4 层：关键数据落库校验）。"""
    path = os.path.abspath(SUT_DB_PATH)
    assert os.path.isfile(path), "SUT 数据库不存在: {}".format(path)
    return SutDatabase(path)


# ===================================================================== 平台侧


@pytest.fixture(scope="session")
def platform() -> ApiClient:
    """LunarLink 平台客户端。

    ★ 注意 token_style 用 TOKEN_STYLE_LUNARLINK（裸 token）——
      与 SUT 的 Bearer 不同，见 utils/client.py 顶部说明。
    """
    return ApiClient(
        _base_url("LUNARLINK_BASE_URL", DEFAULT_PLATFORM_BASE_URL),
        token=None,
        token_style=TOKEN_STYLE_LUNARLINK,
        name="platform",
    )


@pytest.fixture(scope="session")
def platform_admin(platform: ApiClient) -> ApiClient:
    """已登录的平台客户端（供平台冒烟套件使用）。

    依赖 `manage.py ensure_smoke_user` 已创建账号；账号不存在时 skip，
    而不是报一个看不懂的失败。
    """
    resp = platform.post(
        "/login/",
        json={"username": PLATFORM_SMOKE_USER, "password": PLATFORM_SMOKE_PASSWORD},
    )
    if resp.status_code != 200:
        pytest.skip(
            "平台冒烟账号登录失败（HTTP {}）。请先执行："
            "cd backend ; python manage.py ensure_smoke_user".format(resp.status_code)
        )

    platform.token = resp.json()["token"]
    allure.attach(
        "username={} token_style={}".format(PLATFORM_SMOKE_USER, TOKEN_STYLE_LUNARLINK),
        "平台登录",
        allure.attachment_type.TEXT,
    )
    return platform
