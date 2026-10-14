"""طبقة قاعدة البيانات: SQLite مع دليل حسابات افتراضي وترحيل تلقائي للمخطط."""
import os
import shutil
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("ACCOUNTING_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "accounting.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL,
    type      TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense')),
    parent_id INTEGER REFERENCES accounts(id),
    is_group  INTEGER NOT NULL DEFAULT 0,
    system    INTEGER NOT NULL DEFAULT 0,
    is_cash   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parties (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('customer','supplier')),
    phone         TEXT,
    address       TEXT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    price_list_id INTEGER REFERENCES price_lists(id),
    credit_limit  REAL NOT NULL DEFAULT 0     -- 0 = بلا حد
);

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    unit       TEXT NOT NULL DEFAULT 'قطعة',
    sale_price REAL NOT NULL DEFAULT 0,
    avg_cost   REAL NOT NULL DEFAULT 0,
    qty        REAL NOT NULL DEFAULT 0,   -- إجمالي الكمية في كل المستودعات
    min_qty    REAL NOT NULL DEFAULT 0,
    barcode    TEXT,
    parent_id  INTEGER REFERENCES items(id),   -- مادة أصل (للمتغيرات: لون/قياس)
    variant    TEXT,                           -- اسم المتغير
    track_batches INTEGER NOT NULL DEFAULT 0,  -- تتبّع الدفعات وتواريخ الصلاحية
    is_recipe  INTEGER NOT NULL DEFAULT 0,     -- طبق يُحضَّر من مكوّنات (لا يُخزَّن هو نفسه)
    is_service INTEGER NOT NULL DEFAULT 0,     -- صنف غير مخزني (خدمة، مشروب بلا جرد): لا كمية ولا تكلفة
    station_id INTEGER REFERENCES stations(id),
    category   TEXT                            -- تصنيف القائمة: مقبلات، مشاوي، مشروبات
);

CREATE TABLE IF NOT EXISTS warehouses (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS item_stock (
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    qty          REAL NOT NULL DEFAULT 0,
    avg_cost     REAL NOT NULL DEFAULT 0,      -- متوسط التكلفة في هذا المستودع
    PRIMARY KEY (item_id, warehouse_id)
);

CREATE TABLE IF NOT EXISTS currencies (
    code    TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    rate    REAL NOT NULL DEFAULT 1,     -- قيمة الوحدة الواحدة بالعملة الأساسية
    is_base INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'accountant',
    permissions   TEXT NOT NULL DEFAULT '[]',
    active        INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_seen  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS journals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    number      INTEGER NOT NULL,
    date        TEXT NOT NULL,
    description TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    source_id   INTEGER,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id INTEGER NOT NULL REFERENCES journals(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    debit      REAL NOT NULL DEFAULT 0,
    credit     REAL NOT NULL DEFAULT 0,
    memo       TEXT,
    currency   TEXT,       -- عملة الحركة الأصلية إن لم تكن الأساسية
    fx_amount  REAL,       -- المبلغ بالعملة الأصلية
    cost_center_id INTEGER REFERENCES cost_centers(id)
);

CREATE TABLE IF NOT EXISTS invoices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('sale','purchase','sale_return','purchase_return')),
    date         TEXT NOT NULL,
    party_id     INTEGER REFERENCES parties(id),
    payment      TEXT NOT NULL CHECK (payment IN ('cash','credit')),
    subtotal     REAL NOT NULL DEFAULT 0,   -- المبالغ بعملة الفاتورة
    discount     REAL NOT NULL DEFAULT 0,
    tax_rate     REAL NOT NULL DEFAULT 0,
    tax_amount   REAL NOT NULL DEFAULT 0,
    total        REAL NOT NULL DEFAULT 0,
    currency     TEXT,
    rate         REAL NOT NULL DEFAULT 1,
    net_base     REAL NOT NULL DEFAULT 0,   -- الصافي قبل الضريبة بالعملة الأساسية
    total_base   REAL NOT NULL DEFAULT 0,   -- الإجمالي مع الضريبة بالعملة الأساسية
    cost_total   REAL NOT NULL DEFAULT 0,   -- بالعملة الأساسية
    warehouse_id INTEGER REFERENCES warehouses(id),
    notes        TEXT,
    journal_id   INTEGER REFERENCES journals(id),
    created_by   INTEGER REFERENCES users(id),
    shift_id     INTEGER REFERENCES shifts(id),
    cost_center_id INTEGER REFERENCES cost_centers(id),
    salesperson_id INTEGER REFERENCES employees(id),
    order_id     INTEGER,
    service_amount REAL NOT NULL DEFAULT 0,    -- رسم الخدمة (ضمن الإجمالي)
    client_ref   TEXT                          -- مرجع فريد من الجهاز: يمنع تكرار الفاتورة عند إعادة الإرسال
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    qty         REAL NOT NULL,                  -- بالوحدة المختارة
    price       REAL NOT NULL,                  -- سعر الوحدة المختارة
    unit_name   TEXT,                           -- اسم الوحدة (فارغ = الوحدة الأساسية)
    unit_factor REAL NOT NULL DEFAULT 1,
    qty_base    REAL,                           -- الكمية بالوحدة الأساسية = qty × factor
    unit_cost   REAL NOT NULL DEFAULT 0,        -- تكلفة الوحدة الأساسية
    total       REAL NOT NULL,
    batch_code  TEXT,                           -- رقم الدفعة (للشراء)
    batch_expiry TEXT                           -- تاريخ الصلاحية (للشراء)
);

CREATE TABLE IF NOT EXISTS transfers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    number     INTEGER NOT NULL,
    date       TEXT NOT NULL,
    from_wh    INTEGER NOT NULL REFERENCES warehouses(id),
    to_wh      INTEGER NOT NULL REFERENCES warehouses(id),
    notes      TEXT,
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS transfer_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    qty         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS item_units (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,              -- كرتونة، دزينة، ...
    factor   REAL NOT NULL,              -- كم وحدة أساسية فيها
    barcode  TEXT,
    price    REAL NOT NULL DEFAULT 0     -- سعر بيع الوحدة (0 = يُحسب من سعر الوحدة الأساسية)
);

CREATE TABLE IF NOT EXISTS price_lists (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS price_list_items (
    price_list_id INTEGER NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    price         REAL NOT NULL,
    PRIMARY KEY (price_list_id, item_id)
);

CREATE TABLE IF NOT EXISTS promotions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    item_id   INTEGER REFERENCES items(id) ON DELETE CASCADE,   -- فارغ = على كل المواد
    kind      TEXT NOT NULL CHECK (kind IN ('percent','price')),
    value     REAL NOT NULL,
    min_qty   REAL NOT NULL DEFAULT 0,
    date_from TEXT,
    date_to   TEXT,
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS shifts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    number          INTEGER NOT NULL,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    cash_account_id INTEGER NOT NULL REFERENCES accounts(id),
    warehouse_id    INTEGER REFERENCES warehouses(id),
    opened_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    opening_cash    REAL NOT NULL DEFAULT 0,
    closed_at       TEXT,
    counted_cash    REAL,
    expected_cash   REAL,
    difference      REAL,
    notes           TEXT,
    journal_id      INTEGER REFERENCES journals(id)   -- قيد تسوية العجز/الزيادة
);

CREATE TABLE IF NOT EXISTS batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    code         TEXT,                    -- رقم الدفعة (Lot)
    expiry       TEXT,                    -- تاريخ انتهاء الصلاحية
    qty          REAL NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS batch_moves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    invoice_line_id INTEGER REFERENCES invoice_lines(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,        -- invoice / transfer / stocktake
    source_id       INTEGER,
    qty             REAL NOT NULL         -- موجب = دخول، سالب = خروج
);

CREATE TABLE IF NOT EXISTS stocktakes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       INTEGER NOT NULL,
    date         TEXT NOT NULL,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    notes        TEXT,
    journal_id   INTEGER REFERENCES journals(id),
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stocktake_lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stocktake_id INTEGER NOT NULL REFERENCES stocktakes(id) ON DELETE CASCADE,
    item_id      INTEGER NOT NULL REFERENCES items(id),
    system_qty   REAL NOT NULL,
    counted_qty  REAL NOT NULL,
    unit_cost    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       INTEGER NOT NULL,
    date         TEXT NOT NULL,
    supplier_id  INTEGER REFERENCES parties(id),
    warehouse_id INTEGER REFERENCES warehouses(id),
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','received','canceled')),
    expected     TEXT,                    -- تاريخ التسليم المتوقع
    notes        TEXT,
    invoice_id   INTEGER REFERENCES invoices(id),
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS po_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id       INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    qty         REAL NOT NULL,
    price       REAL NOT NULL DEFAULT 0,
    unit_name   TEXT,
    unit_factor REAL NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cost_centers (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    number        INTEGER NOT NULL,          -- رقم السند في النظام
    kind          TEXT NOT NULL CHECK (kind IN ('in','out')),
    check_number  TEXT,                      -- رقم الشيك المطبوع
    bank          TEXT,
    due_date      TEXT NOT NULL,
    amount        REAL NOT NULL,
    currency      TEXT,
    rate          REAL NOT NULL DEFAULT 1,
    party_id      INTEGER REFERENCES parties(id),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','settled','bounced','canceled')),
    settled_at    TEXT,
    cash_account_id INTEGER REFERENCES accounts(id),
    notes         TEXT,
    journal_id    INTEGER REFERENCES journals(id),   -- قيد الاستلام/التحرير
    settle_journal_id INTEGER REFERENCES journals(id),
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS installments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    due_date   TEXT NOT NULL,
    amount     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fixed_assets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),   -- حساب الأصل
    purchase_date  TEXT NOT NULL,
    cost           REAL NOT NULL,
    salvage        REAL NOT NULL DEFAULT 0,
    life_months    INTEGER NOT NULL,
    last_depr_date TEXT,
    disposed_at    TEXT,
    notes          TEXT,
    created_by     INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS asset_depreciation (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id   INTEGER NOT NULL REFERENCES fixed_assets(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    months     INTEGER NOT NULL DEFAULT 1,
    amount     REAL NOT NULL,
    journal_id INTEGER REFERENCES journals(id)
);

CREATE TABLE IF NOT EXISTS employees (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    phone           TEXT,
    job             TEXT,
    salary          REAL NOT NULL DEFAULT 0,
    is_salesperson  INTEGER NOT NULL DEFAULT 0,
    commission_rate REAL NOT NULL DEFAULT 0,    -- نسبة العمولة من صافي المبيعات
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS payroll_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    number     INTEGER NOT NULL,
    month      TEXT NOT NULL,              -- YYYY-MM
    date       TEXT NOT NULL,
    total      REAL NOT NULL DEFAULT 0,
    paid       INTEGER NOT NULL DEFAULT 1, -- 1 = دُفعت نقداً، 0 = مستحقة
    journal_id INTEGER REFERENCES journals(id),
    notes      TEXT,
    created_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payroll_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    salary      REAL NOT NULL DEFAULT 0,
    additions   REAL NOT NULL DEFAULT 0,
    deductions  REAL NOT NULL DEFAULT 0,
    net         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budgets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    year       TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    amount     REAL NOT NULL,
    UNIQUE (year, account_id)
);

CREATE TABLE IF NOT EXISTS invoice_line_components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_line_id INTEGER NOT NULL REFERENCES invoice_lines(id) ON DELETE CASCADE,
    item_id         INTEGER NOT NULL REFERENCES items(id),
    qty             REAL NOT NULL,
    unit_cost       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,   -- الطبق المباع
    ingredient_id INTEGER NOT NULL REFERENCES items(id),                     -- المكوّن المخزني
    qty           REAL NOT NULL,                                             -- كمية المكوّن لكل وحدة من الطبق
    waste_pct     REAL NOT NULL DEFAULT 0,                                   -- نسبة الهدر عند التحضير %
    unit_name     TEXT,                                                      -- الوحدة التي أُدخلت بها الكمية (غرام مثلاً)
    unit_factor   REAL NOT NULL DEFAULT 1,                                   -- كم تساوي من وحدة المكوّن الأساسية
    UNIQUE (item_id, ingredient_id)
);

CREATE TABLE IF NOT EXISTS modifiers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,                     -- «إضافة جبنة»، «بدون بصل»، «حجم كبير»
    price   REAL NOT NULL DEFAULT 0,           -- فرق السعر (0 للملاحظات المجانية)
    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,   -- فارغ = متاح لكل الأصناف
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL                  -- قسم تحضير: مشاوي، مشروبات، حلويات
);

CREATE TABLE IF NOT EXISTS dining_areas (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL                  -- صالة، تراس، VIP
);

CREATE TABLE IF NOT EXISTS dining_tables (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id INTEGER REFERENCES dining_areas(id) ON DELETE SET NULL,
    name    TEXT NOT NULL,
    seats   INTEGER NOT NULL DEFAULT 4,
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS drivers (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    phone  TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'dine_in' CHECK (kind IN ('dine_in','takeaway','delivery')),
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','canceled')),
    table_id     INTEGER REFERENCES dining_tables(id),
    party_id     INTEGER REFERENCES parties(id),
    waiter_id    INTEGER REFERENCES employees(id),
    driver_id    INTEGER REFERENCES drivers(id),
    guests       INTEGER NOT NULL DEFAULT 1,
    customer_name  TEXT,
    customer_phone TEXT,
    address      TEXT,
    delivery_fee REAL NOT NULL DEFAULT 0,
    service_rate REAL NOT NULL DEFAULT 0,      -- نسبة رسم الخدمة %
    discount     REAL NOT NULL DEFAULT 0,
    notes        TEXT,
    delivery_status TEXT,                      -- preparing / out / delivered
    invoice_id   INTEGER REFERENCES invoices(id),
    warehouse_id INTEGER REFERENCES warehouses(id),
    opened_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    closed_at    TEXT,
    created_by   INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS order_lines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES items(id),
    qty        REAL NOT NULL,
    price      REAL NOT NULL,                  -- سعر الوحدة شاملاً الإضافات
    notes      TEXT,                           -- الإضافات والملاحظات (JSON)
    sent_qty   REAL NOT NULL DEFAULT 0,        -- ما أُرسل للمطبخ
    ready      INTEGER NOT NULL DEFAULT 0,     -- جاهز من المطبخ
    sent_at    TEXT
);

CREATE TABLE IF NOT EXISTS sequences (
    name TEXT PRIMARY KEY,
    next INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    user_id   INTEGER REFERENCES users(id),
    user_name TEXT,
    action    TEXT NOT NULL,          -- create / update / delete / login / ...
    entity    TEXT NOT NULL,          -- invoice / journal / item / user / ...
    entity_id INTEGER,
    summary   TEXT NOT NULL,
    details   TEXT                    -- JSON: قبل/بعد
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id INTEGER NOT NULL REFERENCES journals(id) ON DELETE CASCADE,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    amount     REAL NOT NULL          -- بالعملة الأساسية
);

CREATE TABLE IF NOT EXISTS opening_stock (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL REFERENCES items(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    qty          REAL NOT NULL,
    unit_cost    REAL NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_jl_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS ix_jl_journal ON journal_lines(journal_id);
CREATE INDEX IF NOT EXISTS ix_journals_date ON journals(date);
CREATE INDEX IF NOT EXISTS ix_journals_source ON journals(source);
CREATE INDEX IF NOT EXISTS ix_invoices_kind_date ON invoices(kind, date);
CREATE INDEX IF NOT EXISTS ix_invoices_party ON invoices(party_id);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log(at);
CREATE INDEX IF NOT EXISTS ix_alloc_invoice ON payment_allocations(invoice_id);
CREATE INDEX IF NOT EXISTS ix_alloc_journal ON payment_allocations(journal_id);
CREATE INDEX IF NOT EXISTS ix_units_item ON item_units(item_id);
CREATE INDEX IF NOT EXISTS ix_invoices_shift ON invoices(shift_id);
CREATE INDEX IF NOT EXISTS ix_batches_item ON batches(item_id, warehouse_id);
CREATE INDEX IF NOT EXISTS ix_batches_expiry ON batches(expiry);
CREATE INDEX IF NOT EXISTS ix_bmoves_line ON batch_moves(invoice_line_id);
CREATE INDEX IF NOT EXISTS ix_po_lines ON po_lines(po_id);
CREATE INDEX IF NOT EXISTS ix_items_parent ON items(parent_id);
CREATE INDEX IF NOT EXISTS ix_checks_due ON checks(due_date, status);
CREATE INDEX IF NOT EXISTS ix_inst_invoice ON installments(invoice_id);
CREATE INDEX IF NOT EXISTS ix_jl_center ON journal_lines(cost_center_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status, kind);
CREATE INDEX IF NOT EXISTS ix_order_lines ON order_lines(order_id);
CREATE INDEX IF NOT EXISTS ix_recipes_item ON recipes(item_id);
"""

# (code, name, type, parent_code, is_group)
DEFAULT_ACCOUNTS = [
    ("1", "الأصول", "asset", None, 1),
    ("11", "الأصول المتداولة", "asset", "1", 1),
    ("111", "الصندوق", "asset", "11", 0),
    ("112", "البنك", "asset", "11", 0),
    ("113", "العملاء", "asset", "11", 1),
    ("114", "المخزون", "asset", "11", 0),
    ("115", "ضريبة القيمة المضافة - المدخلات", "asset", "11", 0),
    ("116", "أوراق القبض (شيكات برسم التحصيل)", "asset", "11", 0),
    ("12", "الأصول الثابتة", "asset", "1", 1),
    ("121", "أثاث ومعدات", "asset", "12", 0),
    ("122", "سيارات", "asset", "12", 0),
    ("123", "مجمع الاهتلاك", "asset", "12", 0),
    ("2", "الخصوم", "liability", None, 1),
    ("21", "الموردون", "liability", "2", 1),
    ("22", "قروض", "liability", "2", 0),
    ("23", "مستحقات أخرى", "liability", "2", 0),
    ("25", "أوراق الدفع (شيكات صادرة)", "liability", "2", 0),
    ("24", "ضريبة القيمة المضافة - المخرجات", "liability", "2", 0),
    ("3", "حقوق الملكية", "equity", None, 1),
    ("31", "رأس المال", "equity", "3", 0),
    ("32", "الأرباح المحتجزة", "equity", "3", 0),
    ("33", "المسحوبات الشخصية", "equity", "3", 0),
    ("4", "الإيرادات", "revenue", None, 1),
    ("41", "المبيعات", "revenue", "4", 0),
    ("42", "مردودات المبيعات", "revenue", "4", 0),
    ("43", "إيرادات أخرى", "revenue", "4", 0),
    ("44", "إيرادات رسم الخدمة والتوصيل", "revenue", "4", 0),
    ("5", "المصروفات", "expense", None, 1),
    ("51", "تكلفة البضاعة المباعة", "expense", "5", 0),
    ("52", "الرواتب والأجور", "expense", "5", 0),
    ("53", "الإيجار", "expense", "5", 0),
    ("54", "الكهرباء والماء", "expense", "5", 0),
    ("55", "مصاريف عامة وإدارية", "expense", "5", 0),
    ("56", "مصاريف نقل", "expense", "5", 0),
    ("57", "عجز وزيادة الصندوق", "expense", "5", 0),
    ("58", "فروقات الجرد وتلف المخزون", "expense", "5", 0),
    ("59", "مصروف الاهتلاك", "expense", "5", 0),
    ("510", "عمولات المندوبين", "expense", "5", 0),
]

# رموز الحسابات التي يعتمد عليها النظام في الترحيل الآلي
SYS = {
    "cash": "111",
    "bank": "112",
    "customers": "113",
    "inventory": "114",
    "vat_in": "115",
    "suppliers": "21",
    "vat_out": "24",
    "capital": "31",
    "sales": "41",
    "sales_returns": "42",
    "cogs": "51",
    "cash_diff": "57",
    "stock_diff": "58",
    "notes_receivable": "116",
    "notes_payable": "25",
    "accum_depr": "123",
    "depreciation": "59",
    "commissions": "510",
    "salaries": "52",
    "accruals": "23",
    "service_income": "44",
}

DEFAULT_SETTINGS = {
    "company_name": "شركتي",
    "tax_number": "",
    "address": "",
    "phone": "",
    "vat_enabled": "0",
    "vat_rate": "0",
    "lock_before_date": "",     # لا يمكن تعديل أي حركة بتاريخ ≤ هذا التاريخ
    "last_closing_date": "",    # آخر إقفال سنوي
    "allow_negative_stock": "0",
    "scale_prefix": "2",        # بادئة باركود الموازين
    "scale_mode": "weight",     # weight = الرقم المدمج وزن بالغرام، price = مبلغ
    "pos_print": "1",           # طباعة الإيصال تلقائياً بعد البيع
    "expiry_alert_days": "30",  # تنبيه المواد التي تنتهي صلاحيتها خلال هذه المدة
    "enforce_credit_limit": "1",  # منع البيع الآجل فوق حد ائتمان العميل
    "backup_extra_dir": "",        # مجلد إضافي للنسخ الاحتياطي (فلاشة، قرص آخر، مجلد سحابي)
    "restaurant_mode": "0",        # تفعيل شاشات المطعم
    "service_rate": "0",           # نسبة رسم الخدمة الافتراضية لطلبات الصالة
    "delivery_fee": "0",           # أجرة التوصيل الافتراضية
}


def connect(readonly: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        # WAL يسمح بالقراءة أثناء الكتابة (ضروري عند عمل أكثر من مستخدم على الشبكة)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def tx():
    """اتصال داخل معاملة: يُثبَّت عند النجاح ويُتراجع عنه عند أي خطأ."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table: str, col: str, decl: str):
    if col not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migrate(conn):
    """ترقية قواعد البيانات التي أُنشئت بإصدار سابق دون فقدان أي بيانات."""
    _add_column(conn, "accounts", "is_cash", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "users", "failed_attempts", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "parties", "price_list_id", "INTEGER")
    _add_column(conn, "invoices", "shift_id", "INTEGER")
    _add_column(conn, "invoice_lines", "unit_name", "TEXT")
    _add_column(conn, "invoice_lines", "unit_factor", "REAL NOT NULL DEFAULT 1")
    _add_column(conn, "invoice_lines", "qty_base", "REAL")
    conn.execute("UPDATE invoice_lines SET qty_base=qty WHERE qty_base IS NULL")
    _add_column(conn, "items", "parent_id", "INTEGER")
    _add_column(conn, "parties", "credit_limit", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "items", "is_recipe", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "items", "is_service", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "recipes", "waste_pct", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "recipes", "unit_name", "TEXT")
    _add_column(conn, "recipes", "unit_factor", "REAL NOT NULL DEFAULT 1")
    _add_column(conn, "items", "station_id", "INTEGER")
    _add_column(conn, "items", "category", "TEXT")
    _add_column(conn, "invoices", "order_id", "INTEGER")
    _add_column(conn, "invoices", "service_amount", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "invoices", "client_ref", "TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_ref ON invoices(client_ref) WHERE client_ref IS NOT NULL")
    _add_column(conn, "journal_lines", "cost_center_id", "INTEGER")
    _add_column(conn, "invoices", "cost_center_id", "INTEGER")
    _add_column(conn, "invoices", "salesperson_id", "INTEGER")
    _add_column(conn, "items", "variant", "TEXT")
    _add_column(conn, "items", "track_batches", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "invoice_lines", "batch_code", "TEXT")
    _add_column(conn, "invoice_lines", "batch_expiry", "TEXT")
    if "avg_cost" not in _columns(conn, "item_stock"):
        conn.execute("ALTER TABLE item_stock ADD COLUMN avg_cost REAL NOT NULL DEFAULT 0")
        # التكلفة كانت عامة لكل المستودعات: تُنسخ كما هي لكل مستودع ثم تتفرّع مع الحركات الجديدة
        conn.execute("""UPDATE item_stock SET avg_cost = COALESCE(
                          (SELECT i.avg_cost FROM items i WHERE i.id = item_stock.item_id), 0)""")
    _add_column(conn, "users", "locked_until", "TEXT")
    _add_column(conn, "journals", "created_by", "INTEGER")
    _add_column(conn, "journal_lines", "currency", "TEXT")
    _add_column(conn, "journal_lines", "fx_amount", "REAL")
    new_inv_cols = {
        "tax_rate": "REAL NOT NULL DEFAULT 0", "tax_amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT", "rate": "REAL NOT NULL DEFAULT 1", "net_base": "REAL", "total_base": "REAL",
        "warehouse_id": "INTEGER", "created_by": "INTEGER",
    }
    for col, decl in new_inv_cols.items():
        _add_column(conn, "invoices", col, decl)
    conn.execute("UPDATE invoices SET net_base=total WHERE net_base IS NULL")
    conn.execute("UPDATE invoices SET total_base=total WHERE total_base IS NULL")

    # حسابات أُضيفت في هذا الإصدار
    for code, name, typ, parent, is_group in DEFAULT_ACCOUNTS:
        if not conn.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone():
            pid = conn.execute("SELECT id FROM accounts WHERE code=?", (parent,)).fetchone()
            conn.execute("INSERT INTO accounts(code,name,type,parent_id,is_group,system) VALUES (?,?,?,?,?,1)",
                         (code, name, typ, pid and pid[0], is_group))
    conn.execute("UPDATE accounts SET system=1 WHERE code IN (%s)" % ",".join("?" * len(SYS)), list(SYS.values()))
    conn.execute("UPDATE accounts SET is_cash=1 WHERE code IN (?,?)", (SYS["cash"], SYS["bank"]))

    # المستودع الرئيسي: تنتقل إليه كميات الإصدار السابق
    if not conn.execute("SELECT 1 FROM warehouses").fetchone():
        conn.execute("INSERT INTO warehouses(name) VALUES ('المستودع الرئيسي')")
    main_wh = conn.execute("SELECT MIN(id) FROM warehouses").fetchone()[0]
    conn.execute(
        """INSERT INTO item_stock(item_id, warehouse_id, qty)
           SELECT id, ?, qty FROM items WHERE qty != 0
             AND id NOT IN (SELECT item_id FROM item_stock)""", (main_wh,))
    conn.execute("UPDATE invoices SET warehouse_id=? WHERE warehouse_id IS NULL", (main_wh,))

    # العملة الأساسية (رمزها من إعداد العملة القديم إن وُجد)
    if not conn.execute("SELECT 1 FROM currencies").fetchone():
        old = conn.execute("SELECT value FROM settings WHERE key='currency'").fetchone()
        conn.execute("INSERT INTO currencies(code,name,symbol,rate,is_base) VALUES ('SYP','الليرة السورية',?,1,1)",
                     ((old and old[0]) or "ل.س",))
    base = conn.execute("SELECT code FROM currencies WHERE is_base=1").fetchone()[0]
    conn.execute("UPDATE invoices SET currency=? WHERE currency IS NULL", (base,))

    # عدّادات الترقيم: تبدأ من أعلى رقم مستخدم حالياً
    seeds = {"journal": "SELECT MAX(number) FROM journals", "transfer": "SELECT MAX(number) FROM transfers"}
    for kind in ("sale", "purchase", "sale_return", "purchase_return"):
        seeds[f"invoice_{kind}"] = f"SELECT MAX(number) FROM invoices WHERE kind='{kind}'"
    seeds["shift"] = "SELECT MAX(number) FROM shifts"
    seeds["stocktake"] = "SELECT MAX(number) FROM stocktakes"
    seeds["po"] = "SELECT MAX(number) FROM purchase_orders"
    seeds["check"] = "SELECT MAX(number) FROM checks"
    seeds["order"] = "SELECT MAX(number) FROM orders"
    seeds["payroll"] = "SELECT MAX(number) FROM payroll_runs"
    for name, q in seeds.items():
        if not conn.execute("SELECT 1 FROM sequences WHERE name=?", (name,)).fetchone():
            conn.execute("INSERT INTO sequences(name,next) VALUES (?,?)",
                         (name, (conn.execute(q).fetchone()[0] or 0) + 1))


# ───────────────────────── النسخ الاحتياطي ─────────────────────────

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP = 14


def backup_db(tag: str = "auto") -> Path:
    """نسخة احتياطية آمنة (أثناء التشغيل) مع فحص سلامة الملف الناتج وحذف النسخ القديمة."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"accounting-{datetime.now():%Y-%m-%d}-{tag}.db"
    src = connect()
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            bad = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if bad != "ok":
                raise RuntimeError(f"النسخة الاحتياطية تالفة: {bad}")
        finally:
            dst.close()
    finally:
        src.close()
    old = sorted(BACKUP_DIR.glob("accounting-*.db"))[:-BACKUP_KEEP]
    for f in old:
        f.unlink(missing_ok=True)
    _copy_offsite(target)
    return target


def _copy_offsite(target: Path) -> str | None:
    """ينسخ النسخة إلى المجلد الإضافي إن ضُبط. فشله لا يُفشل النسخة المحلية، ويُسجَّل سببه."""
    global LAST_OFFSITE_ERROR
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='backup_extra_dir'").fetchone()
    finally:
        conn.close()
    extra = (row[0] if row else "") or ""
    if not extra.strip():
        LAST_OFFSITE_ERROR = None
        return None
    try:
        dest = Path(extra.strip())
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, dest / target.name)
        for f in sorted(dest.glob("accounting-*.db"))[:-BACKUP_KEEP]:
            f.unlink(missing_ok=True)
        LAST_OFFSITE_ERROR = None
        return str(dest / target.name)
    except OSError as e:
        LAST_OFFSITE_ERROR = f"تعذر النسخ إلى «{extra}»: {e}"
        return None


LAST_OFFSITE_ERROR: str | None = None
REQUIRED_TABLES = {"accounts", "journals", "journal_lines", "invoices", "items", "settings"}


def validate_backup(path: Path) -> dict:
    """يتحقق أن الملف قاعدة بيانات سليمة لهذا البرنامج قبل استعادتها."""
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("الملف تالف")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not REQUIRED_TABLES <= tables:
                raise ValueError("الملف ليس نسخة احتياطية لهذا البرنامج")
            return {"invoices": con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
                    "journals": con.execute("SELECT COUNT(*) FROM journals").fetchone()[0],
                    "last_date": con.execute("SELECT MAX(date) FROM journals").fetchone()[0]}
        finally:
            con.close()
    except sqlite3.DatabaseError:
        raise ValueError("الملف ليس قاعدة بيانات صالحة")


def restore_db(path: Path) -> dict:
    """يستعيد نسخة احتياطية: يتحقق منها، يحفظ الحالة الحالية كنسخة أمان، ثم يستبدل البيانات ويرقّي المخطط."""
    info = validate_backup(path)
    safety = backup_db("before-restore")
    src = sqlite3.connect(path)
    try:
        dst = connect()
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    init_db()
    return {**info, "safety_backup": safety.name}


def backup_if_due() -> Path | None:
    """نسخة واحدة يومياً على الأقل."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if any(BACKUP_DIR.glob(f"accounting-{datetime.now():%Y-%m-%d}-*.db")):
        return None
    return backup_db()


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size, "at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(" ", "seconds")}
            for f in sorted(BACKUP_DIR.glob("accounting-*.db"), reverse=True)]


def init_db():
    with tx() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            ids = {}
            system_codes = set(SYS.values())
            for code, name, typ, parent, is_group in DEFAULT_ACCOUNTS:
                cur = conn.execute(
                    "INSERT INTO accounts(code,name,type,parent_id,is_group,system) VALUES (?,?,?,?,?,?)",
                    (code, name, typ, ids.get(parent), is_group, int(code in system_codes or is_group)),
                )
                ids[code] = cur.lastrowid
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        _migrate(conn)
        conn.executescript(INDEXES)
