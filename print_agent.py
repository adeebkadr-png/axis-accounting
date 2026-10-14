"""وكيل الطباعة المحلي — يُشغَّل على جهاز الكاشير الذي تتصل به الطابعة أو الميزان.

البرنامج الرئيسي يجهّز بايتات الإيصال، والمتصفح يرسلها إلى هذا الوكيل على
http://127.0.0.1:9777 فيطبعها فوراً بلا حوار طباعة ويفتح درج النقود.

الإعداد في ملف agent.json بجانب هذا الملف (يُنشأ تلقائياً عند أول تشغيل):
  {"printer": {"mode": "windows", "name": "POS-80"},
   "kitchen": {"mode": "network", "host": "192.168.1.50", "port": 9100},
   "scale":   {"port": "COM3", "baud": 9600}}
التشغيل:  python print_agent.py   (أو run_agent.bat)
"""
import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", HERE)) / "backend"))
import devices  # noqa: E402

CONFIG = HERE / "agent.json"
PORT = 9777
DEFAULT = {"printer": {"mode": "", "name": "", "host": "", "port": 9100},
           "kitchen": {"mode": "", "name": "", "host": "", "port": 9100},
           "scale": {"port": "", "baud": 9600}}


def load_config() -> dict:
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {k: {**DEFAULT[k], **cfg.get(k, {})} for k in DEFAULT}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        # المتصفح يتصل من صفحة البرنامج (أصل مختلف) فنسمح له صراحةً
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, {})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        cfg = load_config()
        try:
            if self.path.startswith("/status"):
                self._send(200, {"ok": True, "printer": cfg["printer"], "kitchen": cfg["kitchen"], "scale": cfg["scale"]})
            elif self.path.startswith("/printers"):     # استعلام بطيء: يُطلب عند الحاجة فقط
                self._send(200, {"windows_printers": devices.list_windows_printers()})
            elif self.path.startswith("/scale"):
                if not cfg["scale"]["port"]:
                    raise devices.DeviceError("لم يُضبط منفذ الميزان في agent.json")
                self._send(200, {"weight": devices.read_scale(cfg["scale"]["port"], cfg["scale"]["baud"])})
            else:
                self._send(404, {"detail": "مسار غير معروف"})
        except devices.DeviceError as e:
            self._send(503, {"detail": str(e)})

    def do_POST(self):
        cfg = load_config()
        try:
            body = self._body()
            if self.path.startswith("/raw"):
                which = "kitchen" if body.get("target") == "kitchen" and cfg["kitchen"]["mode"] else "printer"
                devices.send(cfg[which], base64.b64decode(body["data"]))
                self._send(200, {"ok": True})
            elif self.path.startswith("/drawer"):
                devices.send(cfg["printer"], devices.INIT + devices.DRAWER)
                self._send(200, {"ok": True})
            elif self.path.startswith("/config"):
                merged = {k: {**load_config()[k], **body.get(k, {})} for k in DEFAULT}
                CONFIG.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send(200, {"ok": True, **merged})
            else:
                self._send(404, {"detail": "مسار غير معروف"})
        except devices.DeviceError as e:
            self._send(503, {"detail": str(e)})
        except Exception as e:
            self._send(400, {"detail": f"طلب غير صالح: {e}"})

    def log_message(self, *args):   # بلا ضجيج في النافذة
        pass


def main():
    load_config()
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"وكيل الطباعة يعمل على http://127.0.0.1:{port}")
    print(f"ملف الإعداد: {CONFIG}")
    server.serve_forever()


if __name__ == "__main__":
    main()
