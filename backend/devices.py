"""طبقة الأجهزة: طابعات الإيصالات والمطبخ (ESC/POS)، درج النقود، والميزان.

طابعات الإيصالات لا تطبع العربية كنص، لذا يُرسم الإيصال كصورة (بتشكيل الحروف واتجاه
صحيح) ثم يُرسل كنقطية. النقل إلى الطابعة ثلاثة أنواع:
- network: طابعة شبكة على المنفذ 9100
- windows: طابعة USB معرّفة في ويندوز (إرسال خام عبر winspool)
- file: كتابة البايتات في ملف (للاختبار)
"""
import ctypes
import os
import re
import socket
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:           # بدون المكتبتين يُطبع النص غير مشكّل
    arabic_reshaper = None

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
CUT = GS + b"V\x42\x00"
DRAWER = ESC + b"p\x00\x19\xfa"          # نبضة فتح الدرج على الطرف 2
WIDTHS = {"80": 576, "58": 384}           # بكسل عند 203 نقطة/بوصة


class DeviceError(Exception):
    pass


# ───────────────────────── رسم النص العربي ─────────────────────────

def shape(text: str) -> str:
    text = str(text if text is not None else "")
    if arabic_reshaper is None:
        return text
    return get_display(arabic_reshaper.reshape(text))


def _font(size: int, bold: bool = False):
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in (("tahomabd.ttf", "arialbd.ttf") if bold else ("tahoma.ttf", "arial.ttf")):
        f = fonts / name
        if f.exists():
            return ImageFont.truetype(str(f), size)
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


class Canvas:
    """لوحة إيصال تكبر عمودياً: أسطر بمحاذاة يمين/وسط، وأسطر بطرفين (بيان ← مبلغ)."""

    def __init__(self, width: int = 576, margin: int = 12):
        self.w, self.m = width, margin
        self.ops, self.y = [], 8

    def _wrap(self, text: str, font, max_w: int) -> list[str]:
        words, lines, cur = str(text).split(" "), [], ""
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        for word in words:
            trial = (cur + " " + word).strip()
            if probe.textlength(shape(trial), font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return lines + ([cur] if cur else [])

    def text(self, text, size=24, bold=False, align="right"):
        font = _font(size, bold)
        for line in self._wrap(text, font, self.w - 2 * self.m):
            self.ops.append(("text", self.y, shape(line), font, align))
            self.y += int(size * 1.45)

    def pair(self, right, left, size=24, bold=False):
        """سطر بطرفين: النص يميناً والقيمة يساراً."""
        font = _font(size, bold)
        self.ops.append(("pair", self.y, shape(right), shape(left), font))
        self.y += int(size * 1.45)

    def rule(self, dashed=True):
        self.ops.append(("rule", self.y + 6, dashed))
        self.y += 16

    def space(self, px=10):
        self.y += px

    def render(self) -> Image.Image:
        img = Image.new("1", (self.w, self.y + 24), 1)
        d = ImageDraw.Draw(img)
        for op in self.ops:
            if op[0] == "text":
                _, y, s, font, align = op
                tw = d.textlength(s, font=font)
                x = self.w - self.m - tw if align == "right" else (self.w - tw) / 2 if align == "center" else self.m
                d.text((x, y), s, font=font, fill=0)
            elif op[0] == "pair":
                _, y, r, left, font = op
                d.text((self.w - self.m - d.textlength(r, font=font), y), r, font=font, fill=0)
                d.text((self.m, y), left, font=font, fill=0)
            else:
                _, y, dashed = op
                if dashed:
                    for x in range(self.m, self.w - self.m, 12):
                        d.line([(x, y), (x + 6, y)], fill=0, width=2)
                else:
                    d.line([(self.m, y), (self.w - self.m, y)], fill=0, width=2)
        return img


def image_to_escpos(img: Image.Image) -> bytes:
    """تحويل الصورة إلى أمر نقطي GS v 0 على شرائح (بعض الطابعات تحدّ ارتفاع الصورة الواحدة)."""
    img = img.convert("1")
    w = (img.width + 7) // 8 * 8
    if w != img.width:
        padded = Image.new("1", (w, img.height), 1)
        padded.paste(img, (0, 0))
        img = padded
    out = bytearray()
    band = 255
    for top in range(0, img.height, band):
        strip = img.crop((0, top, w, min(top + band, img.height)))
        # في ESC/POS البت 1 = نقطة سوداء، وفي Pillow 0 = أسود: نعكس
        data = bytes(b ^ 0xFF for b in strip.tobytes())
        wb, h = w // 8, strip.height
        out += GS + b"v0\x00" + bytes([wb & 255, wb >> 8, h & 255, h >> 8]) + data
    return bytes(out)


def fmt(n) -> str:
    n = float(n or 0)
    s = f"{n:,.2f}"
    return s[:-3] if s.endswith(".00") else s.rstrip("0")


# ───────────────────────── تصميم الإيصالات ─────────────────────────

def receipt_image(inv: dict, shop: dict, paid: float = 0, change: float = 0, cashier: str = "",
                  width: str = "80") -> Image.Image:
    c = Canvas(WIDTHS.get(str(width), 576))
    big = c.w >= 500
    c.text(shop.get("company_name") or "", 34 if big else 28, True, "center")
    if shop.get("phone"):
        c.text(shop["phone"], 22, align="center")
    if shop.get("address"):
        c.text(shop["address"], 20, align="center")
    if shop.get("tax_number"):
        c.text(f"الرقم الضريبي: {shop['tax_number']}", 20, align="center")
    c.rule()
    c.pair(f"{inv.get('kind_label', 'فاتورة')} رقم", str(inv["number"]), 24, True)
    c.pair("التاريخ", inv.get("printed_at") or inv["date"], 20)
    if cashier:
        c.pair("الكاشير", cashier, 20)
    if inv.get("party_name"):
        c.pair("الزبون", inv["party_name"], 20)
    if inv.get("table_name"):
        c.pair("الطاولة", inv["table_name"], 22, True)
    c.rule()
    for ln in inv["lines"]:
        name = ln["item_name"]
        if ln.get("unit_name") and (ln.get("unit_factor") or 1) > 1:
            name += f" ({ln['unit_name']})"
        c.text(name, 23)
        c.pair(f"{fmt(ln['qty'])} × {fmt(ln['price'])}", fmt(ln["total"]), 21)
        for note in ln.get("notes") or []:
            c.text(f"  • {note}", 19)
    c.rule()
    c.pair("المجموع", fmt(inv["subtotal"]), 22)
    if inv.get("discount"):
        c.pair("الحسم", fmt(inv["discount"]), 22)
    if inv.get("service"):
        c.pair("رسم الخدمة", fmt(inv["service"]), 22)
    if inv.get("tax_amount"):
        c.pair(f"الضريبة {fmt(inv.get('tax_rate'))}%", fmt(inv["tax_amount"]), 22)
    c.pair("الإجمالي", f"{fmt(inv['total'])} {inv.get('currency_symbol') or ''}".strip(), 30 if big else 26, True)
    if paid:
        c.pair("المدفوع", fmt(paid), 22)
        c.pair("الباقي", fmt(change), 22)
    c.rule()
    c.text(shop.get("receipt_footer") or "شكراً لزيارتكم", 22, align="center")
    c.space(30)
    return c.render()


def kitchen_image(order: dict, width: str = "80") -> Image.Image:
    """تذكرة تحضير للمطبخ: خط كبير، بلا أسعار، مع الملاحظات والطاولة."""
    c = Canvas(WIDTHS.get(str(width), 576))
    c.text(order.get("station") or "المطبخ", 34, True, "center")
    c.rule(False)
    c.pair("طلب رقم", str(order.get("number", "")), 30, True)
    if order.get("table_name"):
        c.pair("الطاولة", order["table_name"], 30, True)
    if order.get("order_type"):
        c.pair("النوع", order["order_type"], 24)
    c.pair("الوقت", order.get("time", ""), 22)
    if order.get("waiter"):
        c.pair("النادل", order["waiter"], 22)
    c.rule()
    for ln in order.get("lines", []):
        c.pair(ln["item_name"], f"× {fmt(ln['qty'])}", 30, True)
        for note in ln.get("notes") or []:
            c.text(f"  ← {note}", 24)
        c.space(4)
    if order.get("notes"):
        c.rule()
        c.text(f"ملاحظة: {order['notes']}", 24, True)
    c.space(30)
    return c.render()


def build_job(img: Image.Image, cut: bool = True, drawer: bool = False) -> bytes:
    return INIT + (DRAWER if drawer else b"") + image_to_escpos(img) + b"\n\n\n" + (CUT if cut else b"")


# ───────────────────────── النقل إلى الطابعة ─────────────────────────

def send_network(host: str, port: int, data: bytes, timeout: float = 5):
    try:
        with socket.create_connection((host, int(port or 9100)), timeout=timeout) as s:
            s.sendall(data)
    except OSError as e:
        raise DeviceError(f"تعذر الاتصال بالطابعة {host}:{port or 9100} — {e}")


def send_windows(printer: str, data: bytes):
    """إرسال خام إلى طابعة ويندوز عبر winspool (بدون مكتبات إضافية)."""
    if os.name != "nt":
        raise DeviceError("طابعات ويندوز متاحة على ويندوز فقط")
    spool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    handle = ctypes.c_void_p()
    if not spool.OpenPrinterW(ctypes.c_wchar_p(printer), ctypes.byref(handle), None):
        raise DeviceError(f"الطابعة «{printer}» غير موجودة أو غير متاحة")

    class DOCINFO(ctypes.Structure):
        _fields_ = [("pDocName", ctypes.c_wchar_p), ("pOutputFile", ctypes.c_wchar_p), ("pDatatype", ctypes.c_wchar_p)]
    try:
        doc = DOCINFO("Axis receipt", None, "RAW")
        if not spool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise DeviceError("تعذر بدء مهمة الطباعة")
        try:
            spool.StartPagePrinter(handle)
            written = ctypes.c_ulong(0)
            if not spool.WritePrinter(handle, data, len(data), ctypes.byref(written)) or written.value != len(data):
                raise DeviceError("لم تُرسل بيانات الطباعة كاملة")
            spool.EndPagePrinter(handle)
        finally:
            spool.EndDocPrinter(handle)
    finally:
        spool.ClosePrinter(handle)


def send(target: dict, data: bytes):
    """target: {mode: network|windows|file, host, port, name, path}"""
    mode = (target or {}).get("mode")
    if mode == "network":
        if not target.get("host"):
            raise DeviceError("لم يُحدَّد عنوان الطابعة")
        send_network(target["host"], target.get("port") or 9100, data)
    elif mode == "windows":
        if not target.get("name"):
            raise DeviceError("لم يُحدَّد اسم الطابعة")
        send_windows(target["name"], data)
    elif mode == "file":
        Path(target["path"]).write_bytes(data)
    else:
        raise DeviceError("لم تُضبط طابعة لهذا النوع من الطباعة")


def list_windows_printers() -> list[str]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Printer | Select-Object -ExpandProperty Name"],
                             capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace").stdout
        return [x.strip() for x in out.splitlines() if x.strip()]
    except Exception:
        return []


# ───────────────────────── الميزان ─────────────────────────

def parse_weight(raw: str) -> float | None:
    """يستخرج الوزن من سطر الميزان (معظم الموازين ترسل نصاً مثل «ST,GS,  1.250kg»)."""
    m = re.findall(r"-?\d+(?:[.,]\d+)?", raw or "")
    if not m:
        return None
    value = float(m[-1].replace(",", "."))
    if "g" in raw.lower() and "kg" not in raw.lower():
        value /= 1000
    return round(value, 3)


def read_scale(port: str, baud: int = 9600, timeout: float = 2) -> float:
    try:
        import serial
    except ImportError:
        raise DeviceError("مكتبة pyserial غير مثبتة (pip install pyserial)")
    try:
        with serial.Serial(port, int(baud or 9600), timeout=timeout) as s:
            s.reset_input_buffer()
            for _ in range(6):
                w = parse_weight(s.readline().decode("ascii", "ignore"))
                if w is not None:
                    return w
    except Exception as e:
        raise DeviceError(f"تعذر قراءة الميزان على {port}: {e}")
    raise DeviceError("لم يُرسل الميزان وزناً — تأكد من وضعه على الإرسال المستمر")
