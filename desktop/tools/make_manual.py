#!/usr/bin/env python
"""사용자 매뉴얼 빌드 — 그림까지 한 파일에 담는다.

    KFA.exe manual
    python tools/make_manual.py

docs/manual/manual.html 은 그림을 img/ 폴더에서 참조한다(편집하기 좋다).
이 도구는 그림을 base64 로 박아 **파일 하나로** 만든다.

왜 한 파일이어야 하나
    매뉴얼은 현장으로, 담당 기관으로, 점주에게 건네진다.
    폴더째 주고받으면 반드시 누군가는 img/ 를 빼먹고, 그림이 깨진 채로 돌아다닌다.
    메일에 첨부하든 USB 에 넣든 한 파일이면 그런 일이 없다.
"""

from __future__ import annotations

import argparse
import base64
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import output_dir, resource_dir            # noqa: E402

SRC = "docs/manual/v030.html"
OUT_NAME = "사용자매뉴얼.html"
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp"}


def inline_images(html: str, base: Path) -> tuple[str, int, int]:
    """src="img/xx.jpg" 를 data: URI 로 바꾼다."""
    embedded = 0
    missing = 0

    def repl(m: re.Match) -> str:
        nonlocal embedded, missing
        rel = m.group(1)
        if rel.startswith(("data:", "http:", "https:")):
            return m.group(0)
        f = (base / rel).resolve()
        mime = MIME.get(f.suffix.lower())
        if not f.exists() or mime is None:
            missing += 1
            return m.group(0)
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        embedded += 1
        return f'src="data:{mime};base64,{b64}"'

    return re.sub(r'src="([^"]+)"', repl, html), embedded, missing


# 원본은 <head> 없이 시작한다(아티팩트 게시용). 배포본은 완전한 문서로 감싼다 —
# 감싸지 않으면 file:// 로 열었을 때 브라우저가 인코딩을 잘못 짚어 **한글이 전부 깨진다.**
DOC_HEAD = '<!doctype html>\n<html lang="ko">\n<head>\n'
DOC_MID = "</head>\n<body>\n"
DOC_TAIL = "\n</body>\n</html>\n"


def wrap_document(html: str) -> str:
    """<style> 까지를 head 로, 나머지를 body 로 나눈다."""
    m = re.search(r"</style>", html)
    cut = m.end() if m else 0
    return DOC_HEAD + html[:cut] + "\n" + DOC_MID + html[cut:] + DOC_TAIL


def build(src: Path, out: Path, *, bare: bool = False) -> dict:
    """bare=True 면 <!doctype> 껍데기를 씌우지 않는다.

    아티팩트로 게시할 때는 게시 쪽이 <head>·<body> 를 붙여 주므로
    여기서 또 붙이면 문서가 두 겹이 된다.
    """
    html = src.read_text(encoding="utf-8")
    html, embedded, missing = inline_images(html, src.parent)
    if not bare:
        html = wrap_document(html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"out": out, "embedded": embedded, "missing": missing,
            "bytes": out.stat().st_size}


# 헤드리스 브라우저로 PDF 를 뽑는다. 윈도우에는 Edge 가 항상 있다.
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str | None:
    for b in BROWSERS:
        if Path(b).exists():
            return b
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def to_pdf(html: Path, pdf: Path) -> tuple[bool, str]:
    """HTML 을 PDF 로 굽는다.

    한글 경로에서 브라우저가 파일을 못 여는 일이 있어(실제로 한 번 겪었다)
    ASCII 임시 경로로 옮겨 변환한 뒤 결과만 가져온다.
    """
    browser = find_browser()
    if browser is None:
        return False, "Edge 나 Chrome 을 찾지 못했습니다"

    # Edge의 Crashpad가 종료 직후 프로필 파일을 잠깐 붙잡는 경우가 있다.
    # PDF는 이미 정상 생성됐는데 임시 폴더 정리만 실패해 전체 작업이 오류로
    # 보이면 사용자는 결과까지 실패한 것으로 오해한다.
    with tempfile.TemporaryDirectory(prefix="kfa-pdf-", ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        src, out = tmp / "manual.html", tmp / "manual.pdf"
        src.write_bytes(html.read_bytes())
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            # 브라우저가 이미 떠 있으면 새 프로세스가 기존 인스턴스에 일을 넘기고
            # 곧바로 종료한다. 그러면 우리는 '끝났다'고 믿고 임시 폴더를 지우는데
            # 정작 PDF 는 그 뒤에 쓰인다. 별도 프로필을 주어 독립 실행을 강제한다.
            f"--user-data-dir={tmp / 'profile'}",
            "--no-first-run", "--no-default-browser-check",
            "--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"{type(exc).__name__}: {exc}"

        # 프로세스가 끝나도 파일이 아직 안 쓰였을 수 있다. 잠깐 기다린다.
        for _ in range(60):
            if out.exists() and out.stat().st_size > 1024:
                break
            time.sleep(0.5)
        if not out.exists():
            return False, "브라우저가 PDF 를 만들지 못했습니다"

        pdf.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(out, pdf)
        except PermissionError:
            # 윈도우는 열려 있는 PDF 를 덮어쓰지 못한다. 매뉴얼을 보면서 다시 굽는
            # 것은 흔한 일이라, 여기서 죽는 대신 옆에 새 이름으로 놓고 알린다.
            alt = pdf.with_name(f"{pdf.stem}(새로 만든 것){pdf.suffix}")
            shutil.copy2(out, alt)
            return True, f"열려 있어 덮어쓰지 못했습니다. 새 파일: {alt.name}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="사용자 매뉴얼 빌드")
    ap.add_argument("--out", default=None, help="저장 경로 (기본: assets/사용자매뉴얼.html)")
    ap.add_argument("--bare", action="store_true",
                    help="<!doctype> 껍데기 없이 — 아티팩트 게시용")
    ap.add_argument("--pdf", action="store_true", help="PDF 도 함께 만든다")
    a = ap.parse_args()

    src = resource_dir() / SRC
    if not src.exists():
        print(f"\n  [실패] 원본을 찾지 못했습니다: {src}")
        return 2

    out = Path(a.out) if a.out else output_dir() / OUT_NAME
    r = build(src, out, bare=a.bare)

    print("=" * 70)
    print(" 사용자 매뉴얼 빌드")
    print("=" * 70)
    print(f"\n  원본    {src}")
    print(f"  결과    {r['out']}")
    print(f"  그림    {r['embedded']}장 내장" + (f" · {r['missing']}장 누락" if r["missing"] else ""))
    print(f"  크기    {r['bytes'] / 1024:.0f}KB")
    if r["missing"]:
        print("\n  [주의] 일부 그림을 찾지 못해 링크로 남았습니다. docs/manual/img/ 를 확인하세요.")
    if a.pdf:
        pdf = r["out"].with_suffix(".pdf")
        ok, why = to_pdf(r["out"], pdf)
        if ok and why:
            alt = pdf.with_name(f"{pdf.stem}(새로 만든 것){pdf.suffix}")
            print(f"  PDF     {alt}  ({alt.stat().st_size / 1024 / 1024:.1f}MB)")
            print(f"  [주의] {why}")
            print("         보고 있던 PDF 를 닫고 새 파일로 바꿔 놓으세요.")
        elif ok:
            print(f"  PDF     {pdf}  ({pdf.stat().st_size / 1024 / 1024:.1f}MB)")
        else:
            print(f"  [실패] PDF 를 만들지 못했습니다 — {why}")
            print("         HTML 을 브라우저에서 열고 Ctrl+P 로 저장하세요.")

    print("\n  파일 하나로 끝납니다 — 메일·USB 로 그대로 건네도 그림이 깨지지 않습니다.")
    print("  브라우저에서 열고 Ctrl+P 로 인쇄해도 같은 PDF 가 나옵니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
