"""اختبارات المطعم: الوصفات واستهلاك المكوّنات، الطلبات، المطبخ، رسم الخدمة، والتوصيل."""
from conftest import fail, ok
from test_core import balanced


def kitchen_setup(c):
    """مكوّنات في المخزون + طبق شاورما بوصفة: 2 خبز + 0.15 كغ دجاج."""
    accounts = {a["code"]: a["id"] for a in ok(c.get("/api/accounts"))}
    wh = ok(c.get("/api/warehouses"))[0]["id"]
    supp = ok(c.post("/api/parties", json={"name": "مورد اللحوم", "kind": "supplier"}))
    bread = ok(c.post("/api/items", json={"name": "خبز", "unit": "رغيف", "sale_price": 0}))
    chicken = ok(c.post("/api/items", json={"name": "دجاج", "unit": "كغ", "sale_price": 0}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": wh,
                                     "lines": [{"item_id": bread["id"], "qty": 100, "price": 1},
                                               {"item_id": chicken["id"], "qty": 10, "price": 40}]}))
    st = ok(c.post("/api/restaurant/stations", json={"name": "المشاوي"}))
    dish = ok(c.post("/api/items", json={"name": "شاورما دجاج", "sale_price": 25, "category": "ساندويش",
                                         "station_id": st["id"]}))
    rec = ok(c.put(f"/api/items/{dish['id']}/recipe", json={"lines": [
        {"ingredient_id": bread["id"], "qty": 2}, {"ingredient_id": chicken["id"], "qty": 0.15}]}))
    return dict(accounts=accounts, wh=wh, bread=bread, chicken=chicken, dish=dish, recipe=rec, station=st)


def stock(c, item_id):
    return [i for i in ok(c.get("/api/items")) if i["id"] == item_id][0]["qty"]


# ───────── الوصفات ─────────

def test_recipe_cost_and_food_cost_percentage(admin):
    k = kitchen_setup(admin)
    assert k["recipe"]["cost"] == 8                  # 2×1 + 0.15×40
    assert k["recipe"]["margin"] == 17 and k["recipe"]["food_cost_pct"] == 32
    assert ok(admin.get("/api/reports/recipes"))[0]["name"] == "شاورما دجاج"


def test_selling_dish_consumes_ingredients_and_costs_them(admin):
    c = admin
    k = kitchen_setup(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": k["dish"]["id"], "qty": 10, "price": 25}]}))
    assert stock(c, k["bread"]["id"]) == 80          # 100 − 10×2
    assert stock(c, k["chicken"]["id"]) == 8.5       # 10 − 10×0.15
    assert stock(c, k["dish"]["id"]) == 0            # الطبق نفسه لا يُخزَّن
    assert inv["cost_total"] == 80 and inv["profit"] == 170
    assert balanced(c)


def test_deleting_dish_sale_restores_ingredients(admin):
    c = admin
    k = kitchen_setup(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": k["dish"]["id"], "qty": 10, "price": 25}]}))
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    assert stock(c, k["bread"]["id"]) == 100 and stock(c, k["chicken"]["id"]) == 10
    assert balanced(c)


def test_editing_dish_sale_recomputes_ingredients(admin):
    c = admin
    k = kitchen_setup(c)
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": k["dish"]["id"], "qty": 10, "price": 25}]}))
    ok(c.put(f"/api/invoices/{inv['id']}", json={"payment": "cash", "warehouse_id": k["wh"],
                                                 "lines": [{"item_id": k["dish"]["id"], "qty": 4, "price": 25}]}))
    assert stock(c, k["bread"]["id"]) == 92 and stock(c, k["chicken"]["id"]) == 9.4
    assert balanced(c)


def test_dish_blocked_when_ingredient_short_unless_negative_allowed(admin):
    c = admin
    k = kitchen_setup(c)
    body = {"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
            "lines": [{"item_id": k["dish"]["id"], "qty": 80, "price": 25}]}     # يحتاج 160 رغيفاً
    assert "خبز" in fail(c.post("/api/invoices", json=body))["detail"]
    ok(c.put("/api/settings", json={"allow_negative_stock": "1"}))
    ok(c.post("/api/invoices", json=body))
    assert stock(c, k["bread"]["id"]) == -60


def test_dish_cannot_be_purchased_or_used_as_ingredient(admin):
    c = admin
    k = kitchen_setup(c)
    r = fail(c.post("/api/invoices", json={"kind": "purchase", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": k["dish"]["id"], "qty": 1, "price": 5}]}))
    assert "مكوّنات" in r["detail"]
    juice = ok(c.post("/api/items", json={"name": "خدمة توصيل", "sale_price": 5, "is_service": True}))
    r = fail(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [{"ingredient_id": juice["id"], "qty": 1}]}))
    assert "غير مخزني" in r["detail"]


# ───────── الطلبات والطاولات ─────────

def tables(c):
    area = ok(c.post("/api/restaurant/areas", json={"name": "الصالة"}))
    t1 = ok(c.post("/api/restaurant/tables", json={"name": "طاولة 1", "area_id": area["id"], "seats": 4}))
    t2 = ok(c.post("/api/restaurant/tables", json={"name": "طاولة 2", "area_id": area["id"]}))
    return t1["id"], t2["id"]


def test_dine_in_order_flow_with_service_charge(admin):
    c = admin
    k = kitchen_setup(c)
    t1, _ = tables(c)
    ok(c.put("/api/settings", json={"service_rate": "10"}))
    o = ok(c.post("/api/orders", json={"kind": "dine_in", "table_id": t1, "guests": 3,
                                       "lines": [{"item_id": k["dish"]["id"], "qty": 4, "price": 25}]}))
    assert o["subtotal"] == 100 and o["service"] == 10 and o["total"] == 110
    tbl = [t for t in ok(c.get("/api/restaurant/tables")) if t["id"] == t1][0]
    assert tbl["busy"] and tbl["total"] == 110
    assert stock(c, k["bread"]["id"]) == 100                    # الطلب المفتوح لا يخصم المخزون بعد
    inv = ok(c.post(f"/api/orders/{o['id']}/pay", json={"payment": "cash"}))
    assert inv["total"] == 110 and inv["service_amount"] == 10
    assert stock(c, k["bread"]["id"]) == 92                     # الدفع يخصم المكوّنات
    tb = {r["code"]: r for r in ok(c.get("/api/reports/trial-balance"))["rows"]}
    assert tb["44"]["bal_credit"] == 10 and tb["41"]["bal_credit"] == 100     # رسم الخدمة في حسابه
    assert not [t for t in ok(c.get("/api/restaurant/tables")) if t["id"] == t1][0]["busy"]
    assert "مغلق" in fail(c.put(f"/api/orders/{o['id']}", json={"guests": 5}))["detail"]
    assert balanced(c)


def test_kitchen_gets_only_new_items_grouped_by_station(admin):
    c = admin
    k = kitchen_setup(c)
    t1, _ = tables(c)
    drink = ok(c.post("/api/items", json={"name": "عصير", "sale_price": 10}))
    o = ok(c.post("/api/orders", json={"kind": "dine_in", "table_id": t1, "lines": [
        {"item_id": k["dish"]["id"], "qty": 2, "price": 25, "notes": [{"name": "بدون بصل", "price": 0}]},
        {"item_id": drink["id"], "qty": 1, "price": 10}]}))
    r = ok(c.post(f"/api/orders/{o['id']}/send"))
    stations = {t["station"]: t for t in r["tickets"]}
    assert set(stations) == {"المشاوي", "المطبخ"}
    assert stations["المشاوي"]["lines"][0]["qty"] == 2 and stations["المشاوي"]["lines"][0]["notes"] == ["بدون بصل"]
    assert not ok(c.post(f"/api/orders/{o['id']}/send"))["tickets"]          # لا شيء جديد
    lines = ok(c.get(f"/api/orders/{o['id']}"))["lines"]
    lines[0]["qty"] = 5                                                       # الزبون طلب 3 إضافية
    ok(c.put(f"/api/orders/{o['id']}", json={"lines": lines}))
    again = ok(c.post(f"/api/orders/{o['id']}/send"))["tickets"]
    assert len(again) == 1 and again[0]["lines"][0]["qty"] == 3               # تُرسل الزيادة فقط
    queue = ok(c.get("/api/kitchen"))["lines"]
    assert len(queue) == 2
    ok(c.post("/api/kitchen/ready", json={"line_ids": [queue[0]["id"]]}))
    assert len(ok(c.get("/api/kitchen"))["lines"]) == 1


def test_split_move_and_merge_orders(admin):
    c = admin
    k = kitchen_setup(c)
    t1, t2 = tables(c)
    o = ok(c.post("/api/orders", json={"kind": "dine_in", "table_id": t1,
                                       "lines": [{"item_id": k["dish"]["id"], "qty": 4, "price": 25}]}))
    part = ok(c.post(f"/api/orders/{o['id']}/split", json={"parts": [{"line_id": o["lines"][0]["id"], "qty": 1}]}))
    assert part["subtotal"] == 25 and ok(c.get(f"/api/orders/{o['id']}"))["subtotal"] == 75
    assert len([t for t in ok(c.get("/api/restaurant/tables")) if t["id"] == t1][0]["orders"]) == 2
    ok(c.post(f"/api/orders/{part['id']}/move", json={"table_id": t2}))
    assert [t for t in ok(c.get("/api/restaurant/tables")) if t["id"] == t2][0]["busy"]
    merged = ok(c.post(f"/api/orders/{part['id']}/merge", json={"into": o["id"]}))
    assert merged["subtotal"] == 100
    assert not [t for t in ok(c.get("/api/restaurant/tables")) if t["id"] == t2][0]["busy"]


def test_delivery_order_adds_fee_to_service_income(admin):
    c = admin
    k = kitchen_setup(c)
    drv = ok(c.post("/api/restaurant/drivers", json={"name": "سائق 1", "phone": "0999", "active": 1}))
    o = ok(c.post("/api/orders", json={"kind": "delivery", "customer_name": "ليلى", "customer_phone": "0933",
                                       "address": "المزة", "delivery_fee": 15, "driver_id": drv["id"],
                                       "lines": [{"item_id": k["dish"]["id"], "qty": 2, "price": 25}]}))
    assert o["total"] == 65 and o["service"] == 0 and o["delivery_status"] == "preparing"
    inv = ok(c.post(f"/api/orders/{o['id']}/pay", json={"payment": "cash", "delivered": True}))
    assert inv["total"] == 65 and "ليلى" in inv["notes"]
    assert ok(c.get(f"/api/orders/{o['id']}"))["delivery_status"] == "delivered"
    assert balanced(c)


def test_canceling_sent_order_is_audited_and_needs_permission(admin):
    from fastapi.testclient import TestClient
    c = admin
    k = kitchen_setup(c)
    t1, _ = tables(c)
    ok(c.post("/api/users", json={"username": "waiter1", "full_name": "نادل", "password": "pass123", "role": "waiter"}))
    w = TestClient(c.app_module.app)
    ok(w.post("/api/auth/login", json={"username": "waiter1", "password": "pass123"}))
    o = ok(w.post("/api/orders", json={"kind": "dine_in", "table_id": t1,
                                       "lines": [{"item_id": k["dish"]["id"], "qty": 2, "price": 25}]}))
    ok(w.post(f"/api/orders/{o['id']}/send"))
    assert w.post(f"/api/orders/{o['id']}/cancel", json={}).status_code == 403     # النادل لا يلغي بعد الإرسال
    assert w.post(f"/api/orders/{o['id']}/pay", json={}).status_code == 403        # ولا يقبض بلا صلاحية البيع
    assert w.put(f"/api/orders/{o['id']}", json={"discount": 20}).status_code == 403
    ok(c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "خطأ في الطلب"}))
    logs = [r for r in ok(c.get("/api/audit?entity=order"))["rows"] if r["action"] == "delete"]
    assert logs and "خطأ في الطلب" in logs[0]["summary"]
    assert stock(c, k["bread"]["id"]) == 100                                        # لم يُخصم شيء


def test_restaurant_report_breaks_down_sales(admin):
    c = admin
    k = kitchen_setup(c)
    t1, _ = tables(c)
    for kind, extra in (("dine_in", {"table_id": t1}), ("takeaway", {})):
        o = ok(c.post("/api/orders", json={"kind": kind, **extra,
                                           "lines": [{"item_id": k["dish"]["id"], "qty": 2, "price": 25}]}))
        ok(c.post(f"/api/orders/{o['id']}/pay", json={"payment": "cash"}))
    rep = ok(c.get("/api/reports/restaurant"))
    assert rep["orders"] == 2 and rep["total"] == 100
    assert {x["kind_label"] for x in rep["by_kind"]} == {"صالة", "سفري"}
    assert rep["by_category"][0]["category"] == "ساندويش" and rep["by_item"][0]["profit"] == 68   # 100 − 4×8


def test_non_stock_item_sells_without_inventory_or_cost(admin):
    c = admin
    k = kitchen_setup(c)
    juice = ok(c.post("/api/items", json={"name": "عصير طازج", "sale_price": 10, "is_service": True}))
    assert juice["is_service"] == 1
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": juice["id"], "qty": 5, "price": 10}]}))
    assert inv["total"] == 50 and inv["cost_total"] == 0 and stock(c, juice["id"]) == 0
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    assert stock(c, juice["id"]) == 0 and balanced(c)
    r = fail(c.post("/api/invoices", json={"kind": "purchase", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": juice["id"], "qty": 1, "price": 3}]}))
    assert "غير مخزني" in r["detail"]
    r = fail(c.put(f"/api/items/{k['bread']['id']}", json={"name": "خبز", "is_service": True}))
    assert "رصيد" in r["detail"]            # مادة لها رصيد لا تتحول إلى غير مخزنية


# ───────── مكوّنات بلا حد، تحضيرات وسيطة، وهدر ─────────

def test_product_accepts_unlimited_ingredients(admin):
    c = admin
    k = kitchen_setup(c)
    supp = ok(c.get("/api/parties?kind=supplier"))[0]
    ings = [ok(c.post("/api/items", json={"name": f"مكوّن {n}", "unit": "غ", "sale_price": 0})) for n in range(60)]
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": k["wh"],
                                     "lines": [{"item_id": i["id"], "qty": 1000, "price": 0.5} for i in ings]}))
    big = ok(c.post("/api/items", json={"name": "طبق الشيف", "sale_price": 200}))
    rec = ok(c.put(f"/api/items/{big['id']}/recipe", json={"lines": [{"ingredient_id": i["id"], "qty": 3} for i in ings]}))
    assert rec["count"] == 60 and rec["cost"] == 90                    # 60 مكوّناً × 3 × 0.5
    inv = ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                           "lines": [{"item_id": big["id"], "qty": 2, "price": 200}]}))
    assert inv["cost_total"] == 180
    assert all(stock(c, i["id"]) == 994 for i in (ings[0], ings[29], ings[59]))
    ok(c.delete(f"/api/invoices/{inv['id']}"))
    assert all(stock(c, i["id"]) == 1000 for i in (ings[0], ings[59])) and balanced(c)


def test_nested_preparation_explodes_to_raw_materials(admin):
    c = admin
    k = kitchen_setup(c)
    supp = ok(c.get("/api/parties?kind=supplier"))[0]
    garlic = ok(c.post("/api/items", json={"name": "ثوم", "unit": "كغ", "sale_price": 0}))
    oil = ok(c.post("/api/items", json={"name": "زيت", "unit": "لتر", "sale_price": 0}))
    ok(c.post("/api/invoices", json={"kind": "purchase", "party_id": supp["id"], "payment": "credit", "warehouse_id": k["wh"],
                                     "lines": [{"item_id": garlic["id"], "qty": 10, "price": 20},
                                               {"item_id": oil["id"], "qty": 10, "price": 30}]}))
    sauce = ok(c.post("/api/items", json={"name": "ثومية (تحضيرة)", "unit": "كغ", "sale_price": 0}))
    ok(c.put(f"/api/items/{sauce['id']}/recipe", json={"lines": [{"ingredient_id": garlic["id"], "qty": 0.4},
                                                                 {"ingredient_id": oil["id"], "qty": 0.6}]}))   # كغ ثومية = 26
    rec = ok(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [
        {"ingredient_id": k["bread"]["id"], "qty": 2}, {"ingredient_id": k["chicken"]["id"], "qty": 0.15},
        {"ingredient_id": sauce["id"], "qty": 0.05}]}))
    assert rec["cost"] == 9.3                                            # 8 + 0.05×26
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                     "lines": [{"item_id": k["dish"]["id"], "qty": 10, "price": 25}]}))
    assert stock(c, garlic["id"]) == 9.8 and stock(c, oil["id"]) == 9.7   # 10×0.05×0.4 و 10×0.05×0.6
    assert stock(c, sauce["id"]) == 0 and balanced(c)                     # التحضيرة نفسها لا تُخزَّن


def test_recipe_waste_and_cycle_and_duplicates(admin):
    c = admin
    k = kitchen_setup(c)
    rec = ok(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [
        {"ingredient_id": k["chicken"]["id"], "qty": 0.1, "waste_pct": 20},
        {"ingredient_id": k["bread"]["id"], "qty": 1}, {"ingredient_id": k["bread"]["id"], "qty": 1}]}))
    assert rec["count"] == 2 and rec["cost"] == 6.8                       # دجاج 0.12×40 + خبز 2×1 (المكرّر جُمع)
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                     "lines": [{"item_id": k["dish"]["id"], "qty": 10, "price": 25}]}))
    assert stock(c, k["chicken"]["id"]) == 8.8                            # 10 × 0.1 × 1.2
    other = ok(c.post("/api/items", json={"name": "وجبة", "sale_price": 50}))
    ok(c.put(f"/api/items/{other['id']}/recipe", json={"lines": [{"ingredient_id": k["dish"]["id"], "qty": 1}]}))
    r = fail(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [{"ingredient_id": other["id"], "qty": 1}]}))
    assert "حلقة" in r["detail"]
    assert ok(c.get(f"/api/items/{k['dish']['id']}/recipe"))["count"] == 2   # الحفظ المرفوض لم يغيّر الوصفة


def test_ingredient_units_grams_of_a_kilo_item(admin):
    c = admin
    k = kitchen_setup(c)                                                   # الدجاج بالكيلو (40 للكيلو)، الخبز بالرغيف
    units = ok(c.get("/api/recipe-units"))
    assert [u["name"] for u in units[str(k["chicken"]["id"])]] == ["كغ", "غرام"]
    assert str(k["bread"]["id"]) not in units                              # الرغيف لا وحدة بديلة له
    rec = ok(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [
        {"ingredient_id": k["chicken"]["id"], "qty": 10, "unit_name": "غرام"},
        {"ingredient_id": k["bread"]["id"], "qty": 1}]}))
    assert rec["cost"] == 1.4                                              # 10 غرام × 0.04 + رغيف
    meat = next(l for l in rec["lines"] if l["ingredient_id"] == k["chicken"]["id"])
    assert meat["unit_name"] == "غرام" and meat["unit_qty"] == 10 and meat["qty"] == 0.01
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": k["wh"],
                                     "lines": [{"item_id": k["dish"]["id"], "qty": 20, "price": 25}]}))
    assert stock(c, k["chicken"]["id"]) == 9.8 and stock(c, k["bread"]["id"]) == 80 and balanced(c)
    # وحدة معرّفة للمادة (ربطة = 8 أرغفة) تصلح وحدة مكوّن
    ok(c.put(f"/api/items/{k['bread']['id']}/units", json={"units": [{"name": "ربطة", "factor": 8}]}))
    rec = ok(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [
        {"ingredient_id": k["bread"]["id"], "qty": 0.5, "unit_name": "ربطة"}]}))
    assert rec["lines"][0]["qty"] == 4 and rec["cost"] == 4
    r = fail(c.put(f"/api/items/{k['dish']['id']}/recipe", json={"lines": [
        {"ingredient_id": k["bread"]["id"], "qty": 1, "unit_name": "غرام"}]}))
    assert "غير معرّفة" in r["detail"]
