"""اختبارات المرحلة ٢: الوحدات المتعددة، قوائم الأسعار، العروض، باركود الموازين، والورديات."""
from conftest import fail, ok
from test_core import balanced, setup_basics


def test_multi_unit_sale_moves_base_quantity(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)          # 100 وحدة أساسية بتكلفة 10
    ok(c.put(f"/api/items/{item['id']}/units", json={"units": [{"name": "كرتونة", "factor": 12, "price": 216}]}))
    units = ok(c.get(f"/api/items/{item['id']}/units"))
    assert [u["name"] for u in units] == ["قطعة", "كرتونة"] and units[1]["factor"] == 12
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 2, "price": 216,
                                                      "unit_name": "كرتونة", "unit_factor": 12}]}))
    assert inv["total"] == 432
    assert ok(c.get("/api/items"))[0]["qty"] == 76           # 100 − (2 × 12)
    assert abs(inv["cost_total"] - 240) < 0.01               # 24 وحدة × 10
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    assert ok(c.get("/api/items"))[0]["qty"] == 100 and balanced(c)


def test_multi_unit_purchase_costs_per_base_unit(admin):
    c = admin
    _, item, _, supp, wh = setup_basics(c)
    ok(c.put(f"/api/items/{item['id']}/units", json={"units": [{"name": "كرتونة", "factor": 10}]}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 10, "price": 140,
                                                "unit_name": "كرتونة", "unit_factor": 10}]}))
    it = ok(c.get("/api/items"))[0]
    assert it["qty"] == 200                                   # 100 + (10 × 10)
    assert abs(it["avg_cost"] - 12) < 0.01                    # (100×10 + 100×14) / 200


def test_price_list_overrides_item_price(admin):
    c = admin
    _, item, cust, _, _ = setup_basics(c)
    pl = ok(c.post("/api/price-lists", json={"name": "جملة", "items": [{"item_id": item["id"], "price": 16}]}))
    ok(c.put(f"/api/parties/{cust['id']}", json={"name": "أحمد", "price_list_id": pl["id"]}))
    assert ok(c.get(f"/api/price?item_id={item['id']}"))["price"] == 20
    p = ok(c.get(f"/api/price?item_id={item['id']}&party_id={cust['id']}"))
    assert p["price"] == 16 and "قائمة" in p["source"]
    assert ok(c.get(f"/api/price?item_id={item['id']}&party_id={cust['id']}&unit_factor=12"))["price"] == 192


def test_promotion_applies_best_price(admin):
    c = admin
    _, item, _, _, _ = setup_basics(c)
    ok(c.post("/api/promotions", json={"name": "خصم 25%", "item_id": item["id"], "kind": "percent", "value": 25}))
    p = ok(c.get(f"/api/price?item_id={item['id']}"))
    assert p["price"] == 15 and p["promo"] == "خصم 25%"
    ok(c.post("/api/promotions", json={"name": "سعر العيد", "item_id": item["id"], "kind": "price", "value": 12,
                                       "min_qty": 5}))
    assert ok(c.get(f"/api/price?item_id={item['id']}&qty=2"))["price"] == 15     # لم تتحقق الكمية
    assert ok(c.get(f"/api/price?item_id={item['id']}&qty=10"))["price"] == 12    # الأفضل للزبون
    ok(c.post("/api/promotions", json={"name": "منتهٍ", "item_id": item["id"], "kind": "price", "value": 1,
                                       "date_to": "2020-01-01"}))
    assert ok(c.get(f"/api/price?item_id={item['id']}&qty=10"))["price"] == 12    # العرض المنتهي يُتجاهل


def test_unit_own_price_wins_over_multiplied_price(admin):
    c = admin
    _, item, cust, _, _ = setup_basics(c)          # سعر القطعة 20
    ok(c.put(f"/api/items/{item['id']}/units", json={"units": [{"name": "كرتونة", "factor": 12, "price": 200}]}))
    unit = [u for u in ok(c.get(f"/api/items/{item['id']}/units")) if not u["base"]][0]
    assert ok(c.get(f"/api/price?item_id={item['id']}&unit_factor=12"))["price"] == 240          # بدون تحديد الوحدة
    p = ok(c.get(f"/api/price?item_id={item['id']}&unit_factor=12&unit_id={unit['id']}"))
    assert p["price"] == 200 and "وحدة" in p["source"]                                            # سعر الكرتونة الخاص
    ok(c.post("/api/promotions", json={"name": "جملة", "item_id": item["id"], "kind": "percent", "value": 10}))
    assert ok(c.get(f"/api/price?item_id={item['id']}&unit_factor=12&unit_id={unit['id']}"))["price"] == 180  # العرض فوق سعر الوحدة


def test_scale_barcode_reads_weight(admin):
    c = admin
    _, item, _, _, _ = setup_basics(c)
    ok(c.put(f"/api/items/{item['id']}", json={"name": "زيت", "sale_price": 20, "barcode": "2001234"}))
    # 2 + 001234 (رمز المنتج) + 01500 (1.5 كغ) + خانة تحقق
    r = ok(c.get("/api/pos/scan?code=2001234015000"))
    assert r["found"] and r["scale"] and r["qty"] == 1.5 and r["price"] == 20


def test_plain_and_unit_barcode_scan(admin):
    c = admin
    _, item, _, _, _ = setup_basics(c)
    ok(c.put(f"/api/items/{item['id']}", json={"name": "زيت", "sale_price": 20, "barcode": "5901234123457"}))
    ok(c.put(f"/api/items/{item['id']}/units", json={"units": [{"name": "كرتونة", "factor": 12, "barcode": "777", "price": 210}]}))
    r = ok(c.get("/api/pos/scan?code=5901234123457"))
    assert r["found"] and not r["scale"] and r["price"] == 20 and r["unit"] is None
    u = ok(c.get("/api/pos/scan?code=777"))
    assert u["found"] and u["unit"]["name"] == "كرتونة" and u["price"] == 210   # سعر الكرتونة الخاص
    assert not ok(c.get("/api/pos/scan?code=nope"))["found"]


def test_shift_expects_cash_and_posts_difference(admin):
    c = admin
    accounts, item, _, _, wh = setup_basics(c)
    sh = ok(c.post("/api/shifts/open", json={"cash_account_id": accounts["111"], "warehouse_id": wh, "opening_cash": 50}))
    assert sh["number"] == 1 and ok(c.get("/api/shifts"))["current"]["id"] == sh["id"]
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                           "lines": [{"item_id": item["id"], "qty": 3, "price": 20}]}))
    assert ok(c.get(f"/api/invoices/{inv['id']}"))["shift_id"] == sh["id"]
    live = ok(c.get(f"/api/shifts/{sh['id']}"))
    assert live["sales_count"] == 1 and live["sales_cash"] == 60 and live["expected"] == 110
    closed = ok(c.post(f"/api/shifts/{sh['id']}/close", json={"counted_cash": 105, "notes": "نقص"}))
    assert closed["difference"] == -5 and closed["closed_at"]
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["57"]["bal_debit"] == 5          # العجز سُجّل على حساب عجز وزيادة الصندوق
    assert balanced(c)
    ok(c.post("/api/shifts/open", json={"cash_account_id": accounts["111"], "opening_cash": 105}))


def test_cannot_open_two_shifts(admin):
    c = admin
    accounts, *_ = setup_basics(c)
    ok(c.post("/api/shifts/open", json={"cash_account_id": accounts["111"], "opening_cash": 0}))
    assert "مفتوحة" in fail(c.post("/api/shifts/open", json={"cash_account_id": accounts["111"]}))["detail"]


def test_pos_init_returns_everything_the_screen_needs(admin):
    c = admin
    _, item, _, _, _ = setup_basics(c)
    d = ok(c.get("/api/pos/init"))
    assert d["items"] and d["items"][0]["units"][0]["name"] == "قطعة"
    assert d["cash_accounts"] and d["warehouses"] and d["shift"] is None
    assert d["settings"]["scale_prefix"] == "2"
