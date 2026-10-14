# -*- mode: python ; coding: utf-8 -*-
"""وصفة تجميع «المحاسب الذكي» إلى تطبيق ويندوز مستقل عبر PyInstaller."""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("static", "static"), ("backend", "backend")]
binaries, hiddenimports = [], collect_submodules("uvicorn") + [
    "anthropic", "app", "ai", "auth", "accounting", "db", "devices", "multipart", "webview",
    "PIL", "arabic_reshaper", "bidi", "bidi.algorithm", "serial",
]
for pkg in ("anthropic", "httpx2", "httpcore2", "certifi", "truststore", "jiter", "webview", "arabic_reshaper", "bidi"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis(["desktop.py"], pathex=["backend"], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "matplotlib", "numpy", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SmartAccounting",
          console=False, icon=None, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SmartAccounting")
