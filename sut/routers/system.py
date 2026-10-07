# -*- coding: utf-8 -*-
"""系统类接口：健康检查 + 数据重置。

/health 同时被三处使用：
  1. svc.ps1 的探测地址（U 字段）—— 非 2xx 会显示 FAIL
  2. CI 启动 SUT 后的就绪轮询
  3. 测试套件的可用性前置检查（conftest 会断言 test_hooks 字段）

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import __version__, config, db, errors, runtime
from .auth import require_admin

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
        "inflight": runtime.inflight(),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)


@router.post(
    "/api/system/reset",
    summary="重置数据（仅 admin；要求当前无并发请求）",
    description=(
        "删库 + 重建 + 灌种子。\n\n"
        "★ 为什么要有并发保护：重置要删除数据库文件，而 Windows 在文件被其它请求"
        "持有时会拒绝删除。若不做保护，可能「删到一半失败」留下坏库。\n\n"
        "★ 注意：重置会清空 tokens 表，调用方持有的 token 立即失效，需要重新登录/签发。"
        "这也是测试套件约定「reset 只在会话开始时用一次」的原因 —— "
        "用例之间的数据隔离靠 uuid 后缀造独立数据，不靠 reset。"
    ),
)
def reset_system(_admin=Depends(require_admin)):
    current = runtime.inflight()
    if current > 1:
        # current 包含本次请求自身，所以 >1 表示还有别的请求在途
        raise errors.Conflict(
            "有其他请求正在处理，拒绝重置数据",
            code="RESET_BUSY",
            detail={"inflight": current},
        )

    # 延迟导入：避免 seed 模块在服务启动时产生副作用
    from .. import seed as seed_module

    try:
        seed_module.reset_database()
    except OSError as exc:
        # 文件被占用（上面那个检查与这里之间存在竞态窗口，兜一下）
        raise errors.Conflict(
            "数据库文件被占用，无法重置",
            code="RESET_BUSY",
            detail={"error": str(exc)},
        )

    seed_module.seed()
    return {
        "code": "OK",
        "message": "数据已重置（库已重建并灌入种子数据）",
        "db_path": config.DB_PATH,
        "inflight_at_reset": current,
    }
