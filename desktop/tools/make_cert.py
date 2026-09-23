#!/usr/bin/env python
"""수집 앱 HTTPS 인증서 만들기 — 폰에서 오프라인이 되게 하는 유일한 방법.

    KFA.exe cert
    KFA.exe cert --ip 192.168.0.17

왜 필요한가
────────────────────────────────────────────────────────────────────
    서비스 워커(앱을 폰에 저장해 신호 없이도 열리게 하는 기능)는
    **보안 컨텍스트에서만** 등록된다. 브라우저가 인정하는 보안 컨텍스트는
    https 와 localhost 둘뿐이다.

    폰이 진단 PC 에 붙는 주소는 http://192.168.x.x 라 여기 해당하지 않는다.
    실측하면 navigator.serviceWorker 가 아예 존재하지 않는다:

        origin: http://192.168.0.17:8099
        isSecureContext: false
        'serviceWorker' in navigator: false

    그래서 https 로 서비스해야 한다.

자체 서명만으로는 부족하다
────────────────────────────────────────────────────────────────────
    인증서를 만들어 https 로 열어도, 폰이 그 인증서를 **믿지 않으면**
    브라우저는 여전히 보안 컨텍스트로 쳐 주지 않는다. 경고를 눌러 통과해도
    서비스 워커는 등록되지 않는다. '경고를 무시하고 진행'은 답이 아니다.

    그래서 이 도구는 두 개를 만든다.

        1) 자체 CA (기관 인증서)  ← 폰에 **한 번** 설치한다
        2) 그 CA 가 서명한 서버 인증서 (LAN IP 를 SAN 에 넣는다)

    폰에 CA 를 한 번 설치하면 그 뒤로는 경고 없이 https 로 열리고,
    서비스 워커가 등록되어 **와이파이를 벗어나도 앱이 열린다.**

    CA 개인키는 이 PC 에만 남고 어디에도 보내지 않는다.
    유효기간은 825일 — 브라우저가 그보다 긴 서버 인증서를 거부한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import output_dir                       # noqa: E402

CERT_DIR_NAME = "cert"
CA_KEY, CA_CRT = "kfa-ca.key", "kfa-ca.crt"
SRV_KEY, SRV_CRT = "kfa-server.key", "kfa-server.crt"

CA_DAYS = 3650          # CA 는 길게
SRV_DAYS = 820          # 서버 인증서는 825일 미만이어야 브라우저가 받는다


def cert_dir() -> Path:
    d = output_dir() / CERT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _require_cryptography():
    try:
        from cryptography import x509                      # noqa: F401
        from cryptography.hazmat.primitives import hashes, serialization   # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import rsa          # noqa: F401
        return True
    except ImportError:
        return False


def build(hosts: list[str], *, force: bool = False) -> dict:
    """CA 와 서버 인증서를 만든다. 이미 있으면 서버 인증서만 다시 만든다."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = cert_dir()
    now = dt.datetime.now(dt.timezone.utc)

    # ── CA ──────────────────────────────────────────────────────────────
    ca_key_path, ca_crt_path = d / CA_KEY, d / CA_CRT
    reused_ca = ca_key_path.exists() and ca_crt_path.exists() and not force
    if reused_ca:
        # CA 를 다시 만들면 폰에 설치한 것이 무효가 된다. 있으면 그대로 쓴다.
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), None)
        ca_crt = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "KFA Field Capture CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "KFA"),
        ])
        ca_crt = (
            x509.CertificateBuilder()
            .subject_name(ca_name).issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False), critical=True)
            # 키 식별자가 없으면 최신 검증기가 거부한다.
            # 실측: 빼먹었더니 'Missing Authority Key Identifier' 로 핸드셰이크 실패.
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        ca_key_path.write_bytes(ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        ca_crt_path.write_bytes(ca_crt.public_bytes(serialization.Encoding.PEM))

    # ── 서버 인증서 ─────────────────────────────────────────────────────
    sans: list = []
    for h in hosts:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))

    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_crt = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
        .issuer_name(ca_crt.subject)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=SRV_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([
            x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(
            srv_key.public_key()), critical=False)
        # 이 인증서가 어느 CA 에서 나왔는지 가리킨다. 없으면 검증이 실패한다.
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
            ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (d / SRV_KEY).write_bytes(srv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    # 서버 인증서 뒤에 CA 를 붙여 체인을 완성한다 (폰이 중간 검증에 쓴다)
    (d / SRV_CRT).write_bytes(
        srv_crt.public_bytes(serialization.Encoding.PEM)
        + ca_crt.public_bytes(serialization.Encoding.PEM))

    return {
        "dir": d, "hosts": hosts, "reused_ca": reused_ca,
        "ca_crt": ca_crt_path, "server_crt": d / SRV_CRT, "server_key": d / SRV_KEY,
        "expires": (now + dt.timedelta(days=SRV_DAYS)).date().isoformat(),
    }


def files_ready() -> bool:
    d = cert_dir()
    return (d / SRV_CRT).exists() and (d / SRV_KEY).exists()


def main() -> int:
    from tools.serve_app import lan_ips

    ap = argparse.ArgumentParser(description="수집 앱 HTTPS 인증서 만들기")
    ap.add_argument("--ip", action="append", default=None,
                    help="인증서에 넣을 주소 (여러 번 지정 가능. 기본: 자동 탐지)")
    ap.add_argument("--force", action="store_true", help="CA 까지 새로 만든다")
    a = ap.parse_args()

    print("=" * 74)
    print(" 수집 앱 HTTPS 인증서")
    print("=" * 74)

    if not _require_cryptography():
        print("\n  [실패] cryptography 패키지가 필요합니다.")
        print("         pip install cryptography")
        return 2

    hosts = a.ip or (lan_ips() + ["localhost", "127.0.0.1"])
    if not hosts:
        print("\n  [실패] LAN 주소를 찾지 못했습니다. --ip 로 직접 지정하세요.")
        return 2

    r = build(hosts, force=a.force)

    print(f"\n  대상 주소  {', '.join(hosts)}")
    print(f"  CA         {'기존 것 재사용' if r['reused_ca'] else '새로 생성'}")
    print(f"  만료        {r['expires']}")
    print(f"\n  저장 위치  {r['dir']}")
    for k in ("ca_crt", "server_crt", "server_key"):
        print(f"    {Path(r[k]).name}")

    print("\n" + "-" * 74)
    print(" 폰에 한 번만 하면 되는 일")
    print("-" * 74)
    print(f"""
  1) 아래 파일을 폰으로 보낸다 (메신저·메일·USB 무엇이든)
       {r['ca_crt']}

  2) 폰에서 설치한다
       Android  파일을 열면 설치 화면이 뜬다.
                안 뜨면: 설정 → 보안 → 암호화 및 자격증명 → 인증서 설치 → CA 인증서
       iPhone   파일을 열면 '프로파일 다운로드됨' 이 뜬다.
                설정 → 일반 → VPN 및 기기 관리 → 프로파일 설치
                그 다음 **반드시**: 설정 → 일반 → 정보 → 인증서 신뢰 설정
                에서 'KFA Field Capture CA' 스위치를 켠다  ← 이걸 빠뜨리면 안 된다

  3) 서버를 https 로 켠다
       KFA.exe serve-app --https

  이렇게 하면 폰에서 경고 없이 열리고, 앱이 폰에 저장되어
  **와이파이를 벗어나도 촬영을 이어갈 수 있다.**

  CA 개인키는 이 PC 에만 있습니다. 어디에도 전송되지 않습니다.
  이 CA 는 이 진단용 서버 인증서에만 서명합니다.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
