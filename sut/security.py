# -*- coding: utf-8 -*-
"""口令哈希与 token 生成（纯函数，不碰数据库）。

口令哈希用标准库 hashlib.pbkdf2_hmac，不引入第三方（bcrypt / passlib）：
SUT 的目标是稳定可复现，不需要把依赖面搞大。

token 是随机串（secrets.token_urlsafe）而非 JWT —— 简化设计，见 README
「设计简化说明」。token 的有效性靠数据库里的 tokens 表判定，
这样「过期」「登出失效」都能被确定性构造。
"""

import hashlib
import hmac
import secrets
import time

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 100_000
SALT_BYTES = 16

TOKEN_BYTES = 32


def now_ts() -> int:
    """当前 Unix 秒。所有时间字段统一用它，便于测试后门拨动时间。"""
    return int(time.time())


def hash_password(password: str, salt: str = None):
    """返回 (password_hash, password_salt)，均为 hex 字符串。"""
    if salt is None:
        salt = secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """恒定时间比较，避免时序侧信道。"""
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_order_no() -> str:
    """订单号：时间戳 + 随机后缀，便于在报告里肉眼识别。"""
    return "SO{}{}".format(now_ts(), secrets.token_hex(4).upper())


def generate_pay_no() -> str:
    return "PAY{}{}".format(now_ts(), secrets.token_hex(4).upper())
