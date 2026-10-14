"""اختبارات المرحلة ٤: مراكز الكلفة، الشيكات، الائتمان والأقساط، الأصول، الرواتب، الموازنات، والتدفقات."""
from conftest import fail, ok
from test_core import balanced, setup_basics


# ───────── مراكز الكلفة ─────────

def test_cost_center_tracks_profit(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    cc = ok(c.post("/api/cost-centers", json={"name": "فرع دمشق"}))
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh, "cost_center_id": cc["id"],
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ok(c.post("/api/journals", json={"date": "2026-02-01", "description": "إيجار الفرع",
                                     "lines": [{"account_id": accounts["53"], "debit": 50, "cost_center_id": cc["id"]},
                                               {"account_id": accounts["111"], "credit": 50}]}))
    rep = ok(c.get("/api/reports/cost-centers"))[0]
    assert rep["revenue"] == 200 and rep["expense"] == 150 and rep["profit"] == 50   # 100 تكلفة + 50 إيجار
    assert balanced(c)


# ───────── الشيكات ─────────

def test_incoming_check_collect_flow(admin):
    c = admin
    accounts, item, cust, _, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "credit", "party_id": cust["id"], "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ch = ok(c.post("/api/checks", json={"kind": "in", "party_id": cust["id"], "amount": 200,
                                        "check_number": "55512", "bank": "بنك الشام", "due_date": "2026-04-01"}))
    assert ch["status"] == "pending" and ch["base_amount"] == 200
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["116"]["bal_debit"] == 200                       # أوراق القبض
    assert ok(c.get("/api/parties?kind=customer"))[0]["balance"] == 0   # ذمة العميل أُقفلت بالشيك
    ok(c.post(f"/api/checks/{ch['id']}/settle", json={"cash_account_id": accounts["112"]}))
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["116"]["bal_debit"] == 0 and tb["112"]["bal_debit"] == 200   # أُقفلت أوراق القبض وصار المبلغ نقداً
    assert balanced(c)


def test_bounced_check_returns_debt_to_customer(admin):
    c = admin
    accounts, item, cust, _, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "credit", "party_id": cust["id"], "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ch = ok(c.post("/api/checks", json={"kind": "in", "party_id": cust["id"], "amount": 200, "due_date": "2026-04-01"}))
    ok(c.post(f"/api/checks/{ch['id']}/settle", json={"cash_account_id": accounts["112"]}))
    ok(c.post(f"/api/checks/{ch['id']}/bounce", json={}))
    assert ok(c.get(f"/api/checks/{ch['id']}"))["status"] == "bounced"
    assert ok(c.get("/api/parties?kind=customer"))[0]["balance"] == 200   # الدين رجع على العميل
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["112"]["bal_debit"] == 0 and balanced(c)                     # والنقد خرج من البنك


def test_outgoing_check_to_supplier(admin):
    c = admin
    accounts, _, _, supp, _ = setup_basics(c)      # فاتورة شراء آجلة بـ1000
    ch = ok(c.post("/api/checks", json={"kind": "out", "party_id": supp["id"], "amount": 600, "due_date": "2026-05-01"}))
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["25"]["bal_credit"] == 600                       # أوراق الدفع
    assert ok(c.get("/api/parties?kind=supplier"))[0]["balance"] == 400
    ok(c.post(f"/api/checks/{ch['id']}/settle", json={"cash_account_id": accounts["112"]}))
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["25"]["bal_credit"] == 0 and tb["112"]["bal_credit"] == 600 and balanced(c)


# ───────── حد الائتمان والأقساط ─────────

def test_credit_limit_blocks_oversell(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    ok(c.put(f"/api/parties/{cust['id']}", json={"name": "أحمد", "credit_limit": 300}))
    body = {"kind": "sale", "payment": "credit", "party_id": cust["id"], "warehouse_id": wh,
            "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}
    ok(c.post("/api/invoices", json=body))                     # 200 ضمن الحد
    assert "حد ائتمان" in fail(c.post("/api/invoices", json=body))["detail"]      # 400 يتجاوز 300
    ok(c.put("/api/settings", json={"enforce_credit_limit": "0"}))
    ok(c.post("/api/invoices", json=body))                     # عند تعطيل المنع يمر


def test_installment_plan_tracks_payments(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-01-15", "payment": "credit",
                                           "party_id": cust["id"], "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 15, "price": 20}]}))
    plan = ok(c.put(f"/api/invoices/{inv['id']}/installments", json={"count": 3}))
    assert len(plan) == 3 and sum(p["amount"] for p in plan) == 300 and plan[0]["due_date"] == "2026-02-15"
    ok(c.post("/api/vouchers", json={"kind": "receipt", "account_id": cust["account_id"], "amount": 150,
                                     "allocations": [{"invoice_id": inv["id"], "amount": 150}]}))
    plan = ok(c.get(f"/api/invoices/{inv['id']}/installments"))
    assert plan[0]["status"] == "مسدَّد" and plan[1]["remaining"] == 50 and plan[2]["remaining"] == 100
    due = ok(c.get("/api/reports/installments?as_of=2026-12-31"))
    assert due["total"] == 150 and due["rows"][0]["party_name"] == "أحمد"


# ───────── الأصول والاهتلاك ─────────

def test_depreciation_posts_monthly_and_stops_at_life_end(admin):
    c = admin
    accounts, *_ = setup_basics(c)
    a = ok(c.post("/api/assets", json={"name": "سيارة توصيل", "account_id": accounts["122"],
                                       "purchase_date": "2026-01-01", "cost": 12000, "salvage": 0, "life_months": 12}))
    r = ok(c.post("/api/assets/depreciate", json={"up_to": "2026-04-01"}))
    assert r["total"] == 3000                                   # 3 أشهر × 1000
    asset = ok(c.get("/api/assets"))[0]
    assert asset["depreciated"] == 3000 and asset["book_value"] == 9000
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["59"]["bal_debit"] == 3000 and tb["123"]["bal_credit"] == 3000
    ok(c.post("/api/assets/depreciate", json={"up_to": "2027-06-01"}))
    asset = ok(c.get("/api/assets"))[0]
    assert asset["depreciated"] == 12000 and asset["book_value"] == 0   # لا يتجاوز العمر الإنتاجي
    assert "لا يوجد اهتلاك" in fail(c.post("/api/assets/depreciate", json={"up_to": "2028-01-01"}))["detail"]
    assert balanced(c)


# ───────── الرواتب والعمولات ─────────

def test_payroll_posts_and_blocks_duplicate_month(admin):
    c = admin
    accounts, *_ = setup_basics(c)
    e1 = ok(c.post("/api/employees", json={"name": "سامر", "salary": 500}))
    e2 = ok(c.post("/api/employees", json={"name": "ليلى", "salary": 700}))
    run = ok(c.post("/api/payrolls", json={"month": "2026-03", "date": "2026-03-30", "paid": True,
                                           "cash_account_id": accounts["111"],
                                           "lines": [{"employee_id": e1["id"], "salary": 500, "additions": 50},
                                                     {"employee_id": e2["id"], "salary": 700, "deductions": 100}]}))
    assert run["total"] == 1150 and len(run["lines"]) == 2
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["52"]["bal_debit"] == 1150
    assert "مسجّلة مسبقاً" in fail(c.post("/api/payrolls", json={"month": "2026-03",
                                                                 "lines": [{"employee_id": e1["id"]}]}))["detail"]
    assert balanced(c)


def test_salesperson_commission_report(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    emp = ok(c.post("/api/employees", json={"name": "مندوب", "is_salesperson": True, "commission_rate": 5}))
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                     "salesperson_id": emp["id"],
                                     "lines": [{"item_id": item["id"], "qty": 20, "price": 20}]}))
    rep = ok(c.get("/api/reports/commissions"))
    assert rep["rows"][0]["sales"] == 400 and rep["rows"][0]["commission"] == 20 and rep["rows"][0]["profit"] == 200
    ok(c.post("/api/commissions/pay", json={"employee_id": emp["id"], "amount": 20, "cash_account_id": accounts["111"]}))
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["510"]["bal_debit"] == 20 and balanced(c)


# ───────── الموازنة والتدفقات النقدية ─────────

def test_budget_compares_actual(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ok(c.put("/api/budgets", json={"year": "2026", "items": [{"account_id": accounts["41"], "amount": 1000},
                                                             {"account_id": accounts["53"], "amount": 300}]}))
    rep = ok(c.get("/api/budgets?year=2026"))
    sales = [r for r in rep["revenue"] if r["code"] == "41"][0]
    assert sales["budget"] == 1000 and sales["actual"] == 200 and sales["variance"] == 800 and sales["pct"] == 20
    rent = [r for r in rep["expense"] if r["code"] == "53"][0]
    assert rent["budget"] == 300 and rent["actual"] == 0


def test_cash_flow_classifies_sections(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/journals", json={"date": "2026-01-01", "description": "رأس مال",
                                     "lines": [{"account_id": accounts["111"], "debit": 5000},
                                               {"account_id": accounts["31"], "credit": 5000}]}))
    ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ok(c.post("/api/journals", json={"date": "2026-02-05", "description": "شراء سيارة",
                                     "lines": [{"account_id": accounts["122"], "debit": 1000},
                                               {"account_id": accounts["111"], "credit": 1000}]}))
    cf = ok(c.get("/api/reports/cash-flow"))
    assert cf["totals"]["financing"] == 5000 and cf["totals"]["investing"] == -1000
    assert cf["totals"]["operating"] == 200 and cf["net"] == 4200
