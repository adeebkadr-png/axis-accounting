"""اختبارات الأجهزة: رسم الإيصال، الإرسال إلى طابعة شبكة، درج النقود، وكيل الطباعة، والميزان."""
import base64
import json
import socket
import sys
import threading
import urllib.request
from pathlib import Path

from conftest import fail, ok
from test_core import setup_basics


class FakePrinter:
    """طابعة شبكة وهمية: تستقبل البايتات على منفذ محلي كما تفعل طابعة ESC/POS على 9100."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.jobs = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            buf = b""
            while chunk := conn.recv(65536):
                buf += chunk
            conn.close()
            self.jobs.append(buf)

    def wait(self, n=1, timeout=5):
        import time
        end = time.time() + timeout
        while len(self.jobs) < n and time.time() < end:
            time.sleep(0.05)
        return self.jobs

    def close(self):
        self.sock.close()


def make_sale(c):
    _, item, _, _, wh = setup_basics(c)
    return ok(c.post("/api/invoices", json={"kind": "sale", "payment": "cash", "warehouse_id": wh,
                                            "lines": [{"item_id": item["id"], "qty": 2, "price": 20}]}))


def test_receipt_job_is_valid_escpos(admin):
    inv = make_sale(admin)
    r = ok(admin.get(f"/api/devices/job/{inv['id']}?paid=50&change=10&drawer=true"))
    data = base64.b64decode(r["data"])
    assert data[:2] == b"\x1b@"                       # تهيئة الطابعة
    assert data[2:6] == b"\x1bp\x00\x19"              # أمر فتح الدرج قبل الطباعة
    assert b"\x1dv0\x00" in data                      # صورة نقطية
    assert data.endswith(b"\x1dV\x42\x00")            # قص الورق
    i = data.index(b"\x1dv0\x00")
    width_bytes = data[i + 4] + data[i + 5] * 256
    assert width_bytes == 72                          # 576 بكسل = عرض 80 ملم
    no_drawer = base64.b64decode(ok(admin.get(f"/api/devices/job/{inv['id']}"))["data"])
    assert b"\x1bp\x00\x19" not in no_drawer[:10]


def test_server_prints_to_network_printer(admin):
    inv = make_sale(admin)
    printer = FakePrinter()
    try:
        ok(admin.put("/api/devices", json={"printer_mode": "network", "printer_host": "127.0.0.1",
                                           "printer_port": printer.port, "printer_width": "58"}))
        ok(admin.post(f"/api/devices/print/{inv['id']}", json={"paid": 40, "change": 0, "drawer": True}))
        job = printer.wait(1)[0]
        assert job[:2] == b"\x1b@" and b"\x1dv0\x00" in job and len(job) > 5000
        i = job.index(b"\x1dv0\x00")
        assert job[i + 4] == 48                       # 384 بكسل = عرض 58 ملم
        ok(admin.post("/api/devices/drawer"))
        assert printer.wait(2)[1] == b"\x1b@\x1bp\x00\x19\xfa"
        ok(admin.post("/api/devices/test", json={"which": "printer"}))
        assert len(printer.wait(3)) == 3
    finally:
        printer.close()


def test_unconfigured_or_unreachable_printer_gives_clear_error(admin):
    inv = make_sale(admin)
    r = fail(admin.post(f"/api/devices/print/{inv['id']}", json={}), 503)
    assert "لم تُضبط طابعة" in r["detail"]
    ok(admin.put("/api/devices", json={"printer_mode": "network", "printer_host": "127.0.0.1", "printer_port": 1}))
    assert "تعذر الاتصال" in fail(admin.post(f"/api/devices/print/{inv['id']}", json={}), 503)["detail"]


def test_kitchen_ticket_goes_to_kitchen_printer(admin):
    setup_basics(admin)
    receipt, kitchen = FakePrinter(), FakePrinter()
    try:
        ok(admin.put("/api/devices", json={"printer_mode": "network", "printer_host": "127.0.0.1", "printer_port": receipt.port,
                                           "kitchen_mode": "network", "kitchen_host": "127.0.0.1", "kitchen_port": kitchen.port}))
        ok(admin.post("/api/devices/kitchen", json={"number": 7, "table_name": "طاولة 3", "station": "المشاوي",
                                                    "lines": [{"item_name": "كباب", "qty": 2, "notes": ["بدون بصل"]}]}))
        assert len(kitchen.wait(1)) == 1 and not receipt.jobs      # ذهبت لطابعة المطبخ لا الكاشير
    finally:
        receipt.close(); kitchen.close()


def test_print_agent_delivers_raw_bytes(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root))
    sys.modules.pop("print_agent", None)
    import print_agent
    out = tmp_path / "printed.bin"
    monkeypatch.setattr(print_agent, "CONFIG", tmp_path / "agent.json")
    (tmp_path / "agent.json").write_text(json.dumps({"printer": {"mode": "file", "path": str(out)}}), encoding="utf-8")
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), print_agent.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        payload = b"\x1b@hello-receipt"
        req = urllib.request.Request(base + "/raw", data=json.dumps({"data": base64.b64encode(payload).decode()}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["ok"] is True
            assert resp.headers["Access-Control-Allow-Origin"] == "*"      # المتصفح يحتاجها
            assert resp.headers["Access-Control-Allow-Private-Network"] == "true"
        assert out.read_bytes() == payload
        with urllib.request.urlopen(base + "/status", timeout=5) as resp:
            assert json.loads(resp.read())["printer"]["mode"] == "file"
    finally:
        server.shutdown()


def test_scale_weight_parsing():
    import devices
    assert devices.parse_weight("ST,GS,   1.250kg") == 1.25
    assert devices.parse_weight("  850 g\r\n") == 0.85
    assert devices.parse_weight("US,NT,+00.735 kg") == 0.735
    assert devices.parse_weight("") is None


def test_scale_without_port_reports_setup_needed(admin):
    assert "منفذ الميزان" in fail(admin.get("/api/devices/scale"), 503)["detail"]
