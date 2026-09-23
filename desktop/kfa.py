#!/usr/bin/env python
"""KFA — 키오스크 현장 접근성 진단 통합 실행기.

    python kfa.py            대화형 메뉴
    python kfa.py check      설치·동작 점검
    python kfa.py marker     마커 카드 생성 (인쇄용)
    python kfa.py sheet      현장 촬영 체크리스트 생성 (인쇄용)
    python kfa.py verify     측정 체인 검증 (합성 촬영)
    python kfa.py report     리포트 생성
    python kfa.py rules      룰 DB 검증
    python kfa.py calibrate  문자 높이 계수 교정

PyInstaller 로 단일 실행 파일(KFA.exe)로도 묶인다.
묶였을 때와 아닐 때 경로 규칙이 다르므로 paths 모듈이 그 차이를 흡수한다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _setup_console() -> None:
    """콘솔을 UTF-8 로 맞춘다 — **다른 무엇보다 먼저 실행돼야 한다.**

    개발 중에는 PYTHONUTF8=1 을 켜고 돌리기 때문에 이 문제가 보이지 않는다.
    그러나 사용자가 KFA.exe 를 더블클릭하면 콘솔은 시스템 기본 코드페이지(한국어
    Windows 는 CP949)로 열리고, 그 순간 두 가지가 한꺼번에 터진다.

      · 한글이 깨져 나온다 (`별표5` → `��ǥ5`)
      · '✓' 같은 CP949 에 없는 글자에서 UnicodeEncodeError 로 **프로그램이 죽는다**

    그래서 콘솔 코드페이지와 스트림 인코딩을 둘 다 UTF-8 로 바꾸고,
    그래도 못 쓰는 글자가 나오면 죽는 대신 대체 문자로 넘긴다.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:                                    # noqa: BLE001
            pass
    # line_buffering 을 켜는 이유 — **조작판 화면이 멈춘 것처럼 보이는 것을 막는다.**
    #
    # 출력이 파이프로 나가면 파이썬은 블록 버퍼링을 쓴다. 조작판은 자식의 stdout 을
    # 파이프로 읽으므로, 그대로 두면 점검(1분) 내내 로그가 한 줄도 안 나오다가
    # 끝나는 순간 한꺼번에 쏟아진다. 사용자는 그 1분 동안 얼어붙은 화면을 본다.
    # PYTHONUNBUFFERED 로도 되지 않았다(실행 파일에서 확인). 여기서 못 박는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
        except Exception:                                    # noqa: BLE001
            pass


_setup_console()

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.paths import output_dir, resource_dir  # noqa: E402

VERSION = "0.4.0"

MENU = [
    ("1", "check",     "설치·동작 점검",        "처음이라면 여기부터"),
    ("2", "marker",    "마커 카드 만들기",      "인쇄용 · 실제크기 100%로 인쇄"),
    ("3", "sheet",     "현장 체크리스트",       "인쇄용 · 촬영 나갈 때"),
    ("4", "verify",    "측정 체인 검증",        "사진에서 mm를 되찾는지 확인"),
    ("5", "report",    "리포트 만들어보기",     "판정 → 리포트 A·B"),
    ("6", "rules",     "룰 DB 검증",           "별표5 40항목 정합성"),
    ("7", "demo-bundle","데모 번들 만들기",     "진단을 시험해볼 촬영 번들"),
    ("8", "ingest",    "수집 번들 진단하기",     "앱이 찍어 온 폴더 → 리포트"),
    ("9", "serve-app", "수집 앱 켜기",         "폰에서 접속해 촬영"),
    ("r", "review",    "검토자 승인",          "위반 의심 → 부적합 확정"),
    ("g", "gaps",      "구멍 점검",            "무엇이 아직 없는지"),
    ("c", "cert",      "HTTPS 인증서 만들기",    "폰에서 오프라인으로 쓰려면"),
    ("m", "manual",    "사용자 매뉴얼 만들기",     "설치·설정·사용법 한 파일"),
    ("w", "web",       "웹서버용 내보내기",       "촬영 앱을 서버에 올린다"),
    ("s", "self-check","자기 검사",             "우리 화면이 우리 기준을 통과하는가"),
    ("f", "field-qa",  "현장자료 품질 점검",     "누락·마커·음성·기록 확인"),
    ("0", "open",      "결과 폴더 열기",        ""),
]


def banner() -> None:
    print("=" * 66)
    print(f"  KFA  키오스크 현장 접근성 진단  v{VERSION}")
    print("=" * 66)
    print(f"  결과 저장 위치   {output_dir()}")
    if getattr(sys, "frozen", False):
        print("  실행 방식        단일 실행 파일 (파이썬 설치 불필요)")
    print()


def run(cmd: str, argv: list[str]) -> int:
    """서브커맨드 실행. 각 도구의 main() 을 그대로 호출한다."""
    old = sys.argv
    try:
        if cmd == "check":
            import setup_check
            sys.argv = ["check"] + argv
            return setup_check.main()
        if cmd == "marker":
            from tools import make_marker
            sys.argv = ["marker"] + (argv or ["--sheet"])
            return make_marker.main()
        if cmd == "sheet":
            from tools import make_field_sheet
            sys.argv = ["sheet"] + (argv or ["--html"])
            return make_field_sheet.main()
        if cmd == "verify":
            from tools import simulate_capture
            sys.argv = ["verify"] + argv
            return simulate_capture.main()
        if cmd == "report":
            from tools import demo_report
            sys.argv = ["report"] + argv
            return demo_report.main()
        if cmd == "rules":
            from tools import validate_rules
            sys.argv = ["rules"] + (argv or ["--coverage"])
            return validate_rules.main()
        if cmd == "calibrate":
            from tools import calibrate_text_height
            sys.argv = ["calibrate"] + argv
            return calibrate_text_height.main()
        if cmd in ("self-check", "selfcheck"):
            from tools import check_own_ui
            sys.argv = ["self-check"] + argv
            return check_own_ui.main()
        if cmd == "manual":
            from tools import make_manual
            sys.argv = ["manual"] + argv
            return make_manual.main()
        if cmd == "cert":
            from tools import make_cert
            sys.argv = ["cert"] + argv
            return make_cert.main()
        if cmd in ("calibrate-ocr", "ocr"):
            from tools import calibrate_ocr
            sys.argv = ["calibrate-ocr"] + argv
            return calibrate_ocr.main()
        if cmd == "copyright":
            from tools import check_ks_copyright
            sys.argv = ["copyright"] + argv
            return check_ks_copyright.main()
        if cmd == "ingest":
            from tools import ingest
            if not argv:
                d = output_dir() / "demo-bundle"
                if not d.exists():
                    print("  진단할 번들 폴더를 지정하세요:  KFA.exe ingest <폴더>")
                    print("  시험해보려면 먼저 데모 번들을 만드세요:  KFA.exe demo-bundle")
                    return 2
                print(f"  번들 미지정 — 데모 번들을 진단합니다: {d}\n")
                argv = [str(d)]
            sys.argv = ["ingest"] + argv
            return ingest.main()
        if cmd == "review":
            from tools import review
            if not argv:
                d = output_dir() / "demo-bundle"
                if not d.exists():
                    print("  검토할 번들 폴더를 지정하세요:  KFA.exe review <폴더>")
                    return 2
                argv = [str(d)]
            sys.argv = ["review"] + argv
            return review.main()
        if cmd == "gaps":
            from tools import gaps
            sys.argv = ["gaps"] + argv
            return gaps.main()
        if cmd in ("field-qa", "quality"):
            from tools import field_qa
            sys.argv = ["field-qa"] + argv
            return field_qa.main()
        if cmd in ("serve-app", "serve"):
            from tools import serve_app
            sys.argv = ["serve-app"] + argv
            return serve_app.main()
        if cmd in ("web", "publish-web"):
            from tools import publish_web
            sys.argv = ["web"] + argv
            return publish_web.main()
        if cmd == "build-app":
            from tools import build_app
            sys.argv = ["build-app"] + argv
            return build_app.main()
        if cmd in ("demo-bundle", "demo"):
            from tools import make_demo_bundle
            sys.argv = ["demo-bundle"] + argv
            return make_demo_bundle.main()
        if cmd == "open":
            open_output()
            return 0
    finally:
        sys.argv = old

    print(f"  알 수 없는 명령: {cmd}")
    print(f"  사용 가능: {', '.join(c for _, c, _, _ in MENU)}, calibrate, copyright")
    return 2


def open_output() -> None:
    d = output_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(d))            # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", str(d)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(d)], check=False)
        print(f"  폴더를 열었습니다: {d}")
    except Exception:
        print(f"  결과 폴더: {d}")


def interactive() -> int:
    """첫 화면은 **쉬운 모드**다.

    메뉴 15개 중 열은 검증·개발용이다. 현장에 나가는 사람이 하는 일은
    준비·촬영·진단 셋뿐인데, 그 셋을 나머지 열둘과 나란히 놓으면
    셋을 찾는 것부터가 일이 된다. 전체 메뉴는 x 로 언제든 갈 수 있다.
    """
    from tools import easy

    while True:
        banner()
        r = easy.screen(run)
        if r == "quit":
            return 0
        if r == "expert":
            if expert() == "quit":
                return 0
        print("\n" * 2)


def expert() -> str:
    """전체 메뉴. 돌아가려면 b, 종료는 q."""
    while True:
        banner()
        for key, _, title, note in MENU:
            print(f"   {key}.  {title:<18}{note}")
        print("\n   b.  쉬운 화면으로 돌아가기")
        print("   q.  종료\n")
        try:
            sel = input("   번호를 입력하세요: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "quit"
        if sel in ("q", "Q"):
            return "quit"
        if sel in ("b", "B", ""):
            return "back"

        match = next((c for k, c, _, _ in MENU if k == sel), None)
        if not match:
            continue

        print()
        try:
            run(match, [])
        except Exception as exc:                       # noqa: BLE001
            print(f"\n  [오류] {type(exc).__name__}: {exc}")
            if match == "marker":
                print("        assets 폴더에 쓰기 권한이 있는지 확인하세요.")

        _hint(match)
        try:
            input("\n   엔터를 누르면 메뉴로 돌아갑니다... ")
        except (EOFError, KeyboardInterrupt):
            return "quit"
        print("\n" * 2)


def _hint(cmd: str) -> None:
    if cmd == "marker":
        print("\n  " + "-" * 62)
        print("  인쇄 시 반드시 확인하세요")
        print("    · 배율은 '실제 크기' 100%. '용지에 맞춤'을 쓰면 치수 판정이 전부 틀어집니다.")
        print("    · 무광 코팅 200g 이상 용지. 유광은 화면 반사와 겹쳐 검출이 안 됩니다.")
        print("    · 인쇄 후 카드의 20mm 눈금에 자를 대어 확인하세요.")
        print("  " + "-" * 62)
    elif cmd == "sheet":
        p = output_dir() / "field-sheet.html"
        if p.exists():
            print(f"\n  브라우저에서 열어 Ctrl+P 로 인쇄하세요:\n    {p}")


SW_HIDE, SW_SHOW = 0, 5


def _console_is_ours() -> bool:
    """이 검은 창이 우리가 띄운 것인가, 사용자가 열어 둔 것인가.

    더블클릭으로 실행하면 콘솔에 붙은 프로세스는 우리 하나뿐이다.
    명령 프롬프트에서 실행하면 그 셸까지 둘 이상이다. 이 차이로 갈라
    **더블클릭한 사람에게만 검은 창을 숨긴다.** 터미널에서 부른 사람의
    창을 우리가 숨기면 출력이 사라져 버린다.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        buf = (ctypes.c_uint32 * 4)()
        n = ctypes.windll.kernel32.GetConsoleProcessList(buf, 4)
        return n == 1
    except Exception:                                    # noqa: BLE001
        return False


def _show_console(show: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW if show else SW_HIDE)
    except Exception:                                    # noqa: BLE001
        pass


def launch() -> int:
    """더블클릭했을 때의 기본 동작 — 브라우저 조작판.

    검은 창에 번호를 치게 하지 않는다. 조작판을 못 열면 그때만 창을 도로
    보여 주고 예전 메뉴로 내려간다. 조용히 실패하면 사용자는 아무것도 못 한다.
    """
    import os
    if os.environ.get("KFA_NO_PANEL"):       # 조작판이 띄운 자식은 다시 열지 않는다
        return interactive()
    hidden = _console_is_ours()
    if hidden:
        _show_console(False)
    try:
        from tools import panel
        sys.argv = ["panel"]
        return panel.main()
    except Exception as exc:                             # noqa: BLE001
        if hidden:
            _show_console(True)
        print("=" * 66)
        print("  조작판을 열지 못했습니다 — 예전 메뉴로 진행합니다.")
        print(f"  이유: {type(exc).__name__}: {exc}")
        print("=" * 66 + "\n")
        return interactive()


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        return launch()
    if argv[0] in ("menu", "--menu"):
        return interactive()
    if argv[0] in ("panel", "ui"):
        from tools import panel
        sys.argv = ["panel"] + argv[1:]
        return panel.main()
    if argv[0] in ("-h", "--help", "help"):
        banner()
        for _, cmd, title, note in MENU:
            print(f"   {cmd:<12}{title:<18}{note}")
        print(f"   {'calibrate':<12}{'문자 높이 계수 교정':<18}")
        print(f"   {'copyright':<12}{'KS 본문 유출 검사':<18}")
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"KFA {VERSION}")
        return 0
    return run(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
