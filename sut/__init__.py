# -*- coding: utf-8 -*-
"""LunarLink SUT —— 被测系统（mock 电商服务）。

SUT = System Under Test。这是为了给「接口测试平台」提供一个**真实业务语义**的
被测对象而自建的轻量服务，不是生产代码。

- 技术栈：FastAPI + 标准库 sqlite3（不用 SQLAlchemy）
- 端口：8021
- 业务链路：登录 → 浏览商品 → 加购物车 → 下单 → 支付 → 查订单
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
