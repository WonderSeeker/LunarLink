# -*- coding: utf-8 -*-
"""统一错误体与异常处理器。

================================================================================
状态码使用规范
================================================================================
★ 测试套件的第 1 层断言就是「HTTP 状态码 + 业务错误码」双层，
  所以【不要】把错误都塞进 200 + code，否则状态码断言会失效。

  200 / 201 / 204  成功
  400  业务参数非法      例：page=0、page=-1、keyword 超长、数量为 0 或负数、金额为 0
  401  未认证 / 凭证无效 例：无 token、伪造 token、已登出、token 过期
  403  角色权限不足      例：非 admin 调用 admin 专属接口（垂直越权）
  404  资源不存在 或 水平越权
  409  状态冲突          例：库存不足、非法状态流转、重复操作、用户名/商品名已存在
  422  参数格式错误      由 FastAPI/Pydantic 在进入业务代码前抛出（缺字段、类型不对）
  423  账号锁定          连续登录失败次数超过阈值
  500  服务端异常

================================================================================
★ 为什么「水平越权」返回 404 而不是 403（这是安全设计，不是疏漏）
================================================================================
  403 等于明确告诉调用方：「这个资源确实存在，只是你无权访问」——
  这就泄露了资源的存在性。返回 404 则让「无权访问」与「不存在」在外部完全
  不可区分，不泄露任何信息。

  这是业界惯例。先例：GitHub 对无权访问的私有仓库返回 404 而不是 403。

  ⚠️ 必须与「垂直越权」区分开：
     * 水平越权（访问他人的资源，如查别人的订单）→ 404，不泄露存在性
     * 垂直越权（角色不足，如普通用户调 admin 接口）→ 403，因为接口本身
       的存在性不是秘密，明确告知「权限不够」更有助于调用方定位问题
"""

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- 错误体


def error_body(
    code: str, message: str, detail: Optional[Any] = None
) -> Dict[str, Any]:
    """统一错误响应体。"""
    return {"code": code, "message": message, "detail": detail}


# --------------------------------------------------------------------- 异常定义


class BizError(Exception):
    """业务异常基类。抛出后由 register_exception_handlers 统一转成 JSON 响应。"""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "请求无法处理"

    def __init__(
        self,
        message: Optional[str] = None,
        detail: Optional[Any] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        self.message = message or self.message
        self.detail = detail
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


class InvalidParam(BizError):
    """400 —— 业务参数非法。"""

    status_code = 400
    code = "INVALID_PARAM"
    message = "业务参数非法"


class Unauthorized(BizError):
    """401 —— 未认证或凭证无效。"""

    status_code = 401
    code = "UNAUTHORIZED"
    message = "未认证或凭证无效"


class TokenExpired(BizError):
    """401 —— 登录凭证已过期。"""

    status_code = 401
    code = "TOKEN_EXPIRED"
    message = "登录凭证已过期"


class Forbidden(BizError):
    """403 —— 角色权限不足（垂直越权）。"""

    status_code = 403
    code = "FORBIDDEN"
    message = "角色权限不足"


class NotFound(BizError):
    """404 —— 资源不存在，或水平越权（见模块顶部说明）。"""

    status_code = 404
    code = "NOT_FOUND"
    message = "资源不存在"


class Conflict(BizError):
    """409 —— 状态冲突（库存不足、非法状态流转、重复操作等）。"""

    status_code = 409
    code = "CONFLICT"
    message = "状态冲突"


class AccountLocked(BizError):
    """423 —— 账号因连续登录失败被锁定。"""

    status_code = 423
    code = "ACCOUNT_LOCKED"
    message = "账号已被锁定，请稍后再试"


# --------------------------------------------------------------------- 处理器


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。

    注意：异常处理器写成 async def 是【安全】的 —— 它们内部不做任何 sqlite3
    调用，不会阻塞事件循环。路由处理函数则必须用同步 def（见 app.py 顶部说明）。
    """

    @app.exception_handler(BizError)
    async def _handle_biz_error(request: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422 —— 参数格式错误（缺字段 / 类型不对），由 Pydantic 在进入业务代码前抛出。"""
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR", "参数格式错误", jsonable_encoder(exc.errors())
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """兜住 FastAPI/Starlette 自身抛的 HTTPException（如路由不存在 404、方法不允许 405）。"""
        code_map = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code_map.get(exc.status_code, "HTTP_ERROR"), str(exc.detail), None
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("未捕获的异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "服务端内部错误", str(exc)),
        )
