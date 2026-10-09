# -*- coding: utf-8 -*-
"""支付模块（范围收敛版）：仅【创建支付单】+【幂等回调】。

按约定跳过：订单支付超时补偿、退款。（schema 里仍保留 orders.expire_at 字段，
下单时照常写入，只是本次不做超时拦截。）

★ 幂等实现要点（面试重点）：
  用【数据库唯一约束】做幂等闸门，而不是「先查有没有处理过再决定」。
  并发下 check-then-act 必然漏，唯一约束不会：
      INSERT INTO payment_callbacks(notify_id) 成功     -> 首次回调，正常入账
      INSERT 抛 sqlite3.IntegrityError                  -> 重复回调，直接返回首次结果
  重复回调【绝不再改 payments / orders 的任何字段】（测试会断言 paid_at 不变）。

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, StrictInt, StrictStr

from .. import config, errors, store
from .auth import require_auth

router = APIRouter(prefix="/api/payments", tags=["payments"])


class CreatePaymentRequest(BaseModel):
    order_no: StrictStr


class CallbackRequest(BaseModel):
    notify_id: StrictStr
    amount_cents: StrictInt


def _payment_view(pay: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pay_no": pay["pay_no"],
        "order_no": pay["order_no"],
        "amount_cents": int(pay["amount_cents"]),
        "status": pay["status"],
        "amount_paid_cents": int(pay["amount_paid_cents"]),
        "notify_id": pay["notify_id"],
        "created_at": pay["created_at"],
        "paid_at": pay["paid_at"],
    }


@router.post("", summary="创建支付单（同一订单重复发起返回同一支付单）")
def create_payment(
    payload: CreatePaymentRequest,
    response: Response,
    principal: Dict[str, Any] = Depends(require_auth),
):
    payment, created = store.create_payment(
        payload.order_no, principal["user"]["id"]
    )
    # 新建 201；已存在则复用同一个支付单，返回 200
    response.status_code = 201 if created else 200
    return {"payment": _payment_view(payment), "created": created}


@router.get("/{pay_no}", summary="查询支付单（他人的支付单返回 404）")
def get_payment(pay_no: str, principal: Dict[str, Any] = Depends(require_auth)):
    payment = store.get_payment(pay_no, principal["user"]["id"])
    if payment is None:
        raise errors.NotFound(
            "支付单不存在", code="PAYMENT_NOT_FOUND", detail={"pay_no": pay_no}
        )
    return {"payment": _payment_view(payment)}


@router.post(
    "/{pay_no}/callback",
    summary="支付回调（★ 基于数据库唯一约束实现幂等）",
    description=(
        "同一个 notify_id 重复回调只会入账一次；重复回调返回 duplicated=true，"
        "并且不会修改 payments / orders 的任何字段。"
    ),
)
def payment_callback(pay_no: str, payload: CallbackRequest):
    if not payload.notify_id.strip():
        raise errors.InvalidParam("notify_id 不能为空", code="INVALID_NOTIFY_ID")

    try:
        return store.settle_payment(pay_no, payload.notify_id, payload.amount_cents)
    except sqlite3.IntegrityError:
        # ★ 幂等命中：unique(payment_callbacks.notify_id) 拦下的重复回调。
        #   这里只读取当前状态返回，绝不重复入账。
        payment = store.get_payment(pay_no)
        if payment is None:
            raise errors.NotFound(
                "支付单不存在", code="PAYMENT_NOT_FOUND", detail={"pay_no": pay_no}
            )

        order = store.get_order(payment["order_no"])
        return {
            "pay_no": pay_no,
            "order_no": payment["order_no"],
            "duplicated": True,
            "order_status": order["status"] if order else None,
            "amount_paid_cents": int(payment["amount_paid_cents"]),
            "paid_at": payment["paid_at"],
            "notify_id": payment["notify_id"],
        }
