#!/usr/bin/env python
"""촬영 번들의 완전성·분석 가능성을 점검하고 HTML/JSON으로 저장한다."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import pipeline, quality, rules  # noqa: E402
from engine.paths import output_dir          # noqa: E402


def run(bundle_path: str | Path, out: str | Path | None = None):
    bundle = pipeline.load_bundle(bundle_path)
    report = quality.audit(bundle, rules.load_protocol())
    if out is None:
        safe = re.sub(r"[^\w.-]", "_", bundle.device_id)[:60] or "capture"
        out = Path(tempfile.mkdtemp(prefix=f"quality_{safe}_", dir=output_dir()))
    return report, quality.write(report, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="현장자료 품질 점검")
    ap.add_argument("bundle", help="manifest.json이 있는 촬영 번들 폴더")
    ap.add_argument("--out", help="저장 폴더")
    a = ap.parse_args()
    try:
        report, saved = run(a.bundle, a.out)
    except pipeline.BundleError as exc:
        print(f"[실패] {exc}")
        return 2
    print("=" * 72)
    print(f" 현장자료 품질 — {report.status}")
    print("=" * 72)
    print(f" 필수 세트  {report.covered_sets}/{report.required_sets}")
    print(f" 측정 마커  {report.marker_found}/{report.marker_expected}")
    print(f" 음성 품질  {report.audio_analyzed}/{report.audio_total}")
    print(f" 필수 기록  {report.metadata_filled}/{report.metadata_required}")
    blocks = sum(x.level == "block" for x in report.issues)
    warns = sum(x.level == "warn" for x in report.issues)
    print(f" 보완 {blocks}건 · 주의 {warns}건")
    for p in saved:
        print(f" 저장  {p}")
    print("\n 이 품질검사는 법정 적합성 인증이 아닙니다.")
    return 1 if blocks else 0


if __name__ == "__main__":
    raise SystemExit(main())
