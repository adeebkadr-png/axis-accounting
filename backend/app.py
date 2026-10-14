"""خادم الواجهة البرمجية (FastAPI) + تقديم الواجهة الأمامية.

كل مسارات /api تتطلب تسجيل الدخول (عدا مسارات الدخول نفسها)، وكل عملية تتحقق من صلاحية المستخدم.
"""
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import base64
from datetime import datetime

import accounting as acc
import ai
import auth
import devices
import db as dbmod
from db import BACKUP_DIR, DATA_DIR, DB_PATH, backup_db, backup_if_due, connect, init_db, list_backups, tx

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
log = logging.getLogger("axis")


def _setup_logging():
    """سجل أخطاء دوّار في data/logs (٥ ملفات × ١ ميغابايت) — يُعرض من الإعدادات."""
    for old in list(log.handlers):       # عند إعادة التحميل: لا تكرار للمعالجات ولا كتابة في مسار قديم
        log.removeHandler(old)
        old.close()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

# عند التجميع بـ PyInstaller تكون الملفات الثابتة داخل حزمة التطبيق
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
STATIC = BUNDLE / "static"
ENV_FILE = DATA_DIR / ".env"
COOKIE = "sid"
PUBLIC = {"/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/version"}


def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() and v.strip() and not os.environ.get(k.strip()):
                os.environ[k.strip()] = v.strip()


def _backup_loop():
    """نسخة احتياطية يومية تلقائية ما دام البرنامج يعمل."""
    while True:
        try:
            backup_if_due()
        except Exception as e:                      # النسخ الاحتياطي لا يوقف البرنامج أبداً
            log.error("تعذر أخذ نسخة احتياطية: %s", e)
        time.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _setup_logging()
    init_db()
    _load_env()
    log.info("بدء تشغيل البرنامج — قاعدة البيانات: %s", DB_PATH)
    threading.Thread(target=_backup_loop, daemon=True).start()
    yield


app = FastAPI(title="المحاسب الذكي", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2000)     # قوائم آلاف المواد تُضغط قبل إرسالها عبر الشبكة


@app.exception_handler(acc.AccountingError)
async def accounting_error(_: Request, e: acc.AccountingError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(ai.AIError)
async def ai_error(_: Request, e: ai.AIError):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.exception_handler(devices.DeviceError)
async def device_error(_: Request, e: devices.DeviceError):
    return JSONResponse({"detail": str(e)}, status_code=503)


def _same_origin(request: Request) -> bool:
    """يرفض طلبات التعديل القادمة من موقع آخر (حماية من تزوير الطلبات CSRF)."""
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin != "null"
    return urlparse(origin).netloc == request.headers.get("host")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            return JSONResponse({"detail": "طلب مرفوض: مصدر غير موثوق"}, status_code=403)
        if path not in PUBLIC:
            with tx() as conn:
                user = auth.user_from_token(conn, request.cookies.get(COOKIE))
            if not user:
                return JSONResponse({"detail": "انتهت الجلسة، سجّل الدخول من جديد"}, status_code=401)
            request.state.user = user
            request.state.perms = auth.user_perms(user)
    try:
        response = await call_next(request)
    except Exception:
        # خطأ غير متوقع: يُسجَّل كاملاً برقم مرجعي، والمستخدم يرى رسالة مفهومة بدل صفحة خطأ
        ref = uuid.uuid4().hex[:8]
        who = getattr(request.state, "user", None)
        log.error("خطأ [%s] %s %s — المستخدم: %s\n%s", ref, request.method, path,
                  who and who.get("username"), traceback.format_exc())
        return JSONResponse({"detail": f"حدث خطأ غير متوقع ولم تُحفظ العملية. رقم المرجع: {ref}"}, status_code=500)
    if path.startswith("/static/"):      # المتصفح يتحقق من الخادم قبل استخدام نسخته المخزّنة
        response.headers["Cache-Control"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def need(request: Request, *perms: str):
    """يتحقق من امتلاك المستخدم كل الصلاحيات المطلوبة."""
    missing = [p for p in perms if p not in request.state.perms]
    if missing:
        raise HTTPException(403, f"ليست لديك صلاحية: {auth.PERMISSIONS[missing[0]]}")


def need_any(request: Request, *perms: str):
    if not any(p in request.state.perms for p in perms):
        raise HTTPException(403, "ليست لديك صلاحية لهذه العملية")


def uid(request: Request) -> int:
    return request.state.user["id"]


def read(fn, *a, **kw):
    conn = connect()
    try:
        return fn(conn, *a, **kw)
    finally:
        conn.close()


def write(fn, *a, **kw):
    with tx() as conn:
        return fn(conn, *a, **kw)


def invoice_perm(kind: str) -> str:
    return "sales" if kind in ("sale", "sale_return") else "purchases"


# ───── الدخول والمستخدمون ─────
def _session_response(token: str, user: dict, request: Request | None = None):
    resp = JSONResponse({"user": auth.public(user)})
    secure = bool(request and request.url.scheme == "https")
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure, max_age=auth.SESSION_DAYS * 86400)
    return resp


@app.get("/api/auth/status")
def auth_status(request: Request):
    with tx() as conn:
        user = auth.user_from_token(conn, request.cookies.get(COOKIE))
        return {"needs_setup": auth.needs_setup(conn), "user": auth.public(user) if user else None,
                "company_name": acc.settings(conn).get("company_name")}


@app.post("/api/auth/setup")
async def auth_setup(request: Request):
    """إنشاء حساب المدير الأول — متاح فقط عندما لا يوجد أي مستخدم."""
    body = await request.json()
    with tx() as conn:
        if not auth.needs_setup(conn):
            raise HTTPException(403, "تم إعداد النظام مسبقاً")
        auth.create_user(conn, {**body, "role": "admin"})
        token, user = auth.login(conn, body.get("username"), body.get("password"))
    return _session_response(token, user, request)


@app.post("/api/auth/login")
async def auth_login(request: Request):
    body = await request.json()
    r = write(auth.attempt_login, body.get("username"), body.get("password"))
    if not r["ok"]:
        raise HTTPException(400, r["error"])
    return _session_response(r["token"], r["user"], request)


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    write(auth.logout, request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/api/auth/password")
async def auth_password(request: Request):
    b = await request.json()
    write(auth.change_own_password, uid(request), b.get("old_password"), b.get("new_password"),
          request.cookies.get(COOKIE))
    return {"ok": True}


@app.get("/api/users")
def users(request: Request):
    need(request, "users")
    return {"users": read(auth.list_users), "permissions": auth.PERMISSIONS, "roles": auth.ROLES}


@app.post("/api/users")
async def add_user(request: Request):
    need(request, "users")
    return auth.public(write(auth.create_user, await request.json(), uid(request)))


@app.put("/api/users/{user_id}")
async def edit_user(user_id: int, request: Request):
    need(request, "users")
    write(auth.update_user, user_id, await request.json(), uid(request))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def del_user(user_id: int, request: Request):
    need(request, "users")
    write(auth.delete_user, user_id, uid(request))
    return {"ok": True}


# ───── الإعدادات والعملات ─────
@app.get("/api/settings")
def get_settings(request: Request):
    def load(conn):
        s = acc.settings(conn)
        s.pop("currency", None)
        base = acc.base_currency(conn)
        s.update(currency=base["symbol"], base_currency=base["code"], currencies=acc.list_currencies(conn),
                 warehouses=acc.list_warehouses(conn), vat_enabled=s.get("vat_enabled") == "1",
                 vat_rate=float(s.get("vat_rate") or 0))
        return s
    s = read(load)
    s.update(ai_ready=ai.has_credentials(), ai_model=ai.MODEL, user=auth.public(request.state.user))
    return s


@app.put("/api/settings")
async def put_settings(request: Request):
    need(request, "settings")
    body = await request.json()
    key = (body.pop("api_key", "") or "").strip()
    if key:
        ai.set_api_key(key)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ENV_FILE.write_text(f"ANTHROPIC_API_KEY={key}\n", encoding="utf-8")
    if "vat_rate" in body:
        r = float(body["vat_rate"] or 0)
        if r < 0 or r > 100:
            raise HTTPException(400, "نسبة الضريبة غير صحيحة")
    if "vat_enabled" in body:
        body["vat_enabled"] = "1" if body["vat_enabled"] else "0"
    allowed = {"company_name", "tax_number", "address", "phone", "vat_enabled", "vat_rate",
               "scale_prefix", "scale_mode", "pos_print", "allow_negative_stock", "expiry_alert_days",
               "enforce_credit_limit", "restaurant_mode", "service_rate", "delivery_fee", "backup_extra_dir"}
    with tx() as c:
        for k, v in body.items():
            if k in allowed:
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (k, str(v)))
    return get_settings(request)


@app.get("/api/currencies")
def currencies():
    return read(acc.list_currencies)


@app.post("/api/currencies")
async def add_currency(request: Request):
    need(request, "settings")
    write(acc.save_currency, await request.json(), True, uid(request))
    return read(acc.list_currencies)


@app.put("/api/currencies/{code}")
async def edit_currency(code: str, request: Request):
    need(request, "settings")
    write(acc.save_currency, {**await request.json(), "code": code}, False, uid(request))
    return read(acc.list_currencies)


@app.delete("/api/currencies/{code}")
def del_currency(code: str, request: Request):
    need(request, "settings")
    write(acc.delete_currency, code, uid(request))
    return {"ok": True}


@app.get("/api/backup")
def backup(request: Request):
    need(request, "settings")
    return FileResponse(DB_PATH, filename=f"backup-{acc.today()}.db")


# ───── الحسابات ─────
@app.get("/api/accounts")
def accounts(to_date: str | None = None):
    return read(acc.accounts_tree, to_date)


@app.post("/api/accounts")
async def add_account(request: Request):
    need(request, "accounts")
    b = await request.json()
    return write(acc.create_account, b.get("name"), int(b["parent_id"]), bool(b.get("is_group")),
                 bool(b.get("is_cash")), uid(request))


@app.put("/api/accounts/{aid}")
async def edit_account(aid: int, request: Request):
    need(request, "accounts")
    b = await request.json()
    write(acc.update_account, aid, b.get("name"), b.get("is_cash"), uid(request))
    return {"ok": True}


@app.delete("/api/accounts/{aid}")
def del_account(aid: int, request: Request):
    need(request, "accounts", "delete")
    write(acc.delete_account, aid, uid(request))
    return {"ok": True}


# ───── العملاء والموردون ─────
@app.get("/api/parties")
def parties(kind: str | None = None, search: str | None = None):
    return read(acc.list_parties, kind, search)


@app.post("/api/parties")
async def add_party(request: Request):
    need(request, "parties")
    b = await request.json()
    return write(acc.create_party, b.get("name", ""), b.get("kind"), b.get("phone", ""), b.get("address", ""), uid(request))


@app.put("/api/parties/{pid}")
async def edit_party(pid: int, request: Request):
    need(request, "parties")
    b = await request.json()
    write(acc.update_party, pid, b.get("name", ""), b.get("phone", ""), b.get("address", ""), uid(request),
          b.get("price_list_id"), b.get("credit_limit"))
    return {"ok": True}


@app.delete("/api/parties/{pid}")
def del_party(pid: int, request: Request):
    need(request, "parties", "delete")
    write(acc.delete_party, pid, uid(request))
    return {"ok": True}


# ───── المواد ─────
@app.get("/api/items")
def items(search: str | None = None, warehouse_id: int | None = None):
    return read(acc.list_items, search, warehouse_id)


@app.post("/api/items")
async def add_item(request: Request):
    need_any(request, "items", "purchases")
    return write(acc.create_item, await request.json(), uid(request))


@app.put("/api/items/{iid}")
async def edit_item(iid: int, request: Request):
    need(request, "items")
    write(acc.update_item, iid, await request.json(), uid(request))
    return {"ok": True}


@app.delete("/api/items/{iid}")
def del_item(iid: int, request: Request):
    need(request, "items", "delete")
    write(acc.delete_item, iid, uid(request))
    return {"ok": True}


@app.get("/api/items/{iid}/units")
def item_units(iid: int):
    return read(acc.item_units, iid)


@app.put("/api/items/{iid}/units")
async def set_item_units(iid: int, request: Request):
    need(request, "items")
    write(acc.save_item_units, iid, (await request.json()).get("units", []), uid(request))
    return read(acc.item_units, iid)


@app.get("/api/items/{iid}/variants")
def item_variants(iid: int):
    return read(acc.item_variants, iid)


@app.post("/api/items/{iid}/variants")
async def add_variant(iid: int, request: Request):
    need(request, "items")
    return write(acc.create_variant, iid, await request.json(), uid(request))


# ───── الدفعات وتواريخ الصلاحية ─────
@app.get("/api/batches")
def batches(request: Request, item_id: int | None = None, warehouse_id: int | None = None):
    need_any(request, "items", "warehouses", "reports", "sales")
    return read(acc.list_batches, item_id, warehouse_id)


@app.get("/api/reports/expiry")
def expiry(request: Request, days: int | None = None):
    need_any(request, "reports", "items", "warehouses")
    return read(acc.expiry_report, days)


# ───── الجرد الفعلي ─────
@app.get("/api/stocktakes")
def stocktakes(request: Request):
    need(request, "warehouses")
    return read(acc.list_stocktakes)


@app.get("/api/stocktakes/sheet")
def stocktake_sheet(request: Request, warehouse_id: int, search: str | None = None):
    need(request, "warehouses")
    return read(acc.stocktake_sheet, warehouse_id, search)


@app.get("/api/stocktakes/{sid}")
def stocktake(sid: int, request: Request):
    need(request, "warehouses")
    return read(acc.get_stocktake, sid)


@app.post("/api/stocktakes")
async def add_stocktake(request: Request):
    need(request, "warehouses")
    return write(acc.create_stocktake, await request.json(), uid(request))


# ───── أوامر الشراء ─────
@app.get("/api/purchase-orders")
def purchase_orders(request: Request, status: str | None = None):
    need(request, "purchases")
    return read(acc.list_pos, status)


@app.get("/api/purchase-orders/suggestions")
def po_suggestions(request: Request, warehouse_id: int | None = None, days: int = 30):
    need(request, "purchases")
    return read(acc.reorder_suggestions, warehouse_id, days)


@app.get("/api/purchase-orders/{po_id}")
def purchase_order(po_id: int, request: Request):
    need(request, "purchases")
    return read(acc.get_po, po_id)


@app.post("/api/purchase-orders")
async def add_po(request: Request):
    need(request, "purchases")
    return write(acc.save_po, await request.json(), None, uid(request))


@app.put("/api/purchase-orders/{po_id}")
async def edit_po(po_id: int, request: Request):
    need(request, "purchases")
    return write(acc.save_po, {**await request.json(), "existing": True}, po_id, uid(request))


@app.post("/api/purchase-orders/{po_id}/status")
async def po_status(po_id: int, request: Request):
    need(request, "purchases")
    write(acc.set_po_status, po_id, (await request.json()).get("status"), uid(request))
    return read(acc.get_po, po_id)


@app.post("/api/purchase-orders/{po_id}/receive")
async def po_receive(po_id: int, request: Request):
    need(request, "purchases")
    return write(acc.receive_po, po_id, await request.json(), uid(request))


@app.delete("/api/purchase-orders/{po_id}")
def po_delete(po_id: int, request: Request):
    need(request, "purchases", "delete")
    write(acc.delete_po, po_id, uid(request))
    return {"ok": True}


# ───── قوائم الأسعار والعروض ─────
@app.get("/api/price-lists")
def price_lists():
    return read(acc.list_price_lists)


@app.post("/api/price-lists")
async def add_price_list(request: Request):
    need(request, "items")
    return {"id": write(acc.save_price_list, await request.json(), None, uid(request))}


@app.put("/api/price-lists/{pid}")
async def edit_price_list(pid: int, request: Request):
    need(request, "items")
    return {"id": write(acc.save_price_list, {**await request.json(), "existing": True}, pid, uid(request))}


@app.delete("/api/price-lists/{pid}")
def del_price_list(pid: int, request: Request):
    need(request, "items", "delete")
    write(acc.delete_price_list, pid, uid(request))
    return {"ok": True}


@app.get("/api/promotions")
def promotions(active_only: bool = False):
    return read(acc.list_promotions, active_only)


@app.post("/api/promotions")
async def add_promotion(request: Request):
    need(request, "items")
    return {"id": write(acc.save_promotion, await request.json(), None, uid(request))}


@app.put("/api/promotions/{pid}")
async def edit_promotion(pid: int, request: Request):
    need(request, "items")
    return {"id": write(acc.save_promotion, {**await request.json(), "existing": True}, pid, uid(request))}


@app.delete("/api/promotions/{pid}")
def del_promotion(pid: int, request: Request):
    need(request, "items", "delete")
    write(acc.delete_promotion, pid, uid(request))
    return {"ok": True}


@app.get("/api/price")
def price(request: Request, item_id: int, qty: float = 1, party_id: int | None = None,
          unit_factor: float = 1, unit_id: int | None = None):
    return read(acc.effective_price, item_id, qty, party_id, unit_factor, None, unit_id)


# ───── نقطة البيع والورديات ─────
@app.get("/api/pos/init")
def pos_init(request: Request, warehouse_id: int | None = None):
    need(request, "sales")
    def load(conn):
        shift = acc.current_shift(conn, uid(request))
        wid = warehouse_id or (shift and shift["warehouse_id"]) or acc.default_warehouse(conn)
        items = acc.list_items(conn, warehouse_id=wid)
        extra_units = acc.units_by_item(conn)
        for it in items:
            base = {"id": None, "name": it["unit"], "factor": 1.0, "barcode": it["barcode"], "price": it["sale_price"], "base": True}
            it["units"] = [base] + [{**u, "base": False, "price": u["price"] or acc.r2(it["sale_price"] * u["factor"])}
                                    for u in extra_units.get(it["id"], [])]
        return {"shift": shift, "warehouse_id": wid,
                "warehouses": [w for w in acc.list_warehouses(conn) if w["active"]],
                "items": items,
                "customers": acc.list_parties(conn, "customer"),
                "cash_accounts": [a for a in acc.accounts_tree(conn) if a["is_cash"] and not a["is_group"]],
                "promotions": acc.list_promotions(conn, True, acc.today()),
                "settings": {k: acc.settings(conn).get(k) for k in ("scale_prefix", "scale_mode", "pos_print", "vat_enabled", "vat_rate")}}
    return read(load)


@app.get("/api/pos/scan")
def pos_scan(request: Request, code: str, party_id: int | None = None):
    need(request, "sales")
    def load(conn):
        hit = acc.parse_barcode(conn, code)
        if not hit:
            return {"found": False}
        factor = hit["unit"]["factor"] if hit["unit"] else 1
        pricing = acc.effective_price(conn, hit["item"]["id"], hit.get("qty") or 1, party_id, factor,
                                      None, hit["unit"]["id"] if hit["unit"] else None)
        qty = hit.get("qty") or 1
        if hit.get("amount") and pricing["price"] > 0:      # ملصق يحمل المبلغ: نستنتج الكمية
            qty = round(hit["amount"] / pricing["price"], 3)
        return {"found": True, "item": hit["item"], "unit": hit["unit"], "scale": hit["scale"],
                "qty": qty, **pricing}
    return read(load)


@app.get("/api/shifts")
def shifts(request: Request):
    need(request, "sales")
    return {"current": read(acc.current_shift, uid(request)), "list": read(acc.list_shifts)}


@app.get("/api/shifts/{sid}")
def shift(sid: int, request: Request):
    need(request, "sales")
    return read(acc.get_shift, sid)


@app.post("/api/shifts/open")
async def shift_open(request: Request):
    need(request, "sales")
    b = await request.json()
    return write(acc.open_shift, uid(request), int(b["cash_account_id"]), b.get("warehouse_id"),
                 float(b.get("opening_cash") or 0))


@app.post("/api/shifts/{sid}/close")
async def shift_close(sid: int, request: Request):
    need(request, "sales")
    b = await request.json()
    sh = read(acc.get_shift, sid)
    if sh["user_id"] != uid(request) and "users" not in request.state.perms:
        raise HTTPException(403, "لا يمكنك إغلاق وردية مستخدم آخر")
    return write(acc.close_shift, sid, float(b.get("counted_cash") or 0), b.get("notes", ""), uid(request))


# ───── المستودعات والتحويل ─────
@app.get("/api/warehouses")
def warehouses():
    return read(acc.list_warehouses)


@app.post("/api/warehouses")
async def add_warehouse(request: Request):
    need(request, "warehouses")
    write(acc.save_warehouse, (await request.json()).get("name"), None, True, uid(request))
    return read(acc.list_warehouses)


@app.put("/api/warehouses/{wid}")
async def edit_warehouse(wid: int, request: Request):
    need(request, "warehouses")
    b = await request.json()
    write(acc.save_warehouse, b.get("name"), wid, bool(b.get("active", True)), uid(request))
    return read(acc.list_warehouses)


@app.delete("/api/warehouses/{wid}")
def del_warehouse(wid: int, request: Request):
    need(request, "warehouses", "delete")
    write(acc.delete_warehouse, wid, uid(request))
    return {"ok": True}


@app.get("/api/transfers")
def transfers(request: Request):
    need(request, "warehouses")
    return read(acc.list_transfers)


@app.get("/api/transfers/{tid}")
def transfer(tid: int, request: Request):
    need(request, "warehouses")
    return read(acc.get_transfer, tid)


@app.post("/api/transfers")
async def add_transfer(request: Request):
    need(request, "warehouses")
    return write(acc.create_transfer, await request.json(), uid(request))


@app.delete("/api/transfers/{tid}")
def del_transfer(tid: int, request: Request):
    need(request, "warehouses", "delete")
    write(acc.delete_transfer, tid, uid(request))
    return {"ok": True}


# ───── الفواتير ─────
def _paged(data, total: int):
    """يرفق العدد الكلي في ترويسة ليعرض المتصفح «عُرض كذا من كذا» ويطلب المزيد."""
    return JSONResponse(data, headers={"X-Total-Count": str(total)})


def _limit(n: int | None) -> int:
    return max(1, min(int(n or 300), 5000))


def _check_pricing(request: Request, kind: str, body: dict):
    """بلا صلاحية «الحسم» لا يُقبل حسم على الفاتورة ولا سعر دون السعر المعتمد."""
    if "discount" in request.state.perms or kind != "sale":
        return
    if float(body.get("discount") or 0) > 0.004:
        raise HTTPException(403, "ليست لديك صلاحية الحسم — اطلب من المسؤول")
    def check(conn):
        rate = float(body.get("rate") or 1) or 1
        for ln in body.get("lines", []):
            if not ln.get("item_id") or float(ln.get("qty") or 0) <= 0:
                continue
            floor = acc.price_floor(conn, ln, body.get("party_id"), rate)
            if float(ln.get("price") or 0) < floor - 0.01:
                name = conn.execute("SELECT name FROM items WHERE id=?", (int(ln["item_id"]),)).fetchone()
                raise HTTPException(403, f"لا يمكنك بيع «{name and name[0]}» بأقل من {floor:,.2f} — يلزم صلاحية الحسم")
    read(check)


@app.get("/api/invoices")
def invoices(request: Request, kind: str | None = None, date_from: str | None = None,
             date_to: str | None = None, party_id: int | None = None, limit: int | None = None):
    need_any(request, "sales", "purchases", "reports")
    out = read(acc.list_invoices, kind, date_from, date_to, party_id, _limit(limit))
    if "reports" not in request.state.perms:
        out = [i for i in out if invoice_perm(i["kind"]) in request.state.perms]
    extra, args = "", []
    if kind:
        extra += " AND kind=?"; args.append(kind)
    if party_id:
        extra += " AND party_id=?"; args.append(party_id)
    return _paged(out, read(acc.count_rows, "invoices", "date", date_from, date_to, extra, args))


@app.get("/api/invoices/{inv_id}")
def invoice(inv_id: int, request: Request):
    inv = read(acc.get_invoice, inv_id)
    if "reports" not in request.state.perms:
        need(request, invoice_perm(inv["kind"]))
    return inv


@app.post("/api/invoices")
async def add_invoice(request: Request):
    b = await request.json()
    need(request, invoice_perm(b.get("kind")))
    _check_pricing(request, b.get("kind"), b)
    return write(acc.create_invoice, b, uid(request))


@app.put("/api/invoices/{inv_id}")
async def edit_invoice(inv_id: int, request: Request):
    kind = read(acc.get_invoice, inv_id)["kind"]
    need(request, invoice_perm(kind), "edit")
    b = await request.json()
    _check_pricing(request, kind, b)
    return write(acc.update_invoice, inv_id, b, uid(request))


@app.delete("/api/invoices/{inv_id}")
def del_invoice(inv_id: int, request: Request):
    need(request, invoice_perm(read(acc.get_invoice, inv_id)["kind"]), "delete")
    write(acc.delete_invoice, inv_id, uid(request))
    return {"ok": True}


# ───── القيود والسندات ─────
@app.get("/api/journals")
def journals(request: Request, date_from: str | None = None, date_to: str | None = None, limit: int | None = None):
    need_any(request, "journal", "reports")
    return _paged(read(acc.list_journals, date_from, date_to, _limit(limit)),
                  read(acc.count_rows, "journals", "date", date_from, date_to))


@app.get("/api/journals/{jid}")
def journal(jid: int, request: Request):
    need_any(request, "journal", "reports", "vouchers")
    return read(acc.get_journal, jid)


@app.post("/api/journals")
async def add_journal(request: Request):
    need(request, "journal")
    b = await request.json()
    jid = write(acc.post_journal, b.get("date"), b.get("description"), b.get("lines", []), user_id=uid(request))
    return read(acc.get_journal, jid)


@app.put("/api/journals/{jid}")
async def edit_journal(jid: int, request: Request):
    need(request, "journal", "edit")
    b = await request.json()
    write(acc.update_journal, jid, b.get("date"), b.get("description"), b.get("lines", []), uid(request))
    return read(acc.get_journal, jid)


@app.delete("/api/journals/{jid}")
def del_journal(jid: int, request: Request):
    j = read(acc.get_journal, jid)
    need(request, "vouchers" if j["source"] in ("receipt", "payment") else "journal", "delete")
    write(acc.delete_journal, jid, uid(request))
    return {"ok": True}


@app.get("/api/vouchers")
def vouchers(request: Request):
    need(request, "vouchers")
    return read(acc.list_journals, limit=50, sources=("receipt", "payment"))


@app.post("/api/vouchers")
async def add_voucher(request: Request):
    need(request, "vouchers")
    jid = write(acc.create_voucher, await request.json(), uid(request))
    return read(acc.get_journal, jid)


@app.put("/api/vouchers/{jid}")
async def edit_voucher(jid: int, request: Request):
    need(request, "vouchers", "edit")
    write(acc.update_voucher, jid, await request.json(), uid(request))
    return read(acc.get_journal, jid)


@app.get("/api/vouchers/{jid}/allocations")
def voucher_allocations(jid: int, request: Request):
    need(request, "vouchers")
    return read(acc.voucher_allocations, jid)


@app.get("/api/parties/{pid}/open-invoices")
def party_open_invoices(pid: int, request: Request):
    need_any(request, "vouchers", "reports", "parties")
    return read(acc.open_invoices, pid)


# ───── المطعم: الإعداد ─────
def _crud(path: str, table: str, label: str, cols: tuple, perm: str = "settings"):
    """مسارات قائمة/إضافة/تعديل/حذف لجداول الإعداد البسيطة."""
    @app.get(path, name=f"list_{table}")
    def _list():
        return read(acc._simple_list, table)

    @app.post(path, name=f"add_{table}")
    async def _add(request: Request):
        need_any(request, perm, "restaurant")
        return {"id": write(acc._simple_save, table, label, await request.json(), None, cols, uid(request))}

    @app.put(path + "/{rid}", name=f"edit_{table}")
    async def _edit(rid: int, request: Request):
        need_any(request, perm, "restaurant")
        return {"id": write(acc._simple_save, table, label, await request.json(), rid, cols, uid(request))}

    @app.delete(path + "/{rid}", name=f"del_{table}")
    def _del(rid: int, request: Request):
        need(request, perm)
        try:
            with tx() as c:
                c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
        except Exception:
            raise HTTPException(400, f"لا يمكن حذف {label} لارتباطه ببيانات أخرى")
        return {"ok": True}


_crud("/api/restaurant/areas", "dining_areas", "الصالة", ("name",))
_crud("/api/restaurant/stations", "stations", "قسم التحضير", ("name",))
_crud("/api/restaurant/drivers", "drivers", "السائق", ("name", "phone", "active"))
_crud("/api/restaurant/modifiers", "modifiers", "الإضافة", ("name", "price", "item_id", "active"))


@app.get("/api/restaurant/tables")
def r_tables(request: Request):
    need_any(request, "restaurant", "settings")
    return read(acc.list_tables)


@app.post("/api/restaurant/tables")
async def r_add_table(request: Request):
    need_any(request, "settings", "restaurant")
    b = await request.json()
    return {"id": write(acc._simple_save, "dining_tables", "الطاولة",
                        {"name": b.get("name"), "area_id": b.get("area_id") or None, "seats": int(b.get("seats") or 4),
                         "active": int(bool(b.get("active", True)))}, None, ("name", "area_id", "seats", "active"), uid(request))}


@app.put("/api/restaurant/tables/{tid}")
async def r_edit_table(tid: int, request: Request):
    need_any(request, "settings", "restaurant")
    b = await request.json()
    return {"id": write(acc._simple_save, "dining_tables", "الطاولة",
                        {"name": b.get("name"), "area_id": b.get("area_id") or None, "seats": int(b.get("seats") or 4),
                         "active": int(bool(b.get("active", True)))}, tid, ("name", "area_id", "seats", "active"), uid(request))}


@app.delete("/api/restaurant/tables/{tid}")
def r_del_table(tid: int, request: Request):
    need(request, "settings")
    write(acc.delete_table, tid, uid(request))
    return {"ok": True}


@app.get("/api/restaurant/menu")
def r_menu(request: Request):
    """كل ما تحتاجه شاشة الطلب: الأصناف بتصنيفاتها، الإضافات، النُّدُل، السائقون، والصناديق."""
    need_any(request, "restaurant", "sales")
    def load(conn):
        s = acc.settings(conn)
        items = [i for i in acc.list_items(conn) if not i["parent_id"] or True]
        return {"items": [{k: i[k] for k in ("id", "code", "name", "sale_price", "category", "station_id", "is_recipe", "is_service", "qty", "unit")}
                          for i in items if i["sale_price"] > 0],
                "modifiers": acc.list_modifiers(conn),
                "waiters": [e for e in acc.list_employees(conn, True)],
                "drivers": [d for d in acc._simple_list(conn, "drivers") if d["active"]],
                "customers": acc.list_parties(conn, "customer"),
                "cash_accounts": [a for a in acc.accounts_tree(conn) if a["is_cash"] and not a["is_group"]],
                "settings": {k: s.get(k) for k in ("service_rate", "delivery_fee", "vat_enabled", "vat_rate", "pos_print")}}
    return read(load)


@app.get("/api/recipe-units")
def recipe_units(request: Request):
    need_any(request, "items", "reports", "restaurant")
    return read(acc.recipe_units)


@app.get("/api/items/{iid}/recipe")
def item_recipe(iid: int, request: Request):
    need_any(request, "items", "reports", "restaurant")
    return read(acc.get_recipe, iid)


@app.put("/api/items/{iid}/recipe")
async def set_recipe(iid: int, request: Request):
    need(request, "items")
    return write(acc.save_recipe, iid, (await request.json()).get("lines", []), uid(request))


@app.get("/api/reports/recipes")
def recipes_report(request: Request):
    need_any(request, "reports", "items")
    return read(acc.recipes_report)


@app.get("/api/reports/restaurant")
def restaurant_report(request: Request, date_from: str | None = None, date_to: str | None = None):
    need(request, "reports")
    return read(acc.restaurant_report, date_from, date_to)


# ───── المطعم: الطلبات ─────
async def _print_tickets(tickets: list[dict]):
    """يطبع تذاكر التحضير على طابعة المطبخ إن كانت مضبوطة (الفشل لا يوقف الطلب)."""
    cfg = _device_cfg()
    which = "kitchen" if cfg.get("kitchen_mode") else ("printer" if cfg.get("printer_mode") else None)
    if not which:
        return False
    for t in tickets:
        t.setdefault("time", datetime.now().strftime("%H:%M"))
        img = devices.kitchen_image(t, cfg.get("printer_width") or "80")
        await run_in_threadpool(devices.send, _target(cfg, which), devices.build_job(img))
    return True


@app.get("/api/orders")
def orders(request: Request, status: str | None = "open", kind: str | None = None):
    need(request, "restaurant")
    return read(acc.list_orders, status, kind)


@app.get("/api/orders/{oid}")
def order(oid: int, request: Request):
    need(request, "restaurant")
    return read(acc.get_order, oid)


def _check_order_prices(request: Request, body: dict):
    """النادل لا يُنقص سعر صنف عن سعره المعتمد (الإضافات ترفعه فقط)."""
    if "discount" in request.state.perms:
        return
    def check(conn):
        for ln in body.get("lines") or []:
            if not ln.get("item_id") or float(ln.get("qty") or 0) <= 0:
                continue
            if float(ln.get("price") or 0) < acc.price_floor(conn, ln) - 0.01:
                raise HTTPException(403, "لا يمكن تخفيض سعر الصنف — يلزم صلاحية الحسم")
    read(check)


@app.post("/api/orders")
async def add_order(request: Request):
    need(request, "restaurant")
    b = await request.json()
    _check_order_prices(request, b)
    return write(acc.open_order, b, uid(request))


@app.put("/api/orders/{oid}")
async def edit_order(oid: int, request: Request):
    need(request, "restaurant")
    b = await request.json()
    if "discount" in b and "discount" not in request.state.perms and float(b.get("discount") or 0) > 0:
        raise HTTPException(403, "الحسم على الطلب يحتاج صلاحية الحسم")
    _check_order_prices(request, b)
    return write(acc.save_order, oid, b, uid(request))


@app.post("/api/orders/{oid}/send")
async def send_order(oid: int, request: Request):
    need(request, "restaurant")
    try:
        want_print = bool((await request.json()).get("print", True))
    except Exception:
        want_print = True
    tickets = write(acc.send_to_kitchen, oid, uid(request))
    printed, error = False, None
    if want_print:      # الجهاز الذي يطبع عبر الوكيل المحلي يطلب عدم الطباعة من الخادم
        try:
            printed = await _print_tickets(tickets)
        except devices.DeviceError as e:
            error = str(e)
    return {"tickets": tickets, "printed": printed, "print_error": error, "order": read(acc.get_order, oid)}


@app.post("/api/orders/{oid}/move")
async def move_order(oid: int, request: Request):
    need(request, "restaurant")
    return write(acc.move_order, oid, int((await request.json())["table_id"]), uid(request))


@app.post("/api/orders/{oid}/merge")
async def merge_order(oid: int, request: Request):
    need(request, "restaurant")
    return write(acc.merge_orders, oid, int((await request.json())["into"]), uid(request))


@app.post("/api/orders/{oid}/split")
async def split_order(oid: int, request: Request):
    need(request, "restaurant")
    return write(acc.split_order, oid, (await request.json()).get("parts", []), uid(request))


@app.post("/api/orders/{oid}/pay")
async def pay_order(oid: int, request: Request):
    need(request, "restaurant", "sales")
    return write(acc.pay_order, oid, await request.json(), uid(request))


@app.post("/api/orders/{oid}/cancel")
async def cancel_order(oid: int, request: Request):
    need(request, "restaurant")
    o = read(acc.get_order, oid)
    if any(l["sent_qty"] > 0 for l in o["lines"]):
        need(request, "delete")      # إلغاء طلب أُرسل للمطبخ يحتاج صلاحية أعلى
    write(acc.cancel_order, oid, (await request.json()).get("reason", ""), uid(request))
    return {"ok": True}


@app.get("/api/kitchen")
def kitchen(request: Request, station_id: int | None = None):
    need_any(request, "kitchen", "restaurant")
    return {"lines": read(acc.kitchen_queue, station_id), "stations": read(acc._simple_list, "stations")}


@app.post("/api/kitchen/ready")
async def kitchen_ready(request: Request):
    need_any(request, "kitchen", "restaurant")
    write(acc.mark_ready, (await request.json()).get("line_ids", []))
    return {"ok": True}


# ───── مراكز الكلفة ─────
@app.get("/api/cost-centers")
def cost_centers(request: Request, with_totals: bool = False, date_from: str | None = None, date_to: str | None = None):
    return read(acc.list_cost_centers, with_totals, date_from, date_to)


@app.post("/api/cost-centers")
async def add_cost_center(request: Request):
    need(request, "accounts")
    return {"id": write(acc.save_cost_center, (await request.json()).get("name"), None, True, uid(request))}


@app.put("/api/cost-centers/{cid}")
async def edit_cost_center(cid: int, request: Request):
    need(request, "accounts")
    b = await request.json()
    write(acc.save_cost_center, b.get("name"), cid, bool(b.get("active", True)), uid(request))
    return {"ok": True}


@app.delete("/api/cost-centers/{cid}")
def del_cost_center(cid: int, request: Request):
    need(request, "accounts", "delete")
    write(acc.delete_cost_center, cid, uid(request))
    return {"ok": True}


# ───── الشيكات ─────
@app.get("/api/checks")
def checks(request: Request, kind: str | None = None, status: str | None = None):
    need(request, "checks")
    return read(acc.list_checks, kind, status)


@app.get("/api/checks/{cid}")
def check(cid: int, request: Request):
    need(request, "checks")
    return read(acc.get_check, cid)


@app.post("/api/checks")
async def add_check(request: Request):
    need(request, "checks")
    return write(acc.create_check, await request.json(), uid(request))


@app.post("/api/checks/{cid}/settle")
async def check_settle(cid: int, request: Request):
    need(request, "checks")
    return write(acc.settle_check, cid, await request.json(), uid(request))


@app.post("/api/checks/{cid}/bounce")
async def check_bounce(cid: int, request: Request):
    need(request, "checks")
    return write(acc.bounce_check, cid, await request.json(), uid(request))


@app.delete("/api/checks/{cid}")
def check_cancel(cid: int, request: Request):
    need(request, "checks", "delete")
    write(acc.cancel_check, cid, uid(request))
    return {"ok": True}


# ───── الأقساط ─────
@app.get("/api/invoices/{inv_id}/installments")
def invoice_installments(inv_id: int, request: Request):
    need_any(request, "sales", "reports", "vouchers")
    return read(acc.invoice_installments, inv_id)


@app.put("/api/invoices/{inv_id}/installments")
async def set_installments(inv_id: int, request: Request):
    need(request, "sales")
    b = await request.json()
    return write(acc.save_installments, inv_id, b.get("plan"), int(b.get("count") or 0), b.get("start"), uid(request))


@app.get("/api/reports/installments")
def installments_due(request: Request, as_of: str | None = None, only_late: bool = False):
    need(request, "reports")
    return read(acc.installments_due, as_of, only_late)


# ───── الأصول الثابتة ─────
@app.get("/api/assets")
def assets(request: Request):
    need(request, "assets")
    return read(acc.list_assets)


@app.post("/api/assets")
async def add_asset(request: Request):
    need(request, "assets")
    return {"id": write(acc.save_asset, await request.json(), None, uid(request))}


@app.put("/api/assets/{aid}")
async def edit_asset(aid: int, request: Request):
    need(request, "assets")
    return {"id": write(acc.save_asset, {**await request.json(), "existing": True}, aid, uid(request))}


@app.delete("/api/assets/{aid}")
def del_asset(aid: int, request: Request):
    need(request, "assets", "delete")
    write(acc.delete_asset, aid, uid(request))
    return {"ok": True}


@app.post("/api/assets/depreciate")
async def depreciate(request: Request):
    need(request, "assets")
    return write(acc.post_depreciation, (await request.json()).get("up_to"), uid(request))


# ───── الموظفون والرواتب والعمولات ─────
@app.get("/api/employees")
def employees(request: Request):
    need_any(request, "hr", "sales")
    return read(acc.list_employees)


@app.post("/api/employees")
async def add_employee(request: Request):
    need(request, "hr")
    return {"id": write(acc.save_employee, await request.json(), None, uid(request))}


@app.put("/api/employees/{eid}")
async def edit_employee(eid: int, request: Request):
    need(request, "hr")
    return {"id": write(acc.save_employee, {**await request.json(), "existing": True}, eid, uid(request))}


@app.delete("/api/employees/{eid}")
def del_employee(eid: int, request: Request):
    need(request, "hr", "delete")
    write(acc.delete_employee, eid, uid(request))
    return {"ok": True}


@app.get("/api/payrolls")
def payrolls(request: Request):
    need(request, "hr")
    return read(acc.list_payrolls)


@app.get("/api/payrolls/{rid}")
def payroll(rid: int, request: Request):
    need(request, "hr")
    return read(acc.get_payroll, rid)


@app.post("/api/payrolls")
async def add_payroll(request: Request):
    need(request, "hr")
    return write(acc.create_payroll, await request.json(), uid(request))


@app.get("/api/reports/commissions")
def commissions(request: Request, date_from: str | None = None, date_to: str | None = None):
    need_any(request, "hr", "reports")
    return read(acc.commissions_report, date_from, date_to)


@app.post("/api/commissions/pay")
async def commission_pay(request: Request):
    need(request, "hr")
    b = await request.json()
    jid = write(acc.pay_commission, int(b["employee_id"]), b.get("amount"), b, uid(request))
    return read(acc.get_journal, jid)


# ───── الموازنات والتدفقات ─────
@app.get("/api/budgets")
def budgets(request: Request, year: str | None = None):
    need(request, "reports")
    return read(acc.budget_report, year)


@app.put("/api/budgets")
async def put_budgets(request: Request):
    need(request, "reports")
    b = await request.json()
    write(acc.save_budgets, b.get("year") or acc.today()[:4], b.get("items", []), uid(request))
    return read(acc.budget_report, b.get("year"))


@app.get("/api/reports/cash-flow")
def cash_flow(request: Request, date_from: str | None = None, date_to: str | None = None):
    need(request, "reports")
    return read(acc.cash_flow, date_from, date_to)


@app.get("/api/reports/cost-centers")
def cost_center_report(request: Request, date_from: str | None = None, date_to: str | None = None):
    need(request, "reports")
    return read(acc.list_cost_centers, True, date_from, date_to)


# ───── الأرصدة الافتتاحية ─────
@app.get("/api/opening")
def opening(request: Request):
    need(request, "opening")
    return read(acc.get_opening)


@app.post("/api/opening/balances")
async def opening_balances(request: Request):
    need(request, "opening")
    return write(acc.save_opening_balances, await request.json(), uid(request))


@app.post("/api/opening/stock")
async def opening_stock(request: Request):
    need(request, "opening")
    return write(acc.save_opening_stock, await request.json(), uid(request))


# ───── الإقفال المالي وسجل التدقيق ─────
@app.get("/api/period")
def period(request: Request):
    need(request, "period")
    def load(conn):
        s = acc.settings(conn)
        return {"lock_before_date": s.get("lock_before_date") or "", "last_closing_date": s.get("last_closing_date") or "",
                "equity_accounts": [a for a in acc.accounts_tree(conn) if a["type"] == "equity" and not a["is_group"]],
                "closings": acc.list_journals(conn, limit=20, sources=("closing",))}
    return read(load)


@app.post("/api/period/lock")
async def period_lock(request: Request):
    need(request, "period")
    date = (await request.json()).get("lock_before_date")
    return {"lock_before_date": write(acc.set_lock_date, date, uid(request))}


@app.post("/api/period/close-year")
async def period_close(request: Request):
    need(request, "period")
    b = await request.json()
    if not b.get("date_to"):
        raise HTTPException(400, "حدّد تاريخ نهاية السنة المالية")
    return write(acc.close_year, b["date_to"], b.get("retained_account_id"), uid(request))


@app.get("/api/audit")
def audit_log(request: Request, date_from: str | None = None, date_to: str | None = None,
              entity: str | None = None, user_id: int | None = None, search: str | None = None,
              limit: int | None = None):
    need(request, "audit")
    extra, args = "", []
    if entity:
        extra += " AND entity=?"; args.append(entity)
    if user_id:
        extra += " AND user_id=?"; args.append(user_id)
    if search:
        extra += " AND (summary LIKE ? OR details LIKE ?)"; args += [f"%{search}%"] * 2
    return _paged({"rows": read(acc.list_audit, date_from, date_to, entity, user_id, search, _limit(limit)),
                   "users": read(auth.list_users)},
                  read(acc.count_rows, "audit_log", "at", date_from, date_to, extra, args))


@app.get("/api/backups")
def backups(request: Request):
    need(request, "settings")
    return list_backups()


@app.post("/api/backups")
def make_backup(request: Request):
    need(request, "settings")
    path = backup_db("manual")
    write(acc.audit, uid(request), "create", "backup", None, f"نسخة احتياطية يدوية: {path.name}")
    return {"name": path.name, "offsite_error": dbmod.LAST_OFFSITE_ERROR}


def _do_restore(request: Request, path: Path, label: str) -> dict:
    need(request, "settings", "users")          # الاستعادة تستبدل كل البيانات: للمدير فقط
    who = request.state.user["full_name"]
    try:
        info = dbmod.restore_db(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log.warning("استعادة نسخة احتياطية «%s» بواسطة %s — نسخة الأمان: %s", label, who, info["safety_backup"])
    with tx() as c:
        acc.audit(c, None, "update", "backup", None, f"استُعيدت النسخة «{label}» بواسطة {who}", info)
    return info


@app.post("/api/backups/restore-local")
async def restore_local(request: Request):
    need(request, "settings", "users")       # الصلاحية أولاً: لا نكشف وجود الملفات لغير المخوّل
    name = Path((await request.json()).get("name") or "").name      # اسم ملف فقط، بلا مسارات
    path = BACKUP_DIR / name
    if not name or not path.exists():
        raise HTTPException(404, "النسخة غير موجودة")
    return _do_restore(request, path, name)


@app.post("/api/backups/restore-upload")
async def restore_upload(request: Request, file: UploadFile = File(...)):
    need(request, "settings", "users")
    data = await file.read()
    if len(data) > 2 * 1024 ** 3:
        raise HTTPException(400, "الملف كبير جداً")
    fd, tmp = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return _do_restore(request, Path(tmp), file.filename or "ملف مرفوع")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@app.get("/api/health")
def health(request: Request):
    """فحص سلامة النظام: البيانات، النسخ الاحتياطي، والتجهيزات."""
    need(request, "settings")
    checks = read(acc.health_check)
    backups = list_backups()
    age = None
    if backups:
        age = (datetime.now() - datetime.fromisoformat(backups[0]["at"])).total_seconds() / 3600
    checks.append({"name": "نسخة احتياطية حديثة (خلال ٤٨ ساعة)", "ok": age is not None and age <= 48,
                   "detail": f"آخر نسخة منذ {age:.0f} ساعة" if age is not None else "لا توجد نسخ بعد"})
    extra = read(acc.settings).get("backup_extra_dir") or ""
    checks.append({"name": "نسخ احتياطي خارج مجلد البرنامج", "ok": bool(extra) and not dbmod.LAST_OFFSITE_ERROR,
                   "detail": dbmod.LAST_OFFSITE_ERROR or (extra if extra else "لم يُضبط مجلد إضافي — ضياع القرص يعني ضياع البيانات")})
    cfg = _device_cfg()
    checks.append({"name": "طابعة الإيصالات مضبوطة على الخادم", "ok": bool(cfg.get("printer_mode")), "optional": True,
                   "detail": "" if cfg.get("printer_mode") else "اختياري — أو استخدم الوكيل المحلي/المتصفح"})
    checks.append({"name": "مفتاح الذكاء الاصطناعي", "ok": ai.has_credentials(), "optional": True,
                   "detail": "" if ai.has_credentials() else "اختياري — المساعد الذكي وقراءة الفواتير متوقفان"})
    checks.append({"name": "اتصال مشفّر (HTTPS)", "ok": request.url.scheme == "https", "optional": True,
                   "detail": "" if request.url.scheme == "https" else "اختياري داخل شبكة المحل — شغّل run_https.bat لتفعيله"})
    required = [c for c in checks if not c.get("optional")]
    return {"checks": checks, "ok": all(c["ok"] for c in required),
            "failed": len([c for c in required if not c["ok"]])}


@app.get("/api/logs")
def logs(request: Request, lines: int = 200):
    need(request, "settings")
    if not LOG_FILE.exists():
        return {"lines": []}
    text = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": text[-max(1, min(lines, 2000)):]}


# ───── التقارير ─────
@app.get("/api/dashboard")
def dashboard(request: Request):
    need(request, "reports")
    return read(acc.dashboard)


@app.get("/api/reports/trial-balance")
def trial_balance(request: Request, to_date: str | None = None):
    need(request, "reports")
    return read(acc.trial_balance, to_date)


@app.get("/api/reports/statement/{aid}")
def statement(aid: int, request: Request, date_from: str | None = None, date_to: str | None = None):
    # كشف حساب العملاء/الموردين متاح لمن يدير العملاء، وباقي الحسابات للتقارير فقط
    if "reports" not in request.state.perms:
        need(request, "parties")
        if not read(lambda c: c.execute("SELECT 1 FROM parties WHERE account_id=?", (aid,)).fetchone()):
            need(request, "reports")
    return read(acc.account_statement, aid, date_from, date_to)


@app.get("/api/reports/income")
def income(request: Request, date_from: str | None = None, date_to: str | None = None):
    need(request, "reports")
    return read(acc.income_statement, date_from, date_to)


@app.get("/api/reports/balance-sheet")
def balance_sheet(request: Request, as_of: str | None = None):
    need(request, "reports")
    return read(acc.balance_sheet, as_of)


@app.get("/api/reports/aging")
def aging(request: Request, kind: str = "customer", as_of: str | None = None):
    need(request, "reports")
    return read(acc.aging_report, kind, as_of)


@app.get("/api/reports/vat")
def vat(request: Request, date_from: str | None = None, date_to: str | None = None):
    need(request, "reports")
    return read(acc.vat_report, date_from, date_to)


@app.get("/api/reports/stock")
def stock(request: Request):
    need_any(request, "reports", "items", "warehouses")
    return read(acc.stock_report)


# ───── الذكاء الاصطناعي ─────
@app.post("/api/ai/chat")
async def ai_chat(request: Request):
    need(request, "ai")
    b = await request.json()
    msg = (b.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "اكتب رسالة")
    return await run_in_threadpool(ai.chat, uid(request), b.get("session_id"), msg)


@app.post("/api/ai/reset")
async def ai_reset(request: Request):
    need(request, "ai")
    ai.reset(uid(request), (await request.json()).get("session_id", ""))
    return {"ok": True}


@app.post("/api/ai/scan-invoice")
async def ai_scan(request: Request, file: UploadFile = File(...)):
    need(request, "purchases")
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"}
    if file.content_type not in allowed:
        raise HTTPException(400, "الملف يجب أن يكون صورة (JPG/PNG/WEBP) أو PDF")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "حجم الملف كبير جداً (الحد 20MB)")
    return await run_in_threadpool(ai.scan_invoice, data, file.content_type)


@app.post("/api/ai/insights")
def ai_insights(request: Request):
    need(request, "ai", "reports")
    return {"text": ai.insights()}


# ───── الأجهزة: الطابعات ودرج النقود والميزان ─────
DEVICE_KEYS = ("printer_mode", "printer_host", "printer_port", "printer_name", "printer_width",
               "kitchen_mode", "kitchen_host", "kitchen_port", "kitchen_name",
               "drawer_on_cash", "scale_port", "scale_baud", "receipt_footer")


def _device_cfg() -> dict:
    s = read(acc.settings)
    return {k: s.get(k) or "" for k in DEVICE_KEYS}


def _target(cfg: dict, which: str = "printer") -> dict:
    return {"mode": cfg.get(f"{which}_mode"), "host": cfg.get(f"{which}_host"),
            "port": int(cfg.get(f"{which}_port") or 9100), "name": cfg.get(f"{which}_name")}


def _receipt_job(inv_id: int, paid: float, change: float, cashier: str, drawer: bool) -> bytes:
    cfg = _device_cfg()
    inv = read(acc.get_invoice, inv_id)
    inv["printed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    shop = {**read(acc.settings), "receipt_footer": cfg.get("receipt_footer")}
    img = devices.receipt_image(inv, shop, paid, change, cashier, cfg.get("printer_width") or "80")
    return devices.build_job(img, drawer=drawer)


@app.get("/api/devices")
def devices_get(request: Request):
    return _device_cfg()


@app.get("/api/devices/printers")
def devices_printers(request: Request):
    """طابعات ويندوز المعرّفة على جهاز الخادم (استعلام بطيء نسبياً، لذا له مسار خاص)."""
    need(request, "settings")
    return devices.list_windows_printers()


@app.put("/api/devices")
async def devices_put(request: Request):
    need(request, "settings")
    body = await request.json()
    with tx() as c:
        for k in DEVICE_KEYS:
            if k in body:
                acc.set_setting(c, k, body[k] if body[k] is not None else "")
        acc.audit(c, uid(request), "update", "settings", None, "تعديل إعدادات الأجهزة والطابعات")
    return _device_cfg()


@app.get("/api/devices/job/{inv_id}")
def devices_job(inv_id: int, request: Request, paid: float = 0, change: float = 0, drawer: bool = False):
    """بايتات ESC/POS جاهزة ليرسلها وكيل الطباعة المحلي على جهاز الكاشير."""
    need_any(request, "sales", "purchases", "reports")
    data = _receipt_job(inv_id, paid, change, request.state.user["full_name"], drawer)
    return {"data": base64.b64encode(data).decode(), "bytes": len(data)}


@app.post("/api/devices/print/{inv_id}")
async def devices_print(inv_id: int, request: Request):
    """طباعة مباشرة من الخادم إلى الطابعة المضبوطة."""
    need_any(request, "sales", "purchases", "reports")
    b = await request.json()
    cfg = _device_cfg()
    data = _receipt_job(inv_id, float(b.get("paid") or 0), float(b.get("change") or 0),
                        request.state.user["full_name"], bool(b.get("drawer")) and cfg.get("drawer_on_cash") != "0")
    await run_in_threadpool(devices.send, _target(cfg), data)
    return {"ok": True, "bytes": len(data)}


@app.post("/api/devices/drawer")
def devices_drawer(request: Request):
    need(request, "sales")
    devices.send(_target(_device_cfg()), devices.INIT + devices.DRAWER)
    write(acc.audit, uid(request), "update", "drawer", None, "فتح درج النقود يدوياً")
    return {"ok": True}


@app.post("/api/devices/test")
async def devices_test(request: Request):
    need(request, "settings")
    which = (await request.json()).get("which") or "printer"
    cfg = _device_cfg()
    c = devices.Canvas(devices.WIDTHS.get(cfg.get("printer_width") or "80", 576))
    c.text(read(acc.settings).get("company_name") or "", 32, True, "center")
    c.text("صفحة اختبار الطابعة" if which == "printer" else "اختبار طابعة المطبخ", 26, align="center")
    c.rule()
    c.pair("الوقت", datetime.now().strftime("%Y-%m-%d %H:%M"), 22)
    c.pair("النص العربي", "يعمل", 22)
    c.pair("الأرقام", "1,234.50", 22)
    c.space(30)
    await run_in_threadpool(devices.send, _target(cfg, which), devices.build_job(c.render()))
    return {"ok": True}


@app.post("/api/devices/kitchen")
async def devices_kitchen(request: Request):
    need(request, "sales")
    order = await request.json()
    cfg = _device_cfg()
    order.setdefault("time", datetime.now().strftime("%H:%M"))
    img = devices.kitchen_image(order, cfg.get("printer_width") or "80")
    data = devices.build_job(img)
    if order.get("return_job"):
        return {"data": base64.b64encode(data).decode()}
    which = "kitchen" if cfg.get("kitchen_mode") else "printer"
    await run_in_threadpool(devices.send, _target(cfg, which), data)
    return {"ok": True}


@app.get("/api/devices/scale")
def devices_scale(request: Request):
    need(request, "sales")
    cfg = _device_cfg()
    if not cfg.get("scale_port"):
        raise devices.DeviceError("لم يُضبط منفذ الميزان في الإعدادات")
    return {"weight": devices.read_scale(cfg["scale_port"], int(cfg.get("scale_baud") or 9600))}


# ───── الواجهة ─────
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/version")
def version():
    """ختم إصدار الواجهة الحالي — الصفحة المفتوحة تقارنه بختمها لتعرف أن البرنامج حُدِّث."""
    return {"version": str(int((STATIC / "app.js").stat().st_mtime))}


@app.get("/")
def index():
    """الصفحة الرئيسية مع ختم إصدار على ملفات الواجهة: أي تحديث للبرنامج يصل المتصفح فوراً بلا نسخ قديمة."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        stamp = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
