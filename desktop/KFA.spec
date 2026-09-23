# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 — 단일 실행 파일 KFA.exe

    pyinstaller KFA.spec --noconfirm

룰 DB 는 코드가 아니라 데이터이므로 datas 로 동봉한다.
동봉된 파일은 실행 시 임시 폴더에 풀리고, engine/paths.py 가 그 위치를 찾아준다.
"""

from pathlib import Path

# PyInstaller may only warn and omit a syntactically invalid delayed import.
# Reject such a release before dependency analysis.
source_root = Path(SPECPATH)
sources = list(source_root.glob('*.py'))
for folder in ('engine', 'tools'):
    sources.extend((source_root / folder).rglob('*.py'))
for source in sources:
    compile(source.read_bytes(), str(source), 'exec')

datas = [
    ("rules/annex5.yaml", "rules"),
    ("rules/capture-protocol.yaml", "rules"),
    ("rules/measurement-sop.yaml", "rules"),
    ("rules/usability-advisory.yaml", "rules"),
    # 수집 앱 — KFA.exe serve-app 이 폰에 내준다
    ("app/index.html", "app"),
    ("app/review.html", "app"),
    ("app/app.js", "app"),
    ("app/zip.js", "app"),
    ("app/style.css", "app"),
    ("app/sw.js", "app"),
    ("app/icon.svg", "app"),
    ("app/manifest.webmanifest", "app"),
    ("app/protocol.json", "app"),
    # 매뉴얼 원본과 그림 — 실행 파일에서도 KFA.exe manual 이 돌아야 한다
    ("docs/manual/v030.html", "docs/manual"),
]

hiddenimports = [
    "engine", "engine.rules", "engine.verdict", "engine.paths",
    "engine.calc", "engine.calc.contrast", "engine.calc.scale",
    "engine.calc.geometry", "engine.calc.text_height",
    "engine.calc.flash_rate", "engine.calc.sound",
    "engine.report", "engine.report.matrix", "engine.report.export",
    "engine.pipeline", "engine.detect", "engine.detect.text_regions",
    "engine.measurement", "engine.report.coverage",
    "engine.detect.controls",
    # L2 계층. anthropic SDK 자체는 동봉하지 않는다 — 쓰는 사람이 pip 로 넣는다.
    # 71MB 실행파일에 쓰지도 않을 SDK 를 넣을 이유가 없다.
    "engine.l2", "engine.l2.provider", "engine.l2.prompt",
    "engine.l2.judge", "engine.l2.stub", "engine.l2.anthropic_provider",
    # OCR 계층. 엔진(rapidocr/paddleocr/tesseract)은 동봉하지 않는다 —
    # PaddleOCR 만 해도 PyTorch 2GB 를 끌고 온다.
    "engine.ocr", "engine.ocr.provider", "engine.ocr.engines", "engine.ocr.stub",
    "tools.make_marker", "tools.make_field_sheet", "tools.simulate_capture",
    "tools.validate_rules", "tools.demo_report",
    "tools.calibrate_text_height", "tools.check_ks_copyright",
    "tools.ingest", "tools.make_demo_bundle", "tools.serve_app", "tools.build_app",
    "tools.review", "tools.gaps", "tools.calibrate_ocr", "tools.make_cert", "tools.make_manual",
    "tools.check_own_ui", "tools.easy", "tools.panel", "tools.publish_web",
    "tools.review_web",
    "setup_check",
]

# 쓰지 않는 무거운 것들은 뺀다 — 실행 파일 크기가 절반 이하로 줄어든다
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython",
    "notebook", "sphinx", "pytest", "setuptools", "pip",

    # OCR 엔진은 넣지 않는다 — **넣으면 오히려 깨진다.**
    #
    # PyInstaller 가 rapidocr 의 .py 만 끌어가고 모델·config.yaml 은 두고 오는데,
    # 그러면 available() 은 '사용 가능'이라 답하고 read() 가 실행 중에
    # FileExistsError 로 죽는다. 실제로 그렇게 진단이 통째로 멈췄고
    # 실행 파일은 26MB 만 무거워졌다.
    #
    # OCR 이 필요하면 소스로 실행한다(python kfa.py). engine/ocr/engines.py 의
    # FROZEN 검사가 실행 파일에서는 '없음'이라고 정직하게 답한다.
    "onnxruntime", "rapidocr", "rapidocr_onnxruntime", "paddleocr", "paddle",
    "pytesseract", "shapely", "pyclipper", "omegaconf",
]

a = Analysis(
    ["kfa.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KFA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
