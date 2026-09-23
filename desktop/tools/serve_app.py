#!/usr/bin/env python
"""수집 앱 서버 — 진단 PC 가 현장 폰에 앱을 내준다.

    KFA.exe serve-app
    KFA.exe serve-app --port 8099

앱은 정적 파일이라 서버가 따로 필요 없어 보이지만, 브라우저 보안 정책 때문에
파일을 직접 여는(file://) 방식으로는 protocol.json 을 읽지 못한다.
그래서 진단 PC 가 잠깐 서버가 되고, 폰은 같은 와이파이에서 접속한다.

오프라인은 https 에서만 된다
    서비스 워커(앱을 폰에 저장하는 기능)는 브라우저가 보안 컨텍스트로 인정하는
    주소에서만 등록된다 — https 또는 localhost. 폰이 쓰는 http://192.168.x.x 는
    해당하지 않아 navigator.serviceWorker 가 아예 존재하지 않는다(실측 확인).

    그래서 --https 를 붙이면 자체 CA 로 서명한 인증서로 서비스한다.
    폰에 CA 를 한 번 설치하면 경고 없이 열리고 오프라인 저장이 켜진다.
    인증서는 tools/make_cert.py 가 만든다.
"""

from __future__ import annotations

import argparse
import http.server
import os
import socket
import socketserver
import ssl
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, resource_dir  # noqa: E402

DEFAULT_PORT = 8099


def lan_ips() -> list[str]:
    """같은 와이파이의 폰이 접속할 수 있는 주소들."""
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # 실제로 보내지 않는다. 경로만 확인.
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


def find_app_dir() -> Path | None:
    """번들 안(읽기)과 실행 파일 옆(사용자가 고친 것) 둘 다 본다."""
    for base in (app_dir(), resource_dir()):
        d = base / "app"
        if (d / "index.html").exists():
            return d
    return None


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 프로토콜이 바뀌었는데 캐시된 것이 나오면 앱과 엔진이 갈라진다
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        path = args[0] if args else ""
        if "protocol.json" in str(path) or "index.html" in str(path):
            print(f"    접속 {self.client_address[0]}  {path}")


# 조작판(브라우저 화면)에서 켰는지. 그때는 Ctrl+C 나 CLI 명령을 안내하면 안 된다 —
# 사용자가 보고 있는 것은 검은 창이 아니라 '중지' 버튼이다.
def _tail() -> str:
    if os.environ.get("KFA_UI") == "panel":
        return ("      그다음 화면에서 '처음으로' → ③ 진단하기 를 누르세요.\n"
                "\n  끝내려면 화면의 '중지' 버튼을 누르세요.\n")
    return ("      KFA.exe ingest <풀어놓은 폴더>\n"
            "\n  종료하려면 Ctrl+C\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="수집 앱 서버")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--dir", default=None, help="앱 폴더 (기본: 자동 탐색)")
    ap.add_argument("--https", action="store_true",
                    help="https 로 연다 (폰에서 오프라인 저장이 되려면 필요)")
    a = ap.parse_args()

    d = Path(a.dir) if a.dir else find_app_dir()
    if d is None or not (d / "index.html").exists():
        print("  [실패] 앱 폴더를 찾지 못했습니다.")
        print("         KFA.exe build-app 을 먼저 실행하거나 --dir 로 지정하세요.")
        return 2
    if not (d / "protocol.json").exists():
        print("  [실패] protocol.json 이 없습니다.")
        print("         KFA.exe build-app 을 먼저 실행하세요.")
        return 2

    # 출력이 버퍼에 갇히면 사용자가 접속 주소를 보지 못한다.
    # 서버는 계속 돌기 때문에 버퍼가 비워질 기회가 없다 — 줄 단위로 내보낸다.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:                                    # noqa: BLE001
        pass

    # ── https 준비 ──────────────────────────────────────────────────────
    # 폰에서 오프라인이 되려면 이 길밖에 없다. 서비스 워커는 보안 컨텍스트에서만
    # 등록되고, http://192.168.x.x 는 보안 컨텍스트가 아니다.
    ips = lan_ips()
    ssl_ctx = None
    if a.https:
        from tools import make_cert
        if not make_cert.files_ready():
            print("  인증서가 없어 새로 만듭니다…\n")
            try:
                make_cert.build(ips + ["localhost", "127.0.0.1"])
            except Exception as exc:                         # noqa: BLE001
                print(f"  [실패] 인증서를 만들지 못했습니다: {exc}")
                print("         KFA.exe cert 를 먼저 실행해 보세요.")
                return 2
        cd = make_cert.cert_dir()
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cd / make_cert.SRV_CRT, cd / make_cert.SRV_KEY)

    scheme = "https" if a.https else "http"
    print("=" * 70)
    print(" 수집 앱 서버")
    print("=" * 70)
    print(f"\n  앱 폴더   {d}")
    print(f"  방식      {scheme.upper()}")
    print("\n  폰에서 아래 주소로 접속하세요 (같은 와이파이에 있어야 합니다)\n")
    if ips:
        for ip in ips:
            print(f"      {scheme}://{ip}:{a.port}/")
    else:
        print("      (LAN 주소를 찾지 못했습니다. PC 의 IP 를 직접 확인하세요)")
    print(f"\n  이 PC 에서 확인:  {scheme}://localhost:{a.port}/")

    if a.https:
        print(f"""
  폰에서 할 일
    1) 아직 안 했다면 CA 인증서를 폰에 설치한다 (한 번만)
         {make_cert.cert_dir() / make_cert.CA_CRT}
         설치 방법은 KFA.exe cert 가 알려줍니다.
       iPhone 은 설치 뒤 **설정 → 일반 → 정보 → 인증서 신뢰 설정** 에서
       'KFA Field Capture CA' 를 켜야 합니다. 이걸 빠뜨리면 https 가 안 먹습니다.
    2) 위 주소를 브라우저로 연다
    3) 촬영 → 점주 문항 → 제출

  CA 를 설치했다면 앱이 폰에 저장되어 **와이파이를 벗어나도 촬영을 이어갈 수 있습니다.**
  앱 화면 위에 주황색 경고 띠가 없으면 오프라인 저장이 켜진 것입니다.

  촬영이 끝나면 앱에서 ZIP 을 내려받고, 이 PC 로 옮깁니다.
{_tail()}""")
    else:
        print(f"""
  폰에서 할 일
    1) 위 주소를 브라우저로 연다
    2) 촬영 → 점주 문항 → 제출까지 진행한다
    3) ZIP 을 내려받는다

  [알아둘 것] 이 PC 와 같은 와이파이 안에 있어야 앱이 열립니다.
    http:// 주소는 브라우저가 보안 연결로 보지 않아 **앱이 폰에 저장되지 않습니다.**
    찍은 사진은 폰 안에 남으므로 잃지 않지만, 와이파이를 벗어나면 앱이 다시 열리지 않습니다.
    → **제출(ZIP 내려받기)까지 마친 뒤 자리를 뜨세요.** 앱도 화면에 같은 안내를 띄웁니다.

    오프라인으로 쓰려면 https 로 켜세요 (인증서를 폰에 한 번 설치하면 됩니다):
        KFA.exe cert          ← 인증서를 만들고 설치 방법을 안내합니다
        KFA.exe serve-app --https

  촬영이 끝나면 앱에서 ZIP 을 내려받고, 이 PC 로 옮깁니다.
{_tail()}""")

    handler = lambda *args, **kw: Handler(*args, directory=str(d), **kw)  # noqa: E731

    # Windows 에서는 SO_REUSEADDR 을 켜지 않는다.
    #
    # POSIX 에서 이 옵션은 'TIME_WAIT 상태의 포트를 다시 써도 된다'는 뜻이지만,
    # Windows 에서는 **이미 듣고 있는 서버가 있어도 그 위에 겹쳐 붙는 것**을 허용한다.
    # 실제로 낡은 서버 하나가 남아 있는 상태에서 새로 켰더니 둘 다 8099 를 잡았고,
    # 폰은 낡은 쪽에 붙어 **바뀐 촬영 목록 대신 예전 것을 계속 받았다.**
    # 서버는 "켜졌습니다"라고 말하는데 앱과 엔진이 조용히 갈라지는 상황이라
    # 그냥 실패하고 이유를 알려주는 편이 낫다.
    socketserver.TCPServer.allow_reuse_address = (os.name != "nt")
    try:
        with socketserver.TCPServer(("0.0.0.0", a.port), handler) as httpd:
            if ssl_ctx is not None:
                httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
            print("-" * 70, flush=True)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  서버를 종료했습니다.")
        return 0
    except OSError as exc:
        print(f"\n  [실패] 포트 {a.port} 를 열 수 없습니다: {exc}")
        print("         이미 다른 서버가 이 포트를 쓰고 있을 수 있습니다.")
        print("         그대로 두면 폰이 낡은 서버에 붙어 예전 촬영 목록을 받습니다.")
        print("         먼저 켜 둔 서버 창을 닫거나, 다른 포트로 켜세요:")
        print(f"             KFA.exe serve-app --port {a.port + 1}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
