#!/usr/bin/env python
"""배포 꾸러미 만들기 — 받는 사람이 압축만 풀면 되도록.

    python tools/make_package.py
    python tools/make_package.py --out E:/배포

무엇을 넣고 무엇을 빼는가
────────────────────────────────────────────────────────────────────
    넣는다   실행 파일 · 매뉴얼(PDF·HTML) · 인쇄물 2종 · 소스 전체
             소스를 넣는 이유는 **실행 파일에 OCR 이 없기 때문**이다.
             문자 높이(3.g)를 자동으로 재려면 소스로 실행해야 한다.

    뺀다     빌드 찌꺼기(build/ dist/ __pycache__) · 진단 결과물 · 데모 번들
             **그리고 인증서 폴더 전체.**

인증서를 빼는 이유 — 이게 이 도구의 존재 이유 중 하나다
    assets/cert/ 에는 kfa-ca.key 가 있다. **CA 개인키다.**
    이걸 배포에 섞으면, 받은 사람 전부가 같은 키를 갖게 되고
    그 키로 서명한 인증서를 폰이 믿게 된다. 한 사람만 유출돼도
    설치한 모든 폰이 중간자 공격에 열린다.

    인증서는 설치한 자리에서 `KFA.exe cert` 로 새로 만들면 된다.
    그래서 여기서는 **넣지 않는 것이 맞고**, 실수로 들어가지 않도록
    아래 SECRET_PATTERNS 로 한 번 더 막는다.
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
import zipfile
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import resource_dir                       # noqa: E402

VERSION = "0.3.0"
PKG_NAME = f"KFA-{VERSION}"

# 소스에서 제외할 것 (경로 조각 또는 glob)
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "build", "dist",
             "assets", "docs", ".venv", "node_modules"}
SKIP_FILES = {"*.pyc", "*.pyo", "*.bak", "*.spec.bak", "*.log"}

# 절대 꾸러미에 들어가면 안 되는 것. 이름이 아니라 **내용**으로도 막는다.
SECRET_PATTERNS = ["*.key", "*.pem", "*.pfx", "*.p12", "id_rsa*", ".env"]

# 표식을 쪼개 둔 이유: 그대로 적으면 **이 파일 자신이 걸린다.**
# 실제로 첫 실행에서 make_package.py 가 유출로 잡혔다. 검사기가 동작한다는
# 증거이긴 하지만, 자기를 잡는 검사는 쓸 수 없다.
SECRET_MARKERS = [
    b"PRIVATE" + b" KEY",
    b"ANTHROPIC_API_KEY" + b"=sk-",
    b"BEGIN" + b" OPENSSH",
]


class SecretLeak(RuntimeError):
    """비밀이 꾸러미에 섞였다. 만들다 말고 죽는다 — 나가면 되돌릴 수 없다."""


def looks_secret(path: Path) -> str | None:
    """이름과 내용 둘 다 본다. 이름만 보면 확장자를 바꾼 파일을 놓친다."""
    for pat in SECRET_PATTERNS:
        if fnmatch.fnmatch(path.name.lower(), pat):
            return f"파일명 규칙({pat})"
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return None
    for marker in SECRET_MARKERS:
        if marker in head:
            return f"내용에 {marker.decode('latin1')}"
    return None


def _clear(d: Path) -> None:
    """폴더를 비운다. 폴더 자체가 지워지지 않아도 계속한다.

    윈도우 탐색기로 그 폴더를 열어 두면 폴더 자체는 지워지지 않는다.
    꾸러미를 굽는 사람은 대개 그 폴더를 열어 놓고 본다 — 여기서 죽으면
    '탐색기를 닫으세요' 를 매번 알아내야 한다. 안을 비우는 것으로 충분하다.
    """
    if not d.exists():
        return
    try:
        shutil.rmtree(d)
        return
    except OSError:
        pass
    for child in d.iterdir():
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError as exc:
            print(f"  [주의] 지우지 못했습니다: {child.name} — {exc.strerror}")


def _is_stale(built: Path, source: Path) -> bool:
    """만들어진 문서가 원본보다 오래됐는가.

    한 번 당했다. 매뉴얼 PDF 가 열려 있어 갱신되지 않았고, 옆에 놓인 새 파일을
    치워 버리자 다음 빌드가 **낡은 PDF 를 그대로 꾸러미에 넣었다.** 아무도
    실패하지 않았고 아무 메시지도 없었다 — 22쪽짜리 옛날 설명서가 나갔을 뿐이다.
    조용히 낡은 문서를 배포하는 것이 빌드가 죽는 것보다 훨씬 나쁘다.
    """
    try:
        return built.stat().st_mtime < source.stat().st_mtime
    except OSError:
        return False


def _freshest(path: Path) -> Path:
    """열려 있어 덮어쓰지 못한 새 파일이 옆에 있으면 그것을 쓴다.

    윈도우는 보고 있는 PDF 를 덮어쓰지 못한다. 그때 make_manual 은
    '사용자매뉴얼(새로 만든 것).pdf' 를 옆에 놓는다. 그 사실을 모르고 꾸러미를
    구우면 **낡은 매뉴얼이 그대로 나간다** — 조용히 틀린 것을 배포하는 셈이다.
    """
    alt = path.with_name(f"{path.stem}(새로 만든 것){path.suffix}")
    if alt.exists() and (not path.exists()
                         or alt.stat().st_mtime > path.stat().st_mtime):
        print(f"  [주의] {path.name} 이 열려 있어 갱신되지 않았습니다 — "
              f"{alt.name} 을 대신 넣습니다.")
        return alt
    return path


def copy_source(src_root: Path, dst: Path) -> int:
    """소스를 추린다. OCR 을 쓰려면 이게 필요하다."""
    n = 0
    manual_source = src_root / 'docs' / 'manual' / 'v030.html'
    if manual_source.exists():
        manual_dest = dst / 'docs' / 'manual' / 'v030.html'
        manual_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manual_source, manual_dest)
        n += 1
    for item in ("engine", "tools", "rules", "app", "tests"):
        s = src_root / item
        if not s.exists():
            continue
        for f in s.rglob("*"):
            if not f.is_file():
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if any(fnmatch.fnmatch(f.name, p) for p in SKIP_FILES):
                continue
            rel = f.relative_to(src_root)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            n += 1
    for f in ("kfa.py", "setup_check.py", "requirements.txt",
              "KFA.spec", "OPERATIONS.md", "README.md"):
        s = src_root / f
        if s.exists():
            shutil.copy2(s, dst / f)
            n += 1
    return n


START_HERE = """\
키오스크 접근성 진단 (KFA {version})
================================================================

■ 세 걸음이면 시작합니다

  1) 이 폴더를 쓰기가 되는 곳에 두세요.  예: 바탕화면, C:\\KFA
     (C:\\Program Files 아래에 두면 결과물이 문서 폴더로 밀려납니다)

  2) KFA.exe 를 더블클릭합니다.  파이썬 설치 필요 없습니다.

  3) 브라우저 창이 열립니다.  거기서부터는 버튼만 누르면 됩니다.

■ 파란 경고창이 뜨면  (정상입니다)

  'Windows의 PC 보호' 라는 파란 창이 처음 한 번 뜹니다.
  바이러스가 아니라 '처음 보는 프로그램' 이라는 뜻입니다.

      가운데의  [추가 정보]  를 누르면
      아래에    [실행]      단추가 나타납니다.  그것을 누르세요.

  한 번만 하면 그다음부터는 묻지 않습니다.

■ 화면에는 세 가지만 있습니다

  ① 준비하기   점검하고, 인쇄물 두 장을 만들어 인쇄 창까지 열어 줍니다
  ② 촬영하기   QR 이 나옵니다. 폰 카메라로 비추면 촬영 앱이 열립니다
  ③ 진단하기   찍어 온 사진(ZIP 그대로)에서 리포트를 만듭니다

  처음 열었을 때 '바탕화면에 바로가기 만들기' 를 한 번 눌러 두면
  다음부터는 바탕화면 아이콘 하나로 열립니다.

■ 폴더 설명

  KFA.exe               더블클릭하는 것은 이것 하나뿐입니다
  사용자매뉴얼.pdf      설치 · 설정 · 현장 사용법
  사용자매뉴얼.html     같은 내용. 브라우저에서 보기 편합니다
  인쇄물/               ① 준비하기가 알아서 만들지만, 미리 인쇄해도 됩니다
  소스/                 문자 높이(3.g) 자동 판정에 필요 — 아래 설명

■ 마커 카드를 인쇄할 때

  ★ 배율 '실제 크기 100%'. '용지에 맞춤' 을 쓰면 모든 치수가 틀어집니다.
  ★ 인쇄 후 카드의 20mm 눈금에 자를 대어 확인하세요.
  ★ 무광 용지 200g 이상. 유광은 반사로 검출이 안 됩니다.

■ 폰이 촬영 앱을 여는 길은 둘입니다

  길 1  진단 PC 가 내준다   — 준비 없음. 같은 와이파이에 있어야 합니다
  길 2  웹서버에 올려 둔다   — 한 번 올려 두면 PC 도 와이파이도 필요 없습니다

  길 2 가 훨씬 편합니다. 화면의 '촬영 앱을 웹서버용으로 내보내기' 를 누르면
  올릴 폴더가 만들어집니다. 서버에서 도는 코드가 없어 정적 호스팅이면 됩니다.
  (반드시 https 여야 카메라 권한과 오프라인 저장이 동작합니다)

  어느 길이든 사진은 서버로 가지 않습니다. 폰 안에만 있다가 ZIP 으로 나옵니다.

■ 소스 폴더는 왜 있나

  실행 파일에는 OCR 엔진이 들어 있지 않습니다. 모델 파일이 함께 들어가지
  않아 실행 중에 깨지기 때문에 일부러 뺐습니다.

  문자 높이(3.g)까지 자동으로 판정하려면 파이썬을 설치하고 소스로 돌립니다.

      cd 소스
      pip install -r requirements.txt
      pip install rapidocr        (한글 OCR)
      python kfa.py calibrate-ocr (한 번만)
      python kfa.py               (같은 조작판이 열립니다)

  나머지 기능은 실행 파일에서 전부 동일하게 동작합니다.

■ 넣지 않은 것

  HTTPS 인증서를 넣지 않았습니다. CA 개인키가 들어 있어서, 배포본에 섞으면
  받은 모든 사람이 같은 키를 갖게 되기 때문입니다.

  폰에서 오프라인으로 쓰려면 설치한 자리에서 직접 만드세요:

      KFA.exe cert
      KFA.exe serve-app --https

  만드는 법과 폰에 설치하는 법은 매뉴얼 06절에 있습니다.
  촬영 앱을 웹서버에 올리면 이 과정 자체가 필요 없어집니다 (매뉴얼 07절).

■ 이 도구가 하지 않는 것

  이 리포트는 법정 적합성 인증이 아닙니다.
  별표5 2. 사용자 검증 기준(장애인·고령자 12명 대상)은 이 진단의 범위에
  포함되지 않으며, 촬영·계측으로 대체되지 않습니다.

  AI 는 '부적합'을 확정하지 않습니다. 자동 분석의 최대 강도는 '위반 의심'
  이고, 사람 검토자가 승인해야 '부적합'이 됩니다.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 꾸러미 만들기")
    ap.add_argument("--out", default=None, help="저장 폴더 (기본: 프로젝트 옆 _package)")
    ap.add_argument("--no-zip", action="store_true", help="폴더만 만들고 압축은 건너뛴다")
    a = ap.parse_args()

    root = resource_dir()
    out_root = Path(a.out) if a.out else root.parent / "_package"
    stage = out_root / PKG_NAME
    if stage.exists():
        raise FileExistsError(f"기존 배포 폴더를 보존합니다. --out으로 새 위치를 지정하세요: {stage}")
    stage.mkdir(parents=True, exist_ok=True)

    print("=" * 74)
    print(f" 배포 꾸러미 — {PKG_NAME}")
    print("=" * 74)

    missing: list[str] = []

    # ── 실행 파일 ────────────────────────────────────────────────────────
    exe = root / "dist" / "KFA.exe"
    if exe.exists():
        shutil.copy2(exe, stage / "KFA.exe")
        print(f"\n  KFA.exe                {exe.stat().st_size / 1024 / 1024:.0f}MB")
    else:
        missing.append("dist/KFA.exe — python -m PyInstaller KFA.spec 로 먼저 빌드하세요")

    # ── 매뉴얼 ──────────────────────────────────────────────────────────
    for src, dst in (("assets/사용자매뉴얼.html", "사용자매뉴얼.html"),
                     ("assets/사용자매뉴얼.pdf", "사용자매뉴얼.pdf")):
        s = _freshest(root / src)
        from tools.make_manual import SRC
        if s.exists() and _is_stale(s, root / SRC):
            missing.append(
                f"{src} 가 원본보다 낡았습니다 — KFA.exe manual --pdf 로 다시 만드세요.\n"
                f"           (열려 있으면 덮어쓰지 못합니다. 보던 창을 닫고 다시 실행하세요)")
        if s.exists():
            shutil.copy2(s, stage / dst)
            print(f"  {dst:<22}{s.stat().st_size / 1024:.0f}KB")
        else:
            missing.append(f"{src} — KFA.exe manual 로 먼저 만드세요")

    # ── 인쇄물 ──────────────────────────────────────────────────────────
    prints = stage / "인쇄물"
    prints.mkdir()
    for src, dst in (("assets/BFK-MARK-A_A4_sheet.png", "1_마커카드_A4.png"),
                     ("assets/field-sheet.html", "2_현장체크리스트.html")):
        s = root / src
        if s.exists():
            shutil.copy2(s, prints / dst)
            print(f"  인쇄물/{dst}")
        else:
            missing.append(f"{src}")

    # ── 소스 (OCR 용) ───────────────────────────────────────────────────
    n = copy_source(root, stage / "소스")
    print(f"  소스/                   {n}개 파일")

    # ── 올려 둔 촬영 앱 주소 ────────────────────────────────────────────
    # 이미 웹서버에 올려 뒀다면 그 주소를 함께 넣는다. 받는 사람이 조작판을
    # 열자마자 QR 이 나오고, 주소를 다시 물어볼 일이 없다. 비밀이 아니다.
    url_src = root / "assets" / "web-url.txt"
    if url_src.exists():
        (stage / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(url_src, stage / "assets" / "web-url.txt")
        print(f"  assets/web-url.txt      {url_src.read_text(encoding='utf-8').strip()}")

    # ── 시작 안내 ───────────────────────────────────────────────────────
    (stage / "★ 시작하세요.txt").write_text(
        f'KFA {VERSION} 설치·시작 안내\n\n'
        '1. ZIP을 새 폴더에 모두 압축 해제하세요. ZIP 안에서 실행하지 마세요.\n'
        '2. KFA.exe를 실행하면 PC 브라우저 조작판이 열립니다.\n'
        '3. 준비 시작 → 촬영 → 사진 고르기로 진단합니다.\n'
        '4. 검토 · 실측 입력에서 숫자·오차·조건을 입력하고 결과를 확인하세요.\n'
        '5. 검토 저장 후 보고서를 다시 만드세요. 기존 검토를 이어가려면 ZIP이 아닌 가져온 폴더를 선택하세요.\n\n'
        '자세한 설치·사용법: 사용자매뉴얼.pdf (10쪽) 또는 사용자매뉴얼.html\n'
        '기본 EXE 사용에는 Python과 API 키가 필요 없습니다. OCR은 선택 설치이며 음성·영상 자동 의미 분석은 미지원입니다.\n'
        '이전 설치의 assets와 검토 기록은 보존하세요. 이번 묶음에는 사용자 촬영 자료와 API 키·인증서 개인키를 넣지 않았습니다.\n'
        '이 도구와 보고서는 법정 적합성 인증이 아닙니다.\n', encoding="utf-8")
    print("  ★ 시작하세요.txt")
    review = root / "REVIEW-2026-09-09.md"
    if review.exists():
        shutil.copy2(review, stage / review.name)

    # ── 비밀 검사 — 나가기 전 마지막 관문 ────────────────────────────────
    print("\n" + "-" * 74)
    print(" 비밀 검사")
    print("-" * 74)
    leaks = []
    for f in stage.rglob("*"):
        if not f.is_file() or f.name == "KFA.exe":
            continue
        why = looks_secret(f)
        if why:
            leaks.append((f.relative_to(stage), why))
    if leaks:
        for rel, why in leaks:
            print(f"  [유출] {rel}  ← {why}")
        raise SecretLeak(
            f"비밀 {len(leaks)}건이 꾸러미에 섞였습니다. 나가면 되돌릴 수 없습니다.")
    print("  개인키·자격증명 없음")

    if missing:
        print("\n" + "-" * 74)
        for m in missing:
            print(f"  [빠짐] {m}")

    # ── 압축 ────────────────────────────────────────────────────────────
    zip_path = out_root / f"{PKG_NAME}.zip"
    if not a.no_zip:
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, Path(PKG_NAME) / f.relative_to(stage))
        print(f"\n  압축  {zip_path}")
        print(f"        {zip_path.stat().st_size / 1024 / 1024:.0f}MB")

    print("\n" + "=" * 74)
    print(f" 완료 — {stage}")
    print("=" * 74 + "\n")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
