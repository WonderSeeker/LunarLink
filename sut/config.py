# -*- coding: utf-8 -*-
"""SUT 配置中心。

所有可调参数都能用环境变量覆盖 —— 目的是让并发/超时类用例能在不 sleep 的情况下
构造时间窗口（例如把 SUT_ORDER_TTL_SECONDS 调小、或直接注入一个已过期的 token）。
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------- 服务
# ★★ 安全红线：默认绑定 127.0.0.1，禁止改成 0.0.0.0 ★★
#   本服务在测试环境下会挂载测试后门 /api/_test/*，其中 /api/_test/token
#   能签发任意 TTL 的 token（含管理员身份）。绑 0.0.0.0 等于把管理员权限
#   暴露给同局域网的任何人。
HOST = os.getenv("SUT_HOST", "127.0.0.1")
PORT = int(os.getenv("SUT_PORT", "8021"))

# --------------------------------------------------------------------- 数据库
DB_PATH = os.getenv("SUT_DB_PATH", str(BASE_DIR / "sut.sqlite3"))

# --------------------------------------------------------------------- 测试后门
# 仅当 SUT_ENABLE_TEST_HOOKS=1 时才挂载 /api/_test/* 路由。
# 默认关闭，避免测试后门出现在非测试环境。
# 有了它，「token 过期 / 订单支付超时 / 账号锁定」三个场景都是确定性的，
# 测试套件里不需要任何 sleep(60)。
ENABLE_TEST_HOOKS = os.getenv("SUT_ENABLE_TEST_HOOKS", "0") == "1"

# --------------------------------------------------------------------- SQLite 并发
# WAL 模式下写操作是串行的，并发请求会在写锁上排队。
# busy_timeout 给到 15s，并在 db.py 里对 "database is locked" 做带退避的有限重试，
# 避免把「环境排队」误判成「实现 bug」（表现为 500）。
BUSY_TIMEOUT_MS = int(os.getenv("SUT_BUSY_TIMEOUT_MS", "15000"))
WRITE_RETRY_TIMES = int(os.getenv("SUT_WRITE_RETRY_TIMES", "3"))
WRITE_RETRY_BACKOFF_SECONDS = float(
    os.getenv("SUT_WRITE_RETRY_BACKOFF_SECONDS", "0.05")
)

# --------------------------------------------------------------------- 鉴权
# 简化设计：SUT 采用【单 token】（而非 access + refresh 双 token），
# 详见 README「设计简化说明」。
TOKEN_TTL_SECONDS = int(os.getenv("SUT_TOKEN_TTL_SECONDS", "900"))
# 连续登录失败达到该次数即锁定账号（用于「密码连续错误 N 次锁定」用例）
LOGIN_LOCK_THRESHOLD = int(os.getenv("SUT_LOGIN_LOCK_THRESHOLD", "5"))
LOGIN_LOCK_SECONDS = int(os.getenv("SUT_LOGIN_LOCK_SECONDS", "60"))

# --------------------------------------------------------------------- 业务参数
# 订单支付超时窗口：创建订单时写入 expire_at = now + ORDER_TTL_SECONDS
ORDER_TTL_SECONDS = int(os.getenv("SUT_ORDER_TTL_SECONDS", "900"))
# 商品分页
DEFAULT_PAGE_SIZE = int(os.getenv("SUT_DEFAULT_PAGE_SIZE", "5"))
MAX_PAGE_SIZE = int(os.getenv("SUT_MAX_PAGE_SIZE", "100"))
MAX_KEYWORD_LENGTH = int(os.getenv("SUT_MAX_KEYWORD_LENGTH", "50"))
# 账号字段边界（用于边界值用例）
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 32
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 64

# --------------------------------------------------------------------- 角色
ROLE_ADMIN = "admin"
ROLE_CUSTOMER = "customer"

# --------------------------------------------------------------------- 订单状态机
# 状态流转白名单：{当前状态: {允许流转到的状态}}
# 非法流转一律返回 409 INVALID_STATE_TRANSITION，并在 detail 里回传 from/to。
ORDER_STATE_TRANSITIONS = {
    "PENDING": {"PAID", "CANCELLED"},
    "PAID": {"SHIPPED", "REFUNDED"},
    "SHIPPED": {"COMPLETED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "REFUNDED": set(),
}
ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_PAID = "PAID"
ORDER_STATUS_SHIPPED = "SHIPPED"
ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_CANCELLED = "CANCELLED"
ORDER_STATUS_REFUNDED = "REFUNDED"
