"""بيانات تجريبية لاستكشاف البرنامج: python seed_demo.py (على قاعدة بيانات فارغة فقط).

عند أول فتح للبرنامج بعدها ستُطلب منك إنشاء حساب المدير."""
import random
from datetime import date, timedelta

import accounting as acc
from db import init_db, tx

init_db()
random.seed(7)
with tx() as c:
    if c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]:
        raise SystemExit("قاعدة البيانات تحتوي فواتير مسبقاً، لن تُضاف بيانات تجريبية.")
    c.execute("UPDATE settings SET value='مؤسسة النور التجارية' WHERE key='company_name'")
    start = date.today() - timedelta(days=170)
    acc.post_journal(c, start.isoformat(), "رأس المال الافتتاحي", [
        dict(account_id=acc.acc_id(c, "111"), debit=40000), dict(account_id=acc.acc_id(c, "112"), debit=60000),
        dict(account_id=acc.acc_id(c, "31"), credit=100000)])
    customers = [acc.create_party(c, n, "customer", f"09{random.randint(10000000, 99999999)}") for n in
                 ["محل أبو خالد", "سوبرماركت الأمل", "مطعم الياسمين", "بقالية السلام", "شركة الفجر"]]
    suppliers = [acc.create_party(c, n, "supplier") for n in ["شركة الشام للمواد الغذائية", "مؤسسة الريف", "معمل الوادي"]]
    items = [acc.create_item(c, dict(name=n, unit=u, sale_price=p, min_qty=m)) for n, u, p, m in [
        ("زيت زيتون 1 لتر", "عبوة", 95, 20), ("سكر 1 كغ", "كيس", 14, 50), ("أرز بسمتي 5 كغ", "كيس", 120, 15),
        ("شاي 500 غ", "علبة", 45, 20), ("معكرونة", "كيس", 9, 60), ("سمن نباتي 2 كغ", "علبة", 85, 10),
        ("عدس أحمر 1 كغ", "كيس", 22, 30), ("قهوة 250 غ", "علبة", 60, 15)]]
    branch = c.execute("INSERT INTO warehouses(name) VALUES ('مستودع الفرع')").lastrowid
    for d in range(0, 171, 3):
        day = (start + timedelta(days=d)).isoformat()
        if d % 21 == 0:
            acc.create_invoice(c, dict(kind="purchase", date=day, party_id=random.choice(suppliers)["id"], payment="credit",
                lines=[dict(item_id=it["id"], qty=random.randint(30, 90), price=round(it["sale_price"] * random.uniform(.62, .75), 2)) for it in items]))
            acc.create_voucher(c, dict(kind="payment", date=day, account_id=suppliers[0]["account_id"], amount=13000, cash_account_id=acc.acc_id(c, "112")))
        if d % 21 == 3:
            acc.create_transfer(c, dict(date=day, from_wh=acc.default_warehouse(c), to_wh=branch,
                                        lines=[dict(item_id=it["id"], qty=5) for it in items[:4]]))
        if d % 30 == 0:
            acc.create_voucher(c, dict(kind="payment", date=day, account_id=acc.acc_id(c, "53"), amount=2500, description="إيجار المحل"))
            acc.create_voucher(c, dict(kind="payment", date=day, account_id=acc.acc_id(c, "52"), amount=4000, description="رواتب الموظفين"))
            acc.create_voucher(c, dict(kind="payment", date=day, account_id=acc.acc_id(c, "54"), amount=random.randint(300, 600), description="فاتورة كهرباء"))
        stock = {r["id"]: r["qty"] for r in c.execute("SELECT id, qty FROM items")}
        lines = []
        for it in random.sample(items, random.randint(1, 4)):
            q = min(random.randint(2, 12), int(stock[it["id"]]))
            if q > 0:
                lines.append(dict(item_id=it["id"], qty=q, price=it["sale_price"]))
        if lines:
            credit = random.random() < .4
            cust = random.choice(customers)
            acc.create_invoice(c, dict(kind="sale", date=day, payment="credit" if credit else "cash",
                                       party_id=cust["id"] if credit else None, lines=lines))
            if credit and random.random() < .6:
                acc.create_voucher(c, dict(kind="receipt", date=day, account_id=cust["account_id"], amount=round(sum(l["qty"] * l["price"] for l in lines) * .8)))
print("أُضيفت البيانات التجريبية")
