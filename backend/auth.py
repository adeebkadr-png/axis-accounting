"""المستخدمون والصلاحيات وجلسات الدخول."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

from accounting import AccountingError, audit
from db import rows

# الصلاحية ← وصفها
PERMISSIONS = {
    "sales": "فواتير المبيع ومرتجعاتها",
    "purchases": "فواتير الشراء ومرتجعاتها",
    "vouchers": "سندات القبض والدفع",
    "journal": "قيود اليومية اليدوية",
    "accounts": "تعديل دليل الحسابات",
    "parties": "إدارة العملاء والموردين",
    "items": "إدارة المواد",
    "warehouses": "المستودعات والتحويل بينها",
    "reports": "التقارير المالية",
    "ai": "المساعد الذكي",
    "opening": "الأرصدة الافتتاحية",
    "discount": "الحسم وتخفيض الأسعار عند البيع",
    "edit": "تعديل الفواتير والقيود المحفوظة",
    "delete": "حذف الفواتير والقيود",
    "settings": "الإعدادات والعملات والضريبة",
    "users": "إدارة المستخدمين",
    "restaurant": "طلبات المطعم والطاولات",
    "kitchen": "شاشة المطبخ",
    "checks": "الشيكات والأوراق التجارية",
    "assets": "الأصول الثابتة والاهتلاك",
    "hr": "الموظفون والرواتب والعمولات",
    "period": "إقفال الفترات والسنة المالية",
    "audit": "الاطلاع على سجل التدقيق",
}

ROLES = {
    "admin": {"label": "مدير", "perms": list(PERMISSIONS)},
    "accountant": {"label": "محاسب", "perms": [p for p in PERMISSIONS if p not in ("settings", "users")]},
    "cashier": {"label": "كاشير / بائع", "perms": ["sales", "vouchers", "parties"]},
    "storekeeper": {"label": "أمين مستودع", "perms": ["purchases", "items", "warehouses"]},
    "waiter": {"label": "نادل", "perms": ["restaurant"]},
    "chef": {"label": "طاهٍ (شاشة المطبخ)", "perms": ["kitchen"]},
}

SESSION_DAYS = 30
ITERATIONS = 200_000
MAX_ATTEMPTS = 5          # محاولات الدخول الفاشلة قبل القفل
LOCK_MINUTES = 15


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(calc, digest)
    except ValueError:
        return False


def user_perms(user: dict) -> set[str]:
    if user["role"] == "admin":
        return set(PERMISSIONS)
    return set(json.loads(user["permissions"] or "[]")) & set(PERMISSIONS)


def public(user: dict) -> dict:
    locked = bool(user.get("locked_until") and user["locked_until"] > datetime.now().isoformat(" ", "seconds"))
    return {"id": user["id"], "username": user["username"], "full_name": user["full_name"],
            "role": user["role"], "role_label": ROLES.get(user["role"], {}).get("label", user["role"]),
            "active": bool(user["active"]), "permissions": sorted(user_perms(user)),
            "locked": locked, "failed_attempts": user.get("failed_attempts") or 0,
            "created_at": user.get("created_at")}


def needs_setup(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def _validate(conn, data: dict, uid: int | None = None) -> tuple:
    username = (data.get("username") or "").strip()
    full_name = (data.get("full_name") or "").strip() or username
    role = data.get("role") or "accountant"
    if not username or len(username) < 3:
        raise AccountingError("اسم المستخدم يجب أن يكون 3 أحرف على الأقل")
    if role not in ROLES:
        raise AccountingError("الدور غير صحيح")
    dup = conn.execute("SELECT id FROM users WHERE username=? AND id IS NOT ?", (username, uid)).fetchone()
    if dup:
        raise AccountingError("اسم المستخدم مستخدم مسبقاً")
    perms = data.get("permissions")
    if perms is None:
        perms = ROLES[role]["perms"]
    perms = [p for p in perms if p in PERMISSIONS]
    return username, full_name, role, json.dumps(perms)


def _check_password(pw: str):
    if len(pw or "") < 6:
        raise AccountingError("كلمة المرور يجب أن تكون 6 أحرف على الأقل")


def create_user(conn, data: dict, actor=None) -> dict:
    username, full_name, role, perms = _validate(conn, data)
    _check_password(data.get("password"))
    cur = conn.execute(
        "INSERT INTO users(username,full_name,password_hash,role,permissions) VALUES (?,?,?,?,?)",
        (username, full_name, hash_password(data["password"]), role, perms))
    audit(conn, actor or cur.lastrowid, "create", "user", cur.lastrowid,   # إنشاء المدير الأول يُنسب إليه
          f"إنشاء مستخدم «{full_name}» ({username}) بدور {ROLES[role]['label']}", {"permissions": json.loads(perms)})
    return get_user(conn, cur.lastrowid)


def update_user(conn, uid: int, data: dict, acting_user_id: int):
    old = get_user(conn, uid)
    username, full_name, role, perms = _validate(conn, data, uid)
    active = int(bool(data.get("active", True)))
    if old["role"] == "admin" and (role != "admin" or not active) and _admin_count(conn) <= 1:
        raise AccountingError("يجب أن يبقى مدير واحد فعّال على الأقل")
    if uid == acting_user_id and not active:
        raise AccountingError("لا يمكنك إيقاف حسابك أنت")
    conn.execute("UPDATE users SET username=?,full_name=?,role=?,permissions=?,active=? WHERE id=?",
                 (username, full_name, role, perms, active, uid))
    changed = {}
    for k, before, after in (("username", old["username"], username), ("full_name", old["full_name"], full_name),
                             ("role", old["role"], role), ("permissions", json.loads(old["permissions"] or "[]"), json.loads(perms)),
                             ("active", bool(old["active"]), bool(active))):
        if before != after:
            changed[k] = [before, after]
    if data.get("password"):
        _check_password(data["password"])
        conn.execute("UPDATE users SET password_hash=?, failed_attempts=0, locked_until=NULL WHERE id=?",
                     (hash_password(data["password"]), uid))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))   # تغيير المدير لكلمة المرور ينهي جلسات المستخدم
        changed["password"] = "غُيّرت"
    if not active:
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
    if data.get("unlock"):
        conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
        changed["unlock"] = "فُكّ القفل"
    if changed:
        audit(conn, acting_user_id, "update", "user", uid, f"تعديل المستخدم «{old['full_name']}»", changed)


def change_own_password(conn, uid: int, old_pw: str, new_pw: str, keep_token: str | None = None):
    u = get_user(conn, uid)
    if not verify_password(old_pw or "", u["password_hash"]):
        raise AccountingError("كلمة المرور الحالية غير صحيحة")
    _check_password(new_pw)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_pw), uid))
    # أي جلسة أخرى لهذا الحساب (جهاز آخر أو جلسة مسروقة) تنتهي فوراً
    conn.execute("DELETE FROM auth_sessions WHERE user_id=? AND token IS NOT ?", (uid, keep_token))
    audit(conn, uid, "update", "user", uid, f"غيّر «{u['full_name']}» كلمة مروره")


def delete_user(conn, uid: int, acting_user_id: int):
    u = get_user(conn, uid)
    if uid == acting_user_id:
        raise AccountingError("لا يمكنك حذف حسابك أنت")
    if u["role"] == "admin" and _admin_count(conn) <= 1:
        raise AccountingError("لا يمكن حذف آخر مدير")
    used = any(conn.execute(f"SELECT 1 FROM {t} WHERE created_by=? LIMIT 1", (uid,)).fetchone()
               for t in ("journals", "invoices", "transfers"))
    if used:
        raise AccountingError("لهذا المستخدم عمليات مسجلة، أوقفه بدلاً من حذفه")
    audit(conn, acting_user_id, "delete", "user", uid, f"حذف المستخدم «{u['full_name']}» ({u['username']})")
    conn.execute("DELETE FROM users WHERE id=?", (uid,))


def _admin_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]


def get_user(conn, uid: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise AccountingError("المستخدم غير موجود")
    return dict(row)


def list_users(conn) -> list[dict]:
    return [public(u) for u in rows(conn.execute("SELECT * FROM users ORDER BY id"))]


def login(conn, username: str, password: str) -> tuple[str, dict]:
    """يرفع خطأً عند الفشل — للاستخدام الداخلي. واجهة الدخول تستعمل attempt_login."""
    r = attempt_login(conn, username, password)
    if not r["ok"]:
        raise AccountingError(r["error"])
    return r["token"], r["user"]


def attempt_login(conn, username: str, password: str) -> dict:
    """لا يرفع خطأً حتى تُحفظ محاولات الفشل في القاعدة بدل أن تُلغى مع تراجع المعاملة."""
    username = (username or "").strip()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and row["locked_until"] and row["locked_until"] > datetime.now().isoformat(" ", "seconds"):
        left = max(1, round((datetime.fromisoformat(row["locked_until"]) - datetime.now()).total_seconds() / 60))
        return {"ok": False, "error": f"الحساب مقفل مؤقتاً بسبب محاولات دخول فاشلة. أعد المحاولة بعد {left} دقيقة"}
    if not row or not verify_password(password or "", row["password_hash"]):
        if row:
            attempts = row["failed_attempts"] + 1
            locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(" ", "seconds") if attempts >= MAX_ATTEMPTS else None
            conn.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (attempts, locked, row["id"]))
            audit(conn, None, "login_failed", "user", row["id"],
                  f"محاولة دخول فاشلة للحساب «{username}»" + (f" — قُفل الحساب {LOCK_MINUTES} دقيقة" if locked else ""))
            if locked:
                return {"ok": False, "error": f"تجاوزت {MAX_ATTEMPTS} محاولات فاشلة. الحساب مقفل {LOCK_MINUTES} دقيقة"}
            return {"ok": False, "error": f"اسم المستخدم أو كلمة المرور غير صحيحة (تبقّى {MAX_ATTEMPTS - attempts} محاولات)"}
        audit(conn, None, "login_failed", "user", None, f"محاولة دخول باسم غير موجود: «{username}»")
        return {"ok": False, "error": "اسم المستخدم أو كلمة المرور غير صحيحة"}
    if not row["active"]:
        return {"ok": False, "error": "هذا الحساب موقوف، راجع مدير النظام"}
    token = secrets.token_urlsafe(32)
    conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (row["id"],))
    conn.execute("INSERT INTO auth_sessions(token,user_id) VALUES (?,?)", (token, row["id"]))
    audit(conn, row["id"], "login", "user", row["id"], f"تسجيل دخول: {row['full_name']}")
    conn.execute(f"DELETE FROM auth_sessions WHERE last_seen < datetime('now','localtime','-{SESSION_DAYS} days')")
    return {"ok": True, "token": token, "user": dict(row)}


def user_from_token(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        f"""SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token=? AND u.active=1 AND s.last_seen >= datetime('now','localtime','-{SESSION_DAYS} days')""",
        (token,)).fetchone()
    if row:
        conn.execute("UPDATE auth_sessions SET last_seen=datetime('now','localtime') WHERE token=?", (token,))
    return dict(row) if row else None


def logout(conn, token: str):
    conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
