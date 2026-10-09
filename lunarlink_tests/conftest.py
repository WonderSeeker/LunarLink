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
from typing import Any, Dict

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
    auth_headers,
)
from utils.db import SutDatabase  # noqa: E402

# ===================================================================== 并行守卫
# ★ 本套件依赖「所有用例共享同一个 SUT 进程 + 会话级 reset 只做一次」。
#   一旦用 pytest -n 2，每个 worker 是独立进程 → 各自 reset → 互相把对方数据重置掉；
#   而且 SUT 的 RESET_BUSY 守卫会把另一个 worker 的 reset 打成 409 → fixture 直接报错。
#   不加守卫的失败方式是【间歇性 flaky】（最难查），加了就是显式报错 + 说明。
#
#   ★ 为什么写在 pytest_configure 里、而不是 conftest 顶层：
#     在 conftest 导入阶段抛 pytest.exit，pytest 会把它包成 ConftestImportFailure
#     并附一整段 traceback，把 CI 日志搅浑；而这条错误本该一眼看懂。
#
#   ★ 又为什么不直接写在 pytest_configure 里就完事（实测教训）：
#     裸着写会让【每个 worker 都执行一次】，报错信息被打印 11 次，还附带
#     "bringing up nodes" 与 10 行 "[gwN] node down: Not properly terminated"，比原来更糟。
#     所以要显式区分主进程与 worker：
#       - hasattr(config, "workerinput") == True  -> 我是 worker，闭嘴，交给主进程报
#       - config.getoption("numprocesses") 有值    -> 主进程带了 -n，这里一次性报错退出
#     实测（`pytest -n 2`）：只输出 1 条 Exit 信息，无 traceback、无 node down 噪音。


def pytest_configure(config):
    if hasattr(config, "workerinput"):
        return  # 本进程是 xdist worker，不重复报错

    if config.getoption("numprocesses", None) or os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.exit(
            "本套件依赖「共享同一个 SUT 进程 + 会话级 reset」，不支持 xdist 并行。\n"
            "去掉 -n/--dist 参数，或为每个 worker 提供独立的 SUT 实例（不同端口 + 不同 DB）。",
            returncode=1,
        )

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
#   1) 非管理员账号：主力，用于只读 + 权限边界
PLATFORM_SMOKE_USER = os.getenv("SMOKE_USERNAME", "smoke_regression")
PLATFORM_SMOKE_PASSWORD = os.getenv("SMOKE_PASSWORD", "smoke123456")
#   2) 管理员账号：仅用于 admin-only 端点（CustomIsAdminUser 查 is_staff）
PLATFORM_ADMIN_USER = os.getenv("SMOKE_ADMIN_USERNAME", "smoke_admin")
PLATFORM_ADMIN_PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD", "smoke123456")


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
        # ★ 走 auth_headers() 单一入口，不手拼 "Bearer " —— 契约变了这里会一起改
        headers=auth_headers(boot.json()["token"], TOKEN_STYLE_SUT),
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


# ===================================================================== 函数级隔离
# 购物车 / 订单 / 支付是「per-user 有状态资源」，用 session 级买家会让用例互相污染。
# 最干净的隔离是【每个用例一个全新用户】—— 这也正是"靠造数不靠 reset"的落地方式。


@pytest.fixture
def fresh_buyer(sut_base_url: str, sut_seeded: bool) -> Dict[str, Any]:
    """注册一个全新买家并返回 {username, password, id, token, client}。

    客户端每次新建（不复用 session 级 client），避免带着上一个用例的 token。
    """
    from utils.data import register as register_user

    anonymous = ApiClient(sut_base_url, token=None, token_style=TOKEN_STYLE_SUT, name="sut-新买家")
    info = register_user(anonymous)
    info["client"] = ApiClient(
        sut_base_url, info["token"], TOKEN_STYLE_SUT, name="sut-{}".format(info["username"])
    )
    return info


@pytest.fixture
def fresh_buyer2(sut_base_url: str, sut_seeded: bool) -> Dict[str, Any]:
    """第二个全新买家 —— 用于【水平越权】对照。"""
    from utils.data import register as register_user

    anonymous = ApiClient(sut_base_url, token=None, token_style=TOKEN_STYLE_SUT, name="sut-新买家2")
    info = register_user(anonymous)
    info["client"] = ApiClient(
        sut_base_url, info["token"], TOKEN_STYLE_SUT, name="sut-{}".format(info["username"])
    )
    return info


# ===================================================================== 数据库断言


@pytest.fixture(scope="session")
def sut_db(sut_ready: dict) -> SutDatabase:
    """SUT 数据库只读视图（断言体系第 4 层：关键数据落库校验）。"""
    path = os.path.abspath(SUT_DB_PATH)
    assert os.path.isfile(path), "SUT 数据库不存在: {}".format(path)
    return SutDatabase(path)


# ===================================================================== 平台侧


@pytest.fixture(scope="session")
def platform(platform_base_url: str) -> ApiClient:
    """LunarLink 平台的【匿名】客户端。

    ★ token_style 用 TOKEN_STYLE_LUNARLINK（裸 token）——
      与 SUT 的 Bearer 不同，见 utils/client.py 顶部说明。

    ★ 本 fixture 永远不持有 token，也【不允许被别处改写】。原因见下方
      `_platform_client` 的说明（原来这里被 platform_admin 改写，是个隐患）。
    """
    return ApiClient(
        platform_base_url,
        token=None,
        token_style=TOKEN_STYLE_LUNARLINK,
        name="platform-匿名",
    )


def _platform_client(
    base_url: str, username: str, password: str, name: str
) -> ApiClient:
    """登录平台并返回一个【独立】的客户端；平台不可用/账号缺失时 skip。

    ★ 为什么不复用 session 级 platform 再改它的 token（原实现如此）：
      那样「platform 到底带不带 token」取决于 fixture 的实例化顺序，
      于是「未登录访问受保护接口 -> 401」这类用例会时对时错。
      order-dependent 的 flaky 最耗排查时间，所以每个身份各自新建客户端。

    ★ 平台属于【可选的辅助套件】，因此这里用 skip 而不是像 sut_ready 那样
      pytest.exit：平台没起来不应该阻断业务套件的回归。
    """
    try:
        resp = requests.post(
            base_url + "/login/",
            json={"username": username, "password": password},
            timeout=20,
        )
    except requests.RequestException as exc:  # noqa: BLE001
        pytest.skip(
            "LunarLink 平台不可用（{}）：{}\n"
            "请先执行：cd D:\\Projects ; .\\svc.ps1 start lunar-be".format(base_url, exc)
        )
        raise  # pragma: no cover - 仅为类型收窄

    if resp.status_code != 200 or "token" not in resp.json():
        pytest.skip(
            "平台冒烟账号 {} 登录失败（HTTP {}）。请先执行："
            "cd backend ; python manage.py ensure_smoke_user".format(
                username, resp.status_code
            )
        )

    allure.attach(
        "username={} token_style={}".format(username, TOKEN_STYLE_LUNARLINK),
        "平台登录",
        allure.attachment_type.TEXT,
    )
    return ApiClient(base_url, resp.json()["token"], TOKEN_STYLE_LUNARLINK, name=name)


@pytest.fixture(scope="session")
def platform_user(platform_base_url: str) -> ApiClient:
    """平台的【非管理员】冒烟客户端（主力）。

    用于：只读端点可用性 + 权限边界（断言 403）。
    """
    return _platform_client(
        platform_base_url,
        PLATFORM_SMOKE_USER,
        PLATFORM_SMOKE_PASSWORD,
        "platform-非管理员",
    )


@pytest.fixture(scope="session")
def platform_admin(platform_base_url: str) -> ApiClient:
    """平台的【管理员】冒烟客户端（is_staff 为真）。

    仅用于 admin-only 端点（例：删除项目）。有了它，写用例才能自己清理现场，
    把「只剩脏数据」这种问题从源头去掉。
    """
    return _platform_client(
        platform_base_url,
        PLATFORM_ADMIN_USER,
        PLATFORM_ADMIN_PASSWORD,
        "platform-管理员",
    )
