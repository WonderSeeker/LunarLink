# -*- coding: utf-8 -*-
"""测试后门（/api/_test/*）—— 仅当 SUT_ENABLE_TEST_HOOKS=1 时挂载。

为什么需要它：
    「token 过期」「订单支付超时」「账号锁定后解锁」这类场景，如果靠真实时间去等，
    测试套件就会充满 sleep(60)，又慢又不稳定。后门让这些状态可以被**确定性构造**，
    测试里零 sleep。

安全口径：
    这是**测试夹具**的一部分。生产环境不设 SUT_ENABLE_TEST_HOOKS，
    整个模块不会被导入，路由也不存在（访问返回 404）。

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。

本阶段仅提供 /api/_test/ping 用于验证「后门开关是否生效」，
token 签发 / 订单拨过期 / 账号解锁 / 数据重置在后续提交中补齐。
"""

from fastapi import APIRouter

from .. import __version__, config

router = APIRouter(prefix="/api/_test", tags=["test-hooks"])


@router.get("/ping", summary="测试后门可用性探测")
def ping():
    """后门开启时返回 200；未开启时该路由不存在（404）。

    用例可借此断言「后门开关生效」。
    """
    return {
        "ok": True,
        "service": "lunarlink-sut",
        "version": __version__,
        "test_hooks": config.ENABLE_TEST_HOOKS,
    }
