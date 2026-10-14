"""محرك المحاسبة: القيد المزدوج، الفواتير، الضريبة، العملات، المستودعات، الأرصدة الافتتاحية، والتقارير.

كل عملية مالية تنتهي بقيد يومية متوازن بالعملة الأساسية، والتقارير كلها تُشتق من جدول
journal_lines فلا يوجد رصيد مخزّن يمكن أن يختلف عن القيود. الحركات بالعملات الأخرى
تُحوَّل بسعر الصرف وقت الحركة ويُحفظ مبلغها الأصلي للعرض في كشوف الحساب.
"""
import json
import sqlite3
from datetime import date as _date, timedelta as _timedelta

from db import SYS, rows

EPS = 0.005
KIND_LABELS = {
    "sale": "فاتورة مبيع",
    "purchase": "فاتورة شراء",
    "sale_return": "مرتجع مبيع",
    "purchase_return": "مرتجع شراء",
}
MANAGED_SOURCES = {**KIND_LABELS, "opening_stock": "بضاعة أول المدة", "opening": "القيد الافتتاحي",
                   "closing": "الإقفال السنوي"}
DEBIT_NATURE = {"asset", "expense"}


class AccountingError(ValueError):
    """خطأ منطقي يُعرض للمستخدم كما هو."""


def r2(x: float) -> float:
    return round(float(x or 0) + 0.0, 2)


def today() -> str:
    return _date.today().isoformat()


def settings(conn) -> dict:
    return dict(conn.execute("SELECT key,value FROM settings").fetchall())


def set_setting(conn, key: str, value):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(value)))


def next_seq(conn, name: str) -> int:
    """رقم مستند جديد من عدّاد محفوظ — لا يتكرر ولو حُذف مستند أو عمل عدة مستخدمين معاً."""
    conn.execute("INSERT OR IGNORE INTO sequences(name,next) VALUES (?,1)", (name,))
    row = conn.execute("UPDATE sequences SET next = next + 1 WHERE name=? RETURNING next - 1", (name,)).fetchone()
    return row[0]


def audit(conn, user_id, action: str, entity: str, entity_id, summary: str, details: dict | None = None):
    """سجل تدقيق لكل عملية مؤثرة: من، ماذا، متى، وما القيم قبل/بعد."""
    name = None
    if user_id:
        row = conn.execute("SELECT full_name FROM users WHERE id=?", (user_id,)).fetchone()
        name = row and row[0]
    conn.execute(
        "INSERT INTO audit_log(user_id,user_name,action,entity,entity_id,summary,details) VALUES (?,?,?,?,?,?,?)",
        (user_id, name, action, entity, entity_id, summary,
         json.dumps(details, ensure_ascii=False, default=str) if details else None))


def list_audit(conn, date_from=None, date_to=None, entity=None, user_id=None, search=None, limit=300) -> list[dict]:
    q, args = "SELECT * FROM audit_log WHERE 1=1", []
    if date_from:
        q += " AND at>=?"; args.append(date_from)
    if date_to:
        q += " AND at<=?"; args.append(date_to + " 23:59:59")
    if entity:
        q += " AND entity=?"; args.append(entity)
    if user_id:
        q += " AND user_id=?"; args.append(int(user_id))
    if search:
        q += " AND (summary LIKE ? OR details LIKE ?)"; args += [f"%{search}%"] * 2
    return rows(conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]))


def lock_date(conn) -> str:
    return settings(conn).get("lock_before_date") or ""


def ensure_unlocked(conn, *dates):
    """يمنع أي حركة بتاريخ داخل فترة مُقفلة (مراجَعة أو مُقفلة سنوياً)."""
    lock = lock_date(conn)
    if not lock:
        return
    for d in dates:
        if d and str(d) <= lock:
            raise AccountingError(f"الفترة حتى {lock} مقفلة ولا يمكن تعديل حركاتها. غيّر تاريخ الإقفال أولاً إن كنت مخوّلاً")


# ───────────────────────── العملات ─────────────────────────

def list_currencies(conn) -> list[dict]:
    return rows(conn.execute("SELECT * FROM currencies ORDER BY is_base DESC, code"))


def base_currency(conn) -> dict:
    return dict(conn.execute("SELECT * FROM currencies WHERE is_base=1").fetchone())


def get_currency(conn, code: str | None) -> dict:
    if not code:
        return base_currency(conn)
    row = conn.execute("SELECT * FROM currencies WHERE code=?", (code,)).fetchone()
    if not row:
        raise AccountingError(f"العملة {code} غير معرّفة")
    return dict(row)


def save_currency(conn, data: dict, new: bool, user_id=None):
    code = (data.get("code") or "").strip().upper()
    name, symbol = (data.get("name") or "").strip(), (data.get("symbol") or "").strip()
    if not code or not name or not symbol:
        raise AccountingError("رمز العملة واسمها وإشارتها مطلوبة")
    rate = float(data.get("rate") or 0)
    if new:
        if conn.execute("SELECT 1 FROM currencies WHERE code=?", (code,)).fetchone():
            raise AccountingError("العملة موجودة مسبقاً")
        if rate <= 0:
            raise AccountingError("سعر الصرف يجب أن يكون أكبر من صفر")
        conn.execute("INSERT INTO currencies(code,name,symbol,rate) VALUES (?,?,?,?)", (code, name, symbol, rate))
        audit(conn, user_id, "create", "currency", None, f"إضافة عملة {code} بسعر صرف {rate:g}")
    else:
        cur = get_currency(conn, code)
        if cur["is_base"]:
            rate = 1
        elif rate <= 0:
            raise AccountingError("سعر الصرف يجب أن يكون أكبر من صفر")
        conn.execute("UPDATE currencies SET name=?,symbol=?,rate=? WHERE code=?", (name, symbol, rate, code))
        if abs(cur["rate"] - rate) > 1e-9:
            audit(conn, user_id, "update", "currency", None, f"تغيير سعر صرف {code}",
                  {"before": cur["rate"], "after": rate})


def delete_currency(conn, code: str, user_id=None):
    cur = get_currency(conn, code)
    if cur["is_base"]:
        raise AccountingError("لا يمكن حذف العملة الأساسية")
    if conn.execute("SELECT 1 FROM invoices WHERE currency=? UNION SELECT 1 FROM journal_lines WHERE currency=?",
                    (code, code)).fetchone():
        raise AccountingError("العملة مستخدمة في حركات سابقة ولا يمكن حذفها")
    audit(conn, user_id, "delete", "currency", None, f"حذف عملة {code}")
    conn.execute("DELETE FROM currencies WHERE code=?", (code,))


def _fx(conn, code: str | None, rate) -> tuple[dict, float]:
    """يعيد العملة وسعر الصرف المعتمد للحركة (السعر المُدخل أو سعر العملة الحالي)."""
    cur = get_currency(conn, code)
    if cur["is_base"]:
        return cur, 1.0
    rate = float(rate or cur["rate"])
    if rate <= 0:
        raise AccountingError("سعر الصرف يجب أن يكون أكبر من صفر")
    return cur, rate


# ───────────────────────── الحسابات ─────────────────────────

def acc_id(conn, code: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE code=?", (code,)).fetchone()
    if not row:
        raise AccountingError(f"الحساب ذو الرمز {code} غير موجود")
    return row["id"]


def get_account(conn, account_id: int) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        raise AccountingError("الحساب غير موجود")
    return dict(row)


def descendant_ids(conn, account_id: int) -> list[int]:
    cur = conn.execute(
        """WITH RECURSIVE t(id) AS (SELECT ? UNION ALL
               SELECT a.id FROM accounts a JOIN t ON a.parent_id = t.id)
           SELECT id FROM t""",
        (account_id,),
    )
    return [r[0] for r in cur.fetchall()]


def next_child_code(conn, parent: dict) -> str:
    kids = conn.execute("SELECT code FROM accounts WHERE parent_id=?", (parent["id"],)).fetchall()
    width = 3 if parent["code"] in (SYS["customers"], SYS["suppliers"]) else 1
    width = max([len(k["code"]) - len(parent["code"]) for k in kids] + [width])
    seq = max([int(k["code"][len(parent["code"]):] or 0) for k in kids if k["code"].isdigit()] + [0]) + 1
    return f"{parent['code']}{seq:0{width}d}"


def create_account(conn, name: str, parent_id: int, is_group: bool = False, is_cash: bool = False, user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise AccountingError("اسم الحساب مطلوب")
    parent = get_account(conn, parent_id)
    if not parent["is_group"]:
        raise AccountingError("لا يمكن إضافة حساب فرعي تحت حساب تفصيلي، اختر حساباً رئيسياً")
    if is_cash and (is_group or parent["type"] != "asset"):
        raise AccountingError("الحساب النقدي يجب أن يكون حساباً تفصيلياً من الأصول")
    code = next_child_code(conn, parent)
    cur = conn.execute(
        "INSERT INTO accounts(code,name,type,parent_id,is_group,is_cash) VALUES (?,?,?,?,?,?)",
        (code, name, parent["type"], parent_id, int(is_group), int(is_cash)),
    )
    a = get_account(conn, cur.lastrowid)
    audit(conn, user_id, "create", "account", a["id"], f"إضافة حساب {a['code']} — {a['name']}")
    return a


def update_account(conn, account_id: int, name: str, is_cash: bool | None = None, user_id=None):
    if not (name or "").strip():
        raise AccountingError("اسم الحساب مطلوب")
    a = get_account(conn, account_id)
    conn.execute("UPDATE accounts SET name=? WHERE id=?", (name.strip(), account_id))
    if is_cash is not None and a["code"] not in (SYS["cash"], SYS["bank"]):
        if is_cash and (a["is_group"] or a["type"] != "asset"):
            raise AccountingError("الحساب النقدي يجب أن يكون حساباً تفصيلياً من الأصول")
        conn.execute("UPDATE accounts SET is_cash=? WHERE id=?", (int(bool(is_cash)), account_id))
    if a["name"] != name.strip():
        audit(conn, user_id, "update", "account", account_id, f"تعديل حساب {a['code']}",
              {"before": a["name"], "after": name.strip()})


def delete_account(conn, account_id: int, user_id=None):
    a = get_account(conn, account_id)
    if a["system"]:
        raise AccountingError("هذا حساب أساسي في النظام ولا يمكن حذفه")
    if conn.execute("SELECT 1 FROM accounts WHERE parent_id=?", (account_id,)).fetchone():
        raise AccountingError("لا يمكن حذف حساب له حسابات فرعية")
    if conn.execute("SELECT 1 FROM journal_lines WHERE account_id=?", (account_id,)).fetchone():
        raise AccountingError("لا يمكن حذف حساب عليه حركات")
    if conn.execute("SELECT 1 FROM parties WHERE account_id=?", (account_id,)).fetchone():
        raise AccountingError("هذا الحساب مرتبط بعميل/مورد، احذف العميل/المورد أولاً")
    audit(conn, user_id, "delete", "account", account_id, f"حذف حساب {a['code']} — {a['name']}")
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def accounts_tree(conn, to_date: str | None = None) -> list[dict]:
    """كل الحسابات مع أرصدتها (الحسابات الرئيسية تجمع أرصدة فروعها)."""
    accs = rows(conn.execute("SELECT * FROM accounts ORDER BY code"))
    q = "SELECT jl.account_id, SUM(jl.debit) d, SUM(jl.credit) c FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id"
    args = []
    if to_date:
        q += " WHERE j.date<=?"
        args.append(to_date)
    sums = {r["account_id"]: r for r in rows(conn.execute(q + " GROUP BY jl.account_id", args))}
    by_id = {a["id"]: a for a in accs}
    for a in accs:
        a["debit"] = a["credit"] = 0.0
    for a in accs:
        s = sums.get(a["id"])
        if not s:
            continue
        node = a
        while node:  # ارفع الحركة إلى كل الآباء
            node["debit"] += s["d"] or 0
            node["credit"] += s["c"] or 0
            node = by_id.get(node["parent_id"])
    for a in accs:
        a["debit"], a["credit"] = r2(a["debit"]), r2(a["credit"])
        a["balance"] = r2(a["debit"] - a["credit"] if a["type"] in DEBIT_NATURE else a["credit"] - a["debit"])
    return accs


# ───────────────────────── القيود ─────────────────────────

def _clean_lines(conn, lines: list[dict]) -> list[tuple]:
    """يتحقق من الأسطر: لا مبالغ سالبة، حسابات تفصيلية، والقيد متوازن."""
    clean = []
    for ln in lines:
        d, c = r2(ln.get("debit")), r2(ln.get("credit"))
        if d < 0 or c < 0:
            raise AccountingError("لا يُسمح بمبالغ سالبة في القيد")
        if d < EPS and c < EPS:
            continue
        if d >= EPS and c >= EPS:
            raise AccountingError("السطر الواحد لا يكون مديناً ودائناً معاً")
        a = get_account(conn, int(ln["account_id"]))
        if a["is_group"]:
            raise AccountingError(f"الحساب «{a['name']}» حساب رئيسي، يجب الترحيل على حساب تفصيلي")
        fx = ln.get("fx_amount")
        clean.append((a["id"], d, c, ln.get("memo"), ln.get("currency") if fx else None, r2(fx) if fx else None,
                      ln.get("cost_center_id") or None))
    if len(clean) < 2:
        raise AccountingError("القيد يحتاج سطرين على الأقل")
    td, tc = sum(x[1] for x in clean), sum(x[2] for x in clean)
    if abs(td - tc) > EPS:
        raise AccountingError(f"القيد غير متوازن: المدين {td:,.2f} ≠ الدائن {tc:,.2f}")
    return clean


def post_journal(conn, date: str, description: str, lines: list[dict],
                 source: str = "manual", source_id: int | None = None, user_id: int | None = None,
                 log: bool = True) -> int:
    """يرحّل قيداً متوازناً. الفواتير والسندات تستدعيه داخلياً (log=False لأنها تسجّل بنفسها)."""
    date = date or today()
    ensure_unlocked(conn, date)
    clean = _clean_lines(conn, lines)
    cur = conn.execute(
        "INSERT INTO journals(number,date,description,source,source_id,created_by) VALUES (?,?,?,?,?,?)",
        (next_seq(conn, "journal"), date, description, source, source_id, user_id),
    )
    jid = cur.lastrowid
    conn.executemany(
        "INSERT INTO journal_lines(journal_id,account_id,debit,credit,memo,currency,fx_amount,cost_center_id) VALUES (?,?,?,?,?,?,?,?)",
        [(jid, *x) for x in clean],
    )
    if log:
        audit(conn, user_id, "create", "journal", jid,
              f"قيد رقم {conn.execute('SELECT number FROM journals WHERE id=?', (jid,)).fetchone()[0]}: {description}",
              {"date": date, "amount": sum(x[1] for x in clean)})
    return jid


def update_journal(conn, jid: int, date: str, description: str, lines: list[dict], user_id=None) -> int:
    """تعديل قيد يدوي مع بقاء رقمه، ويُسجَّل في سجل التدقيق مع القيم قبل/بعد."""
    old = get_journal(conn, jid)
    if old["source"] in MANAGED_SOURCES:
        raise AccountingError(f"هذا القيد ناتج عن «{MANAGED_SOURCES[old['source']]}»، عدّله من شاشته")
    date = date or today()
    ensure_unlocked(conn, old["date"], date)
    clean = _clean_lines(conn, lines)
    conn.execute("UPDATE journals SET date=?, description=? WHERE id=?", (date, description, jid))
    conn.execute("DELETE FROM journal_lines WHERE journal_id=?", (jid,))
    conn.executemany(
        "INSERT INTO journal_lines(journal_id,account_id,debit,credit,memo,currency,fx_amount,cost_center_id) VALUES (?,?,?,?,?,?,?,?)",
        [(jid, *x) for x in clean])
    audit(conn, user_id, "update", "journal", jid, f"تعديل قيد رقم {old['number']}",
          {"before": {"date": old["date"], "description": old["description"],
                      "lines": [{"account": l["account_name"], "debit": l["debit"], "credit": l["credit"]} for l in old["lines"]]},
           "after": {"date": date, "description": description,
                     "lines": [{"account_id": x[0], "debit": x[1], "credit": x[2]} for x in clean]}})
    return jid


def get_journal(conn, jid: int) -> dict:
    j = conn.execute("SELECT j.*, u.full_name user_name FROM journals j LEFT JOIN users u ON u.id=j.created_by WHERE j.id=?",
                     (jid,)).fetchone()
    if not j:
        raise AccountingError("القيد غير موجود")
    j = dict(j)
    j["lines"] = rows(conn.execute(
        """SELECT jl.*, a.code, a.name account_name FROM journal_lines jl
           JOIN accounts a ON a.id=jl.account_id WHERE journal_id=? ORDER BY jl.id""", (jid,)))
    return j


def list_journals(conn, date_from=None, date_to=None, limit=300, sources=None) -> list[dict]:
    q = """SELECT j.*, u.full_name user_name, COALESCE(SUM(jl.debit),0) amount,
                  MAX(jl.currency) fx_currency, MAX(jl.fx_amount) fx_amount
           FROM journals j LEFT JOIN journal_lines jl ON jl.journal_id=j.id
           LEFT JOIN users u ON u.id=j.created_by WHERE 1=1"""
    args = []
    if date_from:
        q += " AND j.date>=?"; args.append(date_from)
    if date_to:
        q += " AND j.date<=?"; args.append(date_to)
    if sources:
        q += f" AND j.source IN ({','.join('?' * len(sources))})"; args += list(sources)
    q += " GROUP BY j.id ORDER BY j.date DESC, j.id DESC LIMIT ?"
    args.append(limit)
    return rows(conn.execute(q, args))


def delete_journal(conn, jid: int, user_id=None):
    j = get_journal(conn, jid)
    if j["source"] in MANAGED_SOURCES:
        raise AccountingError(f"هذا القيد ناتج عن «{MANAGED_SOURCES[j['source']]}»، عدّله أو احذفه من شاشته")
    ensure_unlocked(conn, j["date"])
    audit(conn, user_id, "delete", "journal", jid, f"حذف قيد رقم {j['number']}: {j['description']}",
          {"date": j["date"], "lines": [{"account": l["account_name"], "debit": l["debit"], "credit": l["credit"]} for l in j["lines"]]})
    conn.execute("DELETE FROM journals WHERE id=?", (jid,))


# ───────────────────────── العملاء والموردون ─────────────────────────

def create_party(conn, name: str, kind: str, phone: str = "", address: str = "", user_id=None) -> dict:
    if kind not in ("customer", "supplier"):
        raise AccountingError("النوع يجب أن يكون عميل أو مورد")
    parent = get_account(conn, acc_id(conn, SYS["customers" if kind == "customer" else "suppliers"]))
    acc = create_account(conn, name, parent["id"], user_id=user_id)
    cur = conn.execute(
        "INSERT INTO parties(name,kind,phone,address,account_id) VALUES (?,?,?,?,?)",
        (name.strip(), kind, phone, address, acc["id"]),
    )
    audit(conn, user_id, "create", "party", cur.lastrowid,
          f"إضافة {'عميل' if kind == 'customer' else 'مورد'}: {name.strip()} (حساب {acc['code']})")
    return get_party(conn, cur.lastrowid)


def update_party(conn, pid: int, name: str, phone: str = "", address: str = "", user_id=None, price_list_id=None,
                 credit_limit=None):
    p = get_party(conn, pid)
    conn.execute("UPDATE parties SET name=?,phone=?,address=?,price_list_id=?,credit_limit=? WHERE id=?",
                 (name.strip(), phone, address, price_list_id or None,
                  r2(credit_limit) if credit_limit is not None else p.get("credit_limit") or 0, pid))
    update_account(conn, p["account_id"], name)
    audit(conn, user_id, "update", "party", pid, f"تعديل بيانات: {name.strip()}",
          {"before": {"name": p["name"], "phone": p["phone"], "address": p["address"]},
           "after": {"name": name.strip(), "phone": phone, "address": address}})


def delete_party(conn, pid: int, user_id=None):
    p = get_party(conn, pid)
    if conn.execute("SELECT 1 FROM invoices WHERE party_id=?", (pid,)).fetchone():
        raise AccountingError("لا يمكن الحذف: توجد فواتير لهذا الطرف")
    if conn.execute("SELECT 1 FROM journal_lines WHERE account_id=?", (p["account_id"],)).fetchone():
        raise AccountingError("لا يمكن الحذف: توجد حركات على حساب هذا الطرف")
    audit(conn, user_id, "delete", "party", pid, f"حذف {'عميل' if p['kind'] == 'customer' else 'مورد'}: {p['name']}")
    conn.execute("DELETE FROM parties WHERE id=?", (pid,))
    conn.execute("DELETE FROM accounts WHERE id=?", (p["account_id"],))


def get_party(conn, pid: int) -> dict:
    row = conn.execute("SELECT * FROM parties WHERE id=?", (pid,)).fetchone()
    if not row:
        raise AccountingError("العميل/المورد غير موجود")
    return dict(row)


def list_parties(conn, kind: str | None = None, search: str | None = None) -> list[dict]:
    q = """SELECT p.*, a.code, l.name price_list_name,
             COALESCE(SUM(jl.debit),0) - COALESCE(SUM(jl.credit),0) AS net
           FROM parties p JOIN accounts a ON a.id=p.account_id
           LEFT JOIN price_lists l ON l.id=p.price_list_id
           LEFT JOIN journal_lines jl ON jl.account_id=p.account_id WHERE 1=1"""
    args = []
    if kind:
        q += " AND p.kind=?"; args.append(kind)
    if search:
        q += " AND (p.name LIKE ? OR p.phone LIKE ?)"; args += [f"%{search}%"] * 2
    out = rows(conn.execute(q + " GROUP BY p.id ORDER BY p.name", args))
    for p in out:
        # العميل: الموجب = له ذمة علينا (مدين). المورد: الموجب = نحن مدينون له (دائن).
        p["balance"] = r2(p["net"] if p["kind"] == "customer" else -p["net"])
    return out


# ───────────────────────── المستودعات والمخزون ─────────────────────────

def list_warehouses(conn) -> list[dict]:
    return rows(conn.execute(
        """SELECT w.*, COALESCE(SUM(s.qty * s.avg_cost),0) value, COUNT(CASE WHEN s.qty != 0 THEN 1 END) items
           FROM warehouses w LEFT JOIN item_stock s ON s.warehouse_id=w.id
           GROUP BY w.id ORDER BY w.id"""))


def default_warehouse(conn) -> int:
    return conn.execute("SELECT MIN(id) FROM warehouses WHERE active=1").fetchone()[0]


def get_warehouse(conn, wid) -> dict:
    row = conn.execute("SELECT * FROM warehouses WHERE id=?", (wid,)).fetchone()
    if not row:
        raise AccountingError("المستودع غير موجود")
    return dict(row)


def save_warehouse(conn, name: str, wid: int | None = None, active: bool = True, user_id=None):
    name = (name or "").strip()
    if not name:
        raise AccountingError("اسم المستودع مطلوب")
    if conn.execute("SELECT 1 FROM warehouses WHERE name=? AND id IS NOT ?", (name, wid)).fetchone():
        raise AccountingError("يوجد مستودع بهذا الاسم")
    if wid:
        get_warehouse(conn, wid)
        if not active and conn.execute("SELECT COUNT(*) FROM warehouses WHERE active=1 AND id!=?", (wid,)).fetchone()[0] == 0:
            raise AccountingError("يجب أن يبقى مستودع فعّال واحد على الأقل")
        conn.execute("UPDATE warehouses SET name=?, active=? WHERE id=?", (name, int(active), wid))
        audit(conn, user_id, "update", "warehouse", wid, f"تعديل مستودع: {name}" + ("" if active else " (موقوف)"))
    else:
        cur = conn.execute("INSERT INTO warehouses(name) VALUES (?)", (name,))
        audit(conn, user_id, "create", "warehouse", cur.lastrowid, f"إضافة مستودع: {name}")


def delete_warehouse(conn, wid: int, user_id=None):
    w = get_warehouse(conn, wid)
    if conn.execute("SELECT 1 FROM item_stock WHERE warehouse_id=? AND qty!=0", (wid,)).fetchone():
        raise AccountingError("المستودع يحتوي بضاعة، انقلها أولاً")
    used = conn.execute("""SELECT 1 FROM invoices WHERE warehouse_id=? UNION SELECT 1 FROM transfers WHERE from_wh=? OR to_wh=?
                           UNION SELECT 1 FROM opening_stock WHERE warehouse_id=?""", (wid, wid, wid, wid)).fetchone()
    if used:
        raise AccountingError("للمستودع حركات سابقة، أوقفه بدلاً من حذفه")
    if conn.execute("SELECT COUNT(*) FROM warehouses WHERE id!=?", (wid,)).fetchone()[0] == 0:
        raise AccountingError("لا يمكن حذف المستودع الوحيد")
    audit(conn, user_id, "delete", "warehouse", wid, f"حذف مستودع: {w['name']}")
    conn.execute("DELETE FROM item_stock WHERE warehouse_id=?", (wid,))
    conn.execute("DELETE FROM warehouses WHERE id=?", (wid,))


def wh_qty(conn, item_id: int, wid: int) -> float:
    row = conn.execute("SELECT qty FROM item_stock WHERE item_id=? AND warehouse_id=?", (item_id, wid)).fetchone()
    return row[0] if row else 0.0


def wh_cost(conn, item_id: int, wid: int) -> float:
    """متوسط تكلفة المادة في مستودع محدد (يرجع للمتوسط العام إن لم يكن للمستودع رصيد)."""
    row = conn.execute("SELECT qty, avg_cost FROM item_stock WHERE item_id=? AND warehouse_id=?", (item_id, wid)).fetchone()
    if row and (row["avg_cost"] or row["qty"]):
        return row["avg_cost"]
    g = conn.execute("SELECT avg_cost FROM items WHERE id=?", (item_id,)).fetchone()
    return g[0] if g else 0.0


def _sync_item_totals(conn, item_id: int):
    """يعيد حساب إجمالي الكمية والمتوسط العام للمادة من أرصدة المستودعات."""
    row = conn.execute("""SELECT COALESCE(SUM(qty),0) q, COALESCE(SUM(qty*avg_cost),0) v
                          FROM item_stock WHERE item_id=?""", (item_id,)).fetchone()
    qty, value = row["q"], row["v"]
    avg = (value / qty) if qty > 1e-9 else None
    if avg is None:
        conn.execute("UPDATE items SET qty=? WHERE id=?", (qty, item_id))     # لا رصيد: أبقِ آخر تكلفة معروفة
    else:
        conn.execute("UPDATE items SET qty=?, avg_cost=? WHERE id=?", (qty, max(avg, 0), item_id))


def move_stock(conn, item_id: int, wid: int, delta: float, cost: float | None = None):
    """يحرّك الكمية في مستودع، ومع تحديد التكلفة يُحدَّث متوسط تكلفة المستودع."""
    cur = conn.execute("SELECT qty, avg_cost FROM item_stock WHERE item_id=? AND warehouse_id=?",
                       (item_id, wid)).fetchone()
    old_qty, old_cost = (cur["qty"], cur["avg_cost"]) if cur else (0.0, 0.0)
    new_qty = old_qty + delta
    if cost is None:
        new_cost = old_cost or wh_cost(conn, item_id, wid)
    elif delta > 0:                      # إدخال: متوسط مرجّح
        new_cost = ((old_qty * old_cost + delta * cost) / new_qty) if new_qty > 1e-9 else cost
    else:                                # إخراج بتكلفة محددة (مرتجع شراء أو عكس إدخال)
        new_cost = ((old_qty * old_cost + delta * cost) / new_qty) if new_qty > 1e-9 else old_cost
    if cur:
        conn.execute("UPDATE item_stock SET qty=?, avg_cost=? WHERE item_id=? AND warehouse_id=?",
                     (new_qty, max(new_cost, 0), item_id, wid))
    else:
        conn.execute("INSERT INTO item_stock(item_id,warehouse_id,qty,avg_cost) VALUES (?,?,?,?)",
                     (item_id, wid, new_qty, max(new_cost, 0)))
    _sync_item_totals(conn, item_id)


def _require_stock(conn, item, wid: int, q: float, msg: str = ""):
    if not msg and settings(conn).get("allow_negative_stock") == "1":
        return      # المطاعم غالباً تسمح بالسالب لأن جرد المكوّنات تقريبي
    have = wh_qty(conn, item["id"], wid)
    if q > have + 1e-9:
        wname = get_warehouse(conn, wid)["name"]
        raise AccountingError(msg or f"الكمية المتوفرة من «{item['name']}» في «{wname}» هي {have:g} فقط")


def _add_at_cost(conn, item, wid: int, q: float, unit_cost: float):
    """إدخال كمية بتكلفة معينة إلى مستودع مع تحديث متوسطه المرجّح."""
    move_stock(conn, item["id"], wid, q, unit_cost)


def _remove_at_cost(conn, item, wid: int, q: float, unit_cost: float):
    """إخراج كمية بتكلفة محددة (مرتجع شراء أو عكس إدخال) مع تصحيح متوسط المستودع."""
    move_stock(conn, item["id"], wid, -q, unit_cost)


def _item(conn, item_id) -> dict:
    row = conn.execute("SELECT * FROM items WHERE id=?", (int(item_id),)).fetchone()
    if not row:
        raise AccountingError("مادة غير موجودة")
    return dict(row)


def create_item(conn, data: dict, user_id=None) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise AccountingError("اسم المادة مطلوب")
    code = (data.get("code") or "").strip() or str(next_item_code(conn))
    if conn.execute("SELECT 1 FROM items WHERE code=?", (code,)).fetchone():
        raise AccountingError(f"رمز المادة {code} مستخدم مسبقاً")
    cur = conn.execute(
        "INSERT INTO items(code,name,unit,sale_price,min_qty,barcode,track_batches,is_service) VALUES (?,?,?,?,?,?,?,?)",
        (code, name, data.get("unit") or "قطعة", r2(data.get("sale_price")),
         float(data.get("min_qty") or 0), data.get("barcode") or None, int(bool(data.get("track_batches"))),
         int(bool(data.get("is_service")))),
    )
    conn.execute("UPDATE items SET category=?, station_id=? WHERE id=?",
                 ((data.get("category") or "").strip() or None, data.get("station_id") or None, cur.lastrowid))
    audit(conn, user_id, "create", "item", cur.lastrowid, f"إضافة مادة: {code} — {name}")
    return _item(conn, cur.lastrowid)


def next_item_code(conn) -> int:
    codes = [r[0] for r in conn.execute("SELECT code FROM items").fetchall()]
    return max([int(c) for c in codes if c.isdigit()] + [1000]) + 1


def update_item(conn, iid: int, data: dict, user_id=None):
    if not (data.get("name") or "").strip():
        raise AccountingError("اسم المادة مطلوب")
    old = _item(conn, iid)
    conn.execute(
        "UPDATE items SET name=?,unit=?,sale_price=?,min_qty=?,barcode=?,track_batches=? WHERE id=?",
        (data["name"].strip(), data.get("unit") or "قطعة", r2(data.get("sale_price")),
         float(data.get("min_qty") or 0), data.get("barcode") or None,
         int(bool(data.get("track_batches"))), iid),
    )
    if "is_service" in data:
        if data["is_service"] and old["qty"] > 1e-9:
            raise AccountingError("لا يمكن تحويل مادة لها رصيد مخزني إلى صنف غير مخزني")
        conn.execute("UPDATE items SET is_service=? WHERE id=?", (int(bool(data["is_service"])), iid))
    if "category" in data or "station_id" in data:
        set_item_menu(conn, iid, data)
    new = _item(conn, iid)
    changed = {k: [old[k], new[k]] for k in ("name", "unit", "sale_price", "min_qty", "barcode") if old[k] != new[k]}
    if changed:
        audit(conn, user_id, "update", "item", iid, f"تعديل مادة: {new['code']} — {new['name']}", changed)


def delete_item(conn, iid: int, user_id=None):
    it = _item(conn, iid)
    if conn.execute("SELECT 1 FROM items WHERE parent_id=?", (iid,)).fetchone():
        raise AccountingError("للمادة متغيرات، احذفها أولاً")
    for t in ("invoice_lines", "transfer_lines", "opening_stock", "po_lines", "stocktake_lines"):
        if conn.execute(f"SELECT 1 FROM {t} WHERE item_id=?", (iid,)).fetchone():
            raise AccountingError("لا يمكن حذف مادة لها حركات")
    audit(conn, user_id, "delete", "item", iid, f"حذف مادة: {it['code']} — {it['name']}")
    conn.execute("DELETE FROM items WHERE id=?", (iid,))


def list_items(conn, search: str | None = None, warehouse_id: int | None = None, parent_id=None) -> list[dict]:
    q, args = "SELECT * FROM items WHERE 1=1", []
    if search:
        q += " AND (name LIKE ? OR code LIKE ? OR barcode=?)"
        args += [f"%{search}%", f"%{search}%", search]
    if parent_id is not None:
        q += " AND parent_id=?"; args.append(parent_id)
    out = rows(conn.execute(q + " ORDER BY name", args))
    stock, costs = {}, {}
    for r in conn.execute("SELECT item_id, warehouse_id, qty, avg_cost FROM item_stock WHERE qty != 0"):
        stock.setdefault(r["item_id"], {})[r["warehouse_id"]] = r["qty"]
        costs.setdefault(r["item_id"], {})[r["warehouse_id"]] = r["avg_cost"]
    for it in out:
        it["stock"] = stock.get(it["id"], {})
        it["costs"] = costs.get(it["id"], {})
        if warehouse_id:
            wid = int(warehouse_id)
            it["qty"] = it["stock"].get(wid, 0)
            it["avg_cost"] = it["costs"].get(wid, it["avg_cost"])
        it["value"] = r2(sum(q * it["costs"].get(w, it["avg_cost"]) for w, q in it["stock"].items())
                         if not warehouse_id else it["qty"] * it["avg_cost"])
        it["low"] = it["min_qty"] > 0 and it["qty"] <= it["min_qty"]
    return out


# ───────────────────────── الوحدات وقوائم الأسعار والعروض ─────────────────────────

def item_units(conn, item_id: int) -> list[dict]:
    """وحدات المادة: الوحدة الأساسية (معامل 1) ثم الوحدات الأكبر."""
    it = _item(conn, item_id)
    units = [{"id": None, "name": it["unit"], "factor": 1.0, "barcode": it["barcode"], "price": it["sale_price"], "base": True}]
    for u in rows(conn.execute("SELECT * FROM item_units WHERE item_id=? ORDER BY factor", (item_id,))):
        u["base"] = False
        u["price"] = u["price"] or r2(it["sale_price"] * u["factor"])
        units.append(u)
    return units


def save_item_units(conn, item_id: int, units: list[dict], user_id=None):
    """يستبدل وحدات المادة الإضافية (الوحدة الأساسية تبقى في جدول المواد)."""
    _item(conn, item_id)
    conn.execute("DELETE FROM item_units WHERE item_id=?", (item_id,))
    for u in units or []:
        name, factor = (u.get("name") or "").strip(), float(u.get("factor") or 0)
        if not name or factor <= 0:
            continue
        if abs(factor - 1) < 1e-9:
            raise AccountingError("معامل الوحدة الإضافية يجب أن يختلف عن 1 (الوحدة الأساسية)")
        conn.execute("INSERT INTO item_units(item_id,name,factor,barcode,price) VALUES (?,?,?,?,?)",
                     (item_id, name, factor, (u.get("barcode") or "").strip() or None, r2(u.get("price"))))
    audit(conn, user_id, "update", "item", item_id, f"تعديل وحدات المادة رقم {item_id}")


def list_price_lists(conn) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM price_lists ORDER BY id"))
    for pl in out:
        pl["items"] = rows(conn.execute(
            """SELECT pi.item_id, pi.price, i.code, i.name FROM price_list_items pi
               JOIN items i ON i.id=pi.item_id WHERE price_list_id=? ORDER BY i.name""", (pl["id"],)))
        pl["parties"] = conn.execute("SELECT COUNT(*) FROM parties WHERE price_list_id=?", (pl["id"],)).fetchone()[0]
    return out


def save_price_list(conn, data: dict, pid: int | None = None, user_id=None) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise AccountingError("اسم قائمة الأسعار مطلوب")
    if pid:
        conn.execute("UPDATE price_lists SET name=?, active=? WHERE id=?", (name, int(bool(data.get("active", True))), pid))
    else:
        pid = conn.execute("INSERT INTO price_lists(name) VALUES (?)", (name,)).lastrowid
    conn.execute("DELETE FROM price_list_items WHERE price_list_id=?", (pid,))
    for it in data.get("items", []):
        if it.get("item_id") and float(it.get("price") or 0) > 0:
            conn.execute("INSERT INTO price_list_items(price_list_id,item_id,price) VALUES (?,?,?)",
                         (pid, int(it["item_id"]), r2(it["price"])))
    audit(conn, user_id, "update" if data.get("existing") else "create", "price_list", pid, f"حفظ قائمة أسعار: {name}")
    return pid


def delete_price_list(conn, pid: int, user_id=None):
    row = conn.execute("SELECT name FROM price_lists WHERE id=?", (pid,)).fetchone()
    if not row:
        raise AccountingError("قائمة الأسعار غير موجودة")
    conn.execute("UPDATE parties SET price_list_id=NULL WHERE price_list_id=?", (pid,))
    conn.execute("DELETE FROM price_lists WHERE id=?", (pid,))
    audit(conn, user_id, "delete", "price_list", pid, f"حذف قائمة أسعار: {row[0]}")


def list_promotions(conn, active_only=False, on_date: str | None = None) -> list[dict]:
    q = """SELECT p.*, i.name item_name, i.code item_code FROM promotions p
           LEFT JOIN items i ON i.id=p.item_id WHERE 1=1"""
    args = []
    if active_only:
        q += " AND p.active=1"
    if on_date:
        q += " AND (p.date_from IS NULL OR p.date_from<=?) AND (p.date_to IS NULL OR p.date_to>=?)"
        args += [on_date, on_date]
    return rows(conn.execute(q + " ORDER BY p.id DESC", args))


def save_promotion(conn, data: dict, pid: int | None = None, user_id=None) -> int:
    name = (data.get("name") or "").strip()
    kind = data.get("kind")
    value = float(data.get("value") or 0)
    if not name:
        raise AccountingError("اسم العرض مطلوب")
    if kind not in ("percent", "price"):
        raise AccountingError("نوع العرض غير صحيح")
    if kind == "percent" and not (0 < value <= 100):
        raise AccountingError("نسبة الحسم يجب أن تكون بين 0 و100")
    if kind == "price" and value <= 0:
        raise AccountingError("سعر العرض يجب أن يكون أكبر من صفر")
    if kind == "price" and not data.get("item_id"):
        raise AccountingError("عرض «سعر ثابت» يحتاج تحديد مادة")
    args = (name, data.get("item_id") or None, kind, value, float(data.get("min_qty") or 0),
            data.get("date_from") or None, data.get("date_to") or None, int(bool(data.get("active", True))))
    if pid:
        conn.execute("""UPDATE promotions SET name=?,item_id=?,kind=?,value=?,min_qty=?,date_from=?,date_to=?,active=?
                        WHERE id=?""", args + (pid,))
    else:
        pid = conn.execute("""INSERT INTO promotions(name,item_id,kind,value,min_qty,date_from,date_to,active)
                              VALUES (?,?,?,?,?,?,?,?)""", args).lastrowid
    audit(conn, user_id, "create" if not data.get("existing") else "update", "promotion", pid, f"حفظ عرض: {name}")
    return pid


def delete_promotion(conn, pid: int, user_id=None):
    row = conn.execute("SELECT name FROM promotions WHERE id=?", (pid,)).fetchone()
    if not row:
        raise AccountingError("العرض غير موجود")
    conn.execute("DELETE FROM promotions WHERE id=?", (pid,))
    audit(conn, user_id, "delete", "promotion", pid, f"حذف عرض: {row[0]}")


def effective_price(conn, item_id: int, qty: float = 1, party_id=None, unit_factor: float = 1,
                    on_date: str | None = None, unit_id=None) -> dict:
    """سعر البيع المعتمد: العرض الساري ← قائمة أسعار العميل ← سعر الوحدة الخاص ← سعر المادة × المعامل."""
    it = _item(conn, item_id)
    on_date = on_date or today()
    base = r2(it["sale_price"] * unit_factor)
    source = "سعر المادة"
    if unit_id:
        u = conn.execute("SELECT * FROM item_units WHERE id=? AND item_id=?", (int(unit_id), item_id)).fetchone()
        if u:
            unit_factor = u["factor"]
            base = r2(u["price"]) if u["price"] else r2(it["sale_price"] * u["factor"])
            source = f"سعر وحدة «{u['name']}»" if u["price"] else source
    if party_id:
        pl = conn.execute("""SELECT pi.price FROM parties p JOIN price_list_items pi ON pi.price_list_id=p.price_list_id
                             JOIN price_lists l ON l.id=p.price_list_id AND l.active=1
                             WHERE p.id=? AND pi.item_id=?""", (party_id, item_id)).fetchone()
        if pl:
            base, source = r2(pl[0] * unit_factor), "قائمة أسعار العميل"
    best, promo_name = base, None
    for pr in rows(conn.execute(
            """SELECT * FROM promotions WHERE active=1 AND (item_id IS NULL OR item_id=?)
               AND (date_from IS NULL OR date_from<=?) AND (date_to IS NULL OR date_to>=?)""",
            (item_id, on_date, on_date))):
        if qty < (pr["min_qty"] or 0):
            continue
        cand = r2(pr["value"] * unit_factor) if pr["kind"] == "price" else r2(base * (1 - pr["value"] / 100))
        if cand < best:
            best, promo_name = cand, pr["name"]
    return {"price": best, "list_price": base, "source": promo_name or source, "promo": promo_name}


def parse_barcode(conn, code: str) -> dict | None:
    """يفسّر الباركود: باركود موازين (يحمل الوزن أو السعر) أو باركود مادة/وحدة عادي."""
    code = (code or "").strip()
    if not code:
        return None
    s = settings(conn)
    prefix, mode = (s.get("scale_prefix") or "").strip(), s.get("scale_mode") or "weight"
    if prefix and code.isdigit() and len(code) == 13 and code.startswith(prefix):
        item_code, embedded = code[len(prefix):7], code[7:12]
        it = conn.execute("SELECT * FROM items WHERE code=? OR barcode=?", (item_code.lstrip("0") or item_code, code[:7])).fetchone()
        if it:
            value = int(embedded) / 1000
            out = {"item": dict(it), "unit": None, "scale": True}
            if mode == "weight":
                out["qty"] = value                      # الوزن بالكيلوغرام
            else:
                out["amount"] = int(embedded) / 100     # المبلغ المطبوع على الملصق
            return out
    it = conn.execute("SELECT * FROM items WHERE barcode=? OR code=?", (code, code)).fetchone()
    if it:
        return {"item": dict(it), "unit": None, "scale": False, "qty": 1}
    u = conn.execute("""SELECT u.*, i.id item_id2 FROM item_units u JOIN items i ON i.id=u.item_id
                        WHERE u.barcode=?""", (code,)).fetchone()
    if u:
        return {"item": _item(conn, u["item_id"]), "unit": dict(u), "scale": False, "qty": 1}
    return None


# ───────────────────────── ورديات الكاشير ─────────────────────────

def open_shift(conn, user_id: int, cash_account_id: int, warehouse_id=None, opening_cash: float = 0) -> dict:
    if current_shift(conn, user_id):
        raise AccountingError("لديك وردية مفتوحة بالفعل، أغلقها أولاً")
    a = get_account(conn, int(cash_account_id))
    if not a["is_cash"]:
        raise AccountingError("اختر حساب صندوق أو بنك")
    sid = conn.execute(
        """INSERT INTO shifts(number,user_id,cash_account_id,warehouse_id,opening_cash) VALUES (?,?,?,?,?)""",
        (next_seq(conn, "shift"), user_id, a["id"], warehouse_id, r2(opening_cash))).lastrowid
    audit(conn, user_id, "create", "shift", sid, f"فتح وردية برصيد افتتاحي {r2(opening_cash):,.2f}")
    return get_shift(conn, sid)


def current_shift(conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
                       (user_id,)).fetchone()
    return get_shift(conn, row[0]) if row else None


def get_shift(conn, sid: int) -> dict:
    row = conn.execute("""SELECT s.*, u.full_name user_name, a.name cash_name, w.name warehouse_name FROM shifts s
                          JOIN users u ON u.id=s.user_id JOIN accounts a ON a.id=s.cash_account_id
                          LEFT JOIN warehouses w ON w.id=s.warehouse_id WHERE s.id=?""", (sid,)).fetchone()
    if not row:
        raise AccountingError("الوردية غير موجودة")
    sh = dict(row)
    end = sh["closed_at"] or "9999-12-31"
    # حركة الصندوق أثناء الوردية تُحسب من القيود المرحّلة بين فتحها وإغلاقها
    cash = conn.execute("""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id
                           WHERE jl.account_id=? AND j.created_at>=? AND j.created_at<=?""",
                        (sh["cash_account_id"], sh["opened_at"], end)).fetchone()[0]
    sales = conn.execute("""SELECT COUNT(*), COALESCE(SUM(total_base),0),
                                   COALESCE(SUM(CASE WHEN payment='cash' THEN total_base ELSE 0 END),0),
                                   COALESCE(SUM(CASE WHEN payment='credit' THEN total_base ELSE 0 END),0),
                                   COALESCE(SUM(net_base-cost_total),0)
                            FROM invoices WHERE shift_id=? AND kind='sale'""", (sid,)).fetchone()
    returns = conn.execute("""SELECT COUNT(*), COALESCE(SUM(total_base),0) FROM invoices
                              WHERE shift_id=? AND kind='sale_return'""", (sid,)).fetchone()
    sh.update(cash_movement=r2(cash), expected=r2(sh["opening_cash"] + cash),
              sales_count=sales[0], sales_total=r2(sales[1]), sales_cash=r2(sales[2]), sales_credit=r2(sales[3]),
              profit=r2(sales[4]), returns_count=returns[0], returns_total=r2(returns[1]))
    if sh["closed_at"]:
        sh["expected"] = r2(sh["expected_cash"] or 0)
    return sh


def close_shift(conn, sid: int, counted_cash: float, notes: str = "", user_id=None) -> dict:
    sh = get_shift(conn, sid)
    if sh["closed_at"]:
        raise AccountingError("الوردية مغلقة مسبقاً")
    expected = sh["expected"]
    counted = r2(counted_cash)
    diff = r2(counted - expected)
    jid = None
    if abs(diff) > EPS:   # تسوية العجز أو الزيادة على حساب «عجز وزيادة الصندوق»
        cash_acc, diff_acc = sh["cash_account_id"], acc_id(conn, SYS["cash_diff"])
        lines = ([dict(account_id=diff_acc, debit=abs(diff)), dict(account_id=cash_acc, credit=abs(diff))] if diff < 0
                 else [dict(account_id=cash_acc, debit=diff), dict(account_id=diff_acc, credit=diff)])
        jid = post_journal(conn, today(), f"تسوية {'عجز' if diff < 0 else 'زيادة'} صندوق وردية رقم {sh['number']}",
                           lines, source="shift", source_id=sid, user_id=user_id, log=False)
    conn.execute("""UPDATE shifts SET closed_at=datetime('now','localtime'), counted_cash=?, expected_cash=?,
                    difference=?, notes=?, journal_id=? WHERE id=?""", (counted, expected, diff, notes, jid, sid))
    audit(conn, user_id, "close", "shift", sid,
          f"إغلاق وردية رقم {sh['number']} — المتوقع {expected:,.2f} والمعدود {counted:,.2f}"
          + (f" ({'عجز' if diff < 0 else 'زيادة'} {abs(diff):,.2f})" if abs(diff) > EPS else " (مطابق)"),
          {"expected": expected, "counted": counted, "difference": diff, "sales": sh["sales_total"]})
    return get_shift(conn, sid)


def list_shifts(conn, limit=50) -> list[dict]:
    out = rows(conn.execute("""SELECT s.*, u.full_name user_name, a.name cash_name FROM shifts s
                               JOIN users u ON u.id=s.user_id JOIN accounts a ON a.id=s.cash_account_id
                               ORDER BY s.id DESC LIMIT ?""", (limit,)))
    for sh in out:
        sales = conn.execute("""SELECT COUNT(*), COALESCE(SUM(total_base),0) FROM invoices
                                WHERE shift_id=? AND kind='sale'""", (sh["id"],)).fetchone()
        sh["sales_count"], sh["sales_total"] = sales[0], r2(sales[1])
    return out




# ───────────────────────── الدفعات وتواريخ الصلاحية ─────────────────────────

def _batch_in(conn, item_id: int, wid: int, qty: float, code=None, expiry=None,
              source="invoice", source_id=None, line_id=None):
    """إدخال كمية إلى دفعة (تُدمج مع دفعة مطابقة بالرقم والتاريخ إن وُجدت)."""
    code = (code or "").strip() or None
    expiry = (expiry or "").strip() or None
    row = conn.execute("""SELECT id FROM batches WHERE item_id=? AND warehouse_id=? AND code IS ? AND expiry IS ?""",
                       (item_id, wid, code, expiry)).fetchone()
    bid = row["id"] if row else conn.execute(
        "INSERT INTO batches(item_id,warehouse_id,code,expiry,qty) VALUES (?,?,?,?,0)",
        (item_id, wid, code, expiry)).lastrowid
    conn.execute("UPDATE batches SET qty=qty+? WHERE id=?", (qty, bid))
    conn.execute("INSERT INTO batch_moves(batch_id,invoice_line_id,source,source_id,qty) VALUES (?,?,?,?,?)",
                 (bid, line_id, source, source_id, qty))
    return bid


def _batch_out(conn, item_id: int, wid: int, qty: float, source="invoice", source_id=None, line_id=None) -> list[dict]:
    """إخراج كمية بنظام «الأقرب انتهاءً أولاً» (FEFO) وتسجيل ما خرج من كل دفعة."""
    remaining, taken = qty, []
    batches = rows(conn.execute(
        """SELECT * FROM batches WHERE item_id=? AND warehouse_id=? AND qty > 0
           ORDER BY CASE WHEN expiry IS NULL THEN 1 ELSE 0 END, expiry, id""", (item_id, wid)))
    for b in batches:
        if remaining <= 1e-9:
            break
        take = min(b["qty"], remaining)
        conn.execute("UPDATE batches SET qty=qty-? WHERE id=?", (take, b["id"]))
        conn.execute("INSERT INTO batch_moves(batch_id,invoice_line_id,source,source_id,qty) VALUES (?,?,?,?,?)",
                     (b["id"], line_id, source, source_id, -take))
        taken.append({"batch_id": b["id"], "code": b["code"], "expiry": b["expiry"], "qty": take})
        remaining -= take
    if remaining > 1e-6:   # كمية بلا دفعة (بضاعة قديمة قبل تفعيل التتبّع): تُسجَّل كدفعة مفتوحة بالسالب
        bid = _batch_in(conn, item_id, wid, 0, None, None, source, source_id, line_id)
        conn.execute("UPDATE batches SET qty=qty-? WHERE id=?", (remaining, bid))
        conn.execute("UPDATE batch_moves SET qty=qty-? WHERE batch_id=? AND source=? AND source_id IS ? AND invoice_line_id IS ?",
                     (remaining, bid, source, source_id, line_id))
        taken.append({"batch_id": bid, "code": None, "expiry": None, "qty": remaining})
    return taken


def _batch_revert_line(conn, line_id: int):
    """يعكس كل حركات الدفعات المرتبطة بسطر فاتورة."""
    for m in rows(conn.execute("SELECT * FROM batch_moves WHERE invoice_line_id=?", (line_id,))):
        conn.execute("UPDATE batches SET qty=qty-? WHERE id=?", (m["qty"], m["batch_id"]))
    conn.execute("DELETE FROM batch_moves WHERE invoice_line_id=?", (line_id,))
    conn.execute("DELETE FROM batches WHERE qty=0 AND id NOT IN (SELECT batch_id FROM batch_moves)")


def _batch_revert_source(conn, source: str, source_id: int):
    for m in rows(conn.execute("SELECT * FROM batch_moves WHERE source=? AND source_id=?", (source, source_id))):
        conn.execute("UPDATE batches SET qty=qty-? WHERE id=?", (m["qty"], m["batch_id"]))
    conn.execute("DELETE FROM batch_moves WHERE source=? AND source_id=?", (source, source_id))


def list_batches(conn, item_id=None, warehouse_id=None, only_stock=True) -> list[dict]:
    q = """SELECT b.*, i.name item_name, i.code item_code, i.unit, w.name warehouse_name FROM batches b
           JOIN items i ON i.id=b.item_id JOIN warehouses w ON w.id=b.warehouse_id WHERE 1=1"""
    args = []
    if item_id:
        q += " AND b.item_id=?"; args.append(item_id)
    if warehouse_id:
        q += " AND b.warehouse_id=?"; args.append(warehouse_id)
    if only_stock:
        q += " AND b.qty > 0.000001"
    out = rows(conn.execute(q + " ORDER BY CASE WHEN b.expiry IS NULL THEN 1 ELSE 0 END, b.expiry, b.id", args))
    t = _date.fromisoformat(today())
    for b in out:
        b["days_left"] = (_date.fromisoformat(b["expiry"]) - t).days if b["expiry"] else None
        b["expired"] = b["days_left"] is not None and b["days_left"] < 0
    return out


def expiry_report(conn, days: int | None = None) -> dict:
    """المواد المنتهية أو القريبة من الانتهاء، مع قيمتها بتكلفة مستودعها."""
    days = int(days if days is not None else (settings(conn).get("expiry_alert_days") or 30))
    out = [b for b in list_batches(conn) if b["expiry"]]
    for b in out:
        b["cost"] = r2(wh_cost(conn, b["item_id"], b["warehouse_id"]))
        b["value"] = r2(b["qty"] * b["cost"])
    soon = [b for b in out if b["days_left"] is not None and b["days_left"] <= days]
    return {"days": days, "rows": soon,
            "expired_value": r2(sum(b["value"] for b in soon if b["expired"])),
            "soon_value": r2(sum(b["value"] for b in soon if not b["expired"]))}


# ───────────────────────── الجرد الفعلي ─────────────────────────

def stocktake_sheet(conn, warehouse_id: int, search: str | None = None) -> list[dict]:
    """ورقة الجرد: كمية النظام وتكلفتها في المستودع المحدد."""
    get_warehouse(conn, warehouse_id)
    out = []
    for it in list_items(conn, search):
        qty = it["stock"].get(warehouse_id, 0)
        cost = it["costs"].get(warehouse_id) or wh_cost(conn, it["id"], warehouse_id)
        out.append({"item_id": it["id"], "code": it["code"], "name": it["name"], "unit": it["unit"],
                    "system_qty": r2(qty), "unit_cost": r2(cost), "value": r2(qty * cost),
                    "track_batches": it["track_batches"]})
    return out


def create_stocktake(conn, data: dict, user_id=None) -> dict:
    """يثبّت الجرد الفعلي: يعدّل الكميات ويرحّل فرق القيمة إلى حساب فروقات الجرد."""
    wid = int(data.get("warehouse_id") or default_warehouse(conn))
    get_warehouse(conn, wid)
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    lines = [ln for ln in data.get("lines", []) if ln.get("item_id") and ln.get("counted_qty") is not None]
    if not lines:
        raise AccountingError("أدخل الكميات المعدودة أولاً")
    sid = conn.execute("INSERT INTO stocktakes(number,date,warehouse_id,notes,created_by) VALUES (?,?,?,?,?)",
                       (next_seq(conn, "stocktake"), date, wid, data.get("notes"), user_id)).lastrowid
    diff_value, changed = 0.0, 0
    for ln in lines:
        item = _item(conn, ln["item_id"])
        counted = float(ln["counted_qty"])
        if counted < 0:
            raise AccountingError("الكمية المعدودة لا تكون سالبة")
        system = wh_qty(conn, item["id"], wid)
        cost = wh_cost(conn, item["id"], wid)
        delta = r2(counted - system)
        conn.execute("""INSERT INTO stocktake_lines(stocktake_id,item_id,system_qty,counted_qty,unit_cost)
                        VALUES (?,?,?,?,?)""", (sid, item["id"], system, counted, cost))
        if abs(delta) < 1e-9:
            continue
        changed += 1
        move_stock(conn, item["id"], wid, delta, cost)
        if item["track_batches"]:
            if delta > 0:
                _batch_in(conn, item["id"], wid, delta, f"جرد {date}", None, "stocktake", sid)
            else:
                _batch_out(conn, item["id"], wid, -delta, "stocktake", sid)
        diff_value += delta * cost
    diff_value = r2(diff_value)
    jid = None
    if abs(diff_value) > EPS:
        inv_acc, diff_acc = acc_id(conn, SYS["inventory"]), acc_id(conn, SYS["stock_diff"])
        jl = ([dict(account_id=inv_acc, debit=diff_value), dict(account_id=diff_acc, credit=diff_value)] if diff_value > 0
              else [dict(account_id=diff_acc, debit=-diff_value), dict(account_id=inv_acc, credit=-diff_value)])
        jid = post_journal(conn, date, f"تسوية جرد رقم {conn.execute('SELECT number FROM stocktakes WHERE id=?', (sid,)).fetchone()[0]}",
                           jl, source="stocktake", source_id=sid, user_id=user_id, log=False)
        conn.execute("UPDATE stocktakes SET journal_id=? WHERE id=?", (jid, sid))
    st = get_stocktake(conn, sid)
    audit(conn, user_id, "create", "stocktake", sid,
          f"جرد رقم {st['number']} في «{st['warehouse_name']}» — {changed} مادة بفارق {diff_value:,.2f}")
    return st


def get_stocktake(conn, sid: int) -> dict:
    row = conn.execute("""SELECT s.*, w.name warehouse_name, u.full_name user_name FROM stocktakes s
                          JOIN warehouses w ON w.id=s.warehouse_id LEFT JOIN users u ON u.id=s.created_by
                          WHERE s.id=?""", (sid,)).fetchone()
    if not row:
        raise AccountingError("الجرد غير موجود")
    st = dict(row)
    st["lines"] = rows(conn.execute(
        """SELECT l.*, i.name item_name, i.code item_code, i.unit FROM stocktake_lines l
           JOIN items i ON i.id=l.item_id WHERE stocktake_id=? ORDER BY l.id""", (sid,)))
    for ln in st["lines"]:
        ln["diff"] = r2(ln["counted_qty"] - ln["system_qty"])
        ln["diff_value"] = r2(ln["diff"] * ln["unit_cost"])
    st["diff_value"] = r2(sum(l["diff_value"] for l in st["lines"]))
    st["changed"] = len([l for l in st["lines"] if abs(l["diff"]) > 1e-9])
    return st


def list_stocktakes(conn, limit=50) -> list[dict]:
    out = rows(conn.execute("""SELECT s.*, w.name warehouse_name, u.full_name user_name FROM stocktakes s
                               JOIN warehouses w ON w.id=s.warehouse_id LEFT JOIN users u ON u.id=s.created_by
                               ORDER BY s.id DESC LIMIT ?""", (limit,)))
    for st in out:
        r = conn.execute("""SELECT COUNT(*), COALESCE(SUM((counted_qty-system_qty)*unit_cost),0),
                                   SUM(CASE WHEN ABS(counted_qty-system_qty) > 0.000001 THEN 1 ELSE 0 END)
                            FROM stocktake_lines WHERE stocktake_id=?""", (st["id"],)).fetchone()
        st["lines_count"], st["diff_value"], st["changed"] = r[0], r2(r[1]), r[2] or 0
    return out


# ───────────────────────── أوامر الشراء واقتراح الطلب ─────────────────────────

PO_STATUS = {"draft": "مسودة", "sent": "مرسل للمورد", "received": "مستلم", "canceled": "ملغى"}


def reorder_suggestions(conn, warehouse_id=None, days: int = 30) -> list[dict]:
    """المواد التي بلغت حد الطلب، مع سرعة بيعها وآخر سعر شراء والمورد الأخير."""
    since = (_date.fromisoformat(today()) - _timedelta(days=days)).isoformat()
    out = []
    for it in list_items(conn, warehouse_id=warehouse_id):
        if not (it["min_qty"] > 0 and it["qty"] <= it["min_qty"]):
            continue
        sold = conn.execute("""SELECT COALESCE(SUM(il.qty_base),0) FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id
                               WHERE il.item_id=? AND i.kind='sale' AND i.date>=?""", (it["id"], since)).fetchone()[0]
        last = conn.execute("""SELECT il.price, il.unit_factor, i.date, p.name supplier, p.id supplier_id
                               FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id
                               LEFT JOIN parties p ON p.id=i.party_id
                               WHERE il.item_id=? AND i.kind='purchase' ORDER BY i.date DESC, i.id DESC LIMIT 1""",
                            (it["id"],)).fetchone()
        suggested = max(r2(it["min_qty"] * 2 - it["qty"]), r2(sold / max(days, 1) * 14))   # تكفي أسبوعين
        out.append({"item_id": it["id"], "code": it["code"], "name": it["name"], "unit": it["unit"],
                    "qty": it["qty"], "min_qty": it["min_qty"], "sold": r2(sold),
                    "suggested": r2(max(suggested, 1)),
                    "last_price": r2((last["price"] / (last["unit_factor"] or 1)) if last else it["avg_cost"]),
                    "supplier": last["supplier"] if last else None,
                    "supplier_id": last["supplier_id"] if last else None})
    return sorted(out, key=lambda x: -x["sold"])


def save_po(conn, data: dict, po_id=None, user_id=None) -> dict:
    lines = [ln for ln in data.get("lines", []) if ln.get("item_id") and float(ln.get("qty") or 0) > 0]
    if not lines:
        raise AccountingError("أضف مادة واحدة على الأقل")
    status = data.get("status") or "draft"
    if status not in PO_STATUS:
        raise AccountingError("حالة الأمر غير صحيحة")
    args = (data.get("date") or today(), data.get("supplier_id") or None,
            data.get("warehouse_id") or default_warehouse(conn), status, data.get("expected") or None, data.get("notes"))
    if po_id:
        po = get_po(conn, po_id)
        if po["status"] == "received":
            raise AccountingError("الأمر مستلم ولا يمكن تعديله")
        conn.execute("UPDATE purchase_orders SET date=?,supplier_id=?,warehouse_id=?,status=?,expected=?,notes=? WHERE id=?",
                     args + (po_id,))
        conn.execute("DELETE FROM po_lines WHERE po_id=?", (po_id,))
    else:
        po_id = conn.execute("""INSERT INTO purchase_orders(date,supplier_id,warehouse_id,status,expected,notes,number,created_by)
                                VALUES (?,?,?,?,?,?,?,?)""", args + (next_seq(conn, "po"), user_id)).lastrowid
    for ln in lines:
        _item(conn, ln["item_id"])
        conn.execute("INSERT INTO po_lines(po_id,item_id,qty,price,unit_name,unit_factor) VALUES (?,?,?,?,?,?)",
                     (po_id, int(ln["item_id"]), float(ln["qty"]), r2(ln.get("price")),
                      ln.get("unit_name"), float(ln.get("unit_factor") or 1)))
    po = get_po(conn, po_id)
    audit(conn, user_id, "update" if data.get("existing") else "create", "purchase_order", po_id,
          f"أمر شراء رقم {po['number']} ({PO_STATUS[po['status']]}) — {len(lines)} مادة")
    return po


def get_po(conn, po_id: int) -> dict:
    row = conn.execute("""SELECT o.*, p.name supplier_name, w.name warehouse_name, u.full_name user_name
                          FROM purchase_orders o LEFT JOIN parties p ON p.id=o.supplier_id
                          LEFT JOIN warehouses w ON w.id=o.warehouse_id LEFT JOIN users u ON u.id=o.created_by
                          WHERE o.id=?""", (po_id,)).fetchone()
    if not row:
        raise AccountingError("أمر الشراء غير موجود")
    po = dict(row)
    po["status_label"] = PO_STATUS[po["status"]]
    po["lines"] = rows(conn.execute(
        """SELECT l.*, i.name item_name, i.code item_code, i.unit FROM po_lines l JOIN items i ON i.id=l.item_id
           WHERE po_id=? ORDER BY l.id""", (po_id,)))
    po["total"] = r2(sum(l["qty"] * l["price"] for l in po["lines"]))
    return po


def list_pos(conn, status=None, limit=100) -> list[dict]:
    q = """SELECT o.*, p.name supplier_name, w.name warehouse_name,
                  (SELECT COALESCE(SUM(qty*price),0) FROM po_lines l WHERE l.po_id=o.id) total,
                  (SELECT COUNT(*) FROM po_lines l WHERE l.po_id=o.id) lines_count
           FROM purchase_orders o LEFT JOIN parties p ON p.id=o.supplier_id
           LEFT JOIN warehouses w ON w.id=o.warehouse_id WHERE 1=1"""
    args = []
    if status:
        q += " AND o.status=?"; args.append(status)
    out = rows(conn.execute(q + " ORDER BY o.id DESC LIMIT ?", args + [limit]))
    for po in out:
        po["status_label"] = PO_STATUS[po["status"]]
    return out


def set_po_status(conn, po_id: int, status: str, user_id=None):
    po = get_po(conn, po_id)
    if status not in PO_STATUS:
        raise AccountingError("حالة غير صحيحة")
    if po["status"] == "received" and status != "received":
        raise AccountingError("لا يمكن تغيير حالة أمر مستلم")
    conn.execute("UPDATE purchase_orders SET status=? WHERE id=?", (status, po_id))
    audit(conn, user_id, "update", "purchase_order", po_id, f"أمر شراء رقم {po['number']}: {PO_STATUS[status]}")


def delete_po(conn, po_id: int, user_id=None):
    po = get_po(conn, po_id)
    if po["status"] == "received":
        raise AccountingError("الأمر مستلم وارتبط بفاتورة، لا يمكن حذفه")
    conn.execute("DELETE FROM purchase_orders WHERE id=?", (po_id,))
    audit(conn, user_id, "delete", "purchase_order", po_id, f"حذف أمر شراء رقم {po['number']}")


def receive_po(conn, po_id: int, data: dict, user_id=None) -> dict:
    """تحويل أمر الشراء إلى فاتورة شراء (يمكن تعديل الكميات والأسعار قبل الاستلام)."""
    po = get_po(conn, po_id)
    if po["status"] == "received":
        raise AccountingError("تم استلام هذا الأمر مسبقاً")
    lines = data.get("lines") or [{"item_id": l["item_id"], "qty": l["qty"], "price": l["price"],
                                   "unit_name": l["unit_name"], "unit_factor": l["unit_factor"]} for l in po["lines"]]
    inv = create_invoice(conn, {"kind": "purchase", "date": data.get("date") or today(),
                                "party_id": data.get("supplier_id") or po["supplier_id"],
                                "payment": data.get("payment") or "credit",
                                "cash_account_id": data.get("cash_account_id"),
                                "warehouse_id": data.get("warehouse_id") or po["warehouse_id"],
                                "currency": data.get("currency"), "rate": data.get("rate"),
                                "tax_rate": data.get("tax_rate") or 0, "discount": data.get("discount") or 0,
                                "notes": f"استلام أمر شراء رقم {po['number']}", "lines": lines}, user_id)
    conn.execute("UPDATE purchase_orders SET status='received', invoice_id=? WHERE id=?", (inv["id"], po_id))
    audit(conn, user_id, "update", "purchase_order", po_id,
          f"استلام أمر شراء رقم {po['number']} بفاتورة رقم {inv['number']}")
    return inv


# ───────────────────────── متغيرات المادة ─────────────────────────

def create_variant(conn, parent_id: int, data: dict, user_id=None) -> dict:
    """متغير من مادة (لون/قياس): مادة مستقلة في المخزون مرتبطة بالمادة الأصل."""
    parent = _item(conn, parent_id)
    if parent["parent_id"]:
        raise AccountingError("لا يمكن إنشاء متغير من متغير")
    variant = (data.get("variant") or "").strip()
    if not variant:
        raise AccountingError("اسم المتغير مطلوب (مثل: أحمر، مقاس 42)")
    it = create_item(conn, {"name": f"{parent['name']} - {variant}", "unit": parent["unit"],
                            "sale_price": data.get("sale_price") or parent["sale_price"],
                            "min_qty": data.get("min_qty") or parent["min_qty"],
                            "barcode": data.get("barcode"), "code": data.get("code")}, user_id)
    conn.execute("UPDATE items SET parent_id=?, variant=?, track_batches=? WHERE id=?",
                 (parent_id, variant, parent["track_batches"], it["id"]))
    return _item(conn, it["id"])


def item_variants(conn, parent_id: int) -> list[dict]:
    return list_items(conn, None, None, parent_id=parent_id)


# ───────────────────────── التحويل بين المستودعات ─────────────────────────

def create_transfer(conn, data: dict, user_id=None) -> dict:
    src, dst = int(data.get("from_wh") or 0), int(data.get("to_wh") or 0)
    if src == dst:
        raise AccountingError("اختر مستودعين مختلفين")
    get_warehouse(conn, src); get_warehouse(conn, dst)
    lines = [ln for ln in data.get("lines", []) if ln.get("item_id") and float(ln.get("qty") or 0) > 0]
    if not lines:
        raise AccountingError("أضف مادة واحدة على الأقل")
    ensure_unlocked(conn, data.get("date") or today())
    cur = conn.execute("INSERT INTO transfers(number,date,from_wh,to_wh,notes,created_by) VALUES (?,?,?,?,?,?)",
                       (next_seq(conn, "transfer"), data.get("date") or today(), src, dst, data.get("notes"), user_id))
    tid = cur.lastrowid
    for ln in lines:
        item, q = _item(conn, ln["item_id"]), float(ln["qty"])
        _require_stock(conn, item, src, q)
        cost = wh_cost(conn, item["id"], src)      # التكلفة تنتقل مع البضاعة
        move_stock(conn, item["id"], src, -q, cost)
        move_stock(conn, item["id"], dst, q, cost)
        if item["track_batches"]:                  # الدفعات تنتقل بأرقامها وتواريخها
            for t in _batch_out(conn, item["id"], src, q, "transfer", tid):
                _batch_in(conn, item["id"], dst, t["qty"], t["code"], t["expiry"], "transfer", tid)
        conn.execute("INSERT INTO transfer_lines(transfer_id,item_id,qty) VALUES (?,?,?)", (tid, item["id"], q))
    t = get_transfer(conn, tid)
    audit(conn, user_id, "create", "transfer", tid,
          f"تحويل رقم {t['number']} من «{t['from_name']}» إلى «{t['to_name']}» ({len(t['lines'])} مادة)")
    return t


def get_transfer(conn, tid: int) -> dict:
    row = conn.execute(
        """SELECT t.*, a.name from_name, b.name to_name, u.full_name user_name FROM transfers t
           JOIN warehouses a ON a.id=t.from_wh JOIN warehouses b ON b.id=t.to_wh
           LEFT JOIN users u ON u.id=t.created_by WHERE t.id=?""", (tid,)).fetchone()
    if not row:
        raise AccountingError("التحويل غير موجود")
    t = dict(row)
    t["lines"] = rows(conn.execute(
        "SELECT tl.*, i.name item_name, i.unit FROM transfer_lines tl JOIN items i ON i.id=tl.item_id WHERE transfer_id=?", (tid,)))
    return t


def list_transfers(conn, limit=100) -> list[dict]:
    return rows(conn.execute(
        """SELECT t.*, a.name from_name, b.name to_name, SUM(tl.qty) total_qty, COUNT(tl.id) n FROM transfers t
           JOIN warehouses a ON a.id=t.from_wh JOIN warehouses b ON b.id=t.to_wh
           LEFT JOIN transfer_lines tl ON tl.transfer_id=t.id GROUP BY t.id ORDER BY t.date DESC, t.id DESC LIMIT ?""", (limit,)))


def delete_transfer(conn, tid: int, user_id=None):
    t = get_transfer(conn, tid)
    ensure_unlocked(conn, t["date"])
    _batch_revert_source(conn, "transfer", tid)
    for ln in t["lines"]:
        item = _item(conn, ln["item_id"])
        _require_stock(conn, item, t["to_wh"], ln["qty"], f"لا يمكن الحذف: خرجت كمية من «{item['name']}» من «{t['to_name']}» بعد التحويل")
        cost = wh_cost(conn, item["id"], t["to_wh"])
        move_stock(conn, item["id"], t["to_wh"], -ln["qty"], cost)
        move_stock(conn, item["id"], t["from_wh"], ln["qty"], cost)
    audit(conn, user_id, "delete", "transfer", tid, f"إلغاء تحويل رقم {t['number']}",
          {"from": t["from_name"], "to": t["to_name"], "lines": [{"item": l["item_name"], "qty": l["qty"]} for l in t["lines"]]})
    conn.execute("DELETE FROM transfers WHERE id=?", (tid,))


# ───────────────────────── الفواتير ─────────────────────────

def _money_account(conn, payment: str, party: dict | None, cash_account_id: int | None) -> int:
    if payment == "credit":
        if not party:
            raise AccountingError("الفاتورة الآجلة تحتاج اختيار عميل/مورد")
        return party["account_id"]
    if cash_account_id:
        a = get_account(conn, int(cash_account_id))
        if not a["is_cash"]:
            raise AccountingError("الحساب المختار ليس صندوقاً أو بنكاً")
        return a["id"]
    return acc_id(conn, SYS["cash"])


def _invoice_inputs(conn, data: dict, kind: str) -> dict:
    """يتحقق من رأس الفاتورة ويحسب مبالغها (بعملتها وبالعملة الأساسية)."""
    payment = data.get("payment") or "cash"
    date = data.get("date") or today()
    party = get_party(conn, int(data["party_id"])) if data.get("party_id") else None
    if party:
        want = "customer" if kind in ("sale", "sale_return") else "supplier"
        if party["kind"] != want:
            raise AccountingError("نوع الطرف لا يطابق نوع الفاتورة (عميل للمبيعات، مورد للمشتريات)")
    wid = int(data.get("warehouse_id") or default_warehouse(conn))
    if not get_warehouse(conn, wid)["active"]:
        raise AccountingError("المستودع المختار موقوف")
    currency, rate = _fx(conn, data.get("currency"), data.get("rate"))
    is_base = bool(currency["is_base"])

    lines = [ln for ln in data.get("lines", []) if ln.get("item_id") and float(ln.get("qty") or 0) > 0]
    if not lines:
        raise AccountingError("أضف مادة واحدة على الأقل بكمية أكبر من صفر")
    if any(float(ln.get("price") or 0) < 0 for ln in lines):
        raise AccountingError("لا يُسمح بسعر سالب")
    subtotal = r2(sum(float(ln["qty"]) * float(ln.get("price") or 0) for ln in lines))
    discount = r2(data.get("discount"))
    if discount < 0 or discount > subtotal:
        raise AccountingError("الحسم يجب أن يكون بين صفر وإجمالي الفاتورة")
    tax_rate = float(data.get("tax_rate") or 0)
    if tax_rate < 0 or tax_rate > 100:
        raise AccountingError("نسبة الضريبة غير صحيحة")
    net = r2(subtotal - discount)
    tax = r2(net * tax_rate / 100)
    total = r2(net + tax)
    service = r2(data.get("service_amount"))           # رسم خدمة/توصيل يُضاف بعد الضريبة
    if service < 0:
        raise AccountingError("رسم الخدمة لا يكون سالباً")
    total = r2(total + service)
    net_b, tax_b, service_b = r2(net * rate), r2(tax * rate), r2(service * rate)
    total_b = r2(net_b + tax_b + service_b)
    factor = (net / subtotal) if subtotal else 1.0   # توزيع الحسم على الأسطر
    if kind == "sale" and payment == "credit" and party:
        check_credit_limit(conn, party, total_b)
    return dict(kind=kind, payment=payment, date=date, party=party, wid=wid, currency=currency, rate=rate,
                lines=lines, subtotal=subtotal, discount=discount, tax_rate=tax_rate, tax=tax, total=total,
                net_b=net_b, tax_b=tax_b, total_b=total_b, factor=factor, notes=data.get("notes"),
                cash_account_id=data.get("cash_account_id"), cost_center_id=data.get("cost_center_id") or None,
                salesperson_id=data.get("salesperson_id") or None, service=service, service_b=service_b,
                order_id=data.get("order_id") or None)


def _apply_invoice(conn, inv_id: int, v: dict, user_id=None) -> dict:
    """يطبّق أسطر الفاتورة على المخزون ويولّد قيدها المحاسبي."""
    kind, wid, rate, factor = v["kind"], v["wid"], v["rate"], v["factor"]
    party, currency = v["party"], v["currency"]
    net_b, tax_b, total_b, date = v["net_b"], v["tax_b"], v["total_b"], v["date"]
    cost_total = 0.0

    for ln in v["lines"]:
        item = _item(conn, ln["item_id"])
        q, price = float(ln["qty"]), r2(ln.get("price"))
        ufactor = float(ln.get("unit_factor") or 1)
        if ufactor <= 0:
            raise AccountingError("معامل الوحدة غير صحيح")
        qb = q * ufactor                      # الكمية بالوحدة الأساسية
        components = None
        if item["is_service"]:          # صنف غير مخزني: إيراد بلا حركة مخزون ولا تكلفة
            if kind in ("purchase", "purchase_return"):
                raise AccountingError(f"«{item['name']}» صنف غير مخزني — سجّل تكلفته بسند دفع أو قيد مصروف")
            components = []
        elif item["is_recipe"]:
            if kind in ("purchase", "purchase_return"):
                raise AccountingError(f"«{item['name']}» طبق يُحضَّر من مكوّنات ولا يُشترى — اشترِ مكوّناته")
            components = _consume_recipe(conn, item, wid, qb) if kind == "sale" else []
        elif kind in ("sale", "purchase_return"):
            _require_stock(conn, item, wid, qb)
        if kind == "purchase":
            unit_cost = (price / ufactor) * factor * rate
            _add_at_cost(conn, item, wid, qb, unit_cost)
        elif kind == "purchase_return":
            unit_cost = (price / ufactor) * factor * rate   # تخرج بسعر الإرجاع
            _remove_at_cost(conn, item, wid, qb, unit_cost)
        elif components is not None:   # طبق: تكلفته مجموع مكوّناته، ومرتجعه لا يعيد المكوّنات للمخزون
            unit_cost = (sum(c["qty"] * c["unit_cost"] for c in components) / qb) if qb else 0
        else:  # sale / sale_return: الحركة بمتوسط تكلفة المستودع
            unit_cost = wh_cost(conn, item["id"], wid)
            move_stock(conn, item["id"], wid, -qb if kind == "sale" else qb, unit_cost)
        cost_total += qb * unit_cost
        line_id = conn.execute(
            """INSERT INTO invoice_lines(invoice_id,item_id,qty,price,unit_name,unit_factor,qty_base,unit_cost,total,
                                         batch_code,batch_expiry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (inv_id, item["id"], q, price, ln.get("unit_name"), ufactor, qb, unit_cost, r2(q * price),
             ln.get("batch_code"), ln.get("batch_expiry")),
        ).lastrowid
        for comp in components or []:
            conn.execute("INSERT INTO invoice_line_components(invoice_line_id,item_id,qty,unit_cost) VALUES (?,?,?,?)",
                         (line_id, comp["item_id"], comp["qty"], comp["unit_cost"]))
        if item["track_batches"] and components is None:      # حركة الدفعات: دخول بالشراء وخروج بالبيع
            if kind in ("purchase", "sale_return"):
                _batch_in(conn, item["id"], wid, qb, ln.get("batch_code"), ln.get("batch_expiry"), "invoice", inv_id, line_id)
            else:
                _batch_out(conn, item["id"], wid, qb, "invoice", inv_id, line_id)

    cost_total = r2(cost_total)
    money = _money_account(conn, v["payment"], party, v["cash_account_id"])
    fx = {} if currency["is_base"] else {"currency": currency["code"], "fx_amount": v["total"]}
    A = {k: acc_id(conn, SYS[k]) for k in ("inventory", "cogs", "sales", "sales_returns", "vat_in", "vat_out")}

    service_b = v.get("service_b") or 0
    svc = acc_id(conn, SYS["service_income"])
    if kind == "sale":
        jl = [dict(account_id=money, debit=total_b, **fx), dict(account_id=A["sales"], credit=net_b),
              dict(account_id=A["vat_out"], credit=tax_b), dict(account_id=svc, credit=service_b),
              dict(account_id=A["cogs"], debit=cost_total), dict(account_id=A["inventory"], credit=cost_total)]
    elif kind == "sale_return":
        jl = [dict(account_id=A["sales_returns"], debit=net_b), dict(account_id=A["vat_out"], debit=tax_b),
              dict(account_id=svc, debit=service_b),
              dict(account_id=money, credit=total_b, **fx),
              dict(account_id=A["inventory"], debit=cost_total), dict(account_id=A["cogs"], credit=cost_total)]
    elif kind == "purchase":
        jl = [dict(account_id=A["inventory"], debit=net_b), dict(account_id=A["vat_in"], debit=tax_b),
              dict(account_id=money, credit=total_b, **fx)]
    else:
        jl = [dict(account_id=money, debit=total_b, **fx), dict(account_id=A["inventory"], credit=net_b),
              dict(account_id=A["vat_in"], credit=tax_b)]

    cc = v.get("cost_center_id")
    if cc:
        for line in jl:
            line["cost_center_id"] = cc
    num = conn.execute("SELECT number FROM invoices WHERE id=?", (inv_id,)).fetchone()[0]
    desc = f"{KIND_LABELS[kind]} رقم {num}" + (f" - {party['name']}" if party else "")
    jid = post_journal(conn, date, desc, jl, source=kind, source_id=inv_id, user_id=user_id, log=False)
    conn.execute("UPDATE invoices SET journal_id=?, cost_total=? WHERE id=?", (jid, cost_total, inv_id))
    return get_invoice(conn, inv_id)


def _invoice_summary(inv: dict) -> dict:
    return {"date": inv["date"], "party": inv.get("party_name"), "total": inv["total"], "currency": inv["currency"],
            "warehouse": inv.get("warehouse_name"), "payment": inv["payment"], "tax": inv["tax_amount"],
            "lines": [{"item": l["item_name"], "qty": l["qty"], "price": l["price"]} for l in inv["lines"]]}


def create_invoice(conn, data: dict, user_id=None) -> dict:
    kind = data.get("kind")
    if kind not in KIND_LABELS:
        raise AccountingError("نوع الفاتورة غير صحيح")
    ref = (data.get("client_ref") or "").strip() or None
    if ref:      # الطلب نفسه وصل مرتين (انقطاع اتصال ثم إعادة إرسال): أعد الفاتورة الأولى ولا تكرّرها
        dup = conn.execute("SELECT id FROM invoices WHERE client_ref=?", (ref,)).fetchone()
        if dup:
            return {**get_invoice(conn, dup["id"]), "duplicate": True}
    v = _invoice_inputs(conn, data, kind)
    ensure_unlocked(conn, v["date"])
    shift = current_shift(conn, user_id) if user_id else None
    cur = conn.execute(
        """INSERT INTO invoices(number,kind,date,party_id,payment,subtotal,discount,tax_rate,tax_amount,total,
                                currency,rate,net_base,total_base,warehouse_id,notes,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (next_seq(conn, f"invoice_{kind}"), kind, v["date"], v["party"] and v["party"]["id"], v["payment"],
         v["subtotal"], v["discount"], v["tax_rate"], v["tax"], v["total"], v["currency"]["code"], v["rate"],
         v["net_b"], v["total_b"], v["wid"], v["notes"], user_id))
    conn.execute("UPDATE invoices SET cost_center_id=?, salesperson_id=?, service_amount=?, order_id=?, client_ref=? WHERE id=?",
                 (v["cost_center_id"], v["salesperson_id"], v["service"], v["order_id"], ref, cur.lastrowid))
    if shift and kind in ("sale", "sale_return"):
        conn.execute("UPDATE invoices SET shift_id=? WHERE id=?", (shift["id"], cur.lastrowid))
    inv = _apply_invoice(conn, cur.lastrowid, v, user_id)
    audit(conn, user_id, "create", "invoice", inv["id"],
          f"{KIND_LABELS[kind]} رقم {inv['number']} بمبلغ {inv['total']:,.2f}", _invoice_summary(inv))
    return inv


def update_invoice(conn, inv_id: int, data: dict, user_id=None) -> dict:
    """تعديل فاتورة محفوظة: يعكس أثرها القديم ثم يعيد تطبيقها برقمها نفسه."""
    old = get_invoice(conn, inv_id)
    kind = old["kind"]
    v = _invoice_inputs(conn, data, kind)
    ensure_unlocked(conn, old["date"], v["date"])
    paid = paid_amount(conn, inv_id)
    if paid > v["total_b"] + EPS:
        raise AccountingError(f"المسدَّد على هذه الفاتورة ({paid:,.2f}) أكبر من قيمتها الجديدة، عدّل سندات القبض أولاً")
    _revert_invoice(conn, old)
    conn.execute("DELETE FROM invoice_lines WHERE invoice_id=?", (inv_id,))
    if old["journal_id"]:
        conn.execute("UPDATE invoices SET journal_id=NULL WHERE id=?", (inv_id,))
        conn.execute("DELETE FROM journals WHERE id=?", (old["journal_id"],))
    conn.execute("""UPDATE invoices SET date=?, party_id=?, payment=?, subtotal=?, discount=?, tax_rate=?, tax_amount=?,
                    total=?, currency=?, rate=?, net_base=?, total_base=?, warehouse_id=?, notes=?,
                    cost_center_id=?, salesperson_id=?, service_amount=? WHERE id=?""",
                 (v["date"], v["party"] and v["party"]["id"], v["payment"], v["subtotal"], v["discount"], v["tax_rate"],
                  v["tax"], v["total"], v["currency"]["code"], v["rate"], v["net_b"], v["total_b"], v["wid"],
                  v["notes"], v["cost_center_id"], v["salesperson_id"], v["service"], inv_id))
    inv = _apply_invoice(conn, inv_id, v, user_id)
    audit(conn, user_id, "update", "invoice", inv_id, f"تعديل {KIND_LABELS[kind]} رقم {old['number']}",
          {"before": _invoice_summary(old), "after": _invoice_summary(inv)})
    return inv


def get_invoice(conn, inv_id: int) -> dict:
    row = conn.execute(
        """SELECT i.*, p.name party_name, w.name warehouse_name, u.full_name user_name, c.symbol currency_symbol
           FROM invoices i LEFT JOIN parties p ON p.id=i.party_id LEFT JOIN warehouses w ON w.id=i.warehouse_id
           LEFT JOIN users u ON u.id=i.created_by LEFT JOIN currencies c ON c.code=i.currency WHERE i.id=?""",
        (inv_id,)).fetchone()
    if not row:
        raise AccountingError("الفاتورة غير موجودة")
    inv = dict(row)
    inv["kind_label"] = KIND_LABELS[inv["kind"]]
    inv["profit"] = r2(inv["net_base"] - inv["cost_total"]) if inv["kind"] == "sale" else None
    inv["paid"] = paid_amount(conn, inv_id)
    inv["installments"] = invoice_installments(conn, inv_id)
    inv["outstanding"] = r2(inv["total_base"] - inv["paid"]) if inv["payment"] == "credit" else 0.0
    inv["lines"] = rows(conn.execute(
        """SELECT il.*, it.name item_name, it.code item_code, it.unit FROM invoice_lines il
           JOIN items it ON it.id=il.item_id WHERE invoice_id=? ORDER BY il.id""", (inv_id,)))
    return inv


def count_rows(conn, table: str, date_col: str = "date", date_from=None, date_to=None, extra: str = "", args=()) -> int:
    """عدد السجلات المطابقة (لإظهار «عُرض كذا من كذا» في القوائم الطويلة)."""
    q, a = f"SELECT COUNT(*) FROM {table} WHERE 1=1 {extra}", list(args)
    if date_from:
        q += f" AND {date_col}>=?"; a.append(date_from)
    if date_to:
        q += f" AND {date_col}<=?"; a.append(date_to + (" 23:59:59" if date_col == "at" else ""))
    return conn.execute(q, a).fetchone()[0]


def units_by_item(conn) -> dict[int, list[dict]]:
    """الوحدات الإضافية لكل المواد باستعلام واحد (لشاشات البيع ذات آلاف المواد)."""
    out: dict[int, list[dict]] = {}
    for u in rows(conn.execute("SELECT * FROM item_units ORDER BY factor")):
        out.setdefault(u["item_id"], []).append(u)
    return out


def price_floor(conn, line: dict, party_id=None, rate: float = 1) -> float:
    """أدنى سعر مسموح لسطر بيع دون صلاحية الحسم = السعر المعتمد (عرض/قائمة/وحدة) بعملة الفاتورة."""
    item_id = int(line["item_id"])
    factor = float(line.get("unit_factor") or 1)
    unit_id = None
    if line.get("unit_name") and abs(factor - 1) > 1e-9:
        u = conn.execute("SELECT id FROM item_units WHERE item_id=? AND name=?", (item_id, line["unit_name"])).fetchone()
        unit_id = u and u["id"]
    p = effective_price(conn, item_id, float(line.get("qty") or 1), party_id, factor, None, unit_id)["price"]
    return r2(p / (rate or 1))


def health_check(conn) -> list[dict]:
    """فحص شامل لسلامة البيانات — يُعرض في الإعدادات ليطمئن صاحب العمل."""
    out = []

    def add(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})
    add("سلامة ملف قاعدة البيانات", conn.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    tb = trial_balance(conn)
    add("توازن ميزان المراجعة", tb["balanced"], f"مدين {tb['totals']['debit']:,.2f} / دائن {tb['totals']['credit']:,.2f}")
    bad_j = conn.execute("""SELECT COUNT(*) FROM (SELECT journal_id FROM journal_lines GROUP BY journal_id
                            HAVING ABS(SUM(debit)-SUM(credit)) > 0.01)""").fetchone()[0]
    add("كل قيد متوازن بمفرده", bad_j == 0, f"{bad_j} قيد غير متوازن" if bad_j else "")
    drift = conn.execute("""SELECT COUNT(*) FROM items i WHERE ABS(i.qty - COALESCE(
                            (SELECT SUM(s.qty) FROM item_stock s WHERE s.item_id=i.id),0)) > 0.0005""").fetchone()[0]
    add("تطابق كميات المواد مع أرصدة المستودعات", drift == 0, f"{drift} مادة غير متطابقة" if drift else "")
    neg = conn.execute("SELECT COUNT(*) FROM item_stock WHERE qty < -0.0005").fetchone()[0]
    add("لا توجد كميات سالبة", neg == 0, f"{neg} رصيد سالب — نفّذ جرداً أو أدخل فواتير الشراء الناقصة" if neg else "")
    stock_value = conn.execute("SELECT COALESCE(SUM(qty*avg_cost),0) FROM item_stock").fetchone()[0]
    inv_bal = conn.execute("""SELECT COALESCE(SUM(debit-credit),0) FROM journal_lines WHERE account_id=?""",
                           (acc_id(conn, SYS["inventory"]),)).fetchone()[0]
    gap = r2(inv_bal - stock_value)
    add("تطابق حساب المخزون مع قيمة البضاعة", abs(gap) < max(1.0, abs(stock_value) * 0.001),
        f"حساب المخزون {inv_bal:,.2f} وقيمة البضاعة {stock_value:,.2f} (فرق {gap:,.2f})")
    old_shift = conn.execute("""SELECT COUNT(*) FROM shifts WHERE closed_at IS NULL
                                AND opened_at < datetime('now','localtime','-1 day')""").fetchone()[0]
    add("لا ورديات مفتوحة منذ أكثر من يوم", old_shift == 0, f"{old_shift} وردية لم تُغلق" if old_shift else "")
    old_orders = conn.execute("""SELECT COUNT(*) FROM orders WHERE status='open'
                                 AND opened_at < datetime('now','localtime','-1 day')""").fetchone()[0]
    add("لا طلبات مطعم معلّقة منذ أكثر من يوم", old_orders == 0, f"{old_orders} طلب مفتوح قديم" if old_orders else "")
    no_recipe = conn.execute("""SELECT COUNT(*) FROM items WHERE is_recipe=1
                                AND id NOT IN (SELECT item_id FROM recipes)""").fetchone()[0]
    add("كل منتج مركّب له مكوّنات", no_recipe == 0, f"{no_recipe} منتج بلا مكوّنات" if no_recipe else "")
    return out


def list_invoices(conn, kind=None, date_from=None, date_to=None, party_id=None, limit=300) -> list[dict]:
    q = """SELECT i.*, p.name party_name, c.symbol currency_symbol, c.is_base FROM invoices i
           LEFT JOIN parties p ON p.id=i.party_id LEFT JOIN currencies c ON c.code=i.currency WHERE 1=1"""
    args = []
    for cond, val in (("i.kind=?", kind), ("i.date>=?", date_from), ("i.date<=?", date_to), ("i.party_id=?", party_id)):
        if val:
            q += f" AND {cond}"; args.append(val)
    out = rows(conn.execute(q + " ORDER BY i.date DESC, i.id DESC LIMIT ?", args + [limit]))
    for i in out:
        i["kind_label"] = KIND_LABELS[i["kind"]]
        i["profit"] = r2(i["net_base"] - i["cost_total"]) if i["kind"] == "sale" else None
    return out


def _revert_invoice(conn, inv: dict):
    """يعيد المخزون وحركات الدفعات إلى ما كانت عليه قبل الفاتورة."""
    wid = inv["warehouse_id"]
    for ln in inv["lines"]:
        _batch_revert_line(conn, ln["id"])
        item = _item(conn, ln["item_id"])
        q = ln["qty_base"] if ln.get("qty_base") else ln["qty"]
        if item["is_service"]:
            continue
        if item["is_recipe"]:          # أعد المكوّنات التي استهلكها هذا السطر بتكلفتها المسجّلة
            for comp in rows(conn.execute("SELECT * FROM invoice_line_components WHERE invoice_line_id=?", (ln["id"],))):
                move_stock(conn, comp["item_id"], wid, comp["qty"], comp["unit_cost"])
            conn.execute("DELETE FROM invoice_line_components WHERE invoice_line_id=?", (ln["id"],))
            continue
        if inv["kind"] in ("purchase", "sale_return"):
            _require_stock(conn, item, wid, q, f"لا يمكن الحذف: خرجت كمية من «{item['name']}» بعد هذه الفاتورة")
            if inv["kind"] == "purchase":
                _remove_at_cost(conn, item, wid, q, ln["unit_cost"])
            else:
                move_stock(conn, item["id"], wid, -q, ln["unit_cost"])
        elif inv["kind"] == "purchase_return":
            _add_at_cost(conn, item, wid, q, ln["unit_cost"])
        else:
            move_stock(conn, item["id"], wid, q, ln["unit_cost"])


def delete_invoice(conn, inv_id: int, user_id=None):
    """يحذف الفاتورة ويعكس أثرها على المخزون والقيود."""
    inv = get_invoice(conn, inv_id)
    ensure_unlocked(conn, inv["date"])
    if paid_amount(conn, inv_id) > EPS:
        raise AccountingError("توجد دفعات مخصَّصة لهذه الفاتورة، ألغِ تخصيصها من السند أولاً")
    _revert_invoice(conn, inv)
    audit(conn, user_id, "delete", "invoice", inv_id,
          f"حذف {KIND_LABELS[inv['kind']]} رقم {inv['number']} بمبلغ {inv['total']:,.2f}", _invoice_summary(inv))
    conn.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    if inv["journal_id"]:
        conn.execute("DELETE FROM journals WHERE id=?", (inv["journal_id"],))


# ───────────────────────── السندات ─────────────────────────

def paid_amount(conn, invoice_id: int) -> float:
    """مجموع ما خُصِّص من دفعات لهذه الفاتورة (بالعملة الأساسية)."""
    r = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE invoice_id=?", (invoice_id,)).fetchone()
    return r2(r[0])


def open_invoices(conn, party_id: int) -> list[dict]:
    """فواتير الطرف الآجلة غير المسدَّدة بالكامل، الأقدم أولاً."""
    out = rows(conn.execute(
        """SELECT i.id, i.number, i.kind, i.date, i.total_base,
                  COALESCE((SELECT SUM(amount) FROM payment_allocations a WHERE a.invoice_id=i.id),0) paid
           FROM invoices i WHERE i.party_id=? AND i.payment='credit' AND i.kind IN ('sale','purchase')
           ORDER BY i.date, i.id""", (party_id,)))
    for i in out:
        i["paid"] = r2(i["paid"])
        i["outstanding"] = r2(i["total_base"] - i["paid"])
        i["kind_label"] = KIND_LABELS[i["kind"]]
    return [i for i in out if i["outstanding"] > EPS]


def _save_allocations(conn, jid: int, allocations, limit: float, party_id):
    """يربط مبالغ السند بفواتير محددة بعد التحقق من الحدود."""
    conn.execute("DELETE FROM payment_allocations WHERE journal_id=?", (jid,))
    total = 0.0
    for a in allocations or []:
        amount = r2(a.get("amount"))
        if amount <= 0:
            continue
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (int(a["invoice_id"]),)).fetchone()
        if not inv:
            raise AccountingError("فاتورة غير موجودة في تخصيص الدفعة")
        if party_id and inv["party_id"] != party_id:
            raise AccountingError("لا يمكن تخصيص الدفعة لفاتورة طرف آخر")
        already = paid_amount(conn, inv["id"]) - r2(conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payment_allocations WHERE journal_id=? AND invoice_id=?",
            (jid, inv["id"])).fetchone()[0])
        if amount > inv["total_base"] - already + EPS:
            raise AccountingError(f"المبلغ المخصَّص للفاتورة رقم {inv['number']} أكبر من المتبقي عليها")
        total += amount
        conn.execute("INSERT INTO payment_allocations(journal_id,invoice_id,amount) VALUES (?,?,?)",
                     (jid, inv["id"], amount))
    if total > limit + EPS:
        raise AccountingError(f"مجموع المخصَّص للفواتير ({total:,.2f}) أكبر من مبلغ السند ({limit:,.2f})")


def create_voucher(conn, data: dict, user_id=None) -> int:
    """سند قبض: من حساب (عميل/إيراد) إلى الصندوق. سند دفع: من الصندوق إلى حساب (مورد/مصروف)."""
    kind = data.get("kind")
    if kind not in ("receipt", "payment"):
        raise AccountingError("نوع السند غير صحيح")
    amount = r2(data.get("amount"))
    if amount <= 0:
        raise AccountingError("المبلغ يجب أن يكون أكبر من صفر")
    currency, rate = _fx(conn, data.get("currency"), data.get("rate"))
    base_amount = r2(amount * rate)
    fx = {} if currency["is_base"] else {"currency": currency["code"], "fx_amount": amount}
    cash = _money_account(conn, "cash", None, data.get("cash_account_id"))
    other = int(data["account_id"])
    if cash == other:
        raise AccountingError("لا يمكن أن يكون الحسابان نفس الحساب")
    a = get_account(conn, other)
    label = "سند قبض" if kind == "receipt" else "سند دفع"
    desc = data.get("description") or f"{label} - {a['name']}"
    if not currency["is_base"]:
        desc += f" ({amount:,.2f} {currency['symbol']} × {rate:g})"
    if kind == "receipt":
        lines = [dict(account_id=cash, debit=base_amount, **fx), dict(account_id=other, credit=base_amount, **fx)]
    else:
        lines = [dict(account_id=other, debit=base_amount, **fx), dict(account_id=cash, credit=base_amount, **fx)]
    jid = post_journal(conn, data.get("date") or today(), desc, lines, source=kind, user_id=user_id, log=False)
    party = conn.execute("SELECT id FROM parties WHERE account_id=?", (other,)).fetchone()
    _save_allocations(conn, jid, data.get("allocations"), base_amount, party and party["id"])
    audit(conn, user_id, "create", "voucher", jid,
          f"{label} رقم {conn.execute('SELECT number FROM journals WHERE id=?', (jid,)).fetchone()[0]} بمبلغ {base_amount:,.2f} — {a['name']}")
    return jid


def update_voucher(conn, jid: int, data: dict, user_id=None) -> int:
    """تعديل سند محفوظ برقمه نفسه (المبلغ، الحساب، التاريخ، والتخصيص على الفواتير)."""
    old = get_journal(conn, jid)
    if old["source"] not in ("receipt", "payment"):
        raise AccountingError("هذا المستند ليس سند قبض أو دفع")
    ensure_unlocked(conn, old["date"], data.get("date") or today())
    data = {**data, "kind": old["source"]}
    amount = r2(data.get("amount"))
    if amount <= 0:
        raise AccountingError("المبلغ يجب أن يكون أكبر من صفر")
    currency, rate = _fx(conn, data.get("currency"), data.get("rate"))
    base_amount = r2(amount * rate)
    fx = {} if currency["is_base"] else {"currency": currency["code"], "fx_amount": amount}
    cash = _money_account(conn, "cash", None, data.get("cash_account_id"))
    other = int(data["account_id"])
    if cash == other:
        raise AccountingError("لا يمكن أن يكون الحسابان نفس الحساب")
    acct = get_account(conn, other)
    label = "سند قبض" if old["source"] == "receipt" else "سند دفع"
    desc = data.get("description") or f"{label} - {acct['name']}"
    if old["source"] == "receipt":
        lines = [dict(account_id=cash, debit=base_amount, **fx), dict(account_id=other, credit=base_amount, **fx)]
    else:
        lines = [dict(account_id=other, debit=base_amount, **fx), dict(account_id=cash, credit=base_amount, **fx)]
    clean = _clean_lines(conn, lines)
    conn.execute("UPDATE journals SET date=?, description=? WHERE id=?", (data.get("date") or today(), desc, jid))
    conn.execute("DELETE FROM journal_lines WHERE journal_id=?", (jid,))
    conn.executemany(
        "INSERT INTO journal_lines(journal_id,account_id,debit,credit,memo,currency,fx_amount,cost_center_id) VALUES (?,?,?,?,?,?,?,?)",
        [(jid, *x) for x in clean])
    party = conn.execute("SELECT id FROM parties WHERE account_id=?", (other,)).fetchone()
    _save_allocations(conn, jid, data.get("allocations"), base_amount, party and party["id"])
    audit(conn, user_id, "update", "voucher", jid, f"تعديل {label} رقم {old['number']}",
          {"before": {"date": old["date"], "description": old["description"],
                      "amount": sum(l["debit"] for l in old["lines"])},
           "after": {"date": data.get("date"), "description": desc, "amount": base_amount}})
    return jid


def voucher_allocations(conn, jid: int) -> list[dict]:
    return rows(conn.execute(
        """SELECT a.invoice_id, a.amount, i.number, i.kind, i.date, i.total_base
           FROM payment_allocations a JOIN invoices i ON i.id=a.invoice_id WHERE a.journal_id=?""", (jid,)))


def aging_report(conn, kind: str = "customer", as_of: str | None = None) -> dict:
    """أعمار الديون: توزيع أرصدة العملاء أو الموردين حسب عمر الفواتير غير المسدَّدة."""
    as_of = as_of or today()
    buckets = [(0, 30, "0-30 يوم"), (31, 60, "31-60 يوم"), (61, 90, "61-90 يوم"), (91, 10 ** 6, "أكثر من 90 يوم")]
    inv_kind = "sale" if kind == "customer" else "purchase"
    data = rows(conn.execute(
        """SELECT p.id, p.name, p.phone, i.id inv_id, i.number, i.date, i.total_base,
                  COALESCE((SELECT SUM(a.amount) FROM payment_allocations a WHERE a.invoice_id=i.id),0) paid
           FROM parties p JOIN invoices i ON i.party_id=p.id
           WHERE p.kind=? AND i.kind=? AND i.payment='credit' AND i.date<=?
           ORDER BY p.name, i.date""", (kind, inv_kind, as_of)))
    by_party: dict = {}
    d0 = _date.fromisoformat(as_of)
    for r in data:
        out = r2(r["total_base"] - r["paid"])
        if out <= EPS:
            continue
        age = (d0 - _date.fromisoformat(r["date"])).days
        label = next(b[2] for b in buckets if b[0] <= age <= b[1])
        p = by_party.setdefault(r["id"], {"id": r["id"], "name": r["name"], "phone": r["phone"],
                                          "total": 0.0, "oldest_days": 0, **{b[2]: 0.0 for b in buckets}})
        p[label] = r2(p[label] + out)
        p["total"] = r2(p["total"] + out)
        p["oldest_days"] = max(p["oldest_days"], age)
    parties_list = sorted(by_party.values(), key=lambda x: -x["total"])
    # دفعات مستلمة لم تُخصَّص لفاتورة بعد
    unalloc = rows(conn.execute(
        """SELECT p.id, p.name, SUM(CASE WHEN ? = 'customer' THEN jl.credit - jl.debit ELSE jl.debit - jl.credit END) amount
           FROM journals j JOIN journal_lines jl ON jl.journal_id=j.id
           JOIN parties p ON p.account_id=jl.account_id
           WHERE j.source IN ('receipt','payment') AND p.kind=? AND j.date<=?
             AND COALESCE((SELECT SUM(a.amount) FROM payment_allocations a WHERE a.journal_id=j.id),0) = 0
           GROUP BY p.id""", (kind, kind, as_of)))
    return {"as_of": as_of, "kind": kind, "buckets": [b[2] for b in buckets], "parties": parties_list,
            "totals": {b[2]: r2(sum(p[b[2]] for p in parties_list)) for b in buckets},
            "grand_total": r2(sum(p["total"] for p in parties_list)),
            "unallocated": [u for u in unalloc if abs(u["amount"] or 0) > EPS]}


def close_year(conn, date_to: str, retained_account_id: int | None = None, user_id=None) -> dict:
    """الإقفال السنوي: ترحيل أرصدة الإيرادات والمصروفات إلى الأرباح المحتجزة ثم قفل الفترة."""
    s = settings(conn)
    date_from = s.get("last_closing_date") or None
    inc = income_statement(conn, date_from and (_date.fromisoformat(date_from) + _timedelta(days=1)).isoformat(), date_to)
    retained = get_account(conn, int(retained_account_id or acc_id(conn, "32")))
    if retained["is_group"] or retained["type"] != "equity":
        raise AccountingError("حساب الأرباح المحتجزة يجب أن يكون حساباً تفصيلياً من حقوق الملكية")
    lines = []
    for r in inc["revenue"]:
        if abs(r["amount"]) > EPS:
            lines.append(dict(account_id=acc_id(conn, r["code"]), debit=max(r["amount"], 0), credit=max(-r["amount"], 0)))
    for e in inc["expense"]:
        if abs(e["amount"]) > EPS:
            lines.append(dict(account_id=acc_id(conn, e["code"]), credit=max(e["amount"], 0), debit=max(-e["amount"], 0)))
    if not lines:
        raise AccountingError("لا توجد أرصدة إيرادات أو مصروفات لإقفالها في هذه الفترة")
    profit = inc["net_profit"]
    lines.append(dict(account_id=retained["id"], credit=max(profit, 0), debit=max(-profit, 0),
                      memo="نتيجة أعمال الفترة"))
    jid = post_journal(conn, date_to, f"قيد الإقفال السنوي حتى {date_to}", lines, source="closing", user_id=user_id, log=False)
    set_setting(conn, "last_closing_date", date_to)
    set_setting(conn, "lock_before_date", date_to)
    audit(conn, user_id, "close", "period", jid,
          f"إقفال سنوي حتى {date_to} — نتيجة الفترة {profit:,.2f}", {"net_profit": profit, "from": date_from})
    return {"journal_id": jid, "net_profit": profit, "locked_until": date_to}


def set_lock_date(conn, date: str | None, user_id=None) -> str:
    date = (date or "").strip()
    if date:
        _date.fromisoformat(date)   # يتحقق من صحة التاريخ
    old = lock_date(conn)
    set_setting(conn, "lock_before_date", date)
    audit(conn, user_id, "update", "period", None,
          f"تغيير تاريخ إقفال القيود من «{old or 'بدون'}» إلى «{date or 'بدون'}»")
    return date


# ───────────────────────── الأرصدة الافتتاحية ─────────────────────────

def get_opening(conn) -> dict:
    j = conn.execute("SELECT id, date FROM journals WHERE source='opening' ORDER BY id DESC LIMIT 1").fetchone()
    lines = rows(conn.execute("SELECT account_id, debit, credit FROM journal_lines WHERE journal_id=?", (j["id"],))) if j else []
    js = conn.execute("SELECT date FROM journals WHERE source='opening_stock' ORDER BY id DESC LIMIT 1").fetchone()
    stock = rows(conn.execute(
        """SELECT o.*, i.name item_name, i.code item_code, i.unit, w.name warehouse_name FROM opening_stock o
           JOIN items i ON i.id=o.item_id JOIN warehouses w ON w.id=o.warehouse_id ORDER BY o.id"""))
    s = settings(conn)
    return {"date": j["date"] if j else (js["date"] if js else today()[:4] + "-01-01"),
            "stock_date": js["date"] if js else None,
            "plug_account_id": int(s.get("opening_plug_account") or acc_id(conn, SYS["capital"])),
            "lines": lines, "stock": stock}


def _plug(conn, plug_account_id) -> dict:
    a = get_account(conn, int(plug_account_id or acc_id(conn, SYS["capital"])))
    if a["is_group"] or a["type"] != "equity":
        raise AccountingError("حساب الفرق يجب أن يكون حساباً تفصيلياً من حقوق الملكية (مثل رأس المال)")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('opening_plug_account',?)", (str(a["id"]),))
    return a


def save_opening_balances(conn, data: dict, user_id=None) -> dict:
    """يستبدل القيد الافتتاحي بالكامل. الفرق بين المدين والدائن يذهب إلى حساب الفرق (رأس المال عادةً)."""
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    plug = _plug(conn, data.get("plug_account_id"))
    inv_acc = acc_id(conn, SYS["inventory"])
    lines, seen = [], set()
    for ln in data.get("lines", []):
        d, c = r2(ln.get("debit")), r2(ln.get("credit"))
        if not d and not c:
            continue
        aid = int(ln["account_id"])
        if aid in seen:
            raise AccountingError("الحساب مكرر في الأرصدة الافتتاحية")
        seen.add(aid)
        if aid == inv_acc:
            raise AccountingError("رصيد المخزون يُدخل من تبويب «بضاعة أول المدة» وليس هنا")
        if aid == plug["id"]:
            raise AccountingError("لا تُدخل رصيداً لحساب الفرق نفسه، يُحسب تلقائياً")
        lines.append(dict(account_id=aid, debit=d, credit=c))
    conn.execute("DELETE FROM journals WHERE source='opening'")
    if not lines:
        return {"journal_id": None, "difference": 0}
    diff = r2(sum(l["debit"] for l in lines) - sum(l["credit"] for l in lines))
    if diff:
        lines.append(dict(account_id=plug["id"], debit=max(-diff, 0), credit=max(diff, 0), memo="فرق الأرصدة الافتتاحية"))
    jid = post_journal(conn, date, "القيد الافتتاحي", lines, source="opening", user_id=user_id, log=False)
    audit(conn, user_id, "update", "opening", jid, f"حفظ الأرصدة الافتتاحية بتاريخ {date} ({len(lines)} حساب)",
          {"difference": diff})
    return {"journal_id": jid, "difference": diff}


def save_opening_stock(conn, data: dict, user_id=None) -> dict:
    """يستبدل بضاعة أول المدة: يعكس الكميات السابقة ثم يُدخل الجديدة ويرحّل قيمتها."""
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    plug = _plug(conn, data.get("plug_account_id"))
    for old in rows(conn.execute("SELECT * FROM opening_stock")):
        item = _item(conn, old["item_id"])
        _require_stock(conn, item, old["warehouse_id"], old["qty"],
                       f"لا يمكن تعديل بضاعة أول المدة: بيعت أو نُقلت كمية من «{item['name']}» منها. عدّل بفاتورة أو مرتجع بدلاً من ذلك")
        _remove_at_cost(conn, item, old["warehouse_id"], old["qty"], old["unit_cost"])
    conn.execute("DELETE FROM opening_stock")
    conn.execute("DELETE FROM journals WHERE source='opening_stock'")
    total = 0.0
    for ln in data.get("lines", []):
        q, cost = float(ln.get("qty") or 0), float(ln.get("unit_cost") or 0)
        if q <= 0:
            continue
        if cost < 0:
            raise AccountingError("التكلفة لا تكون سالبة")
        item = _item(conn, ln["item_id"])
        wid = int(ln.get("warehouse_id") or default_warehouse(conn))
        get_warehouse(conn, wid)
        _add_at_cost(conn, item, wid, q, cost)
        conn.execute("INSERT INTO opening_stock(item_id,warehouse_id,qty,unit_cost) VALUES (?,?,?,?)", (item["id"], wid, q, cost))
        total += q * cost
    total = r2(total)
    if total > 0:
        post_journal(conn, date, "بضاعة أول المدة", [dict(account_id=acc_id(conn, SYS["inventory"]), debit=total),
                                                      dict(account_id=plug["id"], credit=total)],
                     source="opening_stock", user_id=user_id, log=False)
    audit(conn, user_id, "update", "opening_stock", None, f"حفظ بضاعة أول المدة بقيمة {total:,.2f}")
    return {"total": total}




# ───────────────────────── مراكز الكلفة ─────────────────────────

def list_cost_centers(conn, with_totals=False, date_from=None, date_to=None) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM cost_centers ORDER BY id"))
    if not with_totals:
        return out
    cond, args = "", []
    if date_from:
        cond += " AND j.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND j.date<=?"; args.append(date_to)
    for c in out:
        r = conn.execute(f"""SELECT
              COALESCE(SUM(CASE WHEN a.type='revenue' THEN jl.credit-jl.debit ELSE 0 END),0) revenue,
              COALESCE(SUM(CASE WHEN a.type='expense' THEN jl.debit-jl.credit ELSE 0 END),0) expense
            FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id JOIN accounts a ON a.id=jl.account_id
            WHERE jl.cost_center_id=? {cond}""", [c["id"]] + args).fetchone()
        c["revenue"], c["expense"] = r2(r["revenue"]), r2(r["expense"])
        c["profit"] = r2(c["revenue"] - c["expense"])
    return out


def save_cost_center(conn, name: str, cid=None, active=True, user_id=None) -> int:
    name = (name or "").strip()
    if not name:
        raise AccountingError("اسم مركز الكلفة مطلوب")
    if conn.execute("SELECT 1 FROM cost_centers WHERE name=? AND id IS NOT ?", (name, cid)).fetchone():
        raise AccountingError("يوجد مركز كلفة بهذا الاسم")
    if cid:
        conn.execute("UPDATE cost_centers SET name=?, active=? WHERE id=?", (name, int(active), cid))
    else:
        cid = conn.execute("INSERT INTO cost_centers(name) VALUES (?)", (name,)).lastrowid
    audit(conn, user_id, "update" if cid else "create", "cost_center", cid, f"حفظ مركز كلفة: {name}")
    return cid


def delete_cost_center(conn, cid: int, user_id=None):
    row = conn.execute("SELECT name FROM cost_centers WHERE id=?", (cid,)).fetchone()
    if not row:
        raise AccountingError("مركز الكلفة غير موجود")
    if conn.execute("SELECT 1 FROM journal_lines WHERE cost_center_id=?", (cid,)).fetchone():
        raise AccountingError("على مركز الكلفة حركات، أوقفه بدلاً من حذفه")
    conn.execute("DELETE FROM cost_centers WHERE id=?", (cid,))
    audit(conn, user_id, "delete", "cost_center", cid, f"حذف مركز كلفة: {row[0]}")


# ───────────────────────── الشيكات والأوراق التجارية ─────────────────────────

CHECK_STATUS = {"pending": "قيد التحصيل", "settled": "محصَّل/مدفوع", "bounced": "مرتجع", "canceled": "ملغى"}


def create_check(conn, data: dict, user_id=None) -> dict:
    """شيك وارد: من العميل إلى أوراق القبض. شيك صادر: من المورد إلى أوراق الدفع."""
    kind = data.get("kind")
    if kind not in ("in", "out"):
        raise AccountingError("نوع الشيك غير صحيح")
    amount = r2(data.get("amount"))
    if amount <= 0:
        raise AccountingError("مبلغ الشيك يجب أن يكون أكبر من صفر")
    due = data.get("due_date") or today()
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    party = get_party(conn, int(data["party_id"])) if data.get("party_id") else None
    if not party:
        raise AccountingError("اختر العميل أو المورد صاحب الشيك")
    currency, rate = _fx(conn, data.get("currency"), data.get("rate"))
    base = r2(amount * rate)
    fx = {} if currency["is_base"] else {"currency": currency["code"], "fx_amount": amount}
    notes_acc = acc_id(conn, SYS["notes_receivable" if kind == "in" else "notes_payable"])
    label = "شيك وارد" if kind == "in" else "شيك صادر"
    cid = conn.execute(
        """INSERT INTO checks(number,kind,check_number,bank,due_date,amount,currency,rate,party_id,notes,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (next_seq(conn, "check"), kind, data.get("check_number"), data.get("bank"), due, amount,
         currency["code"], rate, party["id"], data.get("notes"), user_id)).lastrowid
    num = conn.execute("SELECT number FROM checks WHERE id=?", (cid,)).fetchone()[0]
    desc = f"{label} رقم {data.get('check_number') or num} - {party['name']} (استحقاق {due})"
    if kind == "in":
        jl = [dict(account_id=notes_acc, debit=base, **fx), dict(account_id=party["account_id"], credit=base, **fx)]
    else:
        jl = [dict(account_id=party["account_id"], debit=base, **fx), dict(account_id=notes_acc, credit=base, **fx)]
    jid = post_journal(conn, date, desc, jl, source="check", source_id=cid, user_id=user_id, log=False)
    conn.execute("UPDATE checks SET journal_id=? WHERE id=?", (jid, cid))
    audit(conn, user_id, "create", "check", cid, f"{label} رقم {num} بمبلغ {base:,.2f} — {party['name']}")
    return get_check(conn, cid)


def settle_check(conn, cid: int, data: dict, user_id=None) -> dict:
    """تحصيل شيك وارد أو صرف شيك صادر (يحرّك النقد ويقفل أوراق القبض/الدفع)."""
    ch = get_check(conn, cid)
    if ch["status"] != "pending":
        raise AccountingError(f"الشيك {CHECK_STATUS[ch['status']]} مسبقاً")
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    cash = _money_account(conn, "cash", None, data.get("cash_account_id"))
    notes_acc = acc_id(conn, SYS["notes_receivable" if ch["kind"] == "in" else "notes_payable"])
    base = r2(ch["amount"] * ch["rate"])
    label = "تحصيل شيك وارد" if ch["kind"] == "in" else "صرف شيك صادر"
    jl = ([dict(account_id=cash, debit=base), dict(account_id=notes_acc, credit=base)] if ch["kind"] == "in"
          else [dict(account_id=notes_acc, debit=base), dict(account_id=cash, credit=base)])
    jid = post_journal(conn, date, f"{label} رقم {ch['check_number'] or ch['number']}", jl,
                       source="check_settle", source_id=cid, user_id=user_id, log=False)
    conn.execute("UPDATE checks SET status='settled', settled_at=?, cash_account_id=?, settle_journal_id=? WHERE id=?",
                 (date, cash, jid, cid))
    audit(conn, user_id, "update", "check", cid, f"{label} رقم {ch['number']} بمبلغ {base:,.2f}")
    return get_check(conn, cid)


def bounce_check(conn, cid: int, data: dict, user_id=None) -> dict:
    """ارتجاع شيك: يعكس قيد الاستلام/التحرير ويعيد المبلغ على حساب الطرف."""
    ch = get_check(conn, cid)
    if ch["status"] not in ("pending", "settled"):
        raise AccountingError("لا يمكن ارتجاع هذا الشيك")
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    party = get_party(conn, ch["party_id"])
    notes_acc = acc_id(conn, SYS["notes_receivable" if ch["kind"] == "in" else "notes_payable"])
    base = r2(ch["amount"] * ch["rate"])
    if ch["status"] == "settled":     # كان محصَّلاً: يرجع النقد أيضاً
        cash = ch["cash_account_id"] or acc_id(conn, SYS["cash"])
        jl = ([dict(account_id=party["account_id"], debit=base), dict(account_id=cash, credit=base)] if ch["kind"] == "in"
              else [dict(account_id=cash, debit=base), dict(account_id=party["account_id"], credit=base)])
    else:
        jl = ([dict(account_id=party["account_id"], debit=base), dict(account_id=notes_acc, credit=base)] if ch["kind"] == "in"
              else [dict(account_id=notes_acc, debit=base), dict(account_id=party["account_id"], credit=base)])
    jid = post_journal(conn, date, f"ارتجاع شيك رقم {ch['check_number'] or ch['number']} - {party['name']}", jl,
                       source="check_bounce", source_id=cid, user_id=user_id, log=False)
    conn.execute("UPDATE checks SET status='bounced', settle_journal_id=? WHERE id=?", (jid, cid))
    audit(conn, user_id, "update", "check", cid, f"ارتجاع شيك رقم {ch['number']} بمبلغ {base:,.2f}")
    return get_check(conn, cid)


def get_check(conn, cid: int) -> dict:
    row = conn.execute("""SELECT c.*, p.name party_name, a.name cash_name FROM checks c
                          LEFT JOIN parties p ON p.id=c.party_id LEFT JOIN accounts a ON a.id=c.cash_account_id
                          WHERE c.id=?""", (cid,)).fetchone()
    if not row:
        raise AccountingError("الشيك غير موجود")
    ch = dict(row)
    ch["status_label"] = CHECK_STATUS[ch["status"]]
    ch["kind_label"] = "شيك وارد" if ch["kind"] == "in" else "شيك صادر"
    ch["base_amount"] = r2(ch["amount"] * ch["rate"])
    ch["days_left"] = (_date.fromisoformat(ch["due_date"]) - _date.fromisoformat(today())).days
    return ch


def list_checks(conn, kind=None, status=None, limit=300) -> list[dict]:
    q = """SELECT c.*, p.name party_name FROM checks c LEFT JOIN parties p ON p.id=c.party_id WHERE 1=1"""
    args = []
    if kind:
        q += " AND c.kind=?"; args.append(kind)
    if status:
        q += " AND c.status=?"; args.append(status)
    out = rows(conn.execute(q + " ORDER BY c.due_date, c.id LIMIT ?", args + [limit]))
    t = _date.fromisoformat(today())
    for ch in out:
        ch["status_label"] = CHECK_STATUS[ch["status"]]
        ch["kind_label"] = "شيك وارد" if ch["kind"] == "in" else "شيك صادر"
        ch["base_amount"] = r2(ch["amount"] * ch["rate"])
        ch["days_left"] = (_date.fromisoformat(ch["due_date"]) - t).days
        ch["overdue"] = ch["status"] == "pending" and ch["days_left"] < 0
    return out


def cancel_check(conn, cid: int, user_id=None):
    ch = get_check(conn, cid)
    if ch["status"] == "settled":
        raise AccountingError("الشيك محصَّل، استخدم «ارتجاع» بدل الإلغاء")
    ensure_unlocked(conn, ch["due_date"])
    for jid in (ch["journal_id"], ch["settle_journal_id"]):
        if jid:
            conn.execute("UPDATE checks SET journal_id=NULL, settle_journal_id=NULL WHERE id=?", (cid,))
            conn.execute("DELETE FROM journals WHERE id=?", (jid,))
    conn.execute("UPDATE checks SET status='canceled' WHERE id=?", (cid,))
    audit(conn, user_id, "update", "check", cid, f"إلغاء شيك رقم {ch['number']}")


# ───────────────────────── حدود الائتمان والأقساط ─────────────────────────

def party_balance(conn, party_id: int) -> float:
    p = get_party(conn, party_id)
    r = conn.execute("SELECT COALESCE(SUM(debit-credit),0) FROM journal_lines WHERE account_id=?",
                     (p["account_id"],)).fetchone()[0]
    return r2(r if p["kind"] == "customer" else -r)


def check_credit_limit(conn, party: dict, addition: float):
    """يمنع تجاوز حد ائتمان العميل عند البيع الآجل (يمكن تعطيله من الإعدادات)."""
    if not party or party["kind"] != "customer":
        return
    limit = party.get("credit_limit") or 0
    if limit <= 0 or settings(conn).get("enforce_credit_limit") != "1":
        return
    new_balance = party_balance(conn, party["id"]) + addition
    if new_balance > limit + EPS:
        raise AccountingError(
            f"تجاوز حد ائتمان «{party['name']}»: الحد {limit:,.2f} والرصيد بعد الفاتورة {new_balance:,.2f}")


def save_installments(conn, invoice_id: int, plan: list[dict] | None = None, count: int = 0,
                      start: str | None = None, user_id=None) -> list[dict]:
    """خطة أقساط للفاتورة: إما دفعات محددة أو تقسيم آلي على عدد أشهر."""
    inv = get_invoice(conn, invoice_id)
    conn.execute("DELETE FROM installments WHERE invoice_id=?", (invoice_id,))
    rowsx = []
    if plan:
        rowsx = [(i + 1, p["due_date"], r2(p["amount"])) for i, p in enumerate(plan) if r2(p.get("amount")) > 0]
    elif count and count > 0:
        each = r2(inv["total_base"] / count)
        d0 = _date.fromisoformat(start or inv["date"])
        for i in range(count):
            m = d0.month - 1 + i + 1
            due = d0.replace(year=d0.year + m // 12, month=m % 12 + 1, day=min(d0.day, 28))
            amount = each if i < count - 1 else r2(inv["total_base"] - each * (count - 1))
            rowsx.append((i + 1, due.isoformat(), amount))
    total = r2(sum(x[2] for x in rowsx))
    if rowsx and abs(total - inv["total_base"]) > 0.05:
        raise AccountingError(f"مجموع الأقساط ({total:,.2f}) لا يساوي قيمة الفاتورة ({inv['total_base']:,.2f})")
    conn.executemany("INSERT INTO installments(invoice_id,seq,due_date,amount) VALUES (?,?,?,?)",
                     [(invoice_id, *x) for x in rowsx])
    if rowsx:
        audit(conn, user_id, "update", "invoice", invoice_id, f"خطة أقساط للفاتورة رقم {inv['number']}: {len(rowsx)} قسط")
    return invoice_installments(conn, invoice_id)


def invoice_installments(conn, invoice_id: int) -> list[dict]:
    """الأقساط مع توزيع المسدَّد عليها بالترتيب (الأقدم أولاً)."""
    out = rows(conn.execute("SELECT * FROM installments WHERE invoice_id=? ORDER BY seq", (invoice_id,)))
    paid = paid_amount(conn, invoice_id)
    t = _date.fromisoformat(today())
    for ins in out:
        cover = min(paid, ins["amount"])
        paid = r2(paid - cover)
        ins["paid"] = r2(cover)
        ins["remaining"] = r2(ins["amount"] - cover)
        ins["days_late"] = (t - _date.fromisoformat(ins["due_date"])).days
        ins["status"] = "مسدَّد" if ins["remaining"] <= EPS else ("متأخر" if ins["days_late"] > 0 else "قادم")
    return out


def installments_due(conn, as_of=None, only_late=False) -> dict:
    as_of = as_of or today()
    out = []
    for r in rows(conn.execute(
            """SELECT i.id, i.number, i.date, i.total_base, p.name party_name, p.id party_id
               FROM invoices i JOIN parties p ON p.id=i.party_id
               WHERE EXISTS (SELECT 1 FROM installments n WHERE n.invoice_id=i.id)""")):
        for ins in invoice_installments(conn, r["id"]):
            if ins["remaining"] <= EPS or ins["due_date"] > as_of and only_late:
                continue
            if only_late and ins["days_late"] <= 0:
                continue
            out.append({**ins, "invoice_number": r["number"], "party_name": r["party_name"],
                        "party_id": r["party_id"], "invoice_id": r["id"]})
    out.sort(key=lambda x: x["due_date"])
    return {"rows": out, "total": r2(sum(x["remaining"] for x in out)),
            "late": r2(sum(x["remaining"] for x in out if x["days_late"] > 0))}


# ───────────────────────── الأصول الثابتة والاهتلاك ─────────────────────────

def list_assets(conn) -> list[dict]:
    out = rows(conn.execute("""SELECT f.*, a.name account_name FROM fixed_assets f
                               JOIN accounts a ON a.id=f.account_id ORDER BY f.id"""))
    for f in out:
        d = conn.execute("SELECT COALESCE(SUM(amount),0) FROM asset_depreciation WHERE asset_id=?", (f["id"],)).fetchone()[0]
        f["depreciated"] = r2(d)
        f["book_value"] = r2(f["cost"] - f["depreciated"])
        f["monthly"] = r2((f["cost"] - f["salvage"]) / f["life_months"]) if f["life_months"] else 0
        f["months_done"] = conn.execute("SELECT COALESCE(SUM(months),0) FROM asset_depreciation WHERE asset_id=?",
                                        (f["id"],)).fetchone()[0]
    return out


def save_asset(conn, data: dict, aid=None, user_id=None) -> int:
    name = (data.get("name") or "").strip()
    life = int(data.get("life_months") or 0)
    cost = r2(data.get("cost"))
    if not name or life <= 0 or cost <= 0:
        raise AccountingError("اسم الأصل وتكلفته وعمره الإنتاجي (بالأشهر) مطلوبة")
    salvage = r2(data.get("salvage"))
    if salvage >= cost:
        raise AccountingError("القيمة التخريدية يجب أن تقل عن التكلفة")
    acc = get_account(conn, int(data["account_id"]))
    if acc["type"] != "asset" or acc["is_group"]:
        raise AccountingError("حساب الأصل يجب أن يكون حساباً تفصيلياً من الأصول")
    args = (name, acc["id"], data.get("purchase_date") or today(), cost, salvage, life, data.get("notes"))
    if aid:
        conn.execute("""UPDATE fixed_assets SET name=?,account_id=?,purchase_date=?,cost=?,salvage=?,life_months=?,notes=?
                        WHERE id=?""", args + (aid,))
    else:
        aid = conn.execute("""INSERT INTO fixed_assets(name,account_id,purchase_date,cost,salvage,life_months,notes,created_by)
                              VALUES (?,?,?,?,?,?,?,?)""", args + (user_id,)).lastrowid
    audit(conn, user_id, "update" if data.get("existing") else "create", "asset", aid, f"حفظ أصل ثابت: {name}")
    return aid


def delete_asset(conn, aid: int, user_id=None):
    row = conn.execute("SELECT name FROM fixed_assets WHERE id=?", (aid,)).fetchone()
    if not row:
        raise AccountingError("الأصل غير موجود")
    if conn.execute("SELECT 1 FROM asset_depreciation WHERE asset_id=?", (aid,)).fetchone():
        raise AccountingError("على الأصل اهتلاكات مرحّلة، لا يمكن حذفه")
    conn.execute("DELETE FROM fixed_assets WHERE id=?", (aid,))
    audit(conn, user_id, "delete", "asset", aid, f"حذف أصل ثابت: {row[0]}")


def _months_between(a: str, b: str) -> int:
    d1, d2 = _date.fromisoformat(a), _date.fromisoformat(b)
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) - (1 if d2.day < d1.day else 0)


def post_depreciation(conn, up_to: str | None = None, user_id=None) -> dict:
    """يرحّل اهتلاك القسط الثابت لكل الأصول حتى تاريخ محدد (لا يُكرَّر لأي شهر مرحَّل)."""
    up_to = up_to or today()
    ensure_unlocked(conn, up_to)
    total, lines, details = 0.0, [], []
    depr_acc, accum_acc = acc_id(conn, SYS["depreciation"]), acc_id(conn, SYS["accum_depr"])
    for f in list_assets(conn):
        if f["disposed_at"] or f["life_months"] <= 0:
            continue
        start = f["last_depr_date"] or f["purchase_date"]
        months = _months_between(start, up_to)
        months = min(months, f["life_months"] - f["months_done"])
        if months <= 0:
            continue
        amount = r2(f["monthly"] * months)
        if amount <= 0:
            continue
        total += amount
        details.append({"asset": f["name"], "months": months, "amount": amount})
        conn.execute("INSERT INTO asset_depreciation(asset_id,date,months,amount) VALUES (?,?,?,?)",
                     (f["id"], up_to, months, amount))
        conn.execute("UPDATE fixed_assets SET last_depr_date=? WHERE id=?", (up_to, f["id"]))
    total = r2(total)
    if total <= 0:
        raise AccountingError("لا يوجد اهتلاك مستحق حتى هذا التاريخ")
    jid = post_journal(conn, up_to, f"اهتلاك الأصول الثابتة حتى {up_to}",
                       [dict(account_id=depr_acc, debit=total), dict(account_id=accum_acc, credit=total)],
                       source="depreciation", user_id=user_id, log=False)
    conn.execute("UPDATE asset_depreciation SET journal_id=? WHERE journal_id IS NULL", (jid,))
    audit(conn, user_id, "create", "asset", None, f"ترحيل اهتلاك بمبلغ {total:,.2f} حتى {up_to}", {"assets": details})
    return {"total": total, "journal_id": jid, "details": details}


# ───────────────────────── الموظفون والرواتب والعمولات ─────────────────────────

def list_employees(conn, active_only=False) -> list[dict]:
    q = "SELECT * FROM employees"
    if active_only:
        q += " WHERE active=1"
    return rows(conn.execute(q + " ORDER BY name"))


def save_employee(conn, data: dict, eid=None, user_id=None) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise AccountingError("اسم الموظف مطلوب")
    args = (name, data.get("phone"), data.get("job"), r2(data.get("salary")),
            int(bool(data.get("is_salesperson"))), float(data.get("commission_rate") or 0),
            int(bool(data.get("active", True))))
    if eid:
        conn.execute("""UPDATE employees SET name=?,phone=?,job=?,salary=?,is_salesperson=?,commission_rate=?,active=?
                        WHERE id=?""", args + (eid,))
    else:
        eid = conn.execute("""INSERT INTO employees(name,phone,job,salary,is_salesperson,commission_rate,active)
                              VALUES (?,?,?,?,?,?,?)""", args).lastrowid
    audit(conn, user_id, "update" if data.get("existing") else "create", "employee", eid, f"حفظ موظف: {name}")
    return eid


def delete_employee(conn, eid: int, user_id=None):
    row = conn.execute("SELECT name FROM employees WHERE id=?", (eid,)).fetchone()
    if not row:
        raise AccountingError("الموظف غير موجود")
    if conn.execute("SELECT 1 FROM payroll_lines WHERE employee_id=?", (eid,)).fetchone():
        raise AccountingError("للموظف رواتب مسجلة، أوقفه بدلاً من حذفه")
    conn.execute("UPDATE invoices SET salesperson_id=NULL WHERE salesperson_id=?", (eid,))
    conn.execute("DELETE FROM employees WHERE id=?", (eid,))
    audit(conn, user_id, "delete", "employee", eid, f"حذف موظف: {row[0]}")


def create_payroll(conn, data: dict, user_id=None) -> dict:
    """مسيّر رواتب شهري: يرحّل إجمالي الصافي على حساب الرواتب مقابل النقد أو المستحقات."""
    month = data.get("month") or today()[:7]
    date = data.get("date") or today()
    ensure_unlocked(conn, date)
    if conn.execute("SELECT 1 FROM payroll_runs WHERE month=?", (month,)).fetchone():
        raise AccountingError(f"رواتب شهر {month} مسجّلة مسبقاً")
    lines = []
    for ln in data.get("lines", []):
        emp = conn.execute("SELECT * FROM employees WHERE id=?", (int(ln["employee_id"]),)).fetchone()
        if not emp:
            raise AccountingError("موظف غير موجود في المسيّر")
        salary, add, ded = r2(ln.get("salary", emp["salary"])), r2(ln.get("additions")), r2(ln.get("deductions"))
        net = r2(salary + add - ded)
        if net < 0:
            raise AccountingError(f"صافي راتب «{emp['name']}» سالب")
        lines.append((emp["id"], salary, add, ded, net))
    if not lines:
        raise AccountingError("أضف موظفاً واحداً على الأقل")
    total = r2(sum(x[4] for x in lines))
    paid = bool(data.get("paid", True))
    rid = conn.execute("""INSERT INTO payroll_runs(number,month,date,total,paid,notes,created_by)
                          VALUES (?,?,?,?,?,?,?)""",
                       (next_seq(conn, "payroll"), month, date, total, int(paid), data.get("notes"), user_id)).lastrowid
    conn.executemany("INSERT INTO payroll_lines(run_id,employee_id,salary,additions,deductions,net) VALUES (?,?,?,?,?,?)",
                     [(rid, *x) for x in lines])
    credit_acc = (_money_account(conn, "cash", None, data.get("cash_account_id")) if paid
                  else acc_id(conn, SYS["accruals"]))
    jid = post_journal(conn, date, f"رواتب شهر {month}",
                       [dict(account_id=acc_id(conn, SYS["salaries"]), debit=total, cost_center_id=data.get("cost_center_id")),
                        dict(account_id=credit_acc, credit=total)],
                       source="payroll", source_id=rid, user_id=user_id, log=False)
    conn.execute("UPDATE payroll_runs SET journal_id=? WHERE id=?", (jid, rid))
    audit(conn, user_id, "create", "payroll", rid, f"رواتب شهر {month} بمبلغ {total:,.2f} لـ{len(lines)} موظف")
    return get_payroll(conn, rid)


def get_payroll(conn, rid: int) -> dict:
    row = conn.execute("SELECT * FROM payroll_runs WHERE id=?", (rid,)).fetchone()
    if not row:
        raise AccountingError("مسيّر الرواتب غير موجود")
    pr = dict(row)
    pr["lines"] = rows(conn.execute("""SELECT l.*, e.name employee_name, e.job FROM payroll_lines l
                                       JOIN employees e ON e.id=l.employee_id WHERE run_id=? ORDER BY l.id""", (rid,)))
    return pr


def list_payrolls(conn, limit=50) -> list[dict]:
    out = rows(conn.execute("SELECT * FROM payroll_runs ORDER BY id DESC LIMIT ?", (limit,)))
    for pr in out:
        pr["count"] = conn.execute("SELECT COUNT(*) FROM payroll_lines WHERE run_id=?", (pr["id"],)).fetchone()[0]
    return out


def commissions_report(conn, date_from=None, date_to=None) -> dict:
    """عمولات المندوبين: مبيعاتهم الصافية وأرباحها والعمولة المستحقة."""
    cond, args = "", []
    if date_from:
        cond += " AND i.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND i.date<=?"; args.append(date_to)
    out = []
    for e in list_employees(conn):
        if not e["is_salesperson"]:
            continue
        r = conn.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN kind='sale' THEN net_base ELSE -net_base END),0) sales,
                                    COALESCE(SUM(CASE WHEN kind='sale' THEN net_base-cost_total ELSE -(net_base-cost_total) END),0) profit
                             FROM invoices i WHERE i.salesperson_id=? AND i.kind IN ('sale','sale_return') {cond}""",
                         [e["id"]] + args).fetchone()
        sales = r2(r["sales"])
        out.append({"id": e["id"], "name": e["name"], "rate": e["commission_rate"], "invoices": r["n"],
                    "sales": sales, "profit": r2(r["profit"]), "commission": r2(sales * e["commission_rate"] / 100)})
    return {"rows": sorted(out, key=lambda x: -x["sales"]),
            "total_sales": r2(sum(x["sales"] for x in out)),
            "total_commission": r2(sum(x["commission"] for x in out))}


def pay_commission(conn, employee_id: int, amount: float, data: dict, user_id=None) -> int:
    """صرف عمولة لمندوب: مصروف عمولات مقابل النقد."""
    e = conn.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not e:
        raise AccountingError("الموظف غير موجود")
    amount = r2(amount)
    if amount <= 0:
        raise AccountingError("المبلغ يجب أن يكون أكبر من صفر")
    cash = _money_account(conn, "cash", None, data.get("cash_account_id"))
    jid = post_journal(conn, data.get("date") or today(), f"عمولة مبيعات - {e['name']}",
                       [dict(account_id=acc_id(conn, SYS["commissions"]), debit=amount),
                        dict(account_id=cash, credit=amount)], source="commission", source_id=employee_id,
                       user_id=user_id, log=False)
    audit(conn, user_id, "create", "employee", employee_id, f"صرف عمولة {amount:,.2f} لـ{e['name']}")
    return jid


# ───────────────────────── الموازنات والتدفقات النقدية ─────────────────────────

def budget_report(conn, year: str | None = None) -> dict:
    """مقارنة الفعلي بالموازنة التقديرية لكل حساب إيراد/مصروف."""
    year = year or today()[:4]
    accs = {a["id"]: a for a in accounts_tree(conn) if a["type"] in ("revenue", "expense") and not a["is_group"]}
    budgets = {b["account_id"]: b["amount"] for b in rows(conn.execute("SELECT * FROM budgets WHERE year=?", (year,)))}
    actual = {}
    for r in rows(conn.execute("""SELECT jl.account_id, SUM(jl.debit) d, SUM(jl.credit) c
                                  FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id
                                  WHERE substr(j.date,1,4)=? GROUP BY jl.account_id""", (year,))):
        actual[r["account_id"]] = (r["d"], r["c"])
    out = []
    for aid, a in accs.items():
        d, c = actual.get(aid, (0, 0))
        act = r2(c - d if a["type"] == "revenue" else d - c)
        bud = r2(budgets.get(aid, 0))
        if not act and not bud:
            continue
        out.append({"account_id": aid, "code": a["code"], "name": a["name"], "type": a["type"],
                    "budget": bud, "actual": act, "variance": r2(bud - act),
                    "pct": r2(act / bud * 100) if bud else None})
    rev = [x for x in out if x["type"] == "revenue"]
    exp = [x for x in out if x["type"] == "expense"]
    return {"year": year, "revenue": rev, "expense": exp,
            "totals": {"budget_revenue": r2(sum(x["budget"] for x in rev)), "actual_revenue": r2(sum(x["actual"] for x in rev)),
                       "budget_expense": r2(sum(x["budget"] for x in exp)), "actual_expense": r2(sum(x["actual"] for x in exp))}}


def save_budgets(conn, year: str, items: list[dict], user_id=None):
    conn.execute("DELETE FROM budgets WHERE year=?", (year,))
    for it in items or []:
        if it.get("account_id") and r2(it.get("amount")) != 0:
            conn.execute("INSERT INTO budgets(year,account_id,amount) VALUES (?,?,?)",
                         (year, int(it["account_id"]), r2(it["amount"])))
    audit(conn, user_id, "update", "budget", None, f"حفظ موازنة سنة {year}")


def cash_flow(conn, date_from=None, date_to=None) -> dict:
    """قائمة التدفقات النقدية: تُصنَّف حركة الصناديق حسب الحساب المقابل لها في القيد."""
    cond, args = "", []
    if date_from:
        cond += " AND j.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND j.date<=?"; args.append(date_to)
    cash_ids = [r[0] for r in conn.execute("SELECT id FROM accounts WHERE is_cash=1")]
    if not cash_ids:
        return {"operating": [], "investing": [], "financing": [], "net": 0, "opening": 0, "closing": 0}
    ph = ",".join("?" * len(cash_ids))
    # لكل قيد فيه حركة نقدية: وزّع صافي النقد على الحسابات المقابلة بنسبة مبالغها
    data = rows(conn.execute(f"""
        SELECT j.id jid,
               SUM(CASE WHEN jl.account_id IN ({ph}) THEN jl.debit - jl.credit ELSE 0 END) cash_delta
        FROM journals j JOIN journal_lines jl ON jl.journal_id=j.id
        WHERE 1=1 {cond} GROUP BY j.id HAVING ABS(cash_delta) > 0.004""", cash_ids + args))
    buckets = {"operating": {}, "investing": {}, "financing": {}}
    for row in data:
        others = rows(conn.execute(f"""SELECT a.id, a.code, a.name, a.type, jl.debit, jl.credit
                                       FROM journal_lines jl JOIN accounts a ON a.id=jl.account_id
                                       WHERE jl.journal_id=? AND jl.account_id NOT IN ({ph})""",
                                   [row["jid"]] + cash_ids))
        weight = sum(abs(o["debit"] - o["credit"]) for o in others) or 1
        for o in others:
            share = row["cash_delta"] * (abs(o["debit"] - o["credit"]) / weight)
            code = o["code"]
            if code.startswith("12") or code.startswith("123"):
                sec = "investing"
            elif o["type"] == "equity" or code.startswith("22"):
                sec = "financing"
            else:
                sec = "operating"
            b = buckets[sec].setdefault(o["id"], {"code": code, "name": o["name"], "amount": 0.0})
            b["amount"] += share
    def clean(sec):
        out = [{**v, "amount": r2(v["amount"])} for v in buckets[sec].values() if abs(v["amount"]) > 0.004]
        return sorted(out, key=lambda x: -abs(x["amount"]))
    sections = {k: clean(k) for k in buckets}
    opening = 0.0
    if date_from:
        opening = conn.execute(f"""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl
                                   JOIN journals j ON j.id=jl.journal_id
                                   WHERE jl.account_id IN ({ph}) AND j.date<?""", cash_ids + [date_from]).fetchone()[0]
    net = r2(sum(x["amount"] for sec in sections.values() for x in sec))
    return {**sections, "totals": {k: r2(sum(x["amount"] for x in v)) for k, v in sections.items()},
            "net": net, "opening": r2(opening), "closing": r2(opening + net)}




# ───────────────────────── الوصفات (مكوّنات الأطباق) ─────────────────────────

# وحدات الوزن والحجم المعروفة وقيمتها بالغرام / المليلتر — تتيح إدخال المكوّن بالغرام ومادته مخزّنة بالكيلو
_MEASURES = (
    ({"كغ": 1000, "كغم": 1000, "كجم": 1000, "كيلو": 1000, "كيلوغرام": 1000, "كيلوجرام": 1000, "kg": 1000,
      "غرام": 1, "جرام": 1, "غم": 1, "غ": 1, "g": 1, "gm": 1, "gr": 1, "طن": 1_000_000},
     (("غرام", 1), ("كغ", 1000))),
    ({"لتر": 1000, "ليتر": 1000, "ل": 1000, "l": 1000, "lt": 1000, "ltr": 1000,
      "مل": 1, "ملل": 1, "ميليلتر": 1, "مليلتر": 1, "ml": 1},
     (("مل", 1), ("لتر", 1000))),
)


def _measure_units(base_unit: str) -> list[dict]:
    """الوحدات القياسية البديلة لوحدة المادة: غرام/كغ للأوزان و مل/لتر للسوائل."""
    key = (base_unit or "").strip().lower()
    for known, offered in _MEASURES:
        if key in known:
            return [{"name": n, "factor": v / known[key]} for n, v in offered if v != known[key]]
    return []


def ingredient_units(conn, item_id: int, base_unit: str, extra: list[dict] | None = None) -> list[dict]:
    """الوحدات التي يصح إدخال كمية المكوّن بها: الأساسية، ثم القياسية (غرام، مل)، ثم وحدات المادة المعرّفة."""
    if extra is None:
        extra = rows(conn.execute("SELECT name, factor FROM item_units WHERE item_id=? ORDER BY factor", (item_id,)))
    out = [{"name": base_unit or "وحدة", "factor": 1.0}]
    for u in _measure_units(base_unit) + [{"name": x["name"], "factor": x["factor"]} for x in extra]:
        if u["factor"] > 0 and all(o["name"] != u["name"] for o in out):
            out.append(u)
    return out


def recipe_units(conn) -> dict[int, list[dict]]:
    """وحدات كل مادة لها أكثر من وحدة (لمحرر المكوّنات) — باستعلامين مهما كثرت المواد."""
    extra = units_by_item(conn)
    out = {}
    for i in conn.execute("SELECT id, unit FROM items WHERE is_service=0"):
        units = ingredient_units(conn, i["id"], i["unit"], extra.get(i["id"], []))
        if len(units) > 1:
            out[i["id"]] = units
    return out


def _recipe_rows(conn, item_id: int) -> list[dict]:
    return rows(conn.execute(
        """SELECT r.ingredient_id, r.qty, r.waste_pct, r.unit_name, r.unit_factor, i.name, i.code, i.unit, i.avg_cost, i.is_recipe
           FROM recipes r JOIN items i ON i.id=r.ingredient_id WHERE r.item_id=? ORDER BY r.id""", (item_id,)))


def explode_recipe(conn, item_id: int, qty: float = 1, _path: tuple = ()) -> dict[int, float]:
    """يفكّ الوصفة إلى موادها المخزنية النهائية (مهما تعدّدت المستويات) مع احتساب الهدر.

    يعيد {رقم المادة: الكمية المطلوبة}. المكوّن الذي هو نفسه وصفة (تحضيرة وسيطة) يُفكّ إلى مكوّناته.
    """
    if item_id in _path:
        raise AccountingError("الوصفة تحتوي حلقة مغلقة (تحضيرة تدخل في مكوّنات نفسها)")
    out: dict[int, float] = {}
    for r in _recipe_rows(conn, item_id):
        need = qty * r["qty"] * (1 + (r["waste_pct"] or 0) / 100)
        if r["is_recipe"]:
            for k, v in explode_recipe(conn, r["ingredient_id"], need, _path + (item_id,)).items():
                out[k] = out.get(k, 0) + v
        else:
            out[r["ingredient_id"]] = out.get(r["ingredient_id"], 0) + need
    return out


def _consume_recipe(conn, item: dict, wid: int, qty: float) -> list[dict]:
    """يخصم المواد النهائية للطبق من المستودع ويعيد ما استُهلك بتكلفته."""
    needs = explode_recipe(conn, item["id"], qty)
    if not needs:
        raise AccountingError(f"المنتج «{item['name']}» بلا مكوّنات — أضف مكوّناته أولاً")
    out = []
    for ing_id, need in needs.items():
        ing = _item(conn, ing_id)
        _require_stock(conn, ing, wid, need)
        cost = wh_cost(conn, ing["id"], wid)
        move_stock(conn, ing["id"], wid, -need, cost)
        if ing["track_batches"]:
            _batch_out(conn, ing["id"], wid, need, "recipe", item["id"])
        out.append({"item_id": ing["id"], "qty": need, "unit_cost": cost})
    return out


def recipe_unit_cost(conn, item_id: int) -> float:
    """تكلفة وحدة واحدة من المنتج = مجموع تكلفة مواده النهائية بمتوسط تكلفتها الحالي."""
    total = 0.0
    for ing_id, need in explode_recipe(conn, item_id, 1).items():
        total += need * (conn.execute("SELECT avg_cost FROM items WHERE id=?", (ing_id,)).fetchone()[0] or 0)
    return total


def get_recipe(conn, item_id: int) -> dict:
    it = _item(conn, item_id)
    lines = _recipe_rows(conn, item_id)
    for l in lines:
        unit = recipe_unit_cost(conn, l["ingredient_id"]) if l["is_recipe"] else l["avg_cost"]
        l["unit_cost"] = unit                # تكلفة الوحدة الأساسية بلا تقريب (غرام واحد من كيلو كسرٌ صغير)
        l["cost"] = r2(l["qty"] * (1 + (l["waste_pct"] or 0) / 100) * unit)
        l["units"] = ingredient_units(conn, l["ingredient_id"], l["unit"])
        factor = l["unit_factor"] or 1
        if not l["unit_name"] or all(u["name"] != l["unit_name"] for u in l["units"]):
            l["unit_name"], factor = l["units"][0]["name"], 1.0          # الوحدة حُذفت من المادة: نعرض بالأساسية
        l["unit_factor"] = factor
        l["unit_qty"] = round(l["qty"] / factor, 6)                      # الكمية كما أدخلها المستخدم بوحدته
    cost = r2(recipe_unit_cost(conn, item_id))
    return {"item_id": item_id, "name": it["name"], "sale_price": it["sale_price"], "lines": lines, "cost": cost,
            "count": len(lines), "margin": r2(it["sale_price"] - cost),
            "food_cost_pct": r2(cost / it["sale_price"] * 100) if it["sale_price"] else None}


def save_recipe(conn, item_id: int, lines: list[dict], user_id=None) -> dict:
    """يحفظ مكوّنات المنتج — بلا حد لعددها. المكوّن مادة مخزنية أو تحضيرة وسيطة لها مكوّناتها."""
    it = _item(conn, item_id)
    if it["is_service"]:
        raise AccountingError("الصنف غير المخزني لا تكون له مكوّنات — ألغِ خيار «غير مخزني» أولاً")
    merged: dict[int, list] = {}
    for ln in lines or []:
        q = float(ln.get("qty") or 0)
        if not ln.get("ingredient_id") or q <= 0:
            continue
        ing = _item(conn, ln["ingredient_id"])
        if ing["id"] == item_id:
            raise AccountingError("لا يكون المنتج مكوّناً لنفسه")
        if ing["is_service"]:
            raise AccountingError(f"«{ing['name']}» صنف غير مخزني ولا يصلح مكوّناً")
        waste = float(ln.get("waste_pct") or 0)
        if waste < 0 or waste > 500:
            raise AccountingError("نسبة الهدر غير صحيحة")
        unit_name, factor = (ln.get("unit_name") or "").strip() or None, 1.0
        if unit_name:                        # الكمية مُدخلة بوحدة غير الأساسية: نحوّلها بمعامل يحدّده الخادم
            unit = next((u for u in ingredient_units(conn, ing["id"], ing["unit"]) if u["name"] == unit_name), None)
            if not unit:
                raise AccountingError(f"الوحدة «{unit_name}» غير معرّفة للمكوّن «{ing['name']}»")
            factor = unit["factor"]
        if ing["id"] in merged:              # المكوّن المكرّر يُجمع
            merged[ing["id"]][0] += q * factor
        else:
            merged[ing["id"]] = [q * factor, waste, unit_name, factor]
    conn.execute("DELETE FROM recipes WHERE item_id=?", (item_id,))
    conn.executemany("INSERT INTO recipes(item_id,ingredient_id,qty,waste_pct,unit_name,unit_factor) VALUES (?,?,?,?,?,?)",
                     [(item_id, k, round(v[0], 9), v[1], v[2], v[3]) for k, v in merged.items()])
    conn.execute("UPDATE items SET is_recipe=? WHERE id=?", (int(bool(merged)), item_id))
    explode_recipe(conn, item_id, 1)         # يرفض الحلقات المغلقة قبل تثبيت الحفظ
    if merged and it["qty"] > 1e-9:
        raise AccountingError("للمادة رصيد مخزني — المنتج ذو المكوّنات لا يُخزَّن. صفّر رصيده بجرد أولاً")
    audit(conn, user_id, "update", "item", item_id, f"تعديل مكوّنات «{it['name']}» ({len(merged)} مكوّن)")
    return get_recipe(conn, item_id)


def recipes_report(conn) -> list[dict]:
    """تكلفة كل طبق وهامشه ونسبة تكلفة الطعام."""
    out = [get_recipe(conn, r["id"]) for r in conn.execute("SELECT id FROM items WHERE is_recipe=1 ORDER BY name")]
    return out


def set_item_menu(conn, item_id: int, data: dict, user_id=None):
    """بيانات المادة الخاصة بقائمة المطعم: التصنيف وقسم التحضير."""
    _item(conn, item_id)
    conn.execute("UPDATE items SET category=?, station_id=? WHERE id=?",
                 ((data.get("category") or "").strip() or None, data.get("station_id") or None, item_id))


# ───────────────────────── إعدادات المطعم ─────────────────────────

def _simple_list(conn, table: str) -> list[dict]:
    return rows(conn.execute(f"SELECT * FROM {table} ORDER BY id"))


def _simple_save(conn, table: str, label: str, data: dict, rid=None, cols=("name",), user_id=None) -> int:
    if not (data.get("name") or "").strip():
        raise AccountingError(f"اسم {label} مطلوب")
    vals = [data.get(c) if c != "name" else data["name"].strip() for c in cols]
    if rid:
        conn.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in cols)} WHERE id=?", vals + [rid])
    else:
        rid = conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals).lastrowid
    audit(conn, user_id, "update", table, rid, f"حفظ {label}: {data['name'].strip()}")
    return rid


def list_tables(conn) -> list[dict]:
    """الطاولات مع حالتها: فارغة أو مشغولة بطلب مفتوح (مع مبلغه ومدته)."""
    out = rows(conn.execute("""SELECT t.*, a.name area_name FROM dining_tables t
                               LEFT JOIN dining_areas a ON a.id=t.area_id ORDER BY a.id, t.id"""))
    for t in out:
        orders = rows(conn.execute("SELECT id FROM orders WHERE table_id=? AND status='open' ORDER BY id", (t["id"],)))
        t["orders"] = [order_summary(conn, o["id"]) for o in orders]
        t["busy"] = bool(orders)
        t["total"] = r2(sum(o["total"] for o in t["orders"]))
    return out


def delete_table(conn, tid: int, user_id=None):
    if conn.execute("SELECT 1 FROM orders WHERE table_id=?", (tid,)).fetchone():
        raise AccountingError("للطاولة طلبات مسجلة، أوقفها بدلاً من حذفها")
    conn.execute("DELETE FROM dining_tables WHERE id=?", (tid,))


def list_modifiers(conn, item_id=None) -> list[dict]:
    q, args = "SELECT * FROM modifiers WHERE active=1", []
    if item_id:
        q += " AND (item_id IS NULL OR item_id=?)"; args.append(item_id)
    return rows(conn.execute(q + " ORDER BY item_id IS NULL, id", args))


# ───────────────────────── الطلبات ─────────────────────────

ORDER_KIND = {"dine_in": "صالة", "takeaway": "سفري", "delivery": "توصيل"}


def _order_row(conn, oid: int) -> dict:
    row = conn.execute(
        """SELECT o.*, t.name table_name, e.name waiter_name, d.name driver_name, p.name party_name
           FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id LEFT JOIN employees e ON e.id=o.waiter_id
           LEFT JOIN drivers d ON d.id=o.driver_id LEFT JOIN parties p ON p.id=o.party_id WHERE o.id=?""", (oid,)).fetchone()
    if not row:
        raise AccountingError("الطلب غير موجود")
    return dict(row)


def _order_totals(conn, o: dict, lines: list[dict]) -> dict:
    s = settings(conn)
    subtotal = r2(sum(l["qty"] * l["price"] for l in lines))
    net = r2(subtotal - (o["discount"] or 0))
    tax_rate = float(s.get("vat_rate") or 0) if s.get("vat_enabled") == "1" else 0
    tax = r2(net * tax_rate / 100)
    service = r2(net * (o["service_rate"] or 0) / 100)
    fee = r2(o["delivery_fee"] or 0)
    return {"subtotal": subtotal, "net": net, "tax_rate": tax_rate, "tax": tax, "service": service,
            "delivery_fee": fee, "total": r2(net + tax + service + fee)}


def order_summary(conn, oid: int) -> dict:
    o = _order_row(conn, oid)
    lines = rows(conn.execute("SELECT qty, price FROM order_lines WHERE order_id=?", (oid,)))
    t = _order_totals(conn, o, lines)
    mins = conn.execute("SELECT CAST((julianday('now','localtime') - julianday(?)) * 1440 AS INTEGER)", (o["opened_at"],)).fetchone()[0]
    return {"id": oid, "number": o["number"], "kind": o["kind"], "guests": o["guests"], "total": t["total"],
            "lines": len(lines), "minutes": mins, "waiter_name": o["waiter_name"], "opened_at": o["opened_at"]}


def get_order(conn, oid: int) -> dict:
    o = _order_row(conn, oid)
    o["kind_label"] = ORDER_KIND[o["kind"]]
    o["lines"] = rows(conn.execute(
        """SELECT l.*, i.name item_name, i.code item_code, i.unit, i.station_id, st.name station_name
           FROM order_lines l JOIN items i ON i.id=l.item_id LEFT JOIN stations st ON st.id=i.station_id
           WHERE l.order_id=? ORDER BY l.id""", (oid,)))
    for l in o["lines"]:
        l["notes"] = json.loads(l["notes"]) if l["notes"] else []
        l["total"] = r2(l["qty"] * l["price"])
        l["pending"] = r2(l["qty"] - l["sent_qty"])
    o.update(_order_totals(conn, o, o["lines"]))
    return o


def open_order(conn, data: dict, user_id=None) -> dict:
    kind = data.get("kind") or "dine_in"
    if kind not in ORDER_KIND:
        raise AccountingError("نوع الطلب غير صحيح")
    s = settings(conn)
    table_id = data.get("table_id") or None
    if kind == "dine_in":
        if not table_id:
            raise AccountingError("اختر الطاولة")
        t = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
        if not t or not t["active"]:
            raise AccountingError("الطاولة غير متاحة")
    else:
        table_id = None
    service_rate = float(data.get("service_rate") if data.get("service_rate") is not None
                         else (s.get("service_rate") or 0) if kind == "dine_in" else 0)
    fee = r2(data.get("delivery_fee") if data.get("delivery_fee") is not None
             else (s.get("delivery_fee") or 0) if kind == "delivery" else 0)
    oid = conn.execute(
        """INSERT INTO orders(number,kind,table_id,party_id,waiter_id,driver_id,guests,customer_name,customer_phone,
                              address,delivery_fee,service_rate,notes,delivery_status,warehouse_id,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (next_seq(conn, "order"), kind, table_id, data.get("party_id") or None, data.get("waiter_id") or None,
         data.get("driver_id") or None, int(data.get("guests") or 1), data.get("customer_name"),
         data.get("customer_phone"), data.get("address"), fee, service_rate, data.get("notes"),
         "preparing" if kind == "delivery" else None, data.get("warehouse_id") or default_warehouse(conn), user_id)).lastrowid
    if data.get("lines"):
        save_order(conn, oid, {"lines": data["lines"]}, user_id)
    return get_order(conn, oid)


def _ensure_open(o: dict):
    if o["status"] != "open":
        raise AccountingError("الطلب مغلق ولا يمكن تعديله")


def save_order(conn, oid: int, data: dict, user_id=None) -> dict:
    """يحدّث رأس الطلب وأسطره. السطر المُرسل للمطبخ لا تُنقص كميته دون صلاحية الحذف (يتحقق منها الخادم)."""
    o = _order_row(conn, oid)
    _ensure_open(o)
    fields = {k: data[k] for k in ("guests", "notes", "discount", "service_rate", "delivery_fee", "waiter_id", "driver_id",
                                   "party_id", "customer_name", "customer_phone", "address", "delivery_status")
              if k in data}
    if fields:
        conn.execute(f"UPDATE orders SET {', '.join(k + '=?' for k in fields)} WHERE id=?", list(fields.values()) + [oid])
    if "lines" in data:
        existing = {l["id"]: l for l in rows(conn.execute("SELECT * FROM order_lines WHERE order_id=?", (oid,)))}
        keep = set()
        for ln in data["lines"]:
            qty, price = float(ln.get("qty") or 0), r2(ln.get("price"))
            if qty <= 0:
                continue
            notes = json.dumps(ln.get("notes") or [], ensure_ascii=False)
            lid = ln.get("id")
            if lid and lid in existing:
                keep.add(lid)
                sent = min(existing[lid]["sent_qty"], qty)
                conn.execute("UPDATE order_lines SET qty=?, price=?, notes=?, sent_qty=? WHERE id=?",
                             (qty, price, notes, sent, lid))
            else:
                _item(conn, ln["item_id"])
                keep.add(conn.execute("INSERT INTO order_lines(order_id,item_id,qty,price,notes) VALUES (?,?,?,?,?)",
                                      (oid, int(ln["item_id"]), qty, price, notes)).lastrowid)
        for lid, old in existing.items():
            if lid not in keep:
                if old["sent_qty"] > 0:
                    audit(conn, user_id, "delete", "order", oid,
                          f"حذف صنف مُرسل للمطبخ من الطلب رقم {o['number']}", {"item_id": old["item_id"], "qty": old["qty"]})
                conn.execute("DELETE FROM order_lines WHERE id=?", (lid,))
    return get_order(conn, oid)


def send_to_kitchen(conn, oid: int, user_id=None) -> list[dict]:
    """يرسل الأصناف الجديدة للمطبخ ويعيد تذاكر التحضير مجمّعة حسب القسم."""
    o = get_order(conn, oid)
    _ensure_open(o)
    tickets: dict = {}
    for l in o["lines"]:
        if l["pending"] <= 1e-9:
            continue
        key = l["station_name"] or "المطبخ"
        tickets.setdefault(key, []).append({"item_name": l["item_name"], "qty": l["pending"],
                                            "notes": [n["name"] if isinstance(n, dict) else str(n) for n in l["notes"]]})
        conn.execute("UPDATE order_lines SET sent_qty=qty, ready=0, sent_at=datetime('now','localtime') WHERE id=?", (l["id"],))
    return [{"station": st, "number": o["number"], "table_name": o["table_name"], "order_type": o["kind_label"],
             "waiter": o["waiter_name"], "notes": o["notes"], "lines": lines} for st, lines in tickets.items()]


def kitchen_queue(conn, station_id=None) -> list[dict]:
    """شاشة المطبخ: الأصناف المُرسلة وغير الجاهزة، الأقدم أولاً."""
    q = """SELECT l.id, l.qty, l.sent_qty, l.notes, l.sent_at, i.name item_name, i.station_id, st.name station_name,
                  o.id order_id, o.number, o.kind, t.name table_name,
                  CAST((julianday('now','localtime') - julianday(l.sent_at)) * 1440 AS INTEGER) minutes
           FROM order_lines l JOIN orders o ON o.id=l.order_id JOIN items i ON i.id=l.item_id
           LEFT JOIN stations st ON st.id=i.station_id LEFT JOIN dining_tables t ON t.id=o.table_id
           WHERE o.status='open' AND l.sent_qty > 0 AND l.ready=0"""
    args = []
    if station_id:
        q += " AND i.station_id=?"; args.append(station_id)
    out = rows(conn.execute(q + " ORDER BY l.sent_at, l.id", args))
    for l in out:
        l["notes"] = [n["name"] if isinstance(n, dict) else str(n) for n in (json.loads(l["notes"]) if l["notes"] else [])]
        l["kind_label"] = ORDER_KIND[l["kind"]]
    return out


def mark_ready(conn, line_ids: list[int]):
    for lid in line_ids or []:
        conn.execute("UPDATE order_lines SET ready=1 WHERE id=?", (int(lid),))


def move_order(conn, oid: int, table_id: int, user_id=None) -> dict:
    o = _order_row(conn, oid)
    _ensure_open(o)
    t = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    if not t:
        raise AccountingError("الطاولة غير موجودة")
    conn.execute("UPDATE orders SET table_id=?, kind='dine_in' WHERE id=?", (table_id, oid))
    audit(conn, user_id, "update", "order", oid, f"نقل الطلب رقم {o['number']} إلى «{t['name']}»")
    return get_order(conn, oid)


def merge_orders(conn, src: int, dst: int, user_id=None) -> dict:
    a, b = _order_row(conn, src), _order_row(conn, dst)
    _ensure_open(a); _ensure_open(b)
    if src == dst:
        raise AccountingError("اختر طلبين مختلفين")
    conn.execute("UPDATE order_lines SET order_id=? WHERE order_id=?", (dst, src))
    conn.execute("UPDATE orders SET status='canceled', closed_at=datetime('now','localtime'), notes=? WHERE id=?",
                 (f"دُمج مع الطلب رقم {b['number']}", src))
    conn.execute("UPDATE orders SET guests=guests+? WHERE id=?", (a["guests"], dst))
    audit(conn, user_id, "update", "order", dst, f"دمج الطلب رقم {a['number']} مع الطلب رقم {b['number']}")
    return get_order(conn, dst)


def split_order(conn, oid: int, parts: list[dict], user_id=None) -> dict:
    """يفصل أصنافاً (بكميات محددة) إلى طلب جديد على الطاولة نفسها لتُدفع منفردة."""
    o = get_order(conn, oid)
    _ensure_open(o)
    by_id = {l["id"]: l for l in o["lines"]}
    moves = [(by_id[int(p["line_id"])], float(p["qty"])) for p in parts or []
             if int(p.get("line_id") or 0) in by_id and float(p.get("qty") or 0) > 0]
    if not moves:
        raise AccountingError("اختر الأصناف المراد فصلها")
    new = conn.execute(
        """INSERT INTO orders(number,kind,table_id,waiter_id,guests,service_rate,warehouse_id,created_by)
           VALUES (?,?,?,?,1,?,?,?)""",
        (next_seq(conn, "order"), o["kind"], o["table_id"], o["waiter_id"], o["service_rate"], o["warehouse_id"], user_id)).lastrowid
    for line, qty in moves:
        qty = min(qty, line["qty"])
        ratio = qty / line["qty"]
        conn.execute("INSERT INTO order_lines(order_id,item_id,qty,price,notes,sent_qty,ready,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                     (new, line["item_id"], qty, line["price"], json.dumps(line["notes"], ensure_ascii=False),
                      line["sent_qty"] * ratio, line["ready"], line["sent_at"]))
        if qty >= line["qty"] - 1e-9:
            conn.execute("DELETE FROM order_lines WHERE id=?", (line["id"],))
        else:
            conn.execute("UPDATE order_lines SET qty=qty-?, sent_qty=sent_qty-? WHERE id=?",
                         (qty, line["sent_qty"] * ratio, line["id"]))
    audit(conn, user_id, "update", "order", oid, f"فصل جزء من الطلب رقم {o['number']} إلى طلب جديد")
    return get_order(conn, new)


def cancel_order(conn, oid: int, reason: str = "", user_id=None):
    o = get_order(conn, oid)
    _ensure_open(o)
    conn.execute("UPDATE orders SET status='canceled', closed_at=datetime('now','localtime'), notes=? WHERE id=?",
                 (reason or o["notes"], oid))
    audit(conn, user_id, "delete", "order", oid,
          f"إلغاء الطلب رقم {o['number']} بقيمة {o['total']:,.2f}" + (f" — {reason}" if reason else ""),
          {"lines": [{"item": l["item_name"], "qty": l["qty"], "sent": l["sent_qty"]} for l in o["lines"]]})


def pay_order(conn, oid: int, data: dict, user_id=None) -> dict:
    """يحوّل الطلب إلى فاتورة مبيع (تخصم المكوّنات وترحّل القيد) ويحرّر الطاولة."""
    o = get_order(conn, oid)
    _ensure_open(o)
    if not o["lines"]:
        raise AccountingError("الطلب فارغ")
    notes = f"طلب {o['kind_label']} رقم {o['number']}" + (f" — {o['table_name']}" if o["table_name"] else "")
    if o["kind"] == "delivery" and o["customer_name"]:
        notes += f" — {o['customer_name']} {o['customer_phone'] or ''}"
    inv = create_invoice(conn, {
        "kind": "sale", "date": data.get("date") or today(), "payment": data.get("payment") or "cash",
        "party_id": data.get("party_id") or o["party_id"], "cash_account_id": data.get("cash_account_id"),
        "warehouse_id": o["warehouse_id"], "discount": o["discount"] or 0, "tax_rate": o["tax_rate"],
        "service_amount": r2(o["service"] + o["delivery_fee"]), "salesperson_id": o["waiter_id"],
        "order_id": oid, "notes": notes,
        "lines": [{"item_id": l["item_id"], "qty": l["qty"], "price": l["price"]} for l in o["lines"]]}, user_id)
    conn.execute("UPDATE orders SET status='paid', closed_at=datetime('now','localtime'), invoice_id=?, delivery_status=? WHERE id=?",
                 (inv["id"], "delivered" if o["kind"] == "delivery" and data.get("delivered") else o["delivery_status"], oid))
    return inv


def list_orders(conn, status="open", kind=None, limit=200) -> list[dict]:
    q, args = "SELECT id FROM orders WHERE 1=1", []
    if status:
        q += " AND status=?"; args.append(status)
    if kind:
        q += " AND kind=?"; args.append(kind)
    out = []
    for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall():
        o = _order_row(conn, r["id"])
        lines = rows(conn.execute("SELECT qty, price FROM order_lines WHERE order_id=?", (r["id"],)))
        o.update(_order_totals(conn, o, lines))
        o["kind_label"], o["lines_count"] = ORDER_KIND[o["kind"]], len(lines)
        out.append(o)
    return out


def restaurant_report(conn, date_from=None, date_to=None) -> dict:
    """مبيعات المطعم: حسب نوع الطلب، التصنيف، الصنف، والنادل."""
    cond, args = "", []
    if date_from:
        cond += " AND i.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND i.date<=?"; args.append(date_to)
    by_kind = rows(conn.execute(f"""SELECT o.kind, COUNT(*) n, COALESCE(SUM(i.total_base),0) total, COALESCE(SUM(o.guests),0) guests
                                    FROM orders o JOIN invoices i ON i.id=o.invoice_id WHERE o.status='paid' {cond}
                                    GROUP BY o.kind""", args))
    for k in by_kind:
        k["kind_label"], k["total"] = ORDER_KIND[k["kind"]], r2(k["total"])
        k["avg"] = r2(k["total"] / k["n"]) if k["n"] else 0
    by_item = rows(conn.execute(f"""SELECT it.name, COALESCE(it.category,'بدون تصنيف') category, SUM(il.qty) qty,
                                           SUM(il.total * i.rate) total, SUM(il.qty_base * il.unit_cost) cost
                                    FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id JOIN items it ON it.id=il.item_id
                                    WHERE i.kind='sale' AND i.order_id IS NOT NULL {cond}
                                    GROUP BY it.id ORDER BY total DESC LIMIT 50""", args))
    cats: dict = {}
    for r in by_item:
        r["total"], r["cost"] = r2(r["total"]), r2(r["cost"])
        r["profit"] = r2(r["total"] - r["cost"])
        c = cats.setdefault(r["category"], {"category": r["category"], "total": 0.0, "profit": 0.0})
        c["total"] = r2(c["total"] + r["total"]); c["profit"] = r2(c["profit"] + r["profit"])
    by_waiter = rows(conn.execute(f"""SELECT COALESCE(e.name,'بدون نادل') name, COUNT(*) n, SUM(i.total_base) total
                                      FROM orders o JOIN invoices i ON i.id=o.invoice_id LEFT JOIN employees e ON e.id=o.waiter_id
                                      WHERE o.status='paid' {cond} GROUP BY o.waiter_id ORDER BY total DESC""", args))
    for w in by_waiter:
        w["total"] = r2(w["total"])
    canceled = conn.execute("SELECT COUNT(*) FROM orders WHERE status='canceled'").fetchone()[0]
    return {"by_kind": by_kind, "by_item": by_item, "by_category": sorted(cats.values(), key=lambda x: -x["total"]),
            "by_waiter": by_waiter, "canceled": canceled,
            "total": r2(sum(k["total"] for k in by_kind)), "orders": sum(k["n"] for k in by_kind)}


# ───────────────────────── التقارير ─────────────────────────

def trial_balance(conn, to_date=None) -> dict:
    accs = [a for a in accounts_tree(conn, to_date) if not a["is_group"] and (a["debit"] or a["credit"])]
    for a in accs:
        net = a["debit"] - a["credit"]
        a["bal_debit"], a["bal_credit"] = r2(max(net, 0)), r2(max(-net, 0))
    tot = {k: r2(sum(a[k] for a in accs)) for k in ("debit", "credit", "bal_debit", "bal_credit")}
    return {"rows": accs, "totals": tot, "balanced": abs(tot["debit"] - tot["credit"]) < EPS}


def account_statement(conn, account_id: int, date_from=None, date_to=None) -> dict:
    a = get_account(conn, account_id)
    ids = descendant_ids(conn, account_id)
    ph = ",".join("?" * len(ids))
    sign = 1 if a["type"] in DEBIT_NATURE else -1
    opening = 0.0
    if date_from:
        r = conn.execute(
            f"""SELECT COALESCE(SUM(debit-credit),0) FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id
                WHERE jl.account_id IN ({ph}) AND j.date<?""", ids + [date_from]).fetchone()
        opening = r[0] * sign
    q = f"""SELECT j.id journal_id, j.number, j.date, j.description, j.source, j.source_id,
                   jl.debit, jl.credit, jl.memo, jl.currency, jl.fx_amount, a.name account_name
            FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id JOIN accounts a ON a.id=jl.account_id
            WHERE jl.account_id IN ({ph})"""
    args = list(ids)
    if date_from:
        q += " AND j.date>=?"; args.append(date_from)
    if date_to:
        q += " AND j.date<=?"; args.append(date_to)
    lines = rows(conn.execute(q + " ORDER BY j.date, j.id, jl.id", args))
    bal = opening
    for ln in lines:
        bal += (ln["debit"] - ln["credit"]) * sign
        ln["balance"] = r2(bal)
    return {
        "account": a, "opening": r2(opening), "lines": lines, "closing": r2(bal),
        "total_debit": r2(sum(l["debit"] for l in lines)), "total_credit": r2(sum(l["credit"] for l in lines)),
    }


def income_statement(conn, date_from=None, date_to=None) -> dict:
    q = """SELECT a.id, a.code, a.name, a.type, SUM(jl.debit) d, SUM(jl.credit) c
           FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id JOIN accounts a ON a.id=jl.account_id
           WHERE a.type IN ('revenue','expense')"""
    args = []
    if date_from:
        q += " AND j.date>=?"; args.append(date_from)
    if date_to:
        q += " AND j.date<=?"; args.append(date_to)
    data = rows(conn.execute(q + " GROUP BY a.id ORDER BY a.code", args))
    revenue = [dict(code=r["code"], name=r["name"], amount=r2(r["c"] - r["d"])) for r in data if r["type"] == "revenue"]
    expense = [dict(code=r["code"], name=r["name"], amount=r2(r["d"] - r["c"])) for r in data if r["type"] == "expense"]
    tr, te = r2(sum(x["amount"] for x in revenue)), r2(sum(x["amount"] for x in expense))
    cogs = next((x["amount"] for x in expense if x["code"] == SYS["cogs"]), 0)
    return {"revenue": revenue, "expense": expense, "total_revenue": tr, "total_expense": te,
            "gross_profit": r2(tr - cogs), "net_profit": r2(tr - te)}


def balance_sheet(conn, as_of=None) -> dict:
    accs = accounts_tree(conn, as_of)
    top = {a["type"]: a for a in accs if a["parent_id"] is None}

    def section(t):
        return [a for a in accs if a["type"] == t and a["parent_id"] is not None and a["balance"]]
    profit = r2(top["revenue"]["balance"] - top["expense"]["balance"])
    assets = top["asset"]["balance"]
    liab = top["liability"]["balance"]
    equity = r2(top["equity"]["balance"] + profit)
    return {
        "assets": section("asset"), "liabilities": section("liability"), "equity": section("equity"),
        "total_assets": assets, "total_liabilities": liab, "period_profit": profit,
        "total_equity": equity, "balanced": abs(assets - liab - equity) < 0.05,
    }


def vat_report(conn, date_from=None, date_to=None) -> dict:
    """إقرار ضريبة القيمة المضافة: المخرجات (على المبيعات) − المدخلات (على المشتريات)."""
    cond, args = "", []
    if date_from:
        cond += " AND j.date>=?"; args.append(date_from)
    if date_to:
        cond += " AND j.date<=?"; args.append(date_to)

    def net(code, sign):
        r = conn.execute(f"""SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM journal_lines jl JOIN journals j ON j.id=jl.journal_id
                             WHERE jl.account_id=? {cond}""", [acc_id(conn, code)] + args).fetchone()
        return r2(r[0] * sign)
    out_tax, in_tax = net(SYS["vat_out"], 1), net(SYS["vat_in"], -1)
    invs = rows(conn.execute(
        f"""SELECT i.id, i.number, i.kind, i.date, p.name party_name, i.net_base, ROUND(i.tax_amount*i.rate,2) tax_base, i.tax_rate
            FROM invoices i LEFT JOIN parties p ON p.id=i.party_id JOIN journals j ON j.id=i.journal_id
            WHERE i.tax_amount != 0 {cond} ORDER BY i.date, i.id""", args))
    sales_net = r2(sum(i["net_base"] * (1 if i["kind"] == "sale" else -1) for i in invs if i["kind"].startswith("sale")))
    purch_net = r2(sum(i["net_base"] * (1 if i["kind"] == "purchase" else -1) for i in invs if i["kind"].startswith("purchase")))
    for i in invs:
        i["kind_label"] = KIND_LABELS[i["kind"]]
    return {"output_tax": out_tax, "input_tax": in_tax, "payable": r2(out_tax - in_tax),
            "taxable_sales": sales_net, "taxable_purchases": purch_net, "invoices": invs}


def stock_report(conn) -> dict:
    whs = rows(conn.execute("SELECT id, name FROM warehouses ORDER BY id"))
    items = list_items(conn)
    return {"warehouses": whs, "items": items,
            "totals": {str(w["id"]): r2(sum(i["stock"].get(w["id"], 0) * i["costs"].get(w["id"], i["avg_cost"])
                                            for i in items)) for w in whs},
            "total_value": r2(sum(i["value"] for i in items))}


def cash_balance(conn) -> float:
    r = conn.execute("""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl
                        JOIN accounts a ON a.id=jl.account_id WHERE a.is_cash=1""").fetchone()
    return r2(r[0])


def dashboard(conn) -> dict:
    t = today()
    month = t[:7]

    def sales_sum(cond, args):
        r = conn.execute(f"SELECT COALESCE(SUM(net_base),0), COALESCE(SUM(net_base-cost_total),0), COUNT(*) FROM invoices WHERE kind='sale' AND {cond}", args).fetchone()
        return {"total": r2(r[0]), "profit": r2(r[1]), "count": r[2]}
    parties = list_parties(conn)
    inc = income_statement(conn, month + "-01", t)
    trend = rows(conn.execute(
        """SELECT substr(date,1,7) month,
                  SUM(CASE WHEN kind='sale' THEN net_base ELSE 0 END) sales,
                  SUM(CASE WHEN kind='purchase' THEN net_base ELSE 0 END) purchases,
                  SUM(CASE WHEN kind='sale' THEN net_base-cost_total ELSE 0 END) profit
           FROM invoices WHERE date >= date(?, 'start of month', '-5 months')
           GROUP BY month ORDER BY month""", (t,)))
    top_items = rows(conn.execute(
        """SELECT it.name, SUM(il.qty) qty, SUM(il.total * i.rate) total FROM invoice_lines il
           JOIN invoices i ON i.id=il.invoice_id JOIN items it ON it.id=il.item_id
           WHERE i.kind='sale' AND substr(i.date,1,7)=? GROUP BY it.id ORDER BY total DESC LIMIT 5""", (month,)))
    items = list_items(conn)
    return {
        "today": sales_sum("date=?", (t,)),
        "month": sales_sum("substr(date,1,7)=?", (month,)),
        "month_net_profit": inc["net_profit"],
        "month_expenses": r2(sum(x["amount"] for x in inc["expense"] if x["code"] != SYS["cogs"])),
        "cash": cash_balance(conn),
        "receivables": r2(sum(p["balance"] for p in parties if p["kind"] == "customer" and p["balance"] > 0)),
        "payables": r2(sum(p["balance"] for p in parties if p["kind"] == "supplier" and p["balance"] > 0)),
        "stock_value": r2(sum(i["value"] for i in items)),
        "low_stock": [i for i in items if i["low"]][:10],
        "trend": trend,
        "top_items": top_items,
        "recent": list_invoices(conn, limit=8),
    }


def is_integrity_error(e: Exception) -> bool:
    return isinstance(e, sqlite3.IntegrityError)
