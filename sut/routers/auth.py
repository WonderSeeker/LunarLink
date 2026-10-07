# -*- coding: utf-8 -*-
"""认证模块：注册 / 登录 / 登出 / 当前用户 / 续签。

★ 路由处理函数全部用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。

鉴权契约（★ 与 LunarLink 平台【不同】，测试里要用不同写法，别串了）：
    本 SUT      : Authorization: Bearer <token>   （RFC 6750 风格）
    LunarLink   : Authorization: <token>          （裸 token，见 CHANGES.md 第 8 节）

登录锁定语义（严格边界，test_01_auth.py 逐条断言）：
    · 连续失败第 1~4 次 → 401 BAD_CREDENTIALS（绝不锁）
    · 连续失败第 5 次   → 423 ACCOUNT_LOCKED（带 Retry-After），此时才写 locked_until
    · 锁定期间用【正确密码】→ 仍 423（不能因为密码对就放行）
    · 登录成功 → 失败计数与 locked_until 一起归零（所以「成功 1 次后再错 4 次」不锁）
    · 锁定自然到期后 → 计数归零，重新开一个窗口（否则到期后错 1 次就又被锁死）
    计数器落库（users.failed_attempts / locked_until），不在内存 —— 重启不会解锁。
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, StrictStr

from .. import config, errors, security, store

router = APIRouter(prefix="/api/auth", tags=["auth"])

BEARER_PREFIX = "bearer"


# --------------------------------------------------------------------- 请求模型
# 只做「类型/必填」校验（由 Pydantic 抛 422），长度等业务规则在业务代码里判 400。
#
# ★ 必须用 StrictStr 而不是 str：pydantic v1 默认做宽松转换，
#   传 {"password": 123456}（int）会被静默转成 "123456" 而通过校验。
#   这会让「类型不对应返回 422」的约定失效（实测确实如此）。
#   换成 StrictStr 后，非字符串直接 422。


class RegisterRequest(BaseModel):
    username: StrictStr
    password: StrictStr
    name: Optional[StrictStr] = None


class LoginRequest(BaseModel):
    username: StrictStr
    password: StrictStr


# --------------------------------------------------------------------- 鉴权依赖


def require_auth(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """校验 Bearer token，返回 {"user", "token", "token_row"}。

    401 一律用同一个 code（UNAUTHORIZED），只有「已过期」单独用 TOKEN_EXPIRED ——
    这样测试既能断言状态码，也能断言错误码分类。
    """
    raw = (authorization or "").strip()
    if not raw:
        raise errors.Unauthorized("缺少 Authorization 请求头")

    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != BEARER_PREFIX:
        raise errors.Unauthorized(
            "认证头格式错误，应为 'Authorization: Bearer <token>'"
        )
    token = parts[1].strip()

    row = store.get_token(token)
    if row is None:
        raise errors.Unauthorized("登录凭证无效")

    if row["revoked"]:
        raise errors.Unauthorized("登录凭证已失效")

    if row["expires_at"] <= security.now_ts():
        raise errors.TokenExpired()

    user = store.get_user_by_id(row["user_id"])
    if user is None:
        raise errors.Unauthorized("登录凭证无效")

    if not user["is_active"]:
        raise errors.Forbidden("账号已被禁用", code="USER_DISABLED")

    return {"user": user, "token": token, "token_row": row}


def require_admin(principal: Dict[str, Any] = Depends(require_auth)):
    """在 require_auth 基础上要求 admin 角色。

    ★ 这里返回 403（而不是 404）：这是【垂直越权】—— 接口本身的存在性不是秘密，
      明确告知"权限不够"更有助于调用方定位问题。
      对比：【水平越权】（访问他人的订单/支付单）返回 404，因为要隐藏资源是否存在。
    """
    if principal["user"]["role"] != config.ROLE_ADMIN:
        raise errors.Forbidden("需要管理员权限")
    return principal


# --------------------------------------------------------------------- 辅助


def _lock_remaining(user: Dict[str, Any], now: int) -> int:
    locked_until = int(user.get("locked_until") or 0)
    return max(locked_until - now, 0)


def _issue_token_response(user: Dict[str, Any], ttl: Optional[int] = None) -> Dict:
    issued = store.create_token(user["id"], ttl)
    return {
        "token": issued["token"],
        "expires_at": issued["expires_at"],
        "token_type": "Bearer",
        "user": store.public_user(user),
    }


# --------------------------------------------------------------------- 接口


@router.post(
    "/register",
    status_code=201,
    summary="注册",
    description="用户名 3~32 字符、密码 6~64 字符；重名返回 409。",
)
def register(payload: RegisterRequest):
    username = payload.username.strip()
    password = payload.password

    if not (config.MIN_USERNAME_LENGTH <= len(username) <= config.MAX_USERNAME_LENGTH):
        raise errors.InvalidParam(
            "用户名长度需为 {}~{} 个字符".format(
                config.MIN_USERNAME_LENGTH, config.MAX_USERNAME_LENGTH
            ),
            code="INVALID_USERNAME",
            detail={"length": len(username)},
        )

    if not (config.MIN_PASSWORD_LENGTH <= len(password) <= config.MAX_PASSWORD_LENGTH):
        raise errors.InvalidParam(
            "密码长度需为 {}~{} 个字符".format(
                config.MIN_PASSWORD_LENGTH, config.MAX_PASSWORD_LENGTH
            ),
            code="INVALID_PASSWORD",
            detail={"length": len(password)},
        )

    if store.get_user_by_username(username) is not None:
        raise errors.Conflict("用户名已存在", code="USERNAME_EXISTS")

    user = store.create_user(
        username=username,
        password=password,
        name=payload.name or username,
        role=config.ROLE_CUSTOMER,
    )
    return {"user": store.public_user(user)}


@router.post(
    "/login",
    summary="登录",
    description="返回 Bearer token。连续失败 5 次锁定账号并返回 423（带 Retry-After）。",
)
def login(payload: LoginRequest):
    now = security.now_ts()
    username = payload.username.strip()

    user = store.get_user_by_username(username)

    # 用户名不存在也返回与密码错误完全相同的 401，
    # 避免通过响应差异枚举出系统里有哪些账号。
    if user is None:
        raise errors.Unauthorized("用户名或密码错误", code="BAD_CREDENTIALS")

    # ① 锁定期间：无条件 423（即使密码正确也不放行）
    remaining = _lock_remaining(user, now)
    if remaining > 0:
        raise errors.AccountLocked(
            retry_after=remaining,
            message="账号已锁定，请 {} 秒后重试".format(remaining),
            detail={"locked_until": user["locked_until"]},
        )

    # ② 锁定已自然到期 → 计数归零，重新开一个窗口
    if int(user.get("locked_until") or 0) > 0:
        store.reset_login_failure(user["id"])
        user["failed_attempts"] = 0
        user["locked_until"] = 0

    # ③ 密码校验
    if not security.verify_password(
        payload.password, user["password_hash"], user["password_salt"]
    ):
        attempts = int(user.get("failed_attempts") or 0) + 1
        if attempts >= config.LOGIN_LOCK_THRESHOLD:
            locked_until = now + config.LOGIN_LOCK_SECONDS
            store.update_login_failure(user["id"], attempts, locked_until)
            raise errors.AccountLocked(
                retry_after=config.LOGIN_LOCK_SECONDS,
                message="密码连续错误 {} 次，账号已锁定 {} 秒".format(
                    attempts, config.LOGIN_LOCK_SECONDS
                ),
                detail={"failed_attempts": attempts, "locked_until": locked_until},
            )

        store.update_login_failure(user["id"], attempts, 0)
        raise errors.Unauthorized(
            "用户名或密码错误",
            code="BAD_CREDENTIALS",
            detail={
                "failed_attempts": attempts,
                "lock_threshold": config.LOGIN_LOCK_THRESHOLD,
            },
        )

    # ④ 登录成功：计数与锁定一起归零
    if int(user.get("failed_attempts") or 0) or int(user.get("locked_until") or 0):
        store.reset_login_failure(user["id"])

    if not user["is_active"]:
        raise errors.Forbidden("账号已被禁用", code="USER_DISABLED")

    return _issue_token_response(user)


@router.post(
    "/logout",
    summary="登出",
    description=(
        "★ 纯 no-op（服务端无状态）。单 token 简化设计下服务端不主动失效 token，"
        "客户端丢弃本地 token 即可。响应里明确写出这一点，不假装服务端已失效。"
    ),
)
def logout():
    return {
        "code": "OK",
        "message": "服务端无状态，不会主动失效 token；请客户端自行丢弃本地 token",
        "token_revoked": False,
    }


@router.get("/profile", summary="获取当前登录用户")
def profile(principal: Dict[str, Any] = Depends(require_auth)):
    return {"user": store.public_user(principal["user"])}


@router.post(
    "/refresh",
    summary="用仍然有效的旧 token 换一个新 token",
    description=(
        "★ 语义刻意定为「旧换新」而不是引入 refresh token —— 后者会带出"
        "过期、轮换、重放等一连串问题，超出这个 mock 服务的目标。"
        "旧 token 在自身过期前仍然有效（服务端不做轮换失效）。"
    ),
)
def refresh(principal: Dict[str, Any] = Depends(require_auth)):
    user = principal["user"]
    return _issue_token_response(user)
