# -*- coding: utf-8 -*-
"""幂等地准备接口自动化套件用的回归冒烟账号。

为什么需要它：
    自动化套件要能在【干净数据库】上自举（重建 DB / 换机器 / 上 CI 之后依然能跑），
    所以这个账号不能只是本地 DB 里的一条手工数据，必须落成代码。

用法：
    python manage.py ensure_smoke_user

可覆盖的环境变量（env 优先于默认值）：
    SMOKE_USERNAME   默认 smoke_regression
    SMOKE_PASSWORD   默认见下方 DEFAULT_PASSWORD

设计说明：
    * get_or_create 语义 —— 已存在则复用并重置密码，可重复执行，不产生重复账号。
    * 该账号刻意设为【非超管】，避免自动化套件持有过高权限、掩盖真实权限问题。
    * 不写明文到文档：CHANGES.md 只指向本命令，实际口令由 env/本文件决定。
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEFAULT_USERNAME = "smoke_regression"
DEFAULT_PASSWORD = "smoke123456"


class Command(BaseCommand):
    help = "幂等地创建/更新回归冒烟账号（供 lunarlink_tests 的 session fixture 调用）"

    def handle(self, *args, **options):
        username = os.environ.get("SMOKE_USERNAME", DEFAULT_USERNAME)
        password = os.environ.get("SMOKE_PASSWORD", DEFAULT_PASSWORD)

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"name": "回归冒烟账号", "is_active": True},
        )

        user.set_password(password)
        user.is_active = True
        # 刻意不给超管权限：套件应验证普通用户的真实权限边界
        user.is_superuser = False
        user.is_staff = False
        user.save()

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                "[ensure_smoke_user] {} username={} id={} is_active={} is_superuser={}".format(
                    action, user.username, user.id, user.is_active, user.is_superuser
                )
            )
        )
