# -*- coding: utf-8 -*-
"""订单模块：下单（防超卖）/ 列表 / 详情 / 取消 / 发货 / 确认收货 / 状态。

★ 两个面试重点都在这里：

  1. 防超卖：下单在 `BEGIN IMMEDIATE` 事务内用【单条条件更新】
     `UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?`，
     靠 `rowcount` 判定成败。绝不用「先 SELECT 再 UPDATE」——那是 check-then-act 竞态。
     抛错会回滚整个事务，此前已扣减的库存全部还原。

  2. 状态机：白名单定义在 config.ORDER_STATE_TRANSITIONS。
     非法流转一律 409 INVALID_STATE_TRANSITION，并且在 detail 里回传 from/to/allowed，
     让失败原因可观测。允许的流转：
         PENDING -> PAID / CANCELLED
         PAID    -> SHIPPED / REFUNDED（REFUNDED 本次未实现）
         SHIPPED -> COMPLETED
         COMPLETED / CANCELLED / REFUNDED -> 终态，不可再流转

  3. 越权：查/取消/确认他人的订单一律 404（不是 403），不泄露订单是否存在。

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import config, errors, store
from .auth import require_admin, require_auth

router = APIRouter(prefix="/api/orders", tags=["orders"])


class CreateOrderRequest(BaseModel):
    """下单入参目前为空（从购物车结算）；保留模型以便将来扩展备注等字段。"""


def _check_pagination(page: int, size: int) -> None:
    if page < 1:
        raise errors.InvalidParam(
            "page 必须 >= 1", code="INVALID_PAGINATION", detail={"page": page}
        )
    if size < 1 or size > config.MAX_PAGE_SIZE:
        raise errors.InvalidParam(
            "size 必须在 1~{} 之间".format(config.MAX_PAGE_SIZE),
            code="INVALID_PAGINATION",
            detail={"size": size},
        )


def _require_own_order(order_no: str, user_id: int) -> Dict[str, Any]:
    """取自己的订单；不存在或不属于自己都返回 404（水平越权不泄露存在性）。"""
    order = store.get_order(order_no, user_id)
    if order is None:
        raise errors.NotFound(
            "订单不存在", code="ORDER_NOT_FOUND", detail={"order_no": order_no}
        )
    return order


@router.post("", status_code=201, summary="从购物车下单（事务内扣库存，防超卖）")
def create_order(
    payload: CreateOrderRequest = None,
    principal: Dict[str, Any] = Depends(require_auth),
):
    return store.create_order_from_cart(principal["user"]["id"])


@router.get("", summary="我的订单列表")
def list_orders(
    status: str = "",
    page: int = 1,
    size: int = config.DEFAULT_PAGE_SIZE,
    principal: Dict[str, Any] = Depends(require_auth),
):
    _check_pagination(page, size)

    valid_status = set(config.ORDER_STATE_TRANSITIONS.keys())
    if status and status not in valid_status:
        raise errors.InvalidParam(
            "status 取值非法",
            code="INVALID_STATUS",
            detail={"status": status, "allowed": sorted(valid_status)},
        )

    return store.list_orders(
        principal["user"]["id"], status or None, page, size
    )


@router.get("/{order_no}", summary="订单详情")
def get_order(order_no: str, principal: Dict[str, Any] = Depends(require_auth)):
    return _require_own_order(order_no, principal["user"]["id"])


@router.get("/{order_no}/status", summary="订单状态")
def get_order_status(order_no: str, principal: Dict[str, Any] = Depends(require_auth)):
    order = _require_own_order(order_no, principal["user"]["id"])
    return {
        "order_no": order["order_no"],
        "status": order["status"],
        "paid_at": order["paid_at"],
        "cancelled_at": order["cancelled_at"],
        "completed_at": order["completed_at"],
    }


@router.post("/{order_no}/cancel", summary="取消订单（PENDING -> CANCELLED，回滚库存）")
def cancel_order(order_no: str, principal: Dict[str, Any] = Depends(require_auth)):
    return store.cancel_order(order_no, principal["user"]["id"])


@router.post("/{order_no}/ship", summary="发货（PAID -> SHIPPED，仅 admin）")
def ship_order(order_no: str, _admin=Depends(require_admin)):
    # ★ 显式走「管理员跨归属」入口：函数名 as_admin + 签名无 user_id，
    #   避免把跨用户操作藏在「user_id=None」这种隐式旁路里。
    return store.ship_order(order_no)


@router.post("/{order_no}/confirm", summary="确认收货（SHIPPED -> COMPLETED）")
def confirm_order(order_no: str, principal: Dict[str, Any] = Depends(require_auth)):
    return store.confirm_order(order_no, principal["user"]["id"])
