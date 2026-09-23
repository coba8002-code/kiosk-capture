#!/usr/bin/env python
"""수집 앱 데이터 빌드 — 룰 DB·촬영 프로토콜을 앱이 읽을 JSON 으로 내보낸다.

    KFA.exe build-app
    python tools/build_app.py --out app

앱이 촬영 목록과 점주 문항을 **손으로 적어 두면** 프로토콜이 바뀔 때마다
앱과 엔진이 갈라진다. 그래서 앱은 자기 데이터를 갖지 않고 이 파일을 읽는다.
단일 진실 원천은 언제나 rules/ 다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import rules as R                    # noqa: E402
from engine.paths import resource_dir            # noqa: E402
from engine.pipeline import SCHEMA               # noqa: E402


def build(rs: R.RuleSet, protocol: dict) -> dict:
    """앱이 쓰는 형태로 추린다. 앱에 필요 없는 필드는 넣지 않는다."""
    sets = []
    for s in protocol["sets"]:
        fed = rs.fed_by(s["id"])
        shots = []
        for sh in s.get("shots", []):
            marker_required = bool(sh.get("marker_required", False))
            marker_plane = sh.get("marker_plane") if marker_required else None
            shots.append({
                "id": sh["id"],
                "label": sh["label"],
                "note": " ".join((sh.get("note") or "").split()) or None,
                "required_for": sh.get("required_for") or [],
                "repeat": sh.get("repeat") or None,
                "input_mode": sh.get("input_mode") or "camera",
                "multiple": bool(sh.get("multiple", False)),
                "marker_required": marker_required,
                "marker_plane": marker_plane,
                "marker_placement": (
                    " ".join((sh.get("marker_placement") or "").split()) or None
                ),
                "metadata": sh.get("metadata") or [],
            })
        marker_planes = list(dict.fromkeys(
            sh["marker_plane"] for sh in shots if sh.get("marker_plane")
        ))
        sets.append({
            "id": s["id"],
            "name": s["name"],
            "medium": s.get("medium", "photo"),
            "required": bool(s.get("required")),
            "marker": bool(marker_planes),
            "marker_planes": marker_planes,
            "constraints": s.get("constraints") or {},
            "feeds": [r["id"] for r in fed],
            "note": " ".join((s.get("note") or "").split()) or None,
            "shots": shots,
        })

    marker = protocol["marker"]
    owner = [
        {
            "id": r["id"],
            "name": next(
                (c["name"] for c in rs.categories
                 if any(i["id"] == r["id"] for i in c["items"])), ""),
            "question": " ".join((r.get("owner_question") or "").split()),
            "options": (
                ["예", "아니오", "해당 없음"]
                if "해당 없음" in (r.get("owner_question") or "") else ["예", "아니오"]
            ),
        }
        for r in rs.by_track("B_OWNER")
    ]

    measure = [
        {
            "id": r["id"],
            "criterion": " ".join((r.get("criterion") or "").split()),
            "instrument": r.get("instrument", ""),
        }
        for r in rs.by_track("C_MEASURE")
    ]

    return {
        "schema": SCHEMA,
        "protocol_version": protocol["meta"]["version"],
        "rules_version": rs.meta["version"],
        "product_types": list(rs.enums["product_type"].keys()),
        "scopes": [k for k in rs.enums["scope"] if k != "기본"],
        "total_items": len(rs),
        # 항목별 최소 정보. 앱이 '이번 진단 대상'과 트랙별 개수를 **직접 계산**하도록
        # 넘긴다. 예전에는 앱이 base=26, AI=29 같은 숫자를 손으로 들고 있었고,
        # 진단 대상 26 아래에 29·4·7 이 찍혀 합이 40 이 되는 화면이 나왔다.
        # 룰 DB 가 바뀌면 앱도 따라 바뀌어야 한다 — 그래서 데이터로 내려보낸다.
        "items": [
            {"id": r["id"], "scope": r["scope"], "applies_to": r["applies_to"],
             "track": r["track"]}
            for r in rs
        ],
        "sets": sets,
        "marker": {
            "id": marker["id"],
            "size_mm": marker["size_mm"],
            "inner_square_mm": marker["inner_square_mm"],
            "finish": marker["print"]["finish"],
            "placements": marker["usage"]["placements"],
            "reject_if": marker["accuracy"]["reject_if"],
        },
        "exemptions": [
            {
                "id": ex["id"],
                "name": ex["source"],
                "exempts": ex["exempts"],
                "evidence_required": ex["evidence_required"],
            }
            for ex in rs.exemptions
        ],
        "owner_questions": owner,
        "measure_items": measure,
        "privacy": {
            "targets": [t["note"] for t in protocol["privacy"]["on_device_masking"]["targets"]],
            "timing": protocol["privacy"]["on_device_masking"]["timing"],
            "consent": protocol["privacy"]["consent"]["note"],
        },
        "submission_gate": protocol["submission_gate"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="수집 앱 데이터 빌드")
    ap.add_argument("--out", default=None, help="앱 폴더 (기본: app/)")
    a = ap.parse_args()

    rs = R.load()
    protocol = R.load_protocol()
    data = build(rs, protocol)

    outdir = Path(a.out) if a.out else resource_dir() / "app"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "protocol.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    shots = sum(len(s["shots"]) for s in data["sets"])
    print("=" * 70)
    print(" 수집 앱 데이터 빌드")
    print("=" * 70)
    print(f"\n  {path}")
    print(f"\n  촬영 세트 {len(data['sets'])}개 · 컷 {shots}개")
    print(f"  점주 문항 {len(data['owner_questions'])}개")
    print(f"  실측 항목 {len(data['measure_items'])}개")
    print(f"  프로토콜 v{data['protocol_version']} · 룰 DB v{data['rules_version']}")
    print("\n  앱은 이 파일을 읽습니다. 프로토콜이 바뀌면 이 명령을 다시 실행하세요.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
