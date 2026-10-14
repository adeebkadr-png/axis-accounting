"""اختبارات المرحلة ٣: التكلفة لكل مستودع، الدفعات والصلاحية، الجرد، أوامر الشراء، والمتغيرات."""
from conftest import fail, ok
from test_core import balanced, setup_basics


def two_warehouses(c):
    ok(c.post("/api/warehouses", json={"name": "فرع"}))
    whs = {w["name"]: w["id"] for w in ok(c.get("/api/warehouses"))}
    return whs["المستودع الرئيسي"], whs["فرع"]


# ───────── التكلفة لكل مستودع ─────────

def test_cost_is_tracked_per_warehouse(admin):
    c = admin
    _, item, _, supp, main = setup_basics(c)                      # 100 قطعة بتكلفة 10 في الرئيسي
    _, branch = two_warehouses(c)
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit",
                                     "warehouse_id": branch, "lines": [{"item_id": item["id"], "qty": 100, "price": 20}]}))
    main_item = ok(c.get(f"/api/items?warehouse_id={main}"))[0]
    branch_item = ok(c.get(f"/api/items?warehouse_id={branch}"))[0]
    assert main_item["avg_cost"] == 10 and branch_item["avg_cost"] == 20     # لكل مستودع تكلفته
    assert ok(c.get("/api/items"))[0]["avg_cost"] == 15                      # المتوسط العام مرجّح
    sale = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": branch,
                                            "lines": [{"item_id": item["id"], "qty": 10, "price": 30}]}))
    assert sale["cost_total"] == 200                                          # بيع من الفرع بتكلفة الفرع
    assert balanced(c)


def test_transfer_carries_cost_to_destination(admin):
    c = admin
    _, item, _, supp, main = setup_basics(c)
    _, branch = two_warehouses(c)
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit",
                                     "warehouse_id": branch, "lines": [{"item_id": item["id"], "qty": 100, "price": 20}]}))
    ok(c.post("/api/transfers", json={"from_wh": main, "to_wh": branch,
                                      "lines": [{"item_id": item["id"], "qty": 100}]}))   # 100 بتكلفة 10 تنتقل للفرع
    branch_item = ok(c.get(f"/api/items?warehouse_id={branch}"))[0]
    assert branch_item["qty"] == 200 and branch_item["avg_cost"] == 15        # (100×20 + 100×10) / 200
    assert ok(c.get(f"/api/items?warehouse_id={main}"))[0]["qty"] == 0
    assert balanced(c)


# ───────── الدفعات وتواريخ الصلاحية ─────────

def test_batches_fefo_and_expiry_report(admin):
    c = admin
    _, item, _, supp, wh = setup_basics(c)
    ok(c.put(f"/api/items/{item['id']}", json={"name": "زيت", "sale_price": 20, "track_batches": True}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 30, "price": 10,
                                                "batch_code": "B2", "batch_expiry": "2027-01-01"}]}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 20, "price": 10,
                                                "batch_code": "B1", "batch_expiry": "2026-03-01"}]}))
    batches = ok(c.get(f"/api/batches?item_id={item['id']}"))
    assert [b["code"] for b in batches][:2] == ["B1", "B2"]        # الأقرب انتهاءً أولاً
    sale = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                            "lines": [{"item_id": item["id"], "qty": 25, "price": 30}]}))
    after = {b["code"]: b["qty"] for b in ok(c.get(f"/api/batches?item_id={item['id']}"))}
    assert after.get("B1") is None and after["B2"] == 25            # استُهلكت B1 كاملة ثم 5 من B2
    ok(c.delete(f"/api/invoices/{sale['id']}"))                     # الحذف يعيد الدفعات كما كانت
    back = {b["code"]: b["qty"] for b in ok(c.get(f"/api/batches?item_id={item['id']}"))}
    assert back["B1"] == 20 and back["B2"] == 30
    rep = ok(c.get("/api/reports/expiry?days=100000"))
    assert any(r["code"] == "B1" for r in rep["rows"]) and rep["rows"][0]["days_left"] is not None


def test_batches_move_with_transfer(admin):
    c = admin
    _, item, _, supp, main = setup_basics(c)
    _, branch = two_warehouses(c)
    ok(c.put(f"/api/items/{item['id']}", json={"name": "زيت", "sale_price": 20, "track_batches": True}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": main,
                                     "lines": [{"item_id": item["id"], "qty": 40, "price": 10,
                                                "batch_code": "L9", "batch_expiry": "2027-05-05"}]}))
    t = ok(c.post("/api/transfers", json={"from_wh": main, "to_wh": branch,
                                          "lines": [{"item_id": item["id"], "qty": 15}]}))
    moved = ok(c.get(f"/api/batches?item_id={item['id']}&warehouse_id={branch}"))
    assert moved[0]["code"] == "L9" and moved[0]["qty"] == 15 and moved[0]["expiry"] == "2027-05-05"
    ok(c.delete(f"/api/transfers/{t['id']}"))
    assert not ok(c.get(f"/api/batches?item_id={item['id']}&warehouse_id={branch}"))


# ───────── الجرد الفعلي ─────────

def test_stocktake_posts_shortage_and_surplus(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)                       # 100 قطعة بتكلفة 10
    sheet = ok(c.get(f"/api/stocktakes/sheet?warehouse_id={wh}"))
    row = [r for r in sheet if r["item_id"] == item["id"]][0]
    assert row["system_qty"] == 100 and row["unit_cost"] == 10
    st = ok(c.post("/api/stocktakes", json={"warehouse_id": wh, "notes": "جرد شهري",
                                            "lines": [{"item_id": item["id"], "counted_qty": 93}]}))
    assert st["diff_value"] == -70 and st["changed"] == 1
    assert ok(c.get("/api/items"))[0]["qty"] == 93
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["58"]["bal_debit"] == 70                        # النقص حُمِّل على فروقات الجرد
    assert balanced(c)
    ok(c.post("/api/stocktakes", json={"warehouse_id": wh, "lines": [{"item_id": item["id"], "counted_qty": 95}]}))
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["58"]["bal_debit"] == 50                        # زيادة 2 × 10 خفّضت الفرق
    assert ok(c.get("/api/items"))[0]["qty"] == 95 and balanced(c)


def test_stocktake_respects_period_lock(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/period/lock", json={"lock_before_date": "2030-01-01"}))
    r = fail(c.post("/api/stocktakes", json={"warehouse_id": wh, "lines": [{"item_id": item["id"], "counted_qty": 5}]}))
    assert "مقفلة" in r["detail"]


# ───────── أوامر الشراء ─────────

def test_purchase_order_receive_creates_invoice(admin):
    c = admin
    _, item, _, supp, wh = setup_basics(c)
    po = ok(c.post("/api/purchase-orders", json={"supplier_id": supp["id"], "warehouse_id": wh, "status": "sent",
                                                 "lines": [{"item_id": item["id"], "qty": 50, "price": 11}]}))
    assert po["number"] == 1 and po["total"] == 550 and po["status_label"] == "مرسل للمورد"
    inv = ok(c.post(f"/api/purchase-orders/{po['id']}/receive", json={"payment": "credit"}))
    assert inv["kind"] == "purchase" and inv["total"] == 550
    assert ok(c.get(f"/api/purchase-orders/{po['id']}"))["status"] == "received"
    assert ok(c.get("/api/items"))[0]["qty"] == 150
    assert "مستلم" in fail(c.delete(f"/api/purchase-orders/{po['id']}"))["detail"]
    assert balanced(c)


def test_reorder_suggestions_use_sales_speed(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    ok(c.put(f"/api/items/{item['id']}", json={"name": "زيت", "sale_price": 20, "min_qty": 40}))
    assert not ok(c.get("/api/purchase-orders/suggestions"))          # المخزون 100 فوق الحد
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 70, "price": 20}]}))
    sug = ok(c.get("/api/purchase-orders/suggestions"))
    assert len(sug) == 1 and sug[0]["qty"] == 30 and sug[0]["sold"] == 70
    assert sug[0]["suggested"] >= 50 and sug[0]["last_price"] == 10 and sug[0]["supplier"] == "مورد"


# ───────── متغيرات المادة ─────────

def test_item_variants_are_independent_stock(admin):
    c = admin
    _, item, _, supp, wh = setup_basics(c)
    red = ok(c.post(f"/api/items/{item['id']}/variants", json={"variant": "أحمر", "sale_price": 25}))
    assert red["name"].endswith("أحمر") and red["parent_id"] == item["id"] and red["qty"] == 0
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": red["id"], "qty": 5, "price": 12}]}))
    assert ok(c.get(f"/api/items/{item['id']}/variants"))[0]["qty"] == 5
    assert [i for i in ok(c.get("/api/items")) if i["id"] == item["id"]][0]["qty"] == 100   # الأصل لم يتأثر
    assert "متغيرات" in fail(c.delete(f"/api/items/{item['id']}"))["detail"]
