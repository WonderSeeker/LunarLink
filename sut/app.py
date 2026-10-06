# -*- coding: utf-8 -*-
"""FastAPI 应用装配。

================================================================================
★★★ 强制约束：所有路由处理函数必须写成【同步 def】，禁止 async def ★★★
================================================================================
原因：本项目的数据访问层用的是标准库 sqlite3，它天生是【阻塞式】的。

  * 写成 `def`（同步）——FastAPI 会把处理函数丢进 anyio 线程池
    （默认 40 个工作线程）执行，多个请求是**真正并行**的。

  * 写成 `async def`——函数直接在事件循环（单线程）里执行，sqlite3 的阻塞
    调用会把整个事件循环卡住，导致并发请求互相排队、甚至超时。

test_07_concurrency.py 中的两个用例：
    「10 线程抢 5 件库存」「20 线程重复支付回调只成功一次」
完全依赖「同步 def 才能真并发」这一点。如果谁把路由改成了 async def，
这两个用例会【假失败】（表现为超时，或成功数不等于预期）。
届时请先回到这里检查路由是否为同步 def。

例外：exception_handler（见 errors.py）与中间件写成 async def 是安全的 ——
它们内部不做任何 sqlite3 调用，不会阻塞事件循环。
================================================================================
"""

from fastapi import FastAPI

from . import __version__, config, db
from .errors import register_exception_handlers
from .routers import system


def create_app() -> FastAPI:
    app = FastAPI(
        title="LunarLink SUT",
        version=__version__,
        description=(
            "LunarLink 接口测试平台的自建被测系统（mock 电商）。\n\n"
            "业务链路：登录 → 浏览商品 → 加购物车 → 下单 → 支付 → 查订单。\n\n"
            "这是**测试夹具**，不是生产服务：采用单 token 简化设计，"
            "金额一律用整数「分」，越权访问统一返回 404。"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    register_exception_handlers(app)

    # 幂等建表。放在这里而不是 startup 事件里，是为了让「直接 import sut.app」
    # 的路径（如 seed.py、CI 脚本）也能拿到可用的表结构。
    db.init_schema()

    app.include_router(system.router)

    # 测试后门路由（/api/_test/*）只在 SUT_ENABLE_TEST_HOOKS=1 时挂载。
    if config.ENABLE_TEST_HOOKS:
        from .routers import test_hooks  # 延迟导入：默认关闭时不产生依赖

        app.include_router(test_hooks.router)

    return app


app = create_app()
