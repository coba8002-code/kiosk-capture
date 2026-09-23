"""경로 해석 — 소스로 돌 때와 단일 실행 파일로 묶였을 때의 차이를 흡수한다.

PyInstaller 로 묶으면 두 가지가 달라진다.

  ① 읽기 — 룰 DB 같은 동봉 자원은 실행 시 임시 폴더(`sys._MEIPASS`)에 풀린다.
     소스 트리 기준으로 찾으면 못 찾는다.
  ② 쓰기 — 그 임시 폴더는 프로그램이 끝나면 사라진다.
     마커 카드나 체크리스트를 거기 쓰면 결과물이 증발한다.

그래서 **읽는 곳과 쓰는 곳을 분리**한다.
읽기는 동봉 자원 위치, 쓰기는 실행 파일이 놓인 폴더다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller 등으로 단일 실행 파일에 묶인 상태인가."""
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """**읽기** 기준 — 룰 DB·촬영 프로토콜 등 동봉 자원이 있는 곳."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """실행 파일(또는 프로젝트)이 놓인 곳. 사용자가 실제로 보는 폴더."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def output_dir() -> Path:
    """**쓰기** 기준 — 마커 카드·체크리스트·리포트가 저장되는 곳.

    임시 폴더에 쓰면 프로그램 종료와 함께 사라지므로 절대 쓰지 않는다.
    쓰기가 막힌 위치(Program Files 등)에 설치된 경우 문서 폴더로 물러난다.
    """
    d = app_dir() / "assets"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        fallback = Path(os.path.expanduser("~")) / "Documents" / "KFA" / "assets"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def rules_path(name: str) -> Path:
    """룰 파일 경로. 읽기 전용 자원이므로 resource_dir 기준."""
    return resource_dir() / "rules" / name
