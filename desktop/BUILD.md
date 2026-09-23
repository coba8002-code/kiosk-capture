# 실행 파일 만들기

```bash
pip install pyinstaller
python -m PyInstaller KFA.spec --noconfirm --clean
```

결과: `dist/KFA.exe` (약 71MB, 파이썬 설치 불필요)

배포용으로 묶으려면 `dist/KFA-0.2.0-win64/` 에 exe 와 `사용법.txt` 를 넣고 zip 한다.

## 빌드 시 주의 — 실제로 겪은 것들

**① 콘솔 인코딩**
개발 중에는 `PYTHONUTF8=1` 을 켜고 돌리므로 문제가 보이지 않는다. 그러나 사용자가
exe 를 더블클릭하면 콘솔이 CP949 로 열리고, 한글이 깨질 뿐 아니라 `✓`(U+2713)에서
`UnicodeEncodeError` 로 **프로그램이 죽는다**. `kfa.py` 의 `_setup_console()` 이
콘솔 코드페이지와 스트림 인코딩을 UTF-8 로 맞추고 `errors="replace"` 로 방어한다.
이 함수는 다른 무엇보다 **먼저** 실행돼야 한다.

**② 읽는 곳과 쓰는 곳의 분리**
PyInstaller 는 동봉 자원을 실행 시 임시 폴더(`sys._MEIPASS`)에 푼다. 그 폴더는
프로그램이 끝나면 사라지므로, 마커 카드나 체크리스트를 거기 쓰면 결과물이 증발한다.
`engine/paths.py` 가 읽기(`resource_dir`)와 쓰기(`output_dir`)를 나눠서 해결한다.
쓰기가 막힌 위치(Program Files 등)에 설치된 경우 문서 폴더로 물러난다.

**③ 실행 순서 의존성**
`verify` 가 마커 카드 **파일**을 요구하면 "marker 를 먼저 실행해야 verify 가 된다"는
숨은 순서가 생긴다. 배포본 첫 실행에서 바로 터진다. 지금은 파일이 없으면
`make_marker.build()` 로 그 자리에서 만든다.

**④ 크기 줄이기**
`KFA.spec` 의 `excludes` 로 tkinter·matplotlib·scipy·pandas 등을 뺐다.
빼지 않으면 실행 파일이 두 배 이상 커진다.

## 검증

빌드 후 **반드시 깨끗한 폴더에서** 확인한다. 프로젝트 폴더에서 돌리면
소스 트리의 파일을 우연히 집어서 통과해 버린다.

```bash
mkdir /tmp/kfa-test && cp dist/KFA.exe /tmp/kfa-test/ && cd /tmp/kfa-test
./KFA.exe rules && ./KFA.exe marker && ./KFA.exe sheet && ./KFA.exe verify && ./KFA.exe report
```

환경변수(`PYTHONUTF8` 등)를 지운 상태로 돌려야 실제 사용자 환경과 같아진다.
