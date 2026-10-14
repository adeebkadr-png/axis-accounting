"""اختبارات المرحلة ١: التعديل، سجل التدقيق، الإقفال، الترقيم، تخصيص الدفعات، والحماية."""
from conftest import fail, ok
from test_core import balanced, setup_basics


# ───────── تعديل المستندات ─────────

def test_invoice_edit_keeps_number_and_fixes_stock(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    assert ok(c.get("/api/items"))[0]["qty"] == 90
    edited = ok(c.put(f"/api/invoices/{inv['id']}", json={"date": "2026-02-02", "payment": "credit",
                                                          "party_id": cust["id"], "warehouse_id": wh,
                                                          "lines": [{"item_id": item["id"], "qty": 4, "price": 25}]}))
    assert edited["number"] == inv["number"]          # الرقم لا يتغير
    assert edited["total"] == 100 and edited["date"] == "2026-02-02"
    assert ok(c.get("/api/items"))[0]["qty"] == 96    # رجعت 10 ثم خرجت 4
    assert ok(c.get(f"/api/parties?kind=customer"))[0]["balance"] == 100
    assert balanced(c)


def test_journal_edit_and_audit_records_before_after(admin):
    c = admin
    accounts, *_ = setup_basics(c)
    j = ok(c.post("/api/journals", json={"date": "2026-03-01", "description": "مصروف",
                                         "lines": [{"account_id": accounts["54"], "debit": 100},
                                                   {"account_id": accounts["111"], "credit": 100}]}))
    ok(c.put(f"/api/journals/{j['id']}", json={"date": "2026-03-01", "description": "كهرباء",
                                               "lines": [{"account_id": accounts["54"], "debit": 150},
                                                         {"account_id": accounts["111"], "credit": 150}]}))
    after = ok(c.get(f"/api/journals/{j['id']}"))
    assert after["number"] == j["number"] and after["description"] == "كهرباء"
    rows = ok(c.get("/api/audit"))["rows"]
    upd = [r for r in rows if r["entity"] == "journal" and r["action"] == "update"]
    assert upd and "before" in upd[0]["details"] and "100" in upd[0]["details"]


def test_unbalanced_journal_rejected(admin):
    c = admin
    accounts, *_ = setup_basics(c)
    r = fail(c.post("/api/journals", json={"date": "2026-03-01", "description": "خطأ",
                                           "lines": [{"account_id": accounts["54"], "debit": 100},
                                                     {"account_id": accounts["111"], "credit": 90}]}))
    assert "متوازن" in r["detail"]


# ───────── سجل التدقيق ─────────

def test_audit_logs_create_and_delete(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 5, "price": 20}]}))
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    actions = {(r["entity"], r["action"]) for r in ok(c.get("/api/audit"))["rows"]}
    assert ("invoice", "create") in actions and ("invoice", "delete") in actions
    assert ("item", "create") in actions and ("party", "create") in actions
    assert all(r["user_name"] for r in ok(c.get("/api/audit"))["rows"] if r["action"] != "login_failed")


# ───────── الترقيم الآمن ─────────

def test_numbers_never_reused_after_delete(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    body = {"kind": "sale", "payment": "cash", "warehouse_id": wh, "lines": [{"item_id": item["id"], "qty": 1, "price": 20}]}
    a = ok(c.post("/api/invoices", json=body))
    ok(c.delete(f"/api/invoices/{a['id']}"))
    b = ok(c.post("/api/invoices", json=body))
    assert b["number"] == a["number"] + 1


# ───────── قفل الفترات والإقفال السنوي ─────────

def test_period_lock_blocks_changes(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 5, "price": 20}]}))
    ok(c.post("/api/period/lock", json={"lock_before_date": "2026-06-30"}))
    assert "مقفلة" in fail(c.delete(f"/api/invoices/{inv['id']}"))["detail"]
    assert "مقفلة" in fail(c.post("/api/invoices", json={"kind": "sale", "date": "2026-03-01", "payment": "cash",
                                                         "warehouse_id": wh,
                                                         "lines": [{"item_id": item["id"], "qty": 1, "price": 20}]}))["detail"]
    ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-07-01", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 1, "price": 20}]}))
    ok(c.post("/api/period/lock", json={"lock_before_date": ""}))
    ok(c.delete(f"/api/invoices/{inv['id']}"))


def test_year_closing_zeroes_income_and_locks(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    before = ok(c.get("/api/reports/income"))["net_profit"]
    assert before == 100          # 200 مبيعات − 100 تكلفة
    r = ok(c.post("/api/period/close-year", json={"date_to": "2026-12-31"}))
    assert r["net_profit"] == 100 and r["locked_until"] == "2026-12-31"
    assert ok(c.get("/api/reports/income"))["net_profit"] == 0
    bs = ok(c.get("/api/reports/balance-sheet"))
    assert bs["balanced"] and bs["period_profit"] == 0
    assert "مقفلة" in fail(c.post("/api/journals", json={"date": "2026-05-01", "description": "x", "lines": []}))["detail"]


# ───────── تخصيص الدفعات وأعمار الديون ─────────

def test_payment_allocation_and_aging(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-01-10", "payment": "credit",
                                           "party_id": cust["id"], "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    openinv = ok(c.get(f"/api/parties/{cust['id']}/open-invoices"))
    assert openinv[0]["outstanding"] == 200
    ok(c.post("/api/vouchers", json={"kind": "receipt", "date": "2026-01-20", "account_id": cust["account_id"],
                                     "amount": 120, "allocations": [{"invoice_id": inv["id"], "amount": 120}]}))
    assert ok(c.get(f"/api/invoices/{inv['id']}"))["outstanding"] == 80
    over = fail(c.post("/api/vouchers", json={"kind": "receipt", "account_id": cust["account_id"], "amount": 500,
                                              "allocations": [{"invoice_id": inv["id"], "amount": 500}]}))
    assert "المتبقي" in over["detail"]
    aging = ok(c.get("/api/reports/aging?kind=customer&as_of=2026-03-01"))
    party = aging["parties"][0]
    assert party["total"] == 80 and party["أكثر من 90 يوم"] == 0 and party["31-60 يوم"] == 80


def test_cannot_delete_invoice_with_allocated_payment(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "credit", "party_id": cust["id"],
                                           "warehouse_id": wh, "lines": [{"item_id": item["id"], "qty": 5, "price": 20}]}))
    ok(c.post("/api/vouchers", json={"kind": "receipt", "account_id": cust["account_id"], "amount": 50,
                                     "allocations": [{"invoice_id": inv["id"], "amount": 50}]}))
    assert "دفعات" in fail(c.delete(f"/api/invoices/{inv['id']}"))["detail"]


# ───────── الحماية ─────────

def test_login_locks_after_failed_attempts(client):
    ok(client.post("/api/auth/setup", json={"username": "admin", "full_name": "م", "password": "secret1"}))
    ok(client.post("/api/auth/logout"))
    for _ in range(4):
        fail(client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}))
    assert "مقفل" in fail(client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}))["detail"]
    assert "مقفل" in fail(client.post("/api/auth/login", json={"username": "admin", "password": "secret1"}))["detail"]


def test_password_change_kills_other_sessions(client):
    from fastapi.testclient import TestClient
    ok(client.post("/api/auth/setup", json={"username": "admin", "full_name": "م", "password": "secret1"}))
    other = TestClient(client.app_module.app)
    ok(other.post("/api/auth/login", json={"username": "admin", "password": "secret1"}))
    ok(other.get("/api/settings"))
    ok(client.post("/api/auth/password", json={"old_password": "secret1", "new_password": "secret2"}))
    assert other.get("/api/settings").status_code == 401     # الجلسة الأخرى انتهت
    ok(client.get("/api/settings"))                          # وجلستي أنا باقية


def test_permissions_enforced_for_edit_and_period(admin):
    from fastapi.testclient import TestClient
    c = admin
    _, item, _, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 1, "price": 20}]}))
    ok(c.post("/api/users", json={"username": "cash1", "full_name": "كاشير", "password": "pass123", "role": "cashier"}))
    c2 = TestClient(c.app_module.app)
    ok(c2.post("/api/auth/login", json={"username": "cash1", "password": "pass123"}))
    body = {"payment": "cash", "warehouse_id": wh, "lines": [{"item_id": item["id"], "qty": 2, "price": 20}]}
    assert c2.put(f"/api/invoices/{inv['id']}", json=body).status_code == 403
    assert c2.get("/api/audit").status_code == 403
    assert c2.post("/api/period/lock", json={"lock_before_date": "2026-01-01"}).status_code == 403
    ok(c.put(f"/api/invoices/{inv['id']}", json=body))        # المدير يستطيع


def test_ai_sql_tool_never_returns_password_hashes(admin):
    out = admin.app_module.ai.run_tool("run_sql", {"query": "select * from users"}, [])
    assert "password_hash" not in out and "pbkdf2" not in out


def test_backup_creates_valid_copy(admin):
    import sqlite3
    name = ok(admin.post("/api/backups"))["name"]
    listed = ok(admin.get("/api/backups"))
    assert any(b["name"] == name for b in listed)
    from db import BACKUP_DIR
    con = sqlite3.connect(BACKUP_DIR / name)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    con.close()
