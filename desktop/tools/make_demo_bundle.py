#!/usr/bin/env python
"""데모 수집 번들 생성 — ingest 를 실제 파일로 검증하기 위한 것.

    KFA.exe demo-bundle
    KFA.exe demo-bundle --out C:/temp/demo

현장 사진이 없어도 `ingest` 를 끝까지 돌려볼 수 있게, 수집 앱이 만드는 것과
**동일한 형식**의 번들을 합성해 만든다. 사진은 정답을 아는 합성 화면이고,
마커는 실물 카드 이미지 그대로다.

이 도구는 번들 형식의 실행 가능한 명세서이기도 하다 —
수집 앱은 여기서 나오는 manifest.json 과 같은 구조를 만들면 된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import output_dir                    # noqa: E402
from engine.pipeline import SCHEMA                     # noqa: E402
from tools import simulate_capture as sim              # noqa: E402

DEVICE_ID = "GB-CAFE-001"


def _write(path: Path, img) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, enc = cv2.imencode(path.suffix, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError(f"인코딩 실패: {path}")
    enc.tofile(str(path))


def _screen(text: str, fg: str, bg: str = "#FFFFFF", *, tilt: float = 0.12):
    """정답을 아는 화면 한 장 — 마커를 붙이고 비스듬히 찍은 것처럼 만든다."""
    panel, quads = sim.build_panel()

    # 문구 영역을 지정한 색으로 다시 그린다
    tx0, ty0 = sim.mm(20.0), sim.mm(105.0)
    cv2.rectangle(panel, (tx0, ty0), (sim.mm(200.0), sim.mm(135.0)), sim.hex_bgr(bg), -1)
    cv2.putText(panel, text, (tx0 + sim.mm(4), ty0 + sim.mm(20)),
                cv2.FONT_HERSHEY_SIMPLEX, sim.PPM / 8 * 1.1, sim.hex_bgr(fg),
                max(1, int(sim.PPM / 4)), cv2.LINE_AA)

    panel, _ = sim.attach_marker(panel, quads)
    photo, _ = sim.photograph(panel, tilt=tilt)
    return photo


def _video(path: Path, *, hz: float, fps: int = 60, seconds: float = 4.0) -> None:
    """깜박임이 있는 조작 영상 — 8.a 판정용."""
    w, h = 640, 360
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("영상 인코더를 열 수 없습니다 (mp4v)")
    n = int(fps * seconds)
    for i in range(n):
        phase = np.sin(2 * np.pi * hz * i / fps)
        v = int(128 + 90 * phase)
        frame = np.full((h, w, 3), v, np.uint8)
        cv2.putText(frame, "ORDER", (40, 200), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (255 - v, 255 - v, 255 - v), 4, cv2.LINE_AA)
        writer.write(frame)
    writer.release()


def build(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)

    shots: list[dict] = []

    # ── S1 화면 캡처 — 대비가 서로 다른 세 화면 ─────────────────────────
    screens = [
        ("S1-01_001.jpg", "Select payment method", "#9AA3AB", 0.12),   # 2.56:1 위반
        ("S1-01_002.jpg", "Choose your drink",     "#5A6470", 0.16),   # 6.5:1 통과
        ("S1-01_003.jpg", "Tap to start",          "#2B2B2B", 0.10),   # 14:1 통과
    ]
    for name, text, fg, tilt in screens:
        p = out / "S1" / name
        _write(p, _screen(text, fg, tilt=tilt))
        shots.append({"set": "S1", "shot": "S1-01", "file": f"S1/{name}",
                      "marker_plane": "display", "masked": True})

    # ── S2 조작 영상 — 2Hz(적합) 와 5Hz(부적합) ────────────────────────
    for name, hz in (("S2-01_001.mp4", 2.0), ("S2-01_002.mp4", 5.0)):
        _video(out / "S2" / name, hz=hz)
        shots.append({"set": "S2", "shot": "S2-01", "file": f"S2/{name}",
                      "marker_plane": None, "masked": True})

    # ── S3 기기 사진 (조작부 평면) ──────────────────────────────────────
    panel, quads = sim.build_panel()
    panel, _ = sim.attach_marker(panel, quads)
    photo, _ = sim.photograph(panel, tilt=0.20)
    _write(out / "S3" / "S3-04_001.jpg", photo)
    shots.append({"set": "S3", "shot": "S3-04", "file": "S3/S3-04_001.jpg",
                  "marker_plane": "control_panel", "masked": True})

    manifest = {
        "schema": SCHEMA,
        "device": {
            "id": DEVICE_ID,
            "location": "경북 구미시 · 무인카페 A",
            "product_type": "중대형",
            "scopes": ["물건 투입", "배출물", "개인정보 입력"],
            "exemptions": [],
        },
        "captured_at": "2026-08-22T10:30:00+09:00",
        "captured_by": "데모",
        "protocol_version": "0.1.0",
        "shots": shots,
        "owner_answers": {
            "1.f": "yes",     # 투입오류 안내 있음
            "3.b": "no",      # 이어폰 단자 없음 → 부적합
            "3.f": "yes",     # 키패드 촉각 표시 있음
        },
        "measurements": {
            "9.a": {"verdict": "fail", "by": "구미계측",
                    "결제 모듈 높이": 1285.0, "기준": "400~1,220mm", "초과": 65.0},
            "3.g": {"verdict": "pass", "by": "구미계측",
                    "최소 문자 높이": 8.1, "기준": 7.25},
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="데모 수집 번들 생성")
    ap.add_argument("--out", default=None, help="저장 폴더 (기본: assets/demo-bundle)")
    a = ap.parse_args()

    out = Path(a.out) if a.out else output_dir() / "demo-bundle"
    print("=" * 74)
    print(" 데모 수집 번들 생성")
    print("=" * 74)
    print(f"\n  수집 앱이 만드는 것과 같은 형식입니다.")
    print(f"  사진은 정답을 아는 합성 화면이고, 마커는 실물 카드 이미지입니다.\n")

    m = build(out)

    print(f"  {out}")
    print(f"    manifest.json")
    for s in m["shots"]:
        print(f"    {s['file']}")
    print(f"\n  촬영 {len(m['shots'])}건 · 점주 응답 {len(m['owner_answers'])}개 "
          f"· 실측 {len(m['measurements'])}개")
    print(f"\n  다음 명령으로 진단하세요:\n\n    KFA.exe ingest \"{out}\"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
