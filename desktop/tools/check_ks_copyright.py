#!/usr/bin/env python
"""KS X 9211 저작권 가드 — CI 게이트.

    python tools/check_ks_copyright.py
    python tools/check_ks_copyright.py --report report.md   # 생성된 리포트도 검사

왜 필요한가
────────────────────────────────────────────────────────────────────
    KS X 9211:2025 원문에는 "이 문서는 저작권 규정에 따라 상업적 활용을 금지합니다"
    표시가 있다. 반면 고시 [별표 5]는 행정규칙이므로 공공저작물이고 원문 인용이 가능하다.

    이 사업의 설계는 그 경계를 이용한다 — **법적 판정에 필요한 문언은 전부 별표5에 있다.**
    KS 는 기술적 배경일 뿐이고, 우리가 필요로 하는 것은 조항 '번호'이지 '문장'이 아니다.
    조항 번호 참조는 인용이 아니라 출처 표시이므로 저작권 문제가 생기지 않는다.

    문제는 이 원칙이 시간이 지나면 조용히 무너진다는 것이다. 누군가 편의상
    KS 문장을 notes 에 붙여넣고, 그게 리포트 템플릿으로 새어 나간다.
    그래서 사람의 주의가 아니라 CI 가 막는다.

무엇을 검사하는가
    1. ks_ref 필드가 조항 번호 형식만 담고 있는지 (문장이 섞여 있지 않은지)
    2. 저장소 전체에서 KS 본문 특유의 문구가 발견되는지
    3. 생성된 리포트에 KS 문구나 'KS X 9211' 원문 표기가 새어 나갔는지

발견 시 대응
    ks_ref 에는 번호만 남기고, 설명이 필요하면 자체 문장으로 다시 쓴다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir  # noqa: E402

ROOT = resource_dir()

# ks_ref 로 허용되는 형식 — 표준명 + 조항 번호(+ 짧은 괄호 주석)
KS_REF_OK = re.compile(
    r"^(KS X 9211:2025\s*§[\d.]+(\s*[a-z]\))?"
    r"|트리아지 원본\s*[\d,·~\s]+행"
    r"|EN 301 549[^,]*§[\d.]+)"
    r"(\s*\([^)]{0,40}\))?$"
)

# ks_ref 에 문장이 섞였는지 — 종결어미가 보이면 본문을 옮겨 적은 것이다
SENTENCE_MARKERS = re.compile(r"(해야 한다|하여야 한다|되어야 한다|이어야 한다|한다\.|있다\.|없다\.)")

# KS 본문 특유의 문구 — 이 표현들이 저장소에 있으면 원문을 옮겨 적었을 가능성이 높다
KS_VERBATIM_MARKERS = [
    "손가락으로 누르는 부위 표면적은",
    "이웃한 버튼 및 키 사이에는",
    "무인정보단말기는 사용자들이 모든 기능을 조작할 수 있도록",
    "사용자가 보조 장치를 연결하지 않고도",
    "무인정보단말기가 제공하는 모든 기능은 키보드만을 사용하여",
    "모든 의미 있는 청각 정보는 시각을 이용하는 동등한 대체 콘텐츠와",
    "스피커와 이어폰의 음량을 조절할 수 있는 수단을 제공해야 한다",
    "세션이 종료될 때, 음소거는 해제되어야 하고",
    "모든 사용자 컨트롤에는 용도와 목적을 알 수 있는 시각과 비시각의 레이블을",
    "무인정보단말기에 설치되는 터치스크린은 의수나 스타일러스",
    "화면에 표시되는 민감한 개인정보는 시각적인 노출을 차단해야 한다",
    "민감한 내용의 음성정보는 이어폰 등의 개인청취장치",
]

# 검사 대상 — 소스와 룰. 이 스크립트 자신과 문서의 '금지 사례' 설명은 제외한다
SCAN_GLOBS = ["rules/*.yaml", "engine/**/*.py", "tools/*.py"]
SELF = Path(__file__).name


def scan_ks_refs() -> list[str]:
    """ks_ref 필드가 번호만 담고 있는지."""
    import yaml
    problems: list[str] = []
    path = ROOT / "rules" / "annex5.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    for cat in raw.get("categories", []):
        for item in cat.get("items", []):
            for ref in item.get("ks_ref", []) or []:
                if SENTENCE_MARKERS.search(ref):
                    problems.append(
                        f"{item['id']}: ks_ref 에 문장이 섞였습니다 → {ref!r}\n"
                        f"        조항 번호만 남기고 설명은 notes 에 자체 문장으로 쓰세요."
                    )
                elif not KS_REF_OK.match(ref.strip()):
                    problems.append(
                        f"{item['id']}: ks_ref 형식이 아닙니다 → {ref!r}\n"
                        f"        허용: 'KS X 9211:2025 §5.6.2' / '트리아지 원본 38행' / 'EN 301 549 ... §5.1.3.12'"
                    )
    return problems


def scan_verbatim(extra_files: list[Path] | None = None) -> list[str]:
    """KS 본문 문구가 저장소나 리포트에 새어 나갔는지."""
    problems: list[str] = []
    targets: list[Path] = []
    for g in SCAN_GLOBS:
        targets.extend(ROOT.glob(g))
    targets.extend(extra_files or [])

    for f in targets:
        if not f.is_file() or f.name == SELF:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in KS_VERBATIM_MARKERS:
            if marker in text:
                line = next(
                    (i + 1 for i, ln in enumerate(text.splitlines()) if marker in ln), 0
                )
                problems.append(
                    f"{_display(f)}:{line} KS 본문 문구로 보입니다 → {marker!r}\n"
                    f"        자체 문장으로 다시 쓰고 조항 번호만 남기세요."
                )
    return problems


def _display(path: Path) -> str:
    """저장소 안이면 상대 경로로, 밖이면(예: --report 로 넘긴 리포트) 그대로."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="KS X 9211 저작권 가드")
    ap.add_argument("--report", nargs="*", default=[], help="생성된 리포트 파일도 검사")
    a = ap.parse_args()

    print("=" * 72)
    print(" KS X 9211 저작권 가드")
    print("=" * 72)
    print("\n  원칙   별표5(행정규칙·공공저작물) → 원문 인용 가능")
    print("         KS X 9211(상업적 활용 금지 표시) → 조항 번호만 참조, 본문 인용 금지\n")

    problems = scan_ks_refs()
    extra = [Path(p) for p in a.report]
    problems += scan_verbatim(extra)

    scanned = sum(len(list(ROOT.glob(g))) for g in SCAN_GLOBS) + len(extra)
    print(f"  검사 대상  {scanned}개 파일")

    if problems:
        print(f"\n✗ 문제 {len(problems)}건\n")
        for p in problems:
            print(f"   - {p}")
        print("\n  KS 본문을 옮겨 적지 마세요. 필요한 것은 조항 번호이지 문장이 아닙니다.")
        return 1

    print("\n✓ KS 본문 유출 없음 — 조항 번호 참조만 확인됨")
    print("\n  참고: 표준 이용 허락 절차(한국표준협회 e-standard)는 별도 진행 항목입니다.")
    print("        본 가드는 '허락 없이도 안전한 설계'가 유지되는지만 검사합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
