#!/usr/bin/env python
"""촬영 앱을 웹서버에 올릴 수 있는 형태로 내보낸다.

    KFA.exe web
    KFA.exe web --out E:/업로드

무엇이 달라지나 — 이게 이 명령의 존재 이유다
────────────────────────────────────────────────────────────────────
    지금은 진단 PC 가 촬영 앱을 내주고, 폰이 같은 와이파이로 붙는다.
    그래서 현장마다 이런 것들이 따라온다.

        · PC 를 들고 가야 한다
        · 폰과 PC 가 같은 와이파이에 있어야 한다
        · 주소가 http://192.168.x.x 라서 **보안 컨텍스트가 아니다**
          → 서비스워커가 등록되지 않는다 → 오프라인으로 못 쓴다
          → 와이파이를 벗어나면 앱이 다시 열리지 않는다
        · 그걸 피하려면 자체 인증서를 만들어 폰마다 설치해야 한다

    HTTPS 웹서버에 한 번 올려 두면 **위 네 가지가 한꺼번에 사라진다.**
    폰은 주소만 알면 되고, 한 번 열면 오프라인에서도 열린다.
    현장에 PC 를 들고 갈 이유도 없어진다.

    필요한 것은 정적 호스팅뿐이다. 서버에서 도는 코드가 없다.

무엇을 올리지 않는가
    **진단 엔진은 올리지 않는다.** 촬영 앱만 나간다.
    사진에는 얼굴·카드번호·주민번호가 찍힐 수 있다. 앱은 그것을 기기 안에서
    가리고, 가려진 사진조차 서버로 보내지 않는다 — ZIP 으로 손에 쥐여 줄 뿐이다.
    그 성질을 유지하려면 판정은 진단 PC 에 남아야 한다.
    (엔진을 서버에 올리는 순간 개인정보 처리자가 되고, 수집·보관·파기 근거가 필요하다)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import output_dir, resource_dir            # noqa: E402

FILES = ["index.html", "app.js", "zip.js", "style.css", "sw.js",
         "icon.svg", "manifest.webmanifest", "protocol.json"]

# 올린 파일이 남의 서버 주소를 물고 가면 안 된다. 전부 상대 경로여야
# 하위 폴더(예: example.org/kfa/)에 올려도 그대로 돈다.
BAD_MARKERS = ["localhost", "127.0.0.1", "192.168.", "http://"]

README = """\
촬영 앱 — 웹서버에 올리는 법
================================================================

이 폴더의 파일을 **그대로** 웹서버에 올리면 됩니다.
서버에서 도는 프로그램이 없습니다. 정적 파일 호스팅이면 무엇이든 됩니다.

■ 반드시 지킬 것 하나

  **HTTPS 여야 합니다.**  http:// 로 올리면 카메라 권한과 오프라인 저장이
  동작하지 않습니다. 요즘 호스팅은 대부분 HTTPS 가 기본입니다.

■ 올리는 곳 (아무거나 하나)

  · GitHub Pages       무료. 아래에 따라 하는 법을 적어 뒀습니다
  · Cloudflare Pages   폴더를 끌어다 놓으면 끝. 무료. 접근 제한도 걸 수 있음
  · Netlify            같음. 무료
  · 기관 웹서버        /kiosk 같은 하위 폴더에 넣어도 됩니다
  · IIS · Apache · nginx  문서 루트에 복사

■ 먼저 — 올리는 것은 **이 폴더뿐**입니다

  프로젝트 폴더 전체를 올리지 마세요.
  assets\\cert\\ 에 **CA 개인키(kfa-ca.key)** 가 들어 있습니다. 그것이 공개되면
  그 키로 서명한 인증서를 폰이 믿게 되어, 설치한 모든 폰이 중간자 공격에 열립니다.
  한 번 올라간 것은 지워도 깃 기록에 남습니다.

  이 폴더(assets\\web) 안의 파일만 올리면 됩니다. 여기에는 비밀이 없습니다.

■ GitHub Pages 로 올리기  (깃 명령 몰라도 됩니다)

  1) github.com 에 로그인 → 오른쪽 위 [+] → New repository
  2) 이름을 정합니다.  예: kiosk-capture
     · Public 으로 만듭니다.  (Private 은 Pages 가 유료 요금제에서만 됩니다)
  3) Create repository
  4) 만들어진 화면에서  [uploading an existing file]  을 누릅니다
     (이미 파일이 있는 저장소라면  Add file → Upload files)
  5) 이 폴더 안의 파일을 **전부 끌어다 놓습니다.**
     · 폴더째 끌지 말고 **파일들을** 끌어 놓으세요. index.html 이 맨 위에 와야 합니다
     · .nojekyll 도 함께 올라가야 합니다 (숨김 파일이라 안 보일 수 있습니다.
       안 올라갔으면 Add file → Create new file 로 이름만 .nojekyll 로 만들고 저장)
     · '올리는 법.txt' 는 올리지 않아도 됩니다
  6) 아래 [Commit changes]
  7) 위쪽 Settings → 왼쪽 Pages
     · Source: Deploy from a branch
     · Branch: main  /  (root)   →  Save
  8) 1~2분 뒤 그 화면에 주소가 나옵니다.

         https://<아이디>.github.io/kiosk-capture/

  9) 폰으로 그 주소를 엽니다. 끝입니다.

  ★ https 가 자동으로 붙습니다. 인증서를 폰에 설치할 필요가 없습니다.
  ★ 촬영 목록이 바뀌면 protocol.json 만 다시 올리면 됩니다.
     (같은 이름으로 Upload files 하면 덮어씁니다)

  [알아둘 것] GitHub Pages 로 올린 주소는 **누구나 열 수 있습니다.**
    비밀번호를 걸 수 없습니다. 다만 여기 올라가는 것은 촬영 화면뿐이고
    사진도 판정 결과도 서버에 저장되지 않으므로 새어 나갈 것이 없습니다.
    그래도 주소가 공개되는 것 자체가 곤란하다면 Cloudflare Pages 를 쓰세요 —
    무료로 접근 제한(Cloudflare Access)을 걸 수 있습니다.

■ 올린 뒤 확인 (2분)

  1) 폰 브라우저로 주소를 엽니다.
  2) 화면 아래에 주황색 띠가 **없어야** 합니다.
     (띠가 보이면 오프라인 저장이 안 되는 상태입니다 — HTTPS 인지 확인하세요)
  3) '홈 화면에 추가' 를 합니다. 앱처럼 열립니다.
  4) 비행기 모드로 바꾸고 다시 열어 봅니다. 그대로 열리면 성공입니다.

■ 직접 운영하는 서버라면 한 가지

  .webmanifest 파일의 MIME 형식이 application/manifest+json 이어야
  '홈 화면에 추가' 가 깔끔하게 됩니다. GitHub Pages · Cloudflare · Netlify 는
  자동입니다. IIS 는 직접 넣어야 할 수 있습니다.

      nginx    types { application/manifest+json webmanifest; }
      IIS      MIME 형식 추가 → .webmanifest → application/manifest+json

  틀려도 촬영에는 지장이 없습니다. '홈 화면에 추가' 했을 때 이름과 아이콘이
  덜 예쁘게 나올 뿐입니다.

■ 촬영한 사진은 어디로 가나

  **서버로 가지 않습니다.** 폰 안에만 있다가 마지막에 ZIP 하나로 나옵니다.
  그 ZIP 을 진단 PC 로 옮겨 진단합니다.

  이 앱은 사진을 업로드하지 않습니다. 서버에는 판정 기능이 없습니다.
  사진에 얼굴·카드번호가 찍힐 수 있어 일부러 그렇게 만들었습니다.

■ 촬영 목록이 바뀌면

  protocol.json 만 다시 올리면 됩니다. 폰은 다음에 열 때 새로 받습니다.
  (이 파일만 네트워크를 먼저 봅니다 — 낡은 목록으로 찍는 일을 막기 위해서입니다)
"""


def export(dest: Path) -> dict:
    src = resource_dir() / "app"
    dest.mkdir(parents=True, exist_ok=True)

    missing = [f for f in FILES if not (src / f).is_file()]
    copied = []
    for f in FILES:
        s = src / f
        if s.is_file():
            shutil.copy2(s, dest / f)
            copied.append(f)

    # 절대 주소가 섞였는지 — 하위 폴더에 올리면 그 순간 깨진다
    absolutes = []
    for f in copied:
        if f in ("app.js", "sw.js"):
            # 이 둘은 주석에 예시 주소가 있다. 코드가 아닌 줄은 넘긴다.
            text = (dest / f).read_text(encoding="utf-8")
            for i, ln in enumerate(text.splitlines(), 1):
                s = ln.strip()
                if s.startswith(("*", "//", "/*")):
                    continue
                for m in BAD_MARKERS:
                    if m in s:
                        absolutes.append(f"{f}:{i}  {m}")
            continue
        text = (dest / f).read_text(encoding="utf-8", errors="ignore")
        # SVG·XML 의 네임스페이스(http://www.w3.org/…)는 주소가 아니라 식별자다.
        # 이걸 걸러내지 않으면 icon.svg 가 매번 잡힌다.
        text = text.replace("http://www.w3.org/", "")
        for m in BAD_MARKERS:
            if m in text:
                absolutes.append(f"{f}  {m}")

    (dest / "올리는 법.txt").write_text(README, encoding="utf-8")

    # GitHub Pages 는 올린 파일을 Jekyll 로 한 번 훑는다. 그 과정에서 밑줄로
    # 시작하는 이름이 사라지고, 빌드가 실패하면 사이트가 통째로 안 올라간다.
    # 이 파일 하나면 훑지 않고 그대로 내보낸다. 다른 호스팅에서는 무해하다.
    (dest / ".nojekyll").write_text("", encoding="utf-8")

    return {"dest": dest, "copied": copied, "missing": missing,
            "absolutes": absolutes}


def main() -> int:
    ap = argparse.ArgumentParser(description="촬영 앱을 웹서버용으로 내보낸다")
    ap.add_argument("--out", default=None, help="저장 폴더 (기본: assets/web)")
    a = ap.parse_args()

    print("=" * 70)
    print(" 촬영 앱 — 웹서버용 내보내기")
    print("=" * 70)

    # 촬영 목록을 먼저 최신으로. 앱과 엔진이 갈라지는 것을 막는다.
    from tools import build_app
    old = sys.argv
    try:
        sys.argv = ["build-app"]
        build_app.main()
    finally:
        sys.argv = old

    dest = Path(a.out) if a.out else output_dir() / "web"
    r = export(dest)

    print("\n" + "-" * 70)
    print(" 내보낸 파일")
    print("-" * 70)
    for f in r["copied"]:
        print(f"  {f:<26}{(dest / f).stat().st_size / 1024:>7.1f}KB")
    print(f"  {'올리는 법.txt':<26}")

    if r["missing"]:
        print("\n  [빠짐]", ", ".join(r["missing"]))
        print("         KFA.exe build-app 을 먼저 실행해 보세요.")

    # KS 본문이 앱에 새어 나가지 않았는지 — 올리는 순간 되돌릴 수 없다
    print("\n" + "-" * 70)
    print(" 나가기 전 검사")
    print("-" * 70)
    from tools import check_ks_copyright as KS
    leaks = KS.scan_verbatim([dest / f for f in r["copied"]])
    if leaks:
        for p in leaks:
            print(f"  [유출] {p}")
        print("\n  올리지 마십시오. KS X 9211 본문은 상업적 활용이 금지돼 있습니다.")
        return 2
    print("  KS 본문 유출 없음")

    if r["absolutes"]:
        for p in r["absolutes"]:
            print(f"  [주의] 절대 주소가 있습니다 — {p}")
        print("         하위 폴더에 올리면 깨질 수 있습니다.")
    else:
        print("  절대 주소 없음 — 하위 폴더에 올려도 됩니다")

    print("\n" + "=" * 70)
    print(f" 완료 — {dest}")
    print("=" * 70)
    print("\n  이 폴더를 통째로 웹서버에 올리세요. 서버에서 도는 코드는 없습니다.")
    print("  **HTTPS 여야 합니다.** http 로 올리면 오프라인 저장이 안 됩니다.")
    print("  자세한 것은 폴더 안 '올리는 법.txt' 에 있습니다.\n")
    print("  올린 뒤에도 진단은 이 PC 에서 합니다 — 사진은 서버로 가지 않습니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
