# -*- coding: utf-8 -*-
"""购物车模块：查看 / 加购（同商品合并）/ 改数量 / 移除 / 清空。

校验规则（test_03_cart.py 逐条断言）：
    quantity <= 0        -> 400 INVALID_QUANTITY
    quantity > 库存       -> 409 INSUFFICIENT_STOCK
    商品不存在或已下架     -> 404 PRODUCT_NOT_FOUND
    同商品重复加购         -> 数量合并（首次 201，合并 200）
    他人在自己的车里看不到、改不到、删不到 -> 404

★ 库存与数量的校验在 store 层的事务内完成（读-判-写原子），
  这样同一用户并发加购同一商品也不会绕过库存上限。

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, StrictInt

from .. import errors, store
from .auth import require_auth

router = APIRouter(prefix="/api/cart", tags=["cart"])


class AddItemRequest(BaseModel):
    product_id: StrictInt
    quantity: StrictInt


class UpdateItemRequest(BaseModel):
    quantity: StrictInt


def _check_quantity(quantity: int) -> None:
    if quantity <= 0:
        raise errors.InvalidParam(
            "数量必须为大于 0 的整数",
            code="INVALID_QUANTITY",
            detail={"quantity": quantity},
        )


@router.get("", summary="查看购物车")
def get_cart(principal: Dict[str, Any] = Depends(require_auth)):
    return store.get_cart(principal["user"]["id"])


@router.post("/items", summary="加入购物车（同商品数量合并）")
def add_item(
    payload: AddItemRequest,
    response: Response,
    principal: Dict[str, Any] = Depends(require_auth),
):
    _check_quantity(payload.quantity)
    item_id, created = store.add_cart_item(
        principal["user"]["id"], payload.product_id, payload.quantity
    )
    # 新增 201，合并到已有条目 200 —— 让调用方/测试能区分两种语义
    response.status_code = 201 if created else 200
    cart = store.get_cart(principal["user"]["id"])
    item = next((i for i in cart["items"] if i["id"] == item_id), None)
    return {"item": item, "created": created, "cart_total_amount_cents": cart["total_amount_cents"]}


@router.put("/items/{item_id}", summary="修改购物车条目数量")
def update_item(
    item_id: int,
    payload: UpdateItemRequest,
    principal: Dict[str, Any] = Depends(require_auth),
):
    _check_quantity(payload.quantity)
    store.update_cart_item(principal["user"]["id"], item_id, payload.quantity)
    cart = store.get_cart(principal["user"]["id"])
    item = next((i for i in cart["items"] if i["id"] == item_id), None)
    return {"item": item, "cart_total_amount_cents": cart["total_amount_cents"]}


@router.delete("/items/{item_id}", status_code=204, summary="移除购物车条目")
def delete_item(item_id: int, principal: Dict[str, Any] = Depends(require_auth)):
    store.delete_cart_item(principal["user"]["id"], item_id)
    return None


@router.delete("", status_code=204, summary="清空购物车")
def clear_cart(principal: Dict[str, Any] = Depends(require_auth)):
    store.clear_cart(principal["user"]["id"])
    return None
