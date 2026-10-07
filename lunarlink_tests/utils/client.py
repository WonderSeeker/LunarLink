# -*- coding: utf-8 -*-
"""HTTP 客户端与【认证头契约】的单一入口。

================================================================================
★★★ 为什么这里要收一个 auth_headers() ★★★
================================================================================
  这两个被测对象的认证头契约【不一样】（均为实测结论，不是猜的）：

    SUT（mock 电商）    Authorization: Bearer <token>     ← RFC 6750 风格
    LunarLink 平台      Authorization: <裸 token>          ← 见下

  后者的原因：`backend/utils/auth.py:MyJWTAuthentication` 把 authenticate() 整体覆写了，
  直接 `jwt_value = request.META.get("HTTP_AUTHORIZATION")` 丢给 jwt_decode_handler，
  而父类 JSONWebTokenAuthentication 默认实现是会按 JWT_KEYWORD 剥前缀的 ——
  覆写后就只剩裸 token 能过（实测带 "JWT " 前缀返回 401「签名解析失败」）。

  ★ 纪律：不要去"修"后端的认证头。这是它的【实际契约】（前端也是这么发的），
    改后端会连带打断前端。测试要跟随契约，而不是把契约改成"符合 RFC 6750 的样子"。
    把它记为一项测试发现；将来契约真变了，只需要改下面这两个常量。
================================================================================
"""

from typing import Any, Dict, Optional

import allure
import requests

TOKEN_STYLE_BEARER = "bearer"
TOKEN_STYLE_BARE = "bare"

# ↓↓↓ 契约的唯一事实源：将来变了只改这两行 ↓↓↓
TOKEN_STYLE_SUT = TOKEN_STYLE_BEARER
TOKEN_STYLE_LUNARLINK = TOKEN_STYLE_BARE


def auth_headers(token: Optional[str], style: str = TOKEN_STYLE_SUT) -> Dict[str, str]:
    """把 token 组装成该被测对象要求的 Authorization 头。

    没有 token 时也返回一个显式头（用于验证「认证头格式错误 -> 401」这类用例）。
    """
    if token is None:
        return {}

    if style == TOKEN_STYLE_BEARER:
        return {"Authorization": "Bearer {}".format(token)}
    if style == TOKEN_STYLE_BARE:
        return {"Authorization": token}

    raise ValueError("未知的 token 风格: {}".format(style))


class ApiClient:
    """带 token 与 allure 附件的轻量 HTTP 客户端（黑盒测试用，不依赖任何服务端代码）。"""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        token_style: str = TOKEN_STYLE_SUT,
        name: str = "api",
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.token_style = token_style
        self.name = name
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------- 基础
    def headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        merged = dict(auth_headers(self.token, self.token_style))
        if extra:
            merged.update(extra)
        return merged

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else self.base_url + path
        kwargs.setdefault("timeout", self.timeout)
        kwargs["headers"] = self.headers(kwargs.pop("headers", None))

        if "json" in kwargs:
            append_json = kwargs.pop("json")
        else:
            append_json = None

        response = self.session.request(method, url, json=append_json, **kwargs)

        # 全量附件：失败时排障不用重跑
        allure.attach(
            "{} {}\nHeaders: {}\nBody: {}".format(
                method,
                url,
                {k: v for k, v in kwargs["headers"].items()},
                append_json,
            ),
            name="{} 请求".format(self.name),
            attachment_type=allure.attachment_type.TEXT,
        )
        allure.attach(
            "HTTP {}\n{}".format(response.status_code, response.text[:8000]),
            name="{} 响应".format(self.name),
            attachment_type=allure.attachment_type.TEXT,
        )
        return response

    def get(self, path: str, **kw) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> requests.Response:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw) -> requests.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> requests.Response:
        return self.request("DELETE", path, **kw)

    # ------------------------------------------------------------- 便捷断言辅助
    @staticmethod
    def code_of(response: requests.Response) -> str:
        """取业务错误码；取不到返回空串（避免断言时抛异常掩盖真实失败）。"""
        try:
            body = response.json()
        except ValueError:
            return ""
        if isinstance(body, dict):
            return str(body.get("code", ""))
        return ""

    @staticmethod
    def json_of(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None
