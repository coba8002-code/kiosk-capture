#!/usr/bin/env python
"""쉬운 모드 — 고를 것을 셋으로 줄인다.

왜 만들었나
────────────────────────────────────────────────────────────────────
    메뉴에 15개가 있었다. 그중 열은 개발·검증용이다.
    현장에 나가는 사람이 실제로 하는 일은 셋뿐인데
    (준비한다 · 찍는다 · 진단한다) 화면은 그 셋을 다른 열둘과 나란히 놓았다.

    그리고 제일 어려운 대목은 메뉴가 아니라 **폴더 경로였다.**
    `KFA.exe ingest <폴더>` 는 검은 창에 한글이 섞인 경로를 손으로 치라는 뜻이다.
    현장에서 그걸 할 사람은 없다. 그래서 여기서는 경로를 묻지 않는다 —
    **찾아서 번호로 고르게** 하고, 폰이 내보낸 ZIP 은 풀지 않고 그대로 받는다.

    만들어진 파일도 알아서 연다. '결과 폴더 열기 → 찾기 → 더블클릭' 은
    세 단계지만 사용자에겐 세 번의 실패 기회다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
import tempfile
import shutil
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir          # noqa: E402

TASKS = [
    ("1", "준비하기", "처음이거나, 현장 나가기 전"),
    ("2", "촬영하기", "폰으로 키오스크 찍기"),
    ("3", "진단하기", "찍어 온 사진 → 리포트"),
]

# 번들을 찾아볼 곳. 폰에서 받은 ZIP 은 십중팔구 다운로드 아니면 바탕화면에 있다.
def _search_roots() -> list[Path]:
    home = Path(os.path.expanduser("~"))
    roots = [output_dir(), app_dir(),
             home / "Downloads", home / "Desktop", home / "Documents"]
    # 한국어 윈도우는 폴더 표시 이름만 한글이고 실제 이름은 영문이지만,
    # 드물게 실제로 한글인 환경이 있어 둘 다 본다.
    roots += [home / "다운로드", home / "바탕 화면", home / "문서"]
    out, seen = [], set()
    for r in roots:
        try:
            key = str(r.resolve()).lower()
        except OSError:
            continue
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


MAX_ZIP_PROBE = 60          # 다운로드 폴더에 ZIP 이 수백 개인 사람이 있다


class Found:
    """찾아낸 번들 하나. 폴더일 수도 ZIP 일 수도 있다."""

    def __init__(self, path: Path, is_zip: bool, info: dict | None):
        self.path = path
        self.is_zip = is_zip
        self.info = info or {}

    @property
    def label(self) -> str:
        d = self.info.get("device", {})
        who = d.get("id") or "기기 미기재"
        loc = d.get("location") or ""
        n = len(self.info.get("shots", []))
        head = f"{who} {loc}".strip()
        return f"{head}  ·  촬영 {n}건" if n else head

    @property
    def when(self) -> str:
        try:
            import datetime as _dt
            t = _dt.datetime.fromtimestamp(self.path.stat().st_mtime)
            return t.strftime("%m/%d %H:%M")
        except OSError:
            return ""


def _read_manifest_dir(d: Path) -> dict | None:
    f = d / "manifest.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if _manifest_shape(data) else None
    except (OSError, ValueError):
        return {}


def _read_manifest_zip(z: Path) -> dict | None:
    """ZIP 안을 들여다본다. 열지 못하는 파일은 조용히 넘긴다 —
    다운로드 폴더에는 우리와 무관한 ZIP 이 훨씬 많다."""
    try:
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                return None
            if zf.getinfo("manifest.json").file_size > 2 * 1024**2:
                return None
            data = json.loads(zf.read("manifest.json").decode("utf-8"))
            return data if _manifest_shape(data) else None
    except (OSError, zipfile.BadZipFile, ValueError, KeyError):
        return None


def _manifest_shape(data) -> bool:
    return (isinstance(data, dict) and data.get("schema") == "kfa.capture/1"
            and isinstance(data.get("device"), dict)
            and isinstance(data.get("shots", []), list))


def find_bundles() -> list[Found]:
    """촬영 번들을 찾는다. 폴더와 ZIP 을 모두 본다."""
    out: list[Found] = []
    seen: set[str] = set()
    probed = 0

    for root in _search_roots():
        # 폴더 — 루트 자신과 두 단계 아래까지
        cands = [root]
        try:
            cands += [p for p in root.iterdir() if p.is_dir()]
            for p in list(cands[1:]):
                try:
                    cands += [q for q in p.iterdir() if q.is_dir()]
                except OSError:
                    pass
        except OSError:
            pass
        for d in cands:
            key = str(d).lower()
            if key in seen:
                continue
            info = _read_manifest_dir(d)
            if info is not None:
                seen.add(key)
                out.append(Found(d, False, info))

        # ZIP — 최신 것부터, 정해진 개수까지만
        try:
            zips = sorted((p for p in root.glob("*.zip") if p.is_file()),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            zips = []
        for z in zips:
            if probed >= MAX_ZIP_PROBE:
                break
            probed += 1
            key = str(z).lower()
            if key in seen:
                continue
            info = _read_manifest_zip(z)
            if info is not None:
                seen.add(key)
                out.append(Found(z, True, info))

    out.sort(key=lambda f: f.path.stat().st_mtime, reverse=True)
    return out


class UnsafeZip(RuntimeError):
    """ZIP 안의 경로가 대상 폴더 밖을 가리킨다."""


def extract(z: Path) -> Path:
    """ZIP 을 풀고 그 폴더를 돌려준다.

    폰에서 온 파일이라 해도 내용을 믿고 풀지 않는다. `../` 가 섞인 항목 하나면
    실행 파일 옆의 다른 파일을 덮어쓸 수 있다. 풀기 전에 전부 확인한다.
    """
    parent = output_dir() / "bundles"
    parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        members = zf.infolist()
        if len(members) > 10000 or sum(m.file_size for m in members) > 4 * 1024**3:
            raise UnsafeZip("ZIP 크기 제한 초과: 최대 10,000개 파일 / 압축 해제 4GB")
        seen = set()
        for m in zf.infolist():
            name = m.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            if (name.startswith("/") or any(p in ("", ".", "..") or ":" in p
                    or p.endswith((" ", ".")) for p in parts)
                    or (m.external_attr >> 16) & 0o170000 == 0o120000):
                raise UnsafeZip(f"ZIP 안에 폴더 밖을 가리키는 항목이 있습니다: {m.filename}")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise UnsafeZip(f"ZIP에 중복된 파일 이름이 있습니다: {m.filename}")
            seen.add(key)
        if "manifest.json" not in zf.namelist():
            raise UnsafeZip("manifest.json이 없는 ZIP입니다")
        dest = Path(tempfile.mkdtemp(prefix="capture-", dir=parent)).resolve()
        try:
            zf.extractall(dest)
        except Exception:
            shutil.rmtree(dest)
            raise
    return dest


def open_path(p: Path) -> bool:
    """파일이나 폴더를 시스템 기본 프로그램으로 연다."""
    try:
        if sys.platform == "win32":
            os.startfile(str(p))                          # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
        return True
    except Exception:                                     # noqa: BLE001
        return False


def _pause(msg: str = "\n   엔터를 누르면 계속합니다... ") -> bool:
    try:
        input(msg)
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def _newest(pattern: str) -> Path | None:
    try:
        hits = sorted(output_dir().glob(pattern),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return hits[0] if hits else None


def _rule(title: str) -> None:
    print("\n" + "=" * 66)
    print(f"  {title}")
    print("=" * 66 + "\n")


# ── ① 준비하기 ──────────────────────────────────────────────────────────
def prepare(run) -> int:
    _rule("준비하기 — 세 단계")
    print("  ① 프로그램이 제대로 깔렸는지 봅니다")
    print("  ② 마커 카드를 만듭니다      (인쇄해서 들고 갑니다)")
    print("  ③ 현장 체크리스트를 만듭니다 (인쇄해서 들고 갑니다)")
    if not _pause("\n   시작하려면 엔터... "):
        return 0

    _rule("① 설치·동작 점검")
    run("check", [])

    _rule("② 마커 카드 만들기")
    run("marker", [])
    card = _newest("BFK-MARK-A_A4_sheet.png") or _newest("*A4*.png")
    if card:
        print(f"\n  만들었습니다: {card.name}")
        if open_path(card):
            print("  사진 앱이 열렸습니다. Ctrl+P 로 인쇄하세요.")

    print("\n  " + "-" * 62)
    print("  인쇄 설정에서 반드시 확인하세요")
    print("    · 배율 '실제 크기 100%'.  '용지에 맞춤' 을 쓰면 모든 치수가 틀어집니다.")
    print("    · 무광 200g 이상 용지.   유광은 화면 반사와 겹쳐 검출이 안 됩니다.")
    print("    · 인쇄 후 카드의 20mm 눈금에 자를 대어 확인하세요.")
    print("  " + "-" * 62)
    if not _pause():
        return 0

    _rule("③ 현장 체크리스트 만들기")
    run("sheet", [])
    sheet = _newest("field-sheet.html")
    if sheet:
        print(f"\n  만들었습니다: {sheet.name}")
        if open_path(sheet):
            print("  브라우저가 열렸습니다. Ctrl+P 로 인쇄하세요.")

    _rule("준비 끝")
    print("  현장에 들고 갈 것 두 장")
    if card:
        print(f"    1. 마커 카드        {card.name}")
    if sheet:
        print(f"    2. 현장 체크리스트   {sheet.name}")
    print(f"\n  둘 다 여기 있습니다:  {output_dir()}")
    print("\n  다음은 '2. 촬영하기' 입니다.")
    return 0


# ── ② 촬영하기 ──────────────────────────────────────────────────────────
def capture(run) -> int:
    _rule("촬영하기 — 폰으로 찍습니다")
    print("  이 PC 가 촬영 앱을 내보내고, 폰이 접속해서 찍습니다.")
    print("  사진은 폰 안에만 있다가 마지막에 ZIP 하나로 나옵니다.\n")
    print("  먼저 확인하세요")
    print("    · 폰과 이 PC 가 **같은 와이파이**에 있어야 합니다.")
    print("    · 마커 카드를 챙겼는지.")
    print("\n  잠시 뒤 주소가 나옵니다. 그 주소를 폰 브라우저에 입력하세요.")
    print("  끝내려면 이 창에서 Ctrl+C 를 누릅니다.")
    if not _pause("\n   앱을 켜려면 엔터... "):
        return 0
    print()
    return run("serve-app", [])


# ── ③ 진단하기 ──────────────────────────────────────────────────────────
def _choose(found: list[Found]) -> Found | None:
    print("  찾은 촬영 번들\n")
    for i, f in enumerate(found, 1):
        kind = "ZIP" if f.is_zip else "폴더"
        print(f"   {i:>2}.  {f.label}")
        print(f"        {kind}  {f.when}   {f.path}")
    print("\n    0.  돌아가기")
    print("\n  번호를 고르거나, 폴더·ZIP 을 이 창에 끌어다 놓고 엔터를 누르세요.")
    try:
        sel = input("\n   선택: ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        return None
    if not sel or sel == "0":
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(found):
        return found[int(sel) - 1]

    # 끌어다 놓은 경로
    p = Path(sel)
    if p.is_dir():
        return Found(p, False, _read_manifest_dir(p))
    if p.is_file() and p.suffix.lower() == ".zip":
        return Found(p, True, _read_manifest_zip(p))
    print(f"\n  [실패] 찾을 수 없습니다: {sel}")
    return None


def _no_bundle(run) -> int:
    print("  촬영 번들을 찾지 못했습니다.\n")
    print("  번들은 폰 촬영 앱이 내보낸 ZIP 이거나, 그것을 푼 폴더입니다.")
    print("  다운로드·바탕화면·문서 폴더와 이 프로그램 폴더를 훑었습니다.\n")
    print("  할 수 있는 것")
    print("    1. 폰에서 ZIP 을 이 PC 로 옮긴 뒤 다시 시도")
    print("    2. 데모 번들을 만들어 진단 과정을 먼저 봐두기")
    print("    0. 돌아가기")
    try:
        sel = input("\n   선택: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if sel == "2":
        _rule("데모 번들 만들기")
        run("demo-bundle", [])
        return diagnose(run)
    return 0


def diagnose(run) -> int:
    _rule("진단하기 — 찍어 온 사진에서 리포트를 만듭니다")
    found = find_bundles()
    if not found:
        return _no_bundle(run)

    pick = _choose(found)
    if pick is None:
        return 0

    target = pick.path
    if pick.is_zip:
        print(f"\n  ZIP 을 푸는 중… {target.name}")
        try:
            target = extract(target)
        except (UnsafeZip, OSError, zipfile.BadZipFile) as exc:
            print(f"\n  [실패] {exc}")
            return 2
        print(f"  풀었습니다: {target}")

    _rule("진단")
    rc = run("ingest", [str(target)])
    if rc != 0:
        return rc

    # 리포트를 알아서 연다. 이 마지막 한 걸음에서 사람들이 제일 많이 멈춘다.
    dev = (pick.info.get("device", {}) or {}).get("id", "")
    outs = sorted(output_dir().glob("report_*"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    rep = next((d for d in outs if dev and d.name == f"report_{dev}"), None) or \
        (outs[0] if outs else None)
    if rep:
        owner = rep / "리포트A_점주용.html"
        if owner.exists() and open_path(owner):
            print(f"\n  브라우저에 리포트를 열었습니다: {owner.name}")
            print("  Ctrl+P → '대상: PDF 로 저장' 하면 제출본이 됩니다.")
        open_path(rep)
        print(f"\n  결과 폴더  {rep}")
        print("    리포트A_점주용.html      점주에게 주는 것")
        print("    리포트B_담당자용.html    기관 제출용 (근거·미판정 포함)")
        print("    report.md · result.json  원자료")
    return 0


def screen(run) -> str | None:
    """첫 화면. 고른 것을 처리하고, 메뉴로 돌아갈지 알린다.

    돌려주는 값
        None       계속 (같은 화면을 다시 그린다)
        "quit"     종료
        "expert"   전체 메뉴로
    """
    print("   무엇을 하시겠습니까?\n")
    for key, title, note in TASKS:
        print(f"    {key}.  {title:<10}{note}")
    print()
    print(f"    {'0.':<4}결과 폴더 열기")
    print(f"    {'h.':<4}설명서 보기       (PDF)")
    print(f"    {'x.':<4}그 밖의 기능      (검증·개발용 전체 메뉴)")
    print(f"    {'q.':<4}종료")

    try:
        sel = input("\n   번호를 입력하세요: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit"

    if sel in ("q", ""):
        return "quit"
    if sel == "x":
        return "expert"
    if sel == "0":
        open_path(output_dir())
        print(f"\n  결과 폴더를 열었습니다: {output_dir()}")
    elif sel == "h":
        doc = None
        for name in ("사용자매뉴얼.pdf", "사용자매뉴얼.html"):
            for base in (app_dir(), output_dir()):
                if (base / name).exists():
                    doc = base / name
                    break
            if doc:
                break
        if doc and open_path(doc):
            print(f"\n  설명서를 열었습니다: {doc.name}")
        else:
            print("\n  설명서를 찾지 못했습니다. 메뉴 x → m 으로 다시 만들 수 있습니다.")
    elif sel == "1":
        prepare(run)
    elif sel == "2":
        capture(run)
    elif sel == "3":
        diagnose(run)
    else:
        return None

    _pause("\n   엔터를 누르면 처음으로 돌아갑니다... ")
    return None
