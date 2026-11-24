"""Корневой сертификат Минцифры в образе сервисов (решение 163): тот самый, не подменённый."""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

ROOT = Path(__file__).parents[3]
CERTIFICATE = ROOT / "infra" / "certs" / "russian_trusted_root_ca.crt"
# Отпечатки Russian Trusted Root CA, опубликованные Минцифры; действует до 28.02.2032.
SHA1 = "8FF915CCAB7BC16F8C5C8099D53E0E115B3AEC2F"
SHA256 = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"


def test_root_certificate_is_the_ministry_one() -> None:
    der = ssl.PEM_cert_to_DER_cert(CERTIFICATE.read_text(encoding="ascii"))

    assert hashlib.sha1(der).hexdigest().upper() == SHA1  # noqa: S324 — отпечаток, не защита
    assert hashlib.sha256(der).hexdigest().upper() == SHA256


def test_service_image_trusts_it() -> None:
    dockerfile = (ROOT / "services" / "Dockerfile").read_text(encoding="utf-8")

    copy = "COPY infra/certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/"
    assert copy in dockerfile
    assert "RUN update-ca-certificates" in dockerfile
