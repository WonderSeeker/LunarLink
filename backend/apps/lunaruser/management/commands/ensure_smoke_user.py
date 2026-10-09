# -*- coding: utf-8 -*-
"""幂等地准备接口自动化套件用的回归冒烟账号。

为什么需要它：
    自动化套件要能在【干净数据库】上自举（重建 DB / 换机器 / 上 CI 之后依然能跑），
    所以这些账号不能只是本地 DB 里的手工数据，必须落成代码。

用法：
    python manage.py ensure_smoke_user

创建两个账号（都是幂等的 get_or_create 语义，可重复执行）：

    1) 非管理员账号（主力）—— 环境变量 SMOKE_USERNAME / SMOKE_PASSWORD
       默认 smoke_regression / smoke123456
       ★ 刻意不给超管权限：套件应验证「普通用户」的真实权限边界，
         否则一个超管账号会把权限不足的问题全部掩盖掉。

    2) 管理员账号（仅用于 admin-only 端点）—— 环境变量 SMOKE_ADMIN_USERNAME / SMOKE_ADMIN_PASSWORD
       默认 smoke_admin / smoke123456
       ★ 为什么还需要第二个账号：平台有一批端点要求管理员
         （`CustomIsAdminUser(IsAdminUser)` 查的是 is_staff），
         例如「删除项目」。没有管理员账号就只能断「403」这一半，
         断不了「管理员确实能删」的另一半，而且写用例无法自己清理现场。

为什么不写明文到文档：CHANGES.md 只指向本命令，实际口令由 env / 本文件决定。
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEFAULT_USERNAME = "smoke_regression"
DEFAULT_PASSWORD = "smoke123456"
DEFAULT_ADMIN_USERNAME = "smoke_admin"
DEFAULT_ADMIN_PASSWORD = "smoke123456"


class Command(BaseCommand):
    help = "幂等地创建/更新回归冒烟账号（供 lunarlink_tests 的 session fixture 调用）"

    def handle(self, *args, **options):
        User = get_user_model()

        # ---------------- 1) 非管理员冒烟账号（主力） ----------------
        username = os.environ.get("SMOKE_USERNAME", DEFAULT_USERNAME)
        password = os.environ.get("SMOKE_PASSWORD", DEFAULT_PASSWORD)

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

        self.stdout.write(
            self.style.SUCCESS(
                "[ensure_smoke_user] {} username={} id={} is_staff={} is_superuser={}".format(
                    "created" if created else "updated",
                    user.username,
                    user.id,
                    user.is_staff,
                    user.is_superuser,
                )
            )
        )

        # ---------------- 2) 管理员账号（仅 admin-only 端点） ----------------
        admin_username = os.environ.get("SMOKE_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME)
        admin_password = os.environ.get("SMOKE_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

        admin, admin_created = User.objects.get_or_create(
            username=admin_username,
            defaults={"name": "回归管理员账号", "is_active": True},
        )
        admin.set_password(admin_password)
        admin.is_active = True
        # ★ is_staff 是 CustomIsAdminUser(IsAdminUser) 的判定依据，必须为真
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()

        self.stdout.write(
            self.style.SUCCESS(
                "[ensure_smoke_user] {} username={} id={} is_staff={} is_superuser={}".format(
                    "created" if admin_created else "updated",
                    admin.username,
                    admin.id,
                    admin.is_staff,
                    admin.is_superuser,
                )
            )
        )
