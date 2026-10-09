# -*- coding: utf-8 -*-
"""商品模块：分页列表 / 详情 / 增删改（admin）/ 库存查询。

分页与关键字的边界规则（test_02_products.py 逐条断言）：
    page < 1                     -> 400 INVALID_PAGINATION
    size < 1 或 > MAX_PAGE_SIZE  -> 400 INVALID_PAGINATION
    page 超出总页数               -> 200 + 空列表（「参数非法」与「没数据」必须区分开）
    keyword 超过 MAX_KEYWORD_LENGTH -> 400 KEYWORD_TOO_LONG
    sort 不在白名单               -> 400 INVALID_SORT

★ 路由处理函数必须用同步 def，禁止 async def —— 原因见 sut/app.py 顶部注释。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictInt, StrictStr

from .. import config, errors, store
from .auth import require_admin

router = APIRouter(prefix="/api/products", tags=["products"])

VALID_SORTS = ("id", "-id", "price", "-price")
PUBLIC_FIELDS = (
    "id",
    "name",
    "description",
    "price_cents",
    "stock",
    "status",
    "created_at",
    "updated_at",
)


def public_product(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in PUBLIC_FIELDS if key in row}


def _check_pagination(page: int, size: int) -> None:
    if page < 1:
        raise errors.InvalidParam(
            "page 必须 >= 1", code="INVALID_PAGINATION", detail={"page": page}
        )
    if size < 1 or size > config.MAX_PAGE_SIZE:
        raise errors.InvalidParam(
            "size 必须在 1~{} 之间".format(config.MAX_PAGE_SIZE),
            code="INVALID_PAGINATION",
            detail={"size": size, "max": config.MAX_PAGE_SIZE},
        )


def _check_product_fields(
    name: Optional[str], price_cents: Optional[int], stock: Optional[int]
) -> None:
    if name is not None and not (1 <= len(name) <= 100):
        raise errors.InvalidParam(
            "商品名称长度需为 1~100 个字符",
            code="INVALID_PRODUCT_NAME",
            detail={"length": len(name)},
        )
    if price_cents is not None and price_cents < 0:
        raise errors.InvalidParam(
            "价格（分）不能为负数",
            code="INVALID_PRICE",
            detail={"price_cents": price_cents},
        )
    if stock is not None and stock < 0:
        raise errors.InvalidParam(
            "库存不能为负数", code="INVALID_STOCK", detail={"stock": stock}
        )


class ProductCreate(BaseModel):
    name: StrictStr
    description: Optional[StrictStr] = None
    price_cents: StrictInt
    stock: StrictInt


class ProductUpdate(BaseModel):
    name: Optional[StrictStr] = None
    description: Optional[StrictStr] = None
    price_cents: Optional[StrictInt] = None
    stock: Optional[StrictInt] = None


@router.get("", summary="商品列表（分页 / 关键字 / 排序）")
def list_products(
    page: int = 1,
    size: int = config.DEFAULT_PAGE_SIZE,
    keyword: str = "",
    sort: str = "id",
):
    _check_pagination(page, size)

    if len(keyword) > config.MAX_KEYWORD_LENGTH:
        raise errors.InvalidParam(
            "keyword 长度不能超过 {} 个字符".format(config.MAX_KEYWORD_LENGTH),
            code="KEYWORD_TOO_LONG",
            detail={"length": len(keyword)},
        )

    if sort not in VALID_SORTS:
        raise errors.InvalidParam(
            "sort 仅支持以下几种：{}".format(" / ".join(VALID_SORTS)),
            code="INVALID_SORT",
            detail={"sort": sort},
        )

    result = store.list_products(
        page=page, size=size, keyword=keyword.strip() or None, sort=sort
    )
    return {
        "items": [public_product(item) for item in result["items"]],
        "total": result["total"],
        "page": page,
        "size": size,
    }


@router.get("/{product_id}", summary="商品详情")
def get_product(product_id: int):
    product = store.get_product(product_id)
    # 已下架的商品对外也视为不存在
    if product is None or int(product["status"]) != 1:
        raise errors.NotFound(
            "商品不存在", code="PRODUCT_NOT_FOUND", detail={"product_id": product_id}
        )
    return public_product(product)


@router.get("/{product_id}/stock", summary="查询商品库存")
def get_product_stock(product_id: int):
    product = store.get_product(product_id)
    if product is None:
        raise errors.NotFound(
            "商品不存在", code="PRODUCT_NOT_FOUND", detail={"product_id": product_id}
        )
    return {
        "product_id": product["id"],
        "name": product["name"],
        "stock": int(product["stock"]),
        "status": int(product["status"]),
    }


@router.post("", status_code=201, summary="创建商品（仅 admin）")
def create_product(payload: ProductCreate, _admin=Depends(require_admin)):
    _check_product_fields(payload.name, payload.price_cents, payload.stock)

    if store.get_product_by_name(payload.name) is not None:
        raise errors.Conflict("商品名称已存在", code="PRODUCT_EXISTS")

    product = store.create_product(
        name=payload.name,
        description=payload.description or "",
        price_cents=payload.price_cents,
        stock=payload.stock,
    )
    return public_product(product)


@router.put("/{product_id}", summary="更新商品（仅 admin）")
def update_product(
    product_id: int, payload: ProductUpdate, _admin=Depends(require_admin)
):
    product = store.get_product(product_id)
    if product is None:
        raise errors.NotFound(
            "商品不存在", code="PRODUCT_NOT_FOUND", detail={"product_id": product_id}
        )

    _check_product_fields(payload.name, payload.price_cents, payload.stock)

    if payload.name is not None and payload.name != product["name"]:
        if store.get_product_by_name(payload.name) is not None:
            raise errors.Conflict("商品名称已存在", code="PRODUCT_EXISTS")

    updated = store.update_product(
        product_id,
        name=payload.name,
        description=payload.description,
        price_cents=payload.price_cents,
        stock=payload.stock,
    )
    return public_product(updated)


@router.delete("/{product_id}", status_code=204, summary="下架商品（仅 admin，软删）")
def delete_product(product_id: int, _admin=Depends(require_admin)):
    product = store.get_product(product_id)
    if product is None or int(product["status"]) != 1:
        raise errors.NotFound(
            "商品不存在", code="PRODUCT_NOT_FOUND", detail={"product_id": product_id}
        )
    store.soft_delete_product(product_id)
    return None
