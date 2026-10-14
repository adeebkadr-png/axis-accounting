"""اختبارات المحرك المحاسبي: التوازن، الفواتير، الضريبة، العملات، المستودعات، والتقارير."""
from conftest import fail, ok


def setup_basics(c, vat=0):
    if vat:
        ok(c.put("/api/settings", json={"vat_enabled": True, "vat_rate": vat}))
    accounts = {a["code"]: a["id"] for a in ok(c.get("/api/accounts"))}
    item = ok(c.post("/api/items", json={"name": "زيت", "sale_price": 20}))
    cust = ok(c.post("/api/parties", json={"name": "أحمد", "kind": "customer"}))
    supp = ok(c.post("/api/parties", json={"name": "مورد", "kind": "supplier"}))
    wh = ok(c.get("/api/warehouses"))[0]["id"]
    ok(c.post("/api/invoices", json={"kind": "purchase", "date": "2026-01-05", "party_id": supp["id"],
                                     "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 100, "price": 10}]}))
    return accounts, item, cust, supp, wh


def balanced(c):
    return ok(c.get("/api/reports/trial-balance"))["balanced"] and ok(c.get("/api/reports/balance-sheet"))["balanced"]


def test_double_entry_stays_balanced(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "sale", "date": "2026-02-01", "party_id": cust["id"], "payment": "credit",
                                     "warehouse_id": wh, "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ok(c.post("/api/vouchers", json={"kind": "receipt", "account_id": cust["account_id"], "amount": 100}))
    assert balanced(c)


def test_sale_cost_and_profit_use_weighted_average(admin):
    c = admin
    _, item, cust, supp, wh = setup_basics(c)
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 100, "price": 14}]}))
    assert abs(ok(c.get("/api/items"))[0]["avg_cost"] - 12) < 0.01        # (100×10 + 100×14) / 200
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    assert abs(inv["cost_total"] - 120) < 0.01 and abs(inv["profit"] - 80) < 0.01


def test_cannot_sell_more_than_stock(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    body = {"kind": "sale", "payment": "cash", "warehouse_id": wh, "lines": [{"item_id": item["id"], "qty": 500, "price": 20}]}
    assert "المتوفرة" in fail(c.post("/api/invoices", json=body))["detail"]


def test_vat_posted_and_reported(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c, vat=10)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh, "tax_rate": 10,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    assert inv["tax_amount"] == 20 and inv["total"] == 220
    vat = ok(c.get("/api/reports/vat"))
    assert vat["output_tax"] == 20 and vat["payable"] == 20 and balanced(c)


def test_foreign_currency_converted_to_base(admin):
    c = admin
    _, item, _, supp, wh = setup_basics(c)
    ok(c.post("/api/currencies", json={"code": "USD", "name": "دولار", "symbol": "$", "rate": 100}))
    inv = ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit",
                                           "warehouse_id": wh, "currency": "USD", "rate": 100,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 2}]}))
    assert inv["total"] == 20 and inv["total_base"] == 2000 and balanced(c)


def test_transfer_moves_stock_between_warehouses(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/warehouses", json={"name": "فرع"}))
    branch = [w for w in ok(c.get("/api/warehouses")) if w["name"] == "فرع"][0]["id"]
    t = ok(c.post("/api/transfers", json={"from_wh": wh, "to_wh": branch, "lines": [{"item_id": item["id"], "qty": 30}]}))
    stock = ok(c.get("/api/items"))[0]["stock"]
    assert stock[str(wh)] == 70 and stock[str(branch)] == 30
    ok(c.delete(f"/api/transfers/{t['id']}"))
    assert ok(c.get("/api/items"))[0]["stock"][str(wh)] == 100


def test_opening_balances_plug_difference_and_replace(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    r = ok(c.post("/api/opening/balances", json={"date": "2026-01-01",
                                                 "lines": [{"account_id": accounts["112"], "debit": 5000}]}))
    assert r["difference"] == 5000 and balanced(c)
    ok(c.post("/api/opening/stock", json={"date": "2026-01-01",
                                          "lines": [{"item_id": item["id"], "warehouse_id": wh, "qty": 10, "unit_cost": 8}]}))
    assert ok(c.get("/api/items"))[0]["qty"] == 110
    ok(c.post("/api/opening/stock", json={"date": "2026-01-01",
                                          "lines": [{"item_id": item["id"], "warehouse_id": wh, "qty": 4, "unit_cost": 8}]}))
    assert ok(c.get("/api/items"))[0]["qty"] == 104 and balanced(c)


def test_invoice_delete_reverses_stock_and_journal(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 10, "price": 20}]}))
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    assert ok(c.get("/api/items"))[0]["qty"] == 100
    assert not [j for j in ok(c.get("/api/journals")) if j["source"] == "sale"]
    assert balanced(c)
