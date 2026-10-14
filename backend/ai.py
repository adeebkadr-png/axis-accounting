"""المساعد الذكي: محادثة مع أدوات محاسبية، قراءة الفواتير من الصور، وتحليل مالي.

يستخدم Claude عبر Anthropic SDK. المساعد يقرأ البيانات بحرية لكنه لا يرحّل أي قيد
بنفسه: يقترح القيد (propose_journal_entry) والمستخدم يضغط «ترحيل» في الواجهة.
"""
import base64
import json
import os
import re
import uuid

import anthropic

import accounting as acc
from db import connect, rows

MODEL = os.environ.get("ACCOUNTING_AI_MODEL", "claude-opus-5")
MAX_TOOL_ROUNDS = 12
MAX_HISTORY_MESSAGES = 60

_client: anthropic.Anthropic | None = None
_sessions: dict[tuple, list] = {}  # (user_id, session_id) ← المحادثة


class AIError(Exception):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _create(**kwargs):
    """استدعاء موحّد مع تفعيل النموذج الاحتياطي عند الرفض (Opus 5)."""
    if MODEL == "claude-opus-5":
        kwargs.setdefault("betas", ["server-side-fallback-2026-07-01"])
        kwargs.setdefault("fallbacks", "default")
    try:
        resp = client().beta.messages.create(model=MODEL, **kwargs)
    except anthropic.AuthenticationError:
        raise AIError("مفتاح Anthropic API غير صحيح. ضع المفتاح في الإعدادات أو في متغير البيئة ANTHROPIC_API_KEY")
    except anthropic.RateLimitError:
        raise AIError("تم تجاوز حد الطلبات، حاول بعد قليل")
    except anthropic.APIConnectionError:
        raise AIError("تعذر الاتصال بخدمة الذكاء الاصطناعي، تحقق من الإنترنت")
    except anthropic.APIStatusError as e:
        raise AIError(f"خطأ من خدمة الذكاء الاصطناعي ({e.status_code}): {e.message}")
    except TypeError as e:  # لا توجد بيانات اعتماد
        if "api_key" in str(e) or "auth" in str(e).lower():
            raise AIError("لم يتم ضبط مفتاح Anthropic API. أضفه من صفحة الإعدادات")
        raise
    if resp.stop_reason == "refusal":
        raise AIError("اعتذر المساعد عن تنفيذ هذا الطلب")
    return resp


def set_api_key(key: str):
    global _client
    os.environ["ANTHROPIC_API_KEY"] = key.strip()
    _client = None


def has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _text(resp) -> str:
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


# ───────────────────────── أدوات المساعد ─────────────────────────

TOOLS = [
    {
        "name": "get_dashboard",
        "description": "ملخص الوضع المالي الحالي: مبيعات اليوم والشهر، الأرباح، رصيد الصندوق والبنك، ذمم العملاء والموردين، قيمة المخزون، المواد الناقصة، واتجاه آخر 6 أشهر.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_accounts",
        "description": "البحث في دليل الحسابات بالاسم أو الرمز، يعيد الرمز والنوع والرصيد. استخدمها لمعرفة رمز الحساب الصحيح قبل اقتراح قيد.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "جزء من اسم الحساب أو رمزه. اتركه فارغاً لعرض الدليل كاملاً."}}},
    },
    {
        "name": "account_statement",
        "description": "كشف حساب تفصيلي (حركات ورصيد تراكمي) لحساب أو عميل أو مورد خلال فترة.",
        "input_schema": {"type": "object", "properties": {
            "account_code": {"type": "string", "description": "رمز الحساب"},
            "date_from": {"type": "string", "description": "YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["account_code"]},
    },
    {
        "name": "trial_balance",
        "description": "ميزان المراجعة بالمجاميع والأرصدة حتى تاريخ معين.",
        "input_schema": {"type": "object", "properties": {"to_date": {"type": "string"}}},
    },
    {
        "name": "income_statement",
        "description": "قائمة الدخل (الأرباح والخسائر) لفترة: الإيرادات، المصروفات، مجمل الربح وصافي الربح.",
        "input_schema": {"type": "object", "properties": {
            "date_from": {"type": "string"}, "date_to": {"type": "string"}}},
    },
    {
        "name": "balance_sheet",
        "description": "الميزانية العمومية (المركز المالي) في تاريخ معين.",
        "input_schema": {"type": "object", "properties": {"as_of": {"type": "string"}}},
    },
    {
        "name": "list_invoices",
        "description": "عرض الفواتير مع إمكانية التصفية حسب النوع والفترة والطرف.",
        "input_schema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["sale", "purchase", "sale_return", "purchase_return"]},
            "date_from": {"type": "string"}, "date_to": {"type": "string"},
            "party_name": {"type": "string"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "list_parties",
        "description": "العملاء أو الموردون مع أرصدتهم (للعميل: الموجب = مستحق لنا. للمورد: الموجب = مستحق علينا).",
        "input_schema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["customer", "supplier"]}, "search": {"type": "string"}}},
    },
    {
        "name": "search_items",
        "description": "المواد والمخزون: الكمية الإجمالية، والكمية في كل مستودع (stock: رقم المستودع ← الكمية)، متوسط التكلفة، سعر البيع، والقيمة.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "list_warehouses",
        "description": "المستودعات مع قيمة البضاعة وعدد المواد في كل منها، وآخر عمليات التحويل بينها.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "vat_report",
        "description": "إقرار ضريبة القيمة المضافة لفترة: ضريبة المخرجات والمدخلات والصافي المستحق.",
        "input_schema": {"type": "object", "properties": {
            "date_from": {"type": "string"}, "date_to": {"type": "string"}}},
    },
    {
        "name": "run_sql",
        "description": (
            "تنفيذ استعلام SELECT للقراءة فقط على قاعدة SQLite للتحليلات غير المغطاة بالأدوات الأخرى. الجداول: "
            "accounts(id,code,name,type,parent_id,is_group), parties(id,name,kind,phone,account_id), "
            "items(id,code,name,unit,sale_price,avg_cost,qty,min_qty), warehouses(id,name,active), item_stock(item_id,warehouse_id,qty), "
            "journals(id,number,date,description,source,source_id,created_by), "
            "journal_lines(id,journal_id,account_id,debit,credit,memo,currency,fx_amount), "
            "invoices(id,number,kind,date,party_id,payment,subtotal,discount,tax_rate,tax_amount,total,currency,rate,"
            "net_base,total_base,cost_total,warehouse_id,notes,journal_id,created_by), "
            "invoice_lines(id,invoice_id,item_id,qty,price,unit_cost,total), currencies(code,name,symbol,rate,is_base), "
            "transfers(id,number,date,from_wh,to_wh), transfer_lines(transfer_id,item_id,qty), users(id,username,full_name). "
            "مبالغ الفاتورة (subtotal,total,tax_amount) بعملة الفاتورة؛ net_base و total_base و cost_total بالعملة الأساسية. "
            "القيود دائماً بالعملة الأساسية. التواريخ نصوص YYYY-MM-DD. لا تقرأ password_hash أو auth_sessions."),
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "propose_journal_entry",
        "description": (
            "اقتراح قيد يومية ليراجعه المستخدم ويرحّله بنفسه بضغطة زر (لا يُرحَّل تلقائياً). "
            "استخدمها عندما يصف المستخدم عملية مالية (دفع مصروف، إيداع، سحب، رأس مال...). "
            "يجب أن يكون القيد متوازناً وعلى حسابات تفصيلية فقط (is_group=0). تحقق من الرموز عبر search_accounts أولاً."),
        "input_schema": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "description": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "account_code": {"type": "string"},
                "debit": {"type": "number"}, "credit": {"type": "number"},
                "memo": {"type": "string"}}, "required": ["account_code"]}}},
            "required": ["description", "lines"]},
    },
]

SQL_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b", re.I)


def _find_account_by_code(conn, code: str) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE code=?", (str(code).strip(),)).fetchone()
    if not row:
        raise acc.AccountingError(f"لا يوجد حساب بالرمز {code}")
    return dict(row)


def run_tool(name: str, args: dict, proposals: list) -> str:
    conn = connect()
    try:
        if name == "get_dashboard":
            out = acc.dashboard(conn)
            out.pop("recent", None)
        elif name == "search_accounts":
            q = (args.get("query") or "").strip()
            out = [{k: a[k] for k in ("code", "name", "type", "is_group", "balance")}
                   for a in acc.accounts_tree(conn) if not q or q in a["name"] or a["code"].startswith(q)]
        elif name == "account_statement":
            a = _find_account_by_code(conn, args["account_code"])
            out = acc.account_statement(conn, a["id"], args.get("date_from"), args.get("date_to"))
            out["lines"] = out["lines"][-150:]
        elif name == "trial_balance":
            out = acc.trial_balance(conn, args.get("to_date"))
        elif name == "income_statement":
            out = acc.income_statement(conn, args.get("date_from"), args.get("date_to"))
        elif name == "balance_sheet":
            out = acc.balance_sheet(conn, args.get("as_of"))
        elif name == "list_invoices":
            out = acc.list_invoices(conn, args.get("kind"), args.get("date_from"), args.get("date_to"),
                                    limit=min(int(args.get("limit") or 50), 200))
            if args.get("party_name"):
                out = [i for i in out if args["party_name"] in (i.get("party_name") or "")]
        elif name == "list_parties":
            out = acc.list_parties(conn, args.get("kind"), args.get("search"))
        elif name == "search_items":
            out = acc.list_items(conn, args.get("query"))
        elif name == "list_warehouses":
            out = {"warehouses": acc.list_warehouses(conn), "recent_transfers": acc.list_transfers(conn, 20)}
        elif name == "vat_report":
            out = acc.vat_report(conn, args.get("date_from"), args.get("date_to"))
        elif name == "run_sql":
            q = args["query"].strip().rstrip(";")
            if re.search(r"password|auth_sessions", q, re.I):
                return "خطأ: لا يُسمح بقراءة بيانات الدخول"
            if not q.lower().startswith(("select", "with")) or SQL_FORBIDDEN.search(q) or ";" in q:
                return "خطأ: يُسمح فقط باستعلام SELECT واحد للقراءة"
            ro = connect(readonly=True)
            try:
                out = [{k: v for k, v in r.items() if not re.search(r"password|token", k, re.I)}
                       for r in rows(ro.execute(q))[:300]]
            finally:
                ro.close()
        elif name == "propose_journal_entry":
            lines, td, tc = [], 0.0, 0.0
            for ln in args["lines"]:
                a = _find_account_by_code(conn, ln["account_code"])
                if a["is_group"]:
                    return f"خطأ: الحساب {a['code']} «{a['name']}» رئيسي، اختر حساباً تفصيلياً تحته"
                d, c = acc.r2(ln.get("debit")), acc.r2(ln.get("credit"))
                td, tc = td + d, tc + c
                lines.append({"account_id": a["id"], "code": a["code"], "account_name": a["name"],
                              "debit": d, "credit": c, "memo": ln.get("memo") or ""})
            if abs(td - tc) > acc.EPS or td == 0:
                return f"خطأ: القيد غير متوازن (مدين {td} / دائن {tc})، صحّحه"
            proposals.append({"id": uuid.uuid4().hex[:8], "date": args.get("date") or acc.today(),
                              "description": args["description"], "lines": lines})
            return "تم عرض القيد المقترح على المستخدم ليراجعه ويرحّله. لا تقل إنه رُحِّل."
        else:
            return f"أداة غير معروفة: {name}"
        return json.dumps(out, ensure_ascii=False, default=str)[:60000]
    except (acc.AccountingError, KeyError, ValueError) as e:
        return f"خطأ: {e}"
    except Exception as e:  # نعيد الخطأ للنموذج ليصحح نفسه بدل انهيار المحادثة
        return f"خطأ في التنفيذ: {e}"
    finally:
        conn.close()


def _system_prompt() -> str:
    conn = connect()
    try:
        s = acc.settings(conn)
        base = acc.base_currency(conn)
        others = [f"{c['name']} ({c['code']}) = {c['rate']:g}" for c in acc.list_currencies(conn) if not c["is_base"]]
    finally:
        conn.close()
    vat = f"مفعّلة بنسبة {s.get('vat_rate')}%" if s.get("vat_enabled") == "1" else "غير مفعّلة"
    return f"""أنت «المحاسب الذكي»، مساعد محاسبي خبير مدمج في برنامج محاسبة لشركة «{s.get('company_name')}».
العملة الأساسية: {base['name']} ({base['symbol']}). العملات الأخرى وأسعار صرفها: {'، '.join(others) or 'لا يوجد'}.
ضريبة القيمة المضافة: {vat}. تاريخ اليوم: {acc.today()}.

- أجب بالعربية بوضوح وإيجاز، واستخدم جداول Markdown عند عرض أرقام متعددة.
- لا تخمّن أرقاماً أبداً: استخدم الأدوات للحصول على البيانات الفعلية.
- النظام يعمل بالقيد المزدوج والجرد المستمر بمتوسط التكلفة. الفواتير تُرحَّل تلقائياً؛ أنت تقترح القيود اليدوية فقط عبر propose_journal_entry والمستخدم يرحّلها.
- عند وصف المستخدم لعملية مالية، حدّد الحسابات المناسبة (ابحث عنها أولاً) ثم اقترح القيد واشرحه باختصار. إن لم يوجد حساب مناسب فاقترح على المستخدم إنشاءه من دليل الحسابات.
- عند التحليل قدّم ملاحظات عملية: السيولة، هامش الربح، الذمم المتأخرة، المواد الراكدة أو الناقصة."""


def chat(user_id: int, session_id: str | None, message: str) -> dict:
    session_id = session_id or uuid.uuid4().hex
    history = _sessions.setdefault((user_id, session_id), [])
    history.append({"role": "user", "content": message})
    proposals: list = []
    system = _system_prompt()

    for _ in range(MAX_TOOL_ROUNDS):
        resp = _create(max_tokens=16000, system=system, tools=TOOLS, messages=history,
                       cache_control={"type": "ephemeral"})
        history.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": run_tool(b.name, b.input or {}, proposals)})
        history.append({"role": "user", "content": results})
    else:
        resp = None

    # نقصّ التاريخ من البداية عند حدّ رسالة مستخدم نصية حتى لا تنفصل أزواج الأدوات
    while len(history) > MAX_HISTORY_MESSAGES:
        del history[0]
        while history and not (history[0]["role"] == "user" and isinstance(history[0]["content"], str)):
            del history[0]

    reply = _text(resp) if resp else "توقفت بعد عدد كبير من الخطوات، حاول تبسيط الطلب."
    return {"session_id": session_id, "reply": reply or "تم.", "proposals": proposals}


def reset(user_id: int, session_id: str):
    _sessions.pop((user_id, session_id), None)


# ───────────────────────── قراءة فاتورة من صورة ─────────────────────────

SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_name": {"type": "string"},
        "invoice_number": {"type": "string"},
        "date": {"type": "string", "description": "YYYY-MM-DD أو فارغ"},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "description": {"type": "string"},
            "matched_item_id": {"type": ["integer", "null"]},
            "qty": {"type": "number"},
            "unit_price": {"type": "number"}},
            "required": ["description", "matched_item_id", "qty", "unit_price"],
            "additionalProperties": False}},
        "discount": {"type": "number"},
        "tax_amount": {"type": "number"},
        "currency_code": {"type": "string", "description": "رمز ISO للعملة مثل USD أو SYP، أو فارغ"},
        "total": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["supplier_name", "invoice_number", "date", "lines", "discount", "tax_amount", "currency_code", "total", "notes"],
    "additionalProperties": False,
}


def scan_invoice(data: bytes, media_type: str) -> dict:
    conn = connect()
    try:
        items = [{"id": i["id"], "name": i["name"], "code": i["code"]} for i in acc.list_items(conn)]
        suppliers = [p["name"] for p in acc.list_parties(conn, "supplier")]
    finally:
        conn.close()
    b64 = base64.standard_b64encode(data).decode()
    if media_type == "application/pdf":
        doc = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        doc = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    prompt = f"""استخرج بيانات فاتورة الشراء هذه.
- لكل سطر: ابحث عن المادة المطابقة في قائمة مواد النظام وضع رقمها في matched_item_id، أو null إن لم توجد مطابقة واضحة.
- supplier_name: إن طابق أحد الموردين الموجودين فاكتبه بنفس الاسم حرفياً.
- unit_price سعر الوحدة قبل الضريبة. tax_amount مبلغ الضريبة إن ظهر في الفاتورة.
- الأرقام أرقام فقط بدون عملة. ما لا يظهر في الفاتورة اتركه فارغاً أو صفراً.

مواد النظام: {json.dumps(items, ensure_ascii=False)}
الموردون الحاليون: {json.dumps(suppliers, ensure_ascii=False)}"""
    resp = _create(
        max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        messages=[{"role": "user", "content": [doc, {"type": "text", "text": prompt}]}],
    )
    return json.loads(_text(resp))


# ───────────────────────── التحليل المالي ─────────────────────────

def insights() -> str:
    conn = connect()
    try:
        t = acc.today()
        snapshot = {
            "dashboard": {k: v for k, v in acc.dashboard(conn).items() if k != "recent"},
            "income_this_month": acc.income_statement(conn, t[:7] + "-01", t),
            "income_this_year": acc.income_statement(conn, t[:4] + "-01-01", t),
            "customers": acc.list_parties(conn, "customer")[:40],
            "suppliers": acc.list_parties(conn, "supplier")[:40],
        }
    finally:
        conn.close()
    resp = _create(
        max_tokens=8000,
        output_config={"effort": "medium"},
        system=_system_prompt(),
        messages=[{"role": "user", "content":
            "حلّل الوضع المالي التالي وقدّم تقريراً قصيراً (بحد أقصى 200 كلمة) بعناوين Markdown: "
            "أبرز المؤشرات، نقاط تحتاج انتباهاً، و3 توصيات عملية محددة.\n\n"
            + json.dumps(snapshot, ensure_ascii=False, default=str)}],
    )
    return _text(resp)
