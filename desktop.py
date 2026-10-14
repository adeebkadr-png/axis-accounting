"""تشغيل «المحاسب الذكي» كتطبيق سطح مكتب (نافذة مستقلة بدون متصفح).

- في وضع التطوير: البيانات في مجلد data بجانب المشروع.
- في النسخة المجمّعة (.exe): البيانات في %LOCALAPPDATA%\\SmartAccounting حتى تبقى
  قابلة للكتابة ومحفوظة بين التحديثات.
خيارات: --no-window (خادم فقط)، --port <رقم>.
"""
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
BUNDLE = Path(getattr(sys, "_MEIPASS", HERE))


def data_dir() -> Path:
    if os.environ.get("ACCOUNTING_DATA_DIR"):
        return Path(os.environ["ACCOUNTING_DATA_DIR"])
    base = (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SmartAccounting") if FROZEN else HERE / "data"
    return base


os.environ["ACCOUNTING_DATA_DIR"] = str(data_dir())
data_dir().mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BUNDLE / "backend"))

import uvicorn  # noqa: E402  (بعد ضبط المسارات)

from app import app  # noqa: E402


def pick_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("تعذر العثور على منفذ متاح")


def wait_ready(url: str, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url + "/api/auth/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else pick_port()
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    if not wait_ready(url):
        print("تعذر تشغيل الخادم", file=sys.stderr)
        return 1
    print(f"المحاسب الذكي يعمل على {url}")

    if "--no-window" in args:
        while True:
            time.sleep(1)

    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        print("لفتح نافذة مستقلة ثبّت pywebview:  pip install pywebview")
        while True:
            time.sleep(1)

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("المحاسب الذكي", url, width=1300, height=860, min_size=(900, 600), text_select=True)
    webview.start()   # يعود عند إغلاق النافذة فينتهي البرنامج
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
