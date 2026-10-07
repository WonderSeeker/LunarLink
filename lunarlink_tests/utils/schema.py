# -*- coding: utf-8 -*-
"""JSON Schema 校验（断言体系第 2 层）。

作用：把「字段名 / 类型 / 必填」的约定固化下来。接口把 `price_cents` 改成字符串、
或把 `total` 改名，结构校验会立刻发现 —— 这比逐个字段 assert 更早、更全面地暴露问题。

★ 金额字段在 schema 里一律约束为 `"type": "integer"`（分），
  这样任何意外引入的浮点/字符串金额都会被结构校验抓住。
"""

import json
import os
from typing import Any, Dict

import jsonschema

try:  # jsonschema >= 4.18 的引用解析方式
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    _HAS_REFERENCING = True
except ImportError:  # pragma: no cover
    _HAS_REFERENCING = False

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas")

_cache: Dict[str, Dict[str, Any]] = {}
_registry = None


def load_schema(name: str) -> Dict[str, Any]:
    """按名字加载 schemas/ 下的 json（带缓存）。name 可带或不带 .json 后缀。"""
    if not name.endswith(".json"):
        name += ".json"
    if name not in _cache:
        path = os.path.join(SCHEMA_DIR, name)
        with open(path, "r", encoding="utf-8") as fp:
            _cache[name] = json.load(fp)
    return _cache[name]


def _get_registry():
    """把 schemas/ 下所有文件注册进去，让 `"$ref": "product.json"` 这类相对引用能解析。

    不注册的话 jsonschema 会抛 Unresolvable —— 这也是为什么必须实测一次 schema 校验。
    """
    global _registry
    if _registry is None:
        if not _HAS_REFERENCING:
            return None
        resources = []
        for filename in os.listdir(SCHEMA_DIR):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(SCHEMA_DIR, filename), "r", encoding="utf-8") as fp:
                resources.append(
                    (filename, Resource.from_contents(json.load(fp), default_specification=DRAFT7))
                )
        _registry = Registry().with_resources(resources)
    return _registry


def validate(payload: Any, schema_name: str) -> None:
    """校验失败时抛出带可读路径的 AssertionError（而不是裸 jsonschema 异常）。"""
    schema = load_schema(schema_name)
    kwargs = {}
    registry = _get_registry()
    if registry is not None:
        kwargs["registry"] = registry

    try:
        jsonschema.validate(instance=payload, schema=schema, **kwargs)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise AssertionError(
            "JSON Schema 校验失败 [{}]\n  路径: {}\n  原因: {}\n  实际值: {}".format(
                schema_name, path, exc.message, json.dumps(payload, ensure_ascii=False)[:1500]
            )
        ) from exc

