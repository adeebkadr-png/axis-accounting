"""شهادة HTTPS موقّعة ذاتياً لتشغيل البرنامج باتصال مشفّر داخل شبكة المحل.

تُنشأ مرة واحدة في data/tls وتشمل اسم الجهاز وعناوينه المحلية. المتصفح سيعرض تحذيراً
عند أول زيارة لأن الشهادة ليست من جهة عامة — اختر «متابعة» مرة واحدة على كل جهاز.
"""
import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import DATA_DIR  # noqa: E402

TLS_DIR = DATA_DIR / "tls"
CERT, KEY = TLS_DIR / "cert.pem", TLS_DIR / "key.pem"


def local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def ensure_cert() -> tuple[Path, Path]:
    if CERT.exists() and KEY.exists():
        return CERT, KEY
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = socket.gethostname()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    san = [x509.DNSName("localhost"), x509.DNSName(host)] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in local_ips()]
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(key, hashes.SHA256()))
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return CERT, KEY


if __name__ == "__main__":
    cert, key = ensure_cert()
    print(cert)
    print(key)
    for ip in local_ips():
        print(f"https://{ip}:8443")
