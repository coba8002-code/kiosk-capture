#!/usr/bin/env python
"""설치 점검 — 한 번에 다 확인한다.

    python setup_check.py       (소스로 개발할 때)
    KFA.exe check               (실행 파일로 쓸 때)

파이썬 버전, 패키지, 룰 DB, 저작권 가드, 테스트, 마커 생성, 데모까지
순서대로 돌려 보고 무엇이 되고 무엇이 안 되는지 알려준다.
막힌 곳이 있으면 무엇을 하면 되는지 함께 알려준다.

실행 파일 안에서는 다르게 돈다
────────────────────────────────────────────────────────────────────
    얼어붙은 exe 옆에는 `.py` 파일이 없다. 그래서 예전처럼
    `python tools/validate_rules.py` 를 새 프로세스로 띄우면 **전부 실패한다.**
    exe 안에서는 도구의 main() 을 직접 부른다.

    pytest 도 마찬가지다. 개발용 패키지이고 exe 사용자에게는 파이썬도 pip 도 없다.
    없다고 설치를 요구하면, 처음 쓰는 사람이 첫 명령에서 막힌다.
    그래서 pytest 는 선택으로 두고, 없으면 그 게이트만 건너뛴다.
"""

from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
MIN_PY = (3, 10)

if not FROZEN:
    sys.path.insert(0, str(ROOT))

# (모듈, 패키지, 설명, 필수인가)
PACKAGES = [
    ("yaml", "PyYAML", "룰 DB · 촬영 프로토콜 로드", True),
    ("numpy", "numpy", "마커 호모그래피 계산", True),
    ("cv2", "opencv-python", "ArUco 마커 검출 · 영상 분석", True),
    ("PIL", "Pillow", "문자 높이 계수 교정", True),
    ("pytest", "pytest", "테스트 (개발용 · 실행 파일에서는 불필요)", False),
]

# (이름, 도구 모듈, 인자, 실행 파일에서도 도는가)
GATES = [
    ("룰 DB 스키마·정합성", "tools.validate_rules", [], True),
    ("KS 본문 유출 차단", "tools.check_ks_copyright", [], True),
    ("엔진 테스트", None, ["-m", "pytest", "tests/", "-q"], False),
    ("마커 카드 생성·자기검증", "tools.make_marker", ["--sheet"], True),
    ("합성 촬영 측정 체인", "tools.simulate_capture", [], True),
    ("엔드투엔드 데모", "tools.demo_report", [], True),
    ("자기 검사 (우리 화면)", "tools.check_own_ui", [], True),
]

OK, NG, WARN, SKIP = "  [ OK ]", "  [FAIL]", "  [WARN]", "  [건너뜀]"


def line(ch: str = "─", n: int = 68) -> str:
    return ch * n


def check_python() -> bool:
    v = sys.version_info
    cur = f"{v.major}.{v.minor}.{v.micro}"
    if FROZEN:
        print(f"{OK} 실행 파일 (파이썬 {cur} 내장) — 별도 설치 불필요")
        return True
    if (v.major, v.minor) >= MIN_PY:
        print(f"{OK} 파이썬 {cur}  (필요: {MIN_PY[0]}.{MIN_PY[1]} 이상)")
        return True
    print(f"{NG} 파이썬 {cur} — {MIN_PY[0]}.{MIN_PY[1]} 이상이 필요합니다")
    print("         python.org 에서 최신 버전을 설치하세요.")
    return False


def check_packages() -> list[str]:
    """없는 **필수** 패키지만 돌려준다. 선택 패키지는 경고만 남긴다."""
    missing: list[str] = []
    for mod, pkg, why, required in PACKAGES:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            print(f"{OK} {pkg:<16} {ver:<10} {why}")
        except ImportError:
            print(f"{NG if required else WARN} {pkg:<16} {'':<10} {why}")
            if required:
                missing.append(pkg)
    return missing


def _has(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except ImportError:
        return False


def run_gate(name: str, module: str | None, args: list[str],
             frozen_ok: bool) -> tuple[bool | None, str]:
    """게이트 하나 실행. (성공여부 또는 None=건너뜀, 마지막 줄)"""
    if FROZEN and not frozen_ok:
        return None, "개발용 — 실행 파일에서는 해당 없음"
    if module is None and not _has("pytest"):
        return None, "pytest 가 없어 건너뜁니다 (개발용)"

    if FROZEN:
        # 새 프로세스를 띄울 수 없다. exe 옆에는 .py 가 없기 때문이다.
        return _run_inprocess(module, args)

    cmd = [sys.executable] + [
        a if a.startswith("-") else str(ROOT / a) if a.endswith(".py") else a
        for a in (args if module is None else [module.replace(".", "/") + ".py"] + args)
    ]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(cmd, capture_output=True, cwd=str(ROOT), env=env, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "시간 초과"
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode == 0, _tail(out)


def _run_inprocess(module: str, args: list[str]) -> tuple[bool, str]:
    """도구의 main() 을 직접 부른다. 출력은 삼키고 마지막 줄만 남긴다."""
    saved = sys.argv
    buf = io.StringIO()
    try:
        mod = importlib.import_module(module)
        sys.argv = [module] + args
        with redirect_stdout(buf):
            rc = mod.main()
        return rc == 0, _tail(buf.getvalue())
    except SystemExit as exc:
        return (exc.code or 0) == 0, _tail(buf.getvalue())
    except Exception as exc:                                 # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:60]
    finally:
        sys.argv = saved


def _tail(text: str) -> str:
    ln = next((x for x in reversed((text or "").strip().splitlines()) if x.strip()), "")
    return ln.strip()[:60]


def next_steps() -> None:
    """다음에 할 일. 남은 구멍은 **손으로 적지 않고** gaps 에서 가져온다.

    예전에는 여기에 '수집 앱은 아직 없다', 'OCR 연동 없음' 같은 목록이 박혀 있었고,
    다 만든 뒤에도 그대로 남아 처음 쓰는 사람에게 거짓말을 했다.
    상태를 두 곳에 적으면 반드시 한 쪽이 낡는다.
    """
    print("""
 다음에 할 것

   1) 마커 카드를 인쇄한다        assets/BFK-MARK-A_A4_sheet.png
        ★ '실제 크기 100%' 로 인쇄. '용지에 맞춤' 금지.
        ★ 인쇄 후 카드의 20mm 눈금을 자로 확인.

   2) 현장 체크리스트를 인쇄한다   KFA.exe sheet

   3) 수집 앱을 켜고 폰으로 접속   KFA.exe serve-app

   4) 찍어 온 폴더를 진단한다      KFA.exe ingest <폴더>
""")
    try:
        from tools import gaps
        d = gaps.audit()
    except Exception:                                        # noqa: BLE001
        print(" 남은 것은 KFA.exe gaps 로 확인하세요.\n")
        return

    l2, ocr = d["l2"], d["ocr"]
    pending = []
    if l2["built"] and not l2["configured"]:
        n = sum(1 for i in d["items"] if i["state"].startswith("L2"))
        pending.append(f"L2 판정 {n}항목 — 코드는 있고 공급자만 켜면 됩니다 (API 키)")
    if ocr["built"] and not ocr["configured"]:
        pending.append("OCR 판정 3.g — 코드는 있고 엔진만 설치하면 됩니다")
    elif ocr["configured"] and not ocr["calibrated"]:
        pending.append("OCR 상자 계수 미교정 — KFA.exe calibrate-ocr 을 한 번 실행하세요")

    if pending:
        print(" 켜면 더 되는 것")
        for p in pending:
            print(f"   · {p}")
        print("\n 자세한 내용은 OPERATIONS.md §5, 지금 상태는 KFA.exe gaps 를 보세요.\n")
    else:
        print(" 남은 구멍은 KFA.exe gaps 로 확인하세요.\n")


def main() -> int:
    print(line("═"))
    print(" 키오스크 현장 접근성 진단 — 설치 점검")
    print(line("═"))

    print("\n1. 실행 환경\n" + line())
    py_ok = check_python()

    print("\n2. 패키지\n" + line())
    missing = check_packages()

    if not py_ok:
        return 2
    if missing:
        print(f"\n{NG} 필수 패키지 {len(missing)}개가 없습니다. 아래를 실행하세요.\n")
        print("      pip install -r requirements.txt\n")
        print("   (또는)")
        print(f"      pip install {' '.join(missing)}\n")
        return 1

    print("\n3. 동작 점검\n" + line())
    failed, skipped = [], []
    for name, module, args, frozen_ok in GATES:
        ok, tail = run_gate(name, module, args, frozen_ok)
        mark = SKIP if ok is None else (OK if ok else NG)
        print(f"{mark} {name:<22} {tail}")
        if ok is None:
            skipped.append(name)
        elif not ok:
            failed.append(name)

    print("\n" + line("═"))
    if failed:
        print(f" 점검 실패 — {len(failed)}개 항목")
        print(line("═"))
        for f in failed:
            print(f"   · {f}")
        print("\n 해당 명령을 직접 실행해 전체 메시지를 확인하세요.")
        return 1

    tail = f" (건너뜀 {len(skipped)}개 — 개발용)" if skipped else ""
    print(f" 모두 정상 — 엔진이 동작합니다{tail}")
    print(line("═"))
    next_steps()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
