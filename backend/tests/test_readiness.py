"""اختبارات الجاهزية التشغيلية: منع التكرار، صلاحية الحسم، ترقيم الصفحات، الاستعادة، فحص النظام، والحماية."""
from fastapi.testclient import TestClient

from conftest import fail, ok
from test_core import balanced, setup_basics


def cashier(c):
    ok(c.post("/api/users", json={"username": "cash1", "full_name": "كاشير", "password": "pass123", "role": "cashier"}))
    k = TestClient(c.app_module.app)
    ok(k.post("/api/auth/login", json={"username": "cash1", "password": "pass123"}))
    return k


def test_resent_sale_is_not_duplicated(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    body = {"kind": "sale", "payment": "cash", "warehouse_id": wh, "client_ref": "pos-abc-123",
            "lines": [{"item_id": item["id"], "qty": 5, "price": 20}]}
    first = ok(c.post("/api/invoices", json=body))
    again = ok(c.post("/api/invoices", json=body))             # إعادة إرسال بعد انقطاع الاتصال
    assert again["id"] == first["id"] and again["duplicate"] is True
    assert ok(c.get("/api/items"))[0]["qty"] == 95              # خُصم مرة واحدة فقط
    assert len([i for i in ok(c.get("/api/invoices?kind=sale"))]) == 1 and balanced(c)


def test_cashier_without_discount_permission_cannot_cut_prices(admin):
    c = admin
    _, item, cust, _, wh = setup_basics(c)                      # سعر البيع 20
    k = cashier(c)
    line = {"item_id": item["id"], "qty": 2}
    base = {"kind": "sale", "payment": "cash", "warehouse_id": wh}
    assert k.post("/api/invoices", json={**base, "lines": [{**line, "price": 15}]}).status_code == 403
    assert k.post("/api/invoices", json={**base, "discount": 5, "lines": [{**line, "price": 20}]}).status_code == 403
    ok(k.post("/api/invoices", json={**base, "lines": [{**line, "price": 20}]}))
    ok(k.post("/api/invoices", json={**base, "lines": [{**line, "price": 25}]}))       # الزيادة مسموحة
    ok(c.post("/api/promotions", json={"name": "عرض", "item_id": item["id"], "kind": "percent", "value": 25}))
    ok(k.post("/api/invoices", json={**base, "lines": [{**line, "price": 15}]}))       # سعر العرض مقبول
    ok(c.put(f"/api/items/{item['id']}/units", json={"units": [{"name": "كرتونة", "factor": 12, "price": 200}]}))
    ok(c.delete(f"/api/promotions/{ok(c.get('/api/promotions'))[0]['id']}"))
    carton = {"item_id": item["id"], "qty": 1, "unit_name": "كرتونة", "unit_factor": 12}
    ok(k.post("/api/invoices", json={**base, "lines": [{**carton, "price": 200}]}))    # سعر الكرتونة الخاص مقبول
    assert k.post("/api/invoices", json={**base, "lines": [{**carton, "price": 150}]}).status_code == 403
    ok(c.post("/api/invoices", json={**base, "discount": 5, "lines": [{**line, "price": 12}]}))   # المدير يحسم


def test_lists_report_total_count_for_paging(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    for _ in range(7):
        ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                         "lines": [{"item_id": item["id"], "qty": 1, "price": 20}]}))
    r = c.get("/api/invoices?kind=sale&limit=3")
    assert len(r.json()) == 3 and r.headers["X-Total-Count"] == "7"
    r = c.get("/api/journals?limit=2")
    assert len(r.json()) == 2 and int(r.headers["X-Total-Count"]) == 8      # 7 مبيعات + فاتورة الشراء
    r = c.get("/api/audit?entity=invoice&limit=4")
    assert len(r.json()["rows"]) == 4 and int(r.headers["X-Total-Count"]) == 8


def test_restore_backup_brings_data_back(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    name = ok(c.post("/api/backups"))["name"]                    # نسخة والمخزون 100
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 40, "price": 20}]}))
    assert ok(c.get("/api/items"))[0]["qty"] == 60
    info = ok(c.post("/api/backups/restore-local", json={"name": name}))
    assert "before-restore" in info["safety_backup"]
    assert ok(c.get("/api/items"))[0]["qty"] == 100               # عادت البيانات كما كانت
    assert not ok(c.get("/api/invoices?kind=sale")) and balanced(c)
    assert any("before-restore" in b["name"] for b in ok(c.get("/api/backups")))


def test_restore_rejects_garbage_and_non_admin(admin, tmp_path):
    c = admin
    setup_basics(c)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database at all")
    with open(bad, "rb") as f:
        r = c.post("/api/backups/restore-upload", files={"file": ("bad.db", f, "application/octet-stream")})
    assert r.status_code == 400 and "صالحة" in r.json()["detail"]
    assert ok(c.get("/api/items"))                                # البيانات لم تُمس
    assert c.post("/api/backups/restore-local", json={"name": "../../x.db"}).status_code == 404
    k = cashier(c)
    assert k.post("/api/backups/restore-local", json={"name": "x"}).status_code == 403


def test_offsite_backup_copy(admin, tmp_path):
    c = admin
    setup_basics(c)
    extra = tmp_path / "usb"
    ok(c.put("/api/settings", json={"backup_extra_dir": str(extra)}))
    r = ok(c.post("/api/backups"))
    assert (extra / r["name"]).exists() and r["offsite_error"] is None
    ok(c.put("/api/settings", json={"backup_extra_dir": "Z:\\\\no\\\\such\\\\drive\\x:y"}))
    r = ok(c.post("/api/backups"))                                # فشل النسخ الخارجي لا يُفشل النسخة المحلية
    assert r["offsite_error"] and any(b["name"] == r["name"] for b in ok(c.get("/api/backups")))


def test_health_check_passes_on_clean_data_and_flags_problems(admin):
    c = admin
    _, item, _, _, wh = setup_basics(c)
    ok(c.post("/api/backups"))
    h = ok(c.get("/api/health"))
    by = {x["name"]: x for x in h["checks"]}
    assert by["توازن ميزان المراجعة"]["ok"] and by["تطابق كميات المواد مع أرصدة المستودعات"]["ok"]
    assert by["تطابق حساب المخزون مع قيمة البضاعة"]["ok"] and by["نسخة احتياطية حديثة (خلال ٤٨ ساعة)"]["ok"]
    assert not by["نسخ احتياطي خارج مجلد البرنامج"]["ok"] and h["failed"] == 1     # لم يُضبط مجلد خارجي
    ok(c.put("/api/settings", json={"allow_negative_stock": "1"}))
    ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                     "lines": [{"item_id": item["id"], "qty": 130, "price": 20}]}))
    by = {x["name"]: x for x in ok(c.get("/api/health"))["checks"]}
    assert not by["لا توجد كميات سالبة"]["ok"]


def test_cross_site_write_is_rejected(admin):
    c = admin
    setup_basics(c)
    r = c.post("/api/items", json={"name": "خبيث", "sale_price": 1}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "مصدر" in r.json()["detail"]
    ok(c.post("/api/items", json={"name": "سليم", "sale_price": 1}, headers={"Origin": "http://testserver"}))
    assert c.get("/api/items", headers={"Origin": "http://evil.example"}).status_code == 200     # القراءة لا تتأثر
    assert c.get("/api/settings").headers["X-Content-Type-Options"] == "nosniff"


def test_unexpected_error_is_logged_with_reference(admin, monkeypatch):
    c = admin
    setup_basics(c)

    def boom(conn):
        raise RuntimeError("انفجار تجريبي")
    monkeypatch.setattr(c.app_module.acc, "dashboard", boom)
    r = c.get("/api/dashboard")
    assert r.status_code == 500 and "رقم المرجع" in r.json()["detail"]
    ref = r.json()["detail"].split(":")[-1].strip()
    logs = "\n".join(ok(c.get("/api/logs"))["lines"])
    assert ref in logs and "انفجار تجريبي" in logs and "/api/dashboard" in logs


def test_frontend_assets_are_versioned_and_revalidated(admin):
    html = admin.get("/")
    assert html.status_code == 200 and html.headers["Cache-Control"] == "no-store"
    assert "/static/app.js?v=" in html.text and "/static/style.css?v=" in html.text
    assert admin.get("/static/app.js").headers["Cache-Control"] == "no-cache"
