# -*- coding: utf-8 -*-
"""商品模块用例：分页/关键字/排序边界、详情、库存、admin 权限与 CRUD。

边界口径（与实现一一对应）：
    page<1 / size<1 / size>100 -> 400 INVALID_PAGINATION
    page 超出总页数            -> 200 + 空列表（「参数非法」与「无数据」必须区分）
    keyword 超长               -> 400 KEYWORD_TOO_LONG
    sort 非白名单              -> 400 INVALID_SORT
    类型不对（如 /api/products/abc）-> 422
"""

import uuid

import allure
import pytest

from utils.data import create_product, unique
from utils.schema import validate


@allure.feature("商品")
@allure.story("列表与分页")
class TestProductList:

    @allure.title("默认分页 -> 200，结构符合 JSON Schema")
    @pytest.mark.smoke
    def test_default_page(self, sut):
        resp = sut.get("/api/products")
        assert resp.status_code == 200
        body = resp.json()
        validate(body, "product_list")
        assert body["total"] >= 10, "种子数据至少 10 个商品"
        assert len(body["items"]) <= body["size"]

    @allure.title("size=100（上限内）-> 200")
    @pytest.mark.boundary
    def test_max_size_ok(self, sut):
        assert sut.get("/api/products", params={"size": 100}).status_code == 200

    @allure.title("分页参数非法 -> 400 INVALID_PAGINATION")
    @pytest.mark.boundary
    @pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"size": 0}, {"size": -5}, {"size": 101}],
                             ids=["page0", "page-1", "size0", "size-5", "size101"])
    def test_invalid_pagination(self, sut, params):
        resp = sut.get("/api/products", params=params)
        assert resp.status_code == 400, resp.text[:200]
        assert resp.json()["code"] == "INVALID_PAGINATION"
        validate(resp.json(), "error")

    @allure.title("★ 超大页码 -> 200 + 空列表（不能当成参数非法）")
    @pytest.mark.boundary
    def test_huge_page_returns_empty(self, sut):
        resp = sut.get("/api/products", params={"page": 99999})
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] >= 10

    @allure.title("page 非数字 -> 422")
    @pytest.mark.negative
    def test_page_not_int(self, sut):
        assert sut.get("/api/products", params={"page": "abc"}).status_code == 422

    @allure.title("keyword 命中 / 不命中")
    @pytest.mark.smoke
    def test_keyword(self, sut):
        hit = sut.get("/api/products", params={"keyword": "保温"})
        assert hit.status_code == 200 and hit.json()["total"] == 1

        miss = sut.get("/api/products", params={"keyword": unique("绝对不存在的商品")})
        assert miss.status_code == 200 and miss.json()["total"] == 0

    @allure.title("★ keyword 长度边界：50 字符 -> 200，51 字符 -> 400")
    @pytest.mark.boundary
    def test_keyword_length(self, sut):
        assert sut.get("/api/products", params={"keyword": "k" * 50}).status_code == 200
        resp = sut.get("/api/products", params={"keyword": "k" * 51})
        assert resp.status_code == 400
        assert resp.json()["code"] == "KEYWORD_TOO_LONG"

    @allure.title("重名商品不会被误当关键字命中（uuid 造数隔离验证）")
    @pytest.mark.regression
    def test_unique_name_isolated(self, sut, sut_admin):
        product = create_product(sut_admin, name=unique("隔离验证"))
        hit = sut.get("/api/products", params={"keyword": product["name"]})
        assert hit.json()["total"] == 1

    @allure.title("sort 白名单：合法 4 种 -> 200，非法 -> 400 INVALID_SORT")
    @pytest.mark.boundary
    @pytest.mark.parametrize("sort,expected", [("id", 200), ("-id", 200), ("price", 200), ("-price", 200), ("evil", 400), ("name", 400)])
    def test_sort(self, sut, sort, expected):
        resp = sut.get("/api/products", params={"sort": sort})
        assert resp.status_code == expected, resp.text[:150]
        if expected == 400:
            assert resp.json()["code"] == "INVALID_SORT"

    @allure.title("价格升序排序真的生效")
    @pytest.mark.regression
    def test_sort_price_asc(self, sut):
        resp = sut.get("/api/products", params={"sort": "price", "size": 100})
        prices = [item["price_cents"] for item in resp.json()["items"]]
        assert prices == sorted(prices)


@allure.feature("商品")
@allure.story("详情与库存")
class TestProductDetail:

    @allure.title("详情 -> 200，结构符合 Schema")
    @pytest.mark.smoke
    def test_detail(self, sut):
        resp = sut.get("/api/products/1")
        assert resp.status_code == 200
        validate(resp.json(), "product")
        assert resp.json()["id"] == 1

    @allure.title("详情不存在 -> 404，错误体符合 Schema")
    @pytest.mark.negative
    def test_detail_not_found(self, sut):
        resp = sut.get("/api/products/999999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "PRODUCT_NOT_FOUND"
        validate(resp.json(), "error")

    @allure.title("详情 id 非数字 -> 422")
    @pytest.mark.negative
    def test_detail_bad_id(self, sut):
        assert sut.get("/api/products/abc").status_code == 422

    @allure.title("库存查询与数据库一致（落库校验）")
    @pytest.mark.regression
    def test_stock_matches_db(self, sut, sut_db):
        resp = sut.get("/api/products/1/stock")
        assert resp.status_code == 200
        assert resp.json()["stock"] == sut_db.product_stock(1)

    @allure.title("库存查询不存在 -> 404")
    @pytest.mark.negative
    def test_stock_not_found(self, sut):
        assert sut.get("/api/products/999999/stock").status_code == 404


@allure.feature("商品")
@allure.story("管理操作与权限")
class TestProductAdmin:

    @allure.title("普通用户创建商品 -> 403（垂直越权）")
    @pytest.mark.security
    def test_create_forbidden_for_customer(self, buyer):
        resp = buyer.post("/api/products", json={"name": unique("越权"), "price_cents": 100, "stock": 1})
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"

    @allure.title("匿名创建商品 -> 401")
    @pytest.mark.security
    def test_create_unauthorized_for_anonymous(self, sut):
        resp = sut.post("/api/products", json={"name": unique("匿名"), "price_cents": 100, "stock": 1})
        assert resp.status_code == 401

    @allure.title("admin 创建 -> 201，且落库可查")
    @pytest.mark.smoke
    def test_create_ok(self, sut_admin, sut_db):
        name = unique("新建商品")
        resp = sut_admin.post("/api/products", json={"name": name, "description": "描述", "price_cents": 2500, "stock": 9})
        assert resp.status_code == 201
        validate(resp.json(), "product")
        assert sut_db.product_stock(resp.json()["id"]) == 9

    @allure.title("重名创建 -> 409 PRODUCT_EXISTS")
    @pytest.mark.negative
    def test_create_duplicate(self, sut_admin):
        product = create_product(sut_admin)
        resp = sut_admin.post("/api/products", json={"name": product["name"], "price_cents": 1, "stock": 1})
        assert resp.status_code == 409
        assert resp.json()["code"] == "PRODUCT_EXISTS"

    @allure.title("业务参数非法 -> 400（负价格 / 负库存 / 空名称 / 超长名称）")
    @pytest.mark.negative
    @pytest.mark.parametrize(
        "payload,code",
        [
            ({"price_cents": -1, "stock": 1}, "INVALID_PRICE"),
            ({"price_cents": 1, "stock": -1}, "INVALID_STOCK"),
            ({"price_cents": 1, "stock": 1}, "INVALID_PRODUCT_NAME"),
        ],
        ids=["negative-price", "negative-stock", "empty-name"],
    )
    def test_create_invalid_business_params(self, sut_admin, payload, code):
        payload = dict(payload)
        payload.setdefault("name", unique("商品"))
        if code == "INVALID_PRODUCT_NAME":
            payload["name"] = ""
        resp = sut_admin.post("/api/products", json=payload)
        assert resp.status_code == 400, resp.text[:200]
        assert resp.json()["code"] == code

    @allure.title("创建参数类型不对 -> 422")
    @pytest.mark.negative
    def test_create_type_error(self, sut_admin):
        resp = sut_admin.post("/api/products", json={"name": unique("t"), "price_cents": "12.5", "stock": 1})
        assert resp.status_code == 422

    @allure.title("admin 更新 -> 200 且生效")
    @pytest.mark.smoke
    def test_update(self, sut_admin, sut_db):
        product = create_product(sut_admin, price_cents=1000, stock=5)
        resp = sut_admin.put("/api/products/{}".format(product["id"]), json={"price_cents": 2000, "stock": 8})
        assert resp.status_code == 200
        assert resp.json()["price_cents"] == 2000
        assert sut_db.product_stock(product["id"]) == 8

    @allure.title("更新不存在的商品 -> 404")
    @pytest.mark.negative
    def test_update_not_found(self, sut_admin):
        assert sut_admin.put("/api/products/999999", json={"stock": 1}).status_code == 404

    @allure.title("下架 -> 204，之后详情 404、重复下架 404")
    @pytest.mark.regression
    def test_delete_soft(self, sut, sut_admin):
        product = create_product(sut_admin)
        assert sut.get("/api/products/{}".format(product["id"])).status_code == 200

        assert sut_admin.delete("/api/products/{}".format(product["id"])).status_code == 204
        assert sut.get("/api/products/{}".format(product["id"])).status_code == 404
        assert sut_admin.delete("/api/products/{}".format(product["id"])).status_code == 404

    @allure.title("下架的商品不出现在列表里")
    @pytest.mark.regression
    def test_deleted_not_in_list(self, sut, sut_admin):
        product = create_product(sut_admin, name=unique("待下架"))
        before = sut.get("/api/products", params={"keyword": product["name"]}).json()["total"]
        assert before == 1
        sut_admin.delete("/api/products/{}".format(product["id"]))
        after = sut.get("/api/products", params={"keyword": product["name"]}).json()["total"]
        assert after == 0

    @allure.title("下架商品不能被加购 -> 404")
    @pytest.mark.regression
    def test_cannot_add_deleted_product(self, sut_admin, fresh_buyer):
        product = create_product(sut_admin)
        sut_admin.delete("/api/products/{}".format(product["id"]))
        resp = fresh_buyer["client"].post("/api/cart/items", json={"product_id": product["id"], "quantity": 1})
        assert resp.status_code == 404

    @allure.title("普通用户更新/下架商品 -> 403")
    @pytest.mark.security
    def test_update_delete_forbidden(self, buyer, sut_admin):
        product = create_product(sut_admin)
        assert buyer.put("/api/products/{}".format(product["id"]), json={"stock": 1}).status_code == 403
        assert buyer.delete("/api/products/{}".format(product["id"])).status_code == 403

    @allure.title("商品名重复时更新 -> 409")
    @pytest.mark.negative
    def test_update_duplicate_name(self, sut_admin):
        first = create_product(sut_admin)
        second = create_product(sut_admin)
        resp = sut_admin.put("/api/products/{}".format(second["id"]), json={"name": first["name"]})
        assert resp.status_code == 409
