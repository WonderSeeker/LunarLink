-- ============================================================================
-- LunarLink SUT（mock 电商）数据库结构
-- ============================================================================
-- 全局约定：
--   * 金额统一用「分」存 INTEGER，禁止浮点（接口层同样返回整数分）。
--   * 时间统一用 Unix 秒（INTEGER）存，便于测试后门直接拨动时间，
--     不用 sleep 就能构造「token 过期 / 订单支付超时」。
--   * 所有建表使用 IF NOT EXISTS，保证 init_schema() 幂等可重复执行。
--   * journal_mode=WAL 不写在这里（它是数据库文件的持久属性），
--     由 db.init_schema() 单独设置一次。
-- ============================================================================

-- ------------------------------------------------------------------ 用户
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    password_salt   TEXT    NOT NULL,
    name            TEXT    NOT NULL DEFAULT '',
    role            TEXT    NOT NULL DEFAULT 'customer',  -- admin / customer
    is_active       INTEGER NOT NULL DEFAULT 1,
    -- 连续登录失败次数与锁定截止时间（Unix 秒，0 表示未锁定）
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL
);

-- ------------------------------------------------------------------ 登录凭证
-- 单 token 简化设计：token 落库，校验时比对 expires_at 与 revoked。
-- 这样「token 过期」「登出后 token 失效」都能被确定性构造与断言。
CREATE TABLE IF NOT EXISTS tokens (
    token       TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS idx_tokens_user_id ON tokens (user_id);

-- ------------------------------------------------------------------ 商品
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock       INTEGER NOT NULL CHECK (stock >= 0),
    status      INTEGER NOT NULL DEFAULT 1,   -- 1=上架 0=下架（软删）
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_status ON products (status);

-- ------------------------------------------------------------------ 购物车
-- UNIQUE(user_id, product_id)：同一用户重复加购同一商品时合并数量，
-- 而不是产生两行。这条唯一约束是「加购合并」实现的依据。
CREATE TABLE IF NOT EXISTS cart_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

-- ------------------------------------------------------------------ 订单
CREATE TABLE IF NOT EXISTS orders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no           TEXT    NOT NULL UNIQUE,
    user_id            INTEGER NOT NULL,
    -- PENDING / PAID / SHIPPED / COMPLETED / CANCELLED / REFUNDED
    status             TEXT    NOT NULL,
    total_amount_cents INTEGER NOT NULL CHECK (total_amount_cents >= 0),
    expire_at          INTEGER NOT NULL,   -- 支付超时截止时间
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL,
    paid_at            INTEGER,
    cancelled_at       INTEGER,
    completed_at       INTEGER,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders (user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);

-- 订单行：下单时对商品名与单价做快照，避免商品改价影响历史订单
CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL,
    product_id       INTEGER NOT NULL,
    product_name     TEXT    NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    quantity         INTEGER NOT NULL,
    subtotal_cents   INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);

-- ------------------------------------------------------------------ 支付单
-- UNIQUE(order_no)：一个订单只允许存在一个支付单，重复发起支付返回同一支付单。
CREATE TABLE IF NOT EXISTS payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_no            TEXT    NOT NULL UNIQUE,
    order_no          TEXT    NOT NULL UNIQUE,
    user_id           INTEGER NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents >= 0),
    status            TEXT    NOT NULL,   -- PENDING / PAID / REFUNDED
    -- 首次成功入账的回调流水号；幂等命中后不再变更（用例会断言它不变）
    notify_id         TEXT,
    amount_paid_cents INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    paid_at           INTEGER,
    refunded_at       INTEGER,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments (user_id);

-- ---------------------------------------------------------------- 支付回调流水
-- ★ 幂等闸门：notify_id 上建 UNIQUE 约束。
-- 并发重复回调时由数据库抛 IntegrityError，应用层据此判定「重复回调」并直接返回
-- 首次结果。绝不能靠应用层「先查再写」——那在并发下必然漏（check-then-act 竞态）。
CREATE TABLE IF NOT EXISTS payment_callbacks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    notify_id  TEXT    NOT NULL UNIQUE,
    pay_no     TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);
