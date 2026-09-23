# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 — 단일 실행 파일 KFA.exe

    pyinstaller KFA.spec --noconfirm

룰 DB 는 코드가 아니라 데이터이므로 datas 로 동봉한다.
동봉된 파일은 실행 시 임시 폴더에 풀리고, engine/paths.py 가 그 위치를 찾아준다.
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

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
    ("rules/ocr-box-calibration.json", "rules"),
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
    ("docs/manual/img/*.jpg", "docs/manual/img"),
]

# 한글 OCR을 실행파일에서도 즉시 쓸 수 있도록 모델·설정·ONNX DLL을 함께 묶는다.
# 단순 hidden import만 하면 모델이 빠져 실행 중에 깨진다.
datas += collect_data_files("rapidocr", includes=["*.yaml", "models/*"])
ocr_binaries = collect_dynamic_libs("onnxruntime")

hiddenimports = [
    "engine", "engine.rules", "engine.verdict", "engine.paths",
    "engine.calc", "engine.calc.contrast", "engine.calc.scale",
    "engine.calc.geometry", "engine.calc.text_height",
    "engine.calc.flash_rate", "engine.calc.sound",
    "engine.media", "engine.media.audio_quality", "engine.quality",
    "engine.report", "engine.report.matrix", "engine.report.export",
    "engine.pipeline", "engine.detect", "engine.detect.text_regions",
    "engine.measurement", "engine.report.coverage",
    "engine.detect.controls",
    # L2 계층과 Anthropic SDK. 0.4부터 조작판에서 세션용 API 키를 입력할 수 있어
    # 실행파일에도 SDK를 넣는다. 키는 어떤 배포 파일에도 넣지 않는다.
    "engine.l2", "engine.l2.provider", "engine.l2.prompt",
    "engine.l2.judge", "engine.l2.stub", "engine.l2.anthropic_provider",
    *collect_submodules("anthropic"),
    # OCR 계층. 엔진(rapidocr/paddleocr/tesseract)은 동봉하지 않는다 —
    # PaddleOCR 만 해도 PyTorch 2GB 를 끌고 온다.
    "engine.ocr", "engine.ocr.provider", "engine.ocr.engines", "engine.ocr.stub",
    *collect_submodules("rapidocr"), "onnxruntime",
    "tools.make_marker", "tools.make_field_sheet", "tools.simulate_capture",
    "tools.validate_rules", "tools.demo_report",
    "tools.calibrate_text_height", "tools.check_ks_copyright",
    "tools.ingest", "tools.make_demo_bundle", "tools.serve_app", "tools.build_app",
    "tools.review", "tools.gaps", "tools.field_qa", "tools.calibrate_ocr", "tools.make_cert", "tools.make_manual",
    "tools.check_own_ui", "tools.easy", "tools.panel", "tools.publish_web",
    "tools.review_web",
    "setup_check",
]

# 쓰지 않는 무거운 것들은 뺀다 — 실행 파일 크기가 절반 이하로 줄어든다
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython",
    "notebook", "sphinx", "pytest", "setuptools", "pip",

    # RapidOCR 3.x와 ONNX Runtime은 위에서 완전 동봉한다. 구형 엔진과
    # 수 GB급 PaddleOCR만 제외한다.
    "rapidocr_onnxruntime", "paddleocr", "paddle",
    "pytesseract",
]

a = Analysis(
    ["kfa.py"],
    pathex=["."],
    binaries=ocr_binaries,
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
