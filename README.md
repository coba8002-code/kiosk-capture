# 배리어프리 키오스크 현장진단 — KFA 0.3.0

- [촬영 앱 열기](https://coba8002-code.github.io/kiosk-capture/)
- [사용자 매뉴얼](manual/사용자매뉴얼.html) · [PDF 다운로드](manual/사용자매뉴얼.pdf)
- [PC 진단 프로그램 소스 및 실행 방법](desktop/README.md)
- [빌드 방법](desktop/BUILD.md) · [검토 및 제한사항](desktop/REVIEW-2026-09-09.md)

## 사용 순서

1. 스마트폰에서 촬영 앱을 열어 대상을 등록하고 증거를 수집합니다.
2. 사진에 개인정보가 있으면 기기에서 가립니다. 영상·음성은 자동 마스킹되지 않습니다.
3. 제출 단계에서 ZIP을 준비한 뒤 공유 또는 내려받기 버튼으로 저장합니다.
4. ZIP을 Windows 진단 PC로 옮겨 분석하고, 검토자가 실측값과 판단을 확인합니다.

GitHub Pages는 촬영 화면만 제공합니다. 사진·진단 결과를 GitHub에 업로드하지 않으며,
Python 진단 엔진은 PC에서 실행합니다. 선택형 외부 AI는 사용자가 API 키와 전송 동의를 설정해야 합니다.

## 소스로 실행

Python 3.10 이상이 설치된 Windows PC에서:

```powershell
cd desktop
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe kfa.py
```

OCR와 외부 AI 패키지는 선택 사항입니다. 자세한 설정과 한계는 사용자 매뉴얼을 확인하세요.

## 저장소 구성

- 루트 정적 파일: 실제 GitHub Pages 촬영 앱
- `desktop/`: 진단 엔진, 검토 화면, 규칙, 테스트, 빌드 도구, 설계 문서
- `manual/`: 현재 배포용 HTML/PDF 설명서

촬영 원본, 진단 결과, API 키, 인증서 개인키, 임시·빌드 산출물은 포함하지 않습니다.
실행 파일은 Git 소스에 포함하지 않습니다. 빌드는 `desktop/BUILD.md`를 참고하세요.

## 판정 범위

AI는 `위반 의심`까지만 제시하며, 검토자의 확인 없이 `부적합`을 확정하지 않습니다.
이 리포트는 법정 적합성 인증이 아닙니다.
KS X 9211:2025는 조항 번호만 참조하며 본문을 재배포하지 않습니다.
단위 테스트와 합성 영상 시험은 실제 현장 정확도 또는 장애 당사자의 사용성 검증을 대신하지 않습니다.

## 2026-09-23 반영

- `serve_app` 구문 오류 수정 및 PyInstaller 빌드 전 전체 Python 구문 검사
- 실제 EXE 서버 구동과 정적 파일 8종 HTTP 응답 검사 도구 추가
- 최신 측정·검토 기능, 개인정보 보호 처리, iPhone ZIP 저장 흐름, 오프라인 캐시 업데이트
- PC 소스·설계·테스트·매뉴얼 통합
