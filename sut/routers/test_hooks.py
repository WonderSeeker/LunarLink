# -*- coding: utf-8 -*-
"""测试后门（/api/_test/*）—— 仅当 SUT_ENABLE_TEST_HOOKS=1 时挂载。

为什么需要它：
    「token 过期」「账号锁定后解锁」这类场景，如果靠真实时间去等，测试套件就会充满
    sleep(60)，又慢又不稳定。后门让这些状态可以被**确定性构造**，测试里零 sleep。

================================================================================
★★★ 安全口径（务必遵守）★★★
================================================================================
    这是【测试专用后门】，绝不可用于生产：
      * /api/_test/token 能签发【任意 TTL 的合法 token】—— 一旦可达，等价于
        【认证绕过】（可以直接签发管理员身份的长期 token）。
      * 因此它由 config.ENABLE_TEST_HOOKS（默认 False）控制是否【注册路由】。
        关闭时 /api/_test/* 一律返回 404（不是 403 —— 403 会暴露"这个后门存在"），
        整个模块根本不会被导入。
      * 服务必须绑 127.0.0.1，禁止 0.0.0.0（见 sut/run-sut.ps1 的安全红线注释）。
      * 生产环境请勿设置 SUT_ENABLE_TEST_HOOKS。
================================================================================

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, StrictInt, StrictStr

from .. import __version__, config, errors, store

router = APIRouter(prefix="/api/_test", tags=["test-hooks"])


class IssueTokenRequest(BaseModel):
    # 同 auth.py：用 Strict* 避免 pydantic v1 的宽松转换让「类型不对 -> 422」失效
    username: StrictStr
    # 允许为负数 → 签发「一出生就已过期」的 token，用于确定性验证过期分支
    ttl_seconds: Optional[StrictInt] = None


@router.get("/ping", summary="测试后门可用性探测")
def ping():
    """后门开启时返回 200；未开启时该路由不存在（404）。"""
    return {
        "ok": True,
        "service": "lunarlink-sut",
        "version": __version__,
        "test_hooks": config.ENABLE_TEST_HOOKS,
    }


@router.post(
    "/token",
    summary="签发任意 TTL 的 token（★ 认证绕过，仅测试环境）",
    description=(
        "ttl_seconds 省略时用服务端默认 TTL；传负数即得到已过期的 token。"
        "用于确定性验证「token 过期」分支，测试里不需要 sleep。"
    ),
)
def issue_token(payload: IssueTokenRequest):
    user = store.get_user_by_username(payload.username)
    if user is None:
        raise errors.NotFound(
            "用户不存在", code="USER_NOT_FOUND", detail={"username": payload.username}
        )

    issued = store.create_token(user["id"], payload.ttl_seconds)
    issued["user"] = store.public_user(user)
    return issued


@router.post(
    "/user/{username}/unlock",
    summary="立即解锁账号并清零失败计数",
    description="用于验证「锁定后解锁即可正常登录」，避免测试等待真实的锁定时长。",
)
def unlock_user(username: str):
    user = store.get_user_by_username(username)
    if user is None:
        raise errors.NotFound(
            "用户不存在", code="USER_NOT_FOUND", detail={"username": username}
        )

    store.unlock_user(user["id"])
    return {"code": "OK", "username": username, "failed_attempts": 0, "locked_until": 0}
