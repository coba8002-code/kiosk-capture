"""리포트 내보내기 — 웹·PDF 외의 제출 형식.

지자체 제출본 문제를 어떻게 풀었는가 (2026-08-22 확정)
────────────────────────────────────────────────────────────────────
    "지자체가 HWP 를 요구하면?" 이 열려 있었다. 세 가지 길이 있었다.

    (가) 발주처에 물어보고 기다린다        → 착수가 막힌다
    (나) HWP/HWPX 생성기를 만든다          → 채택하지 않음. 아래 참조
    (다) 서식 채우기 + 표 붙여넣기로 푼다   → 채택

    (나)를 버린 이유. HWP 5.0 은 OLE 복합문서에 자체 레코드 구조를 얹은 형식이고,
    HWPX 는 ZIP+OWPML 이다. 둘 다 무에서 생성할 수는 있지만, 한글이 열어 주는지는
    실제 한글에서 열어봐야만 안다 — 파이썬 zlib 이 통과시킨 파일을 한글이 거부하는
    경우가 흔하다. 검증할 수 없는 생성기를 만들어 놓고 '해결'이라고 적는 것은
    이 프로젝트의 원칙(판정할 수 없는 것을 판정하지 않는다)에 정면으로 어긋난다.

    (다)가 현실에도 맞는다. 지자체는 거의 언제나 **자기 서식**을 준다.
    우리가 서식을 발명할 이유가 없다. 서식을 받으면 채워 넣으면 된다.

이 모듈이 내보내는 것
    to_field_map()  서식 채우기용 이름-값 맵. 받은 HWP 서식의 문단 키에 매핑해
                    hwp_replace.py 로 치환한다.
    to_csv()        별표5 40항목 매트릭스. 한글·엑셀 표에 그대로 붙여 넣는다.
    to_json()       전체 구조화 데이터. 다른 시스템 연동용.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .matrix import ReportBundle

# 서식 채우기용 필드 이름 — 발주처 서식의 문단 키에 이 이름을 매핑한다
FIELD_ORDER = [
    "기기_ID", "설치_장소", "제품_구분", "진단_일자", "검토자",
    "전체_항목수", "진단_대상수", "적합", "부적합", "위반의심", "미판정", "면제", "해당없음",
    "확정률", "미판정_전문실측", "미판정_점주", "미판정_재촬영", "미판정_검토자",
    "판정_범위_고지",
]


def to_field_map(
    bundle: ReportBundle,
    *,
    inspect_date: str,
    reviewer: str = "",
    location: str = "",
) -> dict[str, str]:
    """서식 채우기용 이름-값 맵.

    모든 값을 **문자열**로 낸다. HWP 치환은 글자 수를 맞춰야 하므로,
    호출부가 길이를 조정할 수 있게 포맷을 강제하지 않는다.
    """
    s = bundle.summary
    by_reason: dict[str, int] = {}
    for u in bundle.undetermined:
        by_reason[u["다음 담당"]] = by_reason.get(u["다음 담당"], 0) + 1

    return {
        "기기_ID": bundle.device_id,
        "설치_장소": location,
        "제품_구분": bundle.product_type,
        "진단_일자": inspect_date,
        "검토자": reviewer,
        "전체_항목수": str(s["별표5 전체 항목"]),
        "진단_대상수": str(s["이번 진단 대상"]),
        "적합": str(s["적합"]),
        "부적합": str(s["부적합"]),
        "위반의심": str(s["위반 의심"]),
        "미판정": str(s["미판정"]),
        "면제": str(s["면제"]),
        "해당없음": str(s["해당 없음"]),
        "확정률": f"{s['확정률']}%",
        "미판정_전문실측": str(by_reason.get("전문 실측", 0)),
        "미판정_점주": str(by_reason.get("점주", 0)),
        "미판정_재촬영": str(by_reason.get("현장 재촬영", 0)),
        "미판정_검토자": str(by_reason.get("검토자", 0)),
        "판정_범위_고지": bundle.scope_note,
    }


def to_hwp_edits(field_map: dict[str, str], key_binding: dict[str, str]) -> dict[str, str]:
    """필드 맵을 hwp_replace.py 가 먹는 형식으로 바꾼다.

    Args:
        field_map: to_field_map() 결과
        key_binding: {"기기_ID": "0:130", ...} — 발주처 서식에서
                     `python scripts/hwp_text.py 서식.hwp --list` 로 확인한 문단 키

    Returns:
        {"0:130": "GB-CAFE-001", ...}

    Raises:
        KeyError: 바인딩이 가리키는 필드가 field_map 에 없을 때.
    """
    out: dict[str, str] = {}
    for field, para_key in key_binding.items():
        if field not in field_map:
            raise KeyError(f"필드 맵에 없는 필드입니다: {field!r}")
        out[para_key] = field_map[field]
    return out


def to_csv(bundle: ReportBundle) -> str:
    """별표5 40항목 매트릭스 CSV.

    한글·엑셀 표에 그대로 붙여 넣는 용도. BOM 을 붙여 한글에서 인코딩이 깨지지 않게 한다.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["영역", "항목", "항목명", "구분", "판정", "등급", "사유", "다음 담당", "트랙", "출처"])
    for row in bundle.matrix:
        w.writerow([
            row["영역"], row["항목"], row["항목명"], row["구분"], row["판정"], row["등급"],
            row["사유"], row["다음 담당"], row["트랙"], row["출처"],
        ])
    return "﻿" + buf.getvalue()


def undetermined_csv(bundle: ReportBundle) -> str:
    """미판정 항목 표 CSV — 실측 업체·점주에게 그대로 넘기는 작업 목록."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["항목", "항목명", "영역", "사유", "다음 담당", "필요 입력", "마커 필요", "조치"])
    for u in bundle.undetermined:
        w.writerow([
            u["항목"], u["항목명"], u["영역"], u["사유"], u["다음 담당"],
            " ".join(u["필요 입력"]), "예" if u["마커 필요"] else "아니오",
            " ".join(u["안내"].split()),
        ])
    return "﻿" + buf.getvalue()


def to_json(bundle: ReportBundle, *, indent: int = 2) -> str:
    """전체 구조화 데이터. 다른 시스템 연동용."""
    payload: dict[str, Any] = {
        "device_id": bundle.device_id,
        "product_type": bundle.product_type,
        "summary": bundle.summary,
        "matrix": bundle.matrix,
        "undetermined": bundle.undetermined,
        "action_cards": [c.to_dict() for c in bundle.cards],
        "scope_note": bundle.scope_note,
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent, default=str)


# 발주처 서식이 도착했을 때의 절차 — README·BUILD_SPEC 에서 참조한다
HWP_FILL_PROCEDURE = """\
발주처 HWP 서식 채우기

  1) 서식의 문단 키를 확인한다
       python scripts/hwp_text.py 서식.hwp --list --min 1

  2) 필드 ↔ 문단 키 바인딩을 만든다 (서식마다 한 번)
       binding.json:  {"기기_ID": "0:130", "적합": "0:214", ...}

  3) 진단 결과로 치환 파일을 만든다
       edits = to_hwp_edits(to_field_map(bundle, ...), binding)

  4) 치환한다 — EXACT 모드가 기본이고 글자 수를 맞춰야 한다
       python scripts/hwp_replace.py 서식.hwp 제출본.hwp edits.json

  5) **한글에서 실제로 열어 확인한다.** 파이썬 검증은 믿지 않는다.

  표는 치환이 아니라 붙여넣기로 넣는다 — to_csv() / undetermined_csv() 결과를
  한글 표에 붙여 넣으면 된다.
"""
