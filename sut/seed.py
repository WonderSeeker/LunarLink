# -*- coding: utf-8 -*-
"""SUT 种子数据脚本 —— 让 CI 与本地都能一键重建数据库，不依赖手工造数据。

用法（CWD 在 sut/ 下）：
    python seed.py            # 幂等：库为空时灌种子，已有数据则跳过
    python seed.py --reset    # 删库重建 + 灌种子（CI 用这个）

设计说明：
  * 种子数据刻意覆盖边界库存，供并发/边界用例直接引用：
        库存 0 → 「库存不足」边界
        库存 1 / 3 → 并发抢购边界（断言「恰好 N 成功」）
        库存 200 → 正常流程
  * 用户名/商品名固定且可预测，便于用例直接用；需要隔离的场景由用例
    自己用 uuid 后缀创建独立数据（见 lunarlink_tests/utils/data.py）。
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sut import config, db, store  # noqa: E402

# --------------------------------------------------------------------- 种子数据

# (username, password, name, role)
SEED_USERS = [
    ("admin", "admin123", "管理员", config.ROLE_ADMIN),
    ("buyer", "buyer123", "买家一号", config.ROLE_CUSTOMER),
    # buyer2 专供「水平越权」用例：用它去访问 buyer 的订单，断言 404
    ("buyer2", "buyer123", "买家二号", config.ROLE_CUSTOMER),
]

# (name, description, price_cents, stock)
# ★ stock 一列是刻意设计的边界值，改数据前请先看 lunarlink_tests 里的引用
SEED_PRODUCTS = [
    ("极简白 T 恤", "100% 新疆棉，圆领短袖", 3900, 200),
    ("帆布单肩包", "加厚帆布，可放 14 寸笔记本", 12900, 50),
    ("不锈钢保温杯", "316 内胆，容量 500ml", 6900, 1),  # 库存 1：并发抢购边界
    ("机械键盘 87 键", "Cherry 红轴，PBT 键帽", 39900, 30),
    ("蓝牙耳机", "主动降噪，续航 30 小时", 29900, 0),  # 库存 0：库存不足边界
    ("陶瓷马克杯", "釉下彩，可入洗碗机", 2900, 120),
    ("人体工学椅", "网面靠背，四级气压杆", 89900, 10),
    ("移动电源 20000mAh", "双向 22.5W 快充", 15900, 75),
    ("无线鼠标", "静音微动，1600DPI", 9900, 60),
    ("桌面收纳盒", "可叠加，磨砂 PP 材质", 1900, 3),  # 库存 3：并发下单边界
]


def reset_database() -> None:
    if os.path.exists(config.DB_PATH):
        db.reset_database()
        print("[seed] 已删除数据库文件: {}".format(config.DB_PATH))
    else:
        print("[seed] 数据库文件不存在，无需删除: {}".format(config.DB_PATH))


def seed() -> None:
    """建表并灌入种子数据（假设库是空的或已 reset）。"""
    db.init_schema()

    for username, password, name, role in SEED_USERS:
        store.create_user(username=username, password=password, name=name, role=role)

    for name, description, price_cents, stock in SEED_PRODUCTS:
        store.create_product(
            name=name, description=description, price_cents=price_cents, stock=stock
        )


def print_summary() -> None:
    users = store.count_users()
    products = store.count_products()
    print("")
    print("=" * 68)
    print(" SUT 种子数据就绪")
    print("=" * 68)
    print(" 数据库文件 : {}".format(config.DB_PATH))
    print(" 用户       : {} 个".format(users))
    for username, password, name, role in SEED_USERS:
        print("     - {:<8} / {:<10}  role={:<9} ({})".format(
            username, password, role, name
        ))
    print(" 商品       : {} 个".format(products))
    for name, _, price_cents, stock in SEED_PRODUCTS:
        print("     - {:<24} {:>8} 分  库存 {:<5}".format(name, price_cents, stock))
    print("=" * 68)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LunarLink SUT 种子数据脚本")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先删除数据库文件再重建（CI 使用；要求当前没有进程持有该库）",
    )
    args = parser.parse_args(argv)

    if args.reset:
        reset_database()
    elif store.count_users() > 0 or store.count_products() > 0:
        print("[seed] 数据库中已有数据，跳过（如需重建请加 --reset）")
        print_summary()
        return 0

    seed()
    print_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
