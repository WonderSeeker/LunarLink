# -*- coding: utf-8 -*-
"""SUT 路由模块集合。

★ 约定：本目录下所有路由处理函数必须写成【同步 def】，禁止 async def。
  原因见 sut/app.py 顶部注释（sqlite3 是阻塞式的，async def 会卡死事件循环）。
"""
