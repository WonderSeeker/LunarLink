# -*- coding: utf-8 -*-
"""系统类接口：健康检查。

/health 同时被三处使用：
  1. svc.ps1 的探测地址（U 字段）—— 非 2xx 会显示 FAIL
  2. CI 启动 SUT 后的就绪轮询
  3. 测试套件的可用性前置检查

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import __version__, config, db

router = APIRouter(tags=["system"])


@router.get("/health", summary="健康检查")
def health():
    """返回服务与数据库状态。数据库不可用返回 503，方便 CI 判定启动失败。"""
    try:
        row = db.query_one("SELECT 1 AS ok")
        db_ok = bool(row and row["ok"] == 1)
    except Exception:  # noqa: BLE001 —— 健康检查要兜住所有异常，不能自己 500
        db_ok = False

    payload = {
        "status": "ok" if db_ok else "degraded",
        "service": "lunarlink-sut",
        "version": __version__,
        "db": db_ok,
        "db_path": config.DB_PATH,
        "test_hooks": config.ENABLE_TEST_HOOKS,
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)
