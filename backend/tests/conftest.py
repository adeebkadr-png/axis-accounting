"""تهيئة الاختبارات: قاعدة بيانات مؤقتة لكل جلسة اختبار، وعميل HTTP مسجَّل الدخول."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def data_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="acc_test_"))
    monkeypatch.setenv("ACCOUNTING_DATA_DIR", str(d))
    for mod in ("db", "accounting", "auth", "ai", "app"):
        sys.modules.pop(mod, None)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def client(data_dir):
    from fastapi.testclient import TestClient
    import app as app_module
    c = TestClient(app_module.app)
    c.__enter__()
    c.app_module = app_module
    yield c
    c.__exit__(None, None, None)


@pytest.fixture()
def admin(client):
    """عميل بصلاحيات مدير كاملة."""
    r = client.post("/api/auth/setup", json={"username": "admin", "full_name": "المدير", "password": "secret1"})
    assert r.status_code == 200, r.text
    return client


def ok(r):
    assert r.status_code == 200, (r.request.method, r.request.url.path, r.status_code, r.text)
    return r.json()


def fail(r, status=400):
    assert r.status_code == status, (r.request.method, r.request.url.path, r.status_code, r.text)
    return r.json()
