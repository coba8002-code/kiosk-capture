#!/usr/bin/env python
"""OCR 상자 계수 교정 — 3.g 판정에서 마지막으로 남은 미교정 상수.

    KFA.exe calibrate-ocr
    KFA.exe calibrate-ocr --engine rapidocr

왜 필요한가
────────────────────────────────────────────────────────────────────
    문자 높이 판정은 OCR 이 돌려주는 상자가 **무엇을 감싸느냐**에 통째로 달려 있다.
    어떤 엔진은 잉크 범위를, 어떤 엔진은 행 높이를 준다. 둘은 20~30% 다르다.

    교정하지 않으면 상자를 잉크로 착각해 문자 높이를 과대평가하고,
    **7mm 짜리 글자가 7.25mm 기준을 통과해 버린다** — 위반을 놓친다.
    그래서 교정 전에는 확신도에 상한이 걸려 3.g 가 자동으로 검토자에게 넘어간다.
    이 도구를 한 번 돌리면 그 상한이 풀린다.

어떻게 재는가
────────────────────────────────────────────────────────────────────
    정답을 아는 화면을 만든다. 실제 폰트로 렌더하고, Pillow 로 **잉크 바운딩 박스**를
    직접 재서 참값을 확보한다(문자 높이의 정의 = 잉크 높이, KS §5.2.3 비고 3).
    그 화면을 OCR 에 먹여 상자 높이를 받고, 두 값을 비교한다.

        계수 = 참 잉크 높이 ÷ (OCR 상자 높이 × 자종 계수)

    자종별로 여러 표본을 재고 **중앙값**을 채택한다. 평균은 오인식 한 건에 끌려간다.

    엔진이 문구를 부분만 읽으면 그 표본은 버린다.
    'Order now' 를 'now' 로 읽으면 상자는 'now' 것인데 참값은 전체 잉크 높이라
    계수가 2.4 로 튄다. 실제로 이 오염 때문에 산포가 0.34 까지 벌어졌다.
    인식 문자열이 렌더한 문자열과 맞아떨어질 때만 표본으로 쓴다.

    결과는 assets/ocr-box-calibration.json 에 엔진 이름별로 쌓인다.
    소스 상수를 고쳐 쓰지 않는 이유는 실행 파일 안의 .py 가 읽기 전용이기 때문이다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import ocr as OCR                                   # noqa: E402
from engine.calc import text_height as TH                       # noqa: E402
from engine.paths import output_dir                             # noqa: E402
from tools.calibrate_text_height import SAMPLES, find_font      # noqa: E402

RENDER_PX = 64          # 화면 문구다운 크기. 너무 크면 OCR 이 오히려 못 읽는다
MARGIN_PX = 40
MIN_SAMPLES = 4         # 이보다 적게 인식되면 교정을 신뢰할 수 없다
IOU_MATCH = 0.30        # OCR 상자와 렌더 위치를 짝지을 최소 겹침


def render_sample(text: str, font_path: Path, size_px: int = RENDER_PX):
    """문구 하나를 렌더하고 참 잉크 높이를 함께 돌려준다.

    Returns:
        (BGR 이미지, 잉크 높이 px, 잉크 상자 (x0, y0, x1, y1))
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), size_px)
    w = int(size_px * (len(text) + 2))
    h = size_px * 3
    img = Image.new("L", (w, h), 255)
    ImageDraw.Draw(img).text((MARGIN_PX, MARGIN_PX), text, font=font, fill=0)

    bbox = img.point(lambda v: 255 if v < 128 else 0).getbbox()
    if bbox is None:
        return None, 0.0, None

    gray = np.array(img, dtype=np.uint8)
    bgr = np.stack([gray, gray, gray], axis=-1)
    return bgr, float(bbox[3] - bbox[1]), bbox


def _same_text(got: str, want: str) -> bool:
    """엔진이 렌더한 문구를 통째로 읽었는가 (공백·대소문자 무시)."""
    return "".join((got or "").split()).lower() == "".join((want or "").split()).lower()


def _overlap(a, b) -> float:
    """두 상자의 겹침 / 작은 쪽 면적."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-9, min(aa, bb))


def calibrate(provider, font_path: Path, *, verbose: bool = False) -> dict:
    """엔진 하나의 상자 계수를 잰다."""
    per_script: dict[str, list[float]] = {}
    read_failures = 0

    for script, texts in SAMPLES.items():
        for text in texts:
            img, true_ink_px, ink_box = render_sample(text, font_path)
            if img is None:
                continue

            result = provider.read(img)
            if not result.lines:
                read_failures += 1
                continue

            # 렌더한 잉크 위치와 가장 많이 겹치는 상자를 고른다.
            # 여러 줄로 쪼개 읽는 엔진이 있어 '첫 줄'을 그냥 믿으면 안 된다.
            best, best_ov = None, 0.0
            for line in result.lines:
                xs = [p[0] for p in line.box_px]
                ys = [p[1] for p in line.box_px]
                ov = _overlap(ink_box, (min(xs), min(ys), max(xs), max(ys)))
                if ov > best_ov:
                    best, best_ov = line, ov
            if best is None or best_ov < IOU_MATCH:
                read_failures += 1
                continue

            box_h = best.height_px()
            if box_h <= 0:
                continue

            # 부분 인식은 버린다 — 상자는 일부인데 참값은 전체라 계수가 튄다
            if not _same_text(best.text, text):
                read_failures += 1
                continue

            # 엔진이 실제로 읽어낸 글자로 자종을 정한다.
            # 우리가 렌더한 자종을 쓰면, 오인식했을 때 계수가 조용히 틀어진다.
            got_script = TH.classify_script(best.text or text)
            cap = TH.CAP_RATIO.get(got_script, TH.CAP_RATIO["mixed"])
            factor = true_ink_px / (box_h * cap)
            per_script.setdefault(script, []).append(factor)

            if verbose:
                print(f"    {script:<12}{text[:18]:<20}"
                      f"참 {true_ink_px:5.1f}px  상자 {box_h:5.1f}px  "
                      f"인식 {best.text[:14]!r:<16} 계수 {factor:.3f}")

    hangul_read, hangul_total = provider.probe_korean(font_path=font_path)

    values = [v for vs in per_script.values() for v in vs]
    if len(values) < MIN_SAMPLES:
        return {"ok": False, "n": len(values), "read_failures": read_failures,
                "hangul_read": hangul_read, "hangul_total": hangul_total,
                "reason": f"인식된 표본이 {len(values)}개뿐입니다 (최소 {MIN_SAMPLES}개 필요)"}

    factor = statistics.median(values)
    spread = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "ok": True,
        "factor": round(factor, 4),
        "spread": round(spread, 4),
        "n": len(values),
        "read_failures": read_failures,
        # 국내 키오스크 화면은 대부분 한글이다. 못 읽는 엔진이면 3.g 의 '위반 없음'은
        # '위반이 없다'가 아니라 '읽지를 못했다'는 뜻이므로 반드시 기록한다.
        "hangul_read": hangul_read,
        "hangul_total": hangul_total,
        "hangul_ok": hangul_total > 0 and hangul_read >= hangul_total // 2,
        "font": font_path.name,
        "render_px": RENDER_PX,
        "box_convention": provider.box_convention,
        "per_script": {k: round(statistics.median(v), 4) for k, v in per_script.items()},
        "method": "참 잉크 높이 ÷ (OCR 상자 높이 × CAP_RATIO). 중앙값 채택",
    }


def save(engine: str, record: dict) -> Path:
    path = output_dir() / TH.OCR_CALIBRATION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("schema", "kfa.ocr-calibration/1")
    data.setdefault("engines", {})[engine] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR 상자 계수 교정")
    ap.add_argument("--engine", default=None,
                    help="교정할 엔진 (기본: 설치된 것을 자동 탐지)")
    ap.add_argument("--verbose", action="store_true", help="표본별 측정값 표시")
    a = ap.parse_args()

    print("=" * 74)
    print(" OCR 상자 계수 교정")
    print("=" * 74)

    try:
        provider = OCR.resolve(a.engine)
    except ValueError as exc:
        print(f"\n  [실패] {exc}")
        return 2

    ok, why = provider.available()
    print(f"\n  엔진      {provider.name}")
    print(f"  상태      {'사용 가능' if ok else '사용 불가'} — {why}")
    print(f"  상자 규약  {provider.box_convention}")
    if not ok:
        print("\n  교정할 엔진이 없습니다. 먼저 설치하세요:")
        print("    pip install rapidocr-onnxruntime")
        return 3

    font = find_font()
    if font is None:
        print("\n  [실패] 한글 폰트를 찾지 못했습니다. 교정 표본을 만들 수 없습니다.")
        return 4
    print(f"  교정 폰트  {font.name}")

    print("\n" + "-" * 74)
    print(" 측정")
    print("-" * 74)
    rec = calibrate(provider, font, verbose=a.verbose)

    if not rec["ok"]:
        print(f"\n  [실패] {rec['reason']}")
        print("         엔진이 렌더 표본을 거의 읽지 못했습니다.")
        print("         다른 엔진을 쓰거나, 엔진 설치 상태를 확인하세요.")
        return 5

    print(f"\n  표본 {rec['n']}개 · 버린 표본 {rec['read_failures']}개")
    hr, ht = rec.get("hangul_read", 0), rec.get("hangul_total", 0)
    mark = "OK" if rec.get("hangul_ok") else "← 이 엔진은 한글을 읽지 못합니다"
    print(f"  한글 인식  {hr}/{ht} {mark}")
    for script, v in sorted(rec["per_script"].items()):
        print(f"    {script:<14}{v:.4f}")
    print(f"\n  채택 계수  {rec['factor']:.4f}  (산포 {rec['spread']:.4f})")

    if rec["factor"] > 1.0:
        print("\n  이 엔진은 상자를 잉크보다 **작게** 줍니다 — 교정 없이 쓰면 문자 높이를")
        print("  과소평가해 없는 위반을 만들어 냅니다.")
    elif rec["factor"] < 1.0:
        print("\n  이 엔진은 상자를 잉크보다 **크게** 줍니다 — 교정 없이 쓰면 문자 높이를")
        print("  과대평가해 **실제 위반을 놓칩니다.** 교정이 특히 중요합니다.")

    if not rec.get("hangul_ok"):
        print()
        print("  [경고] 이 엔진은 한글을 읽지 못합니다.")
        print("         국내 키오스크 화면은 대부분 한글이므로, 이 상태에서 3.g 는")
        print("         '위반 없음'이 아니라 '읽지 못함'입니다. 엔진이 그렇게 보고하도록")
        print("         이 사실을 기록해 두었고, 3.g 는 실측으로 넘어갑니다.")
        print("         한글이 되는 엔진을 붙이려면 OPERATIONS.md §5 우선순위 2 를 보세요.")

    if rec["spread"] > 0.15:
        print(f"\n  [주의] 산포가 큽니다({rec['spread']:.3f}). 계수 하나로 이 엔진을 대표하기")
        print("         어렵다는 뜻입니다. 현장 표본으로 다시 교정하는 편이 좋습니다.")

    # 이름이 아니라 교정 키로 저장한다 — 같은 엔진이라도 모델·언어가 바뀌면
    # 상자 규약이 달라지므로, 예전 계수를 물려받지 않게 키를 나눈다.
    key = getattr(provider, "calibration_key", provider.name)
    path = save(key, rec)
    print(f"\n  저장  {path}")
    print(f"  교정 키  {key}")
    print("\n  이제 KFA.exe ingest 가 3.g 를 교정된 계수로 판정합니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
