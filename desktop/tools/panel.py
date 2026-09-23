#!/usr/bin/env python
"""조작판 — 검은 창 대신 브라우저 화면으로 쓴다.

    KFA.exe            (그냥 더블클릭하면 이게 열린다)
    KFA.exe panel
    KFA.exe menu       예전 터미널 메뉴

왜 브라우저인가
────────────────────────────────────────────────────────────────────
    현장에서 이 도구를 쓰는 사람은 개발자가 아니다. 검은 창에 번호를 치고
    한글 섞인 폴더 경로를 손으로 입력하라는 것은, 접근성을 재는 도구가
    스스로 접근성을 포기한 것과 같다.

    창 도구(tkinter)를 쓰지 않고 브라우저를 고른 이유는 하나 더 있다.
    우리는 이미 4.5:1 대비와 48px 터치 타깃을 지키는 CSS 를 갖고 있고,
    그 CSS 는 `KFA.exe self-check` 가 매번 검사한다. 조작판이 같은 토큰을
    쓰면 **조작판도 같은 검사를 받는다.** 우리 화면이 우리 기준을 통과한다.

무엇을 조심했나
    · 127.0.0.1 에만 연다. 촬영 앱(8099)은 폰이 붙어야 해서 LAN 에 열리지만,
      이 화면은 명령을 실행한다. LAN 에 열면 같은 와이파이의 누구나 실행할 수 있다.
    · 토큰을 요구한다. 브라우저에 떠 있는 아무 웹페이지나 localhost 로
      POST 를 던질 수 있기 때문이다(CSRF). 주소에 붙은 토큰이 없으면 거절한다.
    · 실행할 수 있는 명령을 목록으로 못 박았다. 화면에서 임의 명령이 만들어지지 않는다.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.paths import app_dir, output_dir, resource_dir   # noqa: E402
from tools import easy                                       # noqa: E402

VERSION = "0.3.1"
DEFAULT_PORT = 8097          # 촬영 앱은 8099. 겹치지 않게 둔다.
DEFAULT_WEB_URL = "https://coba8002-code.github.io/kiosk-capture/"

# 화면에서 부를 수 있는 것 — 여기 없는 것은 실행되지 않는다.
JOBS: dict[str, dict] = {
    "prepare": {
        "title": "준비하기",
        "steps": [["check"], ["marker"], ["sheet"]],
        "note": "점검 → 마커 카드 → 현장 체크리스트",
    },
    "check":   {"title": "설치·동작 점검", "steps": [["check"]]},
    "marker":  {"title": "마커 카드 만들기", "steps": [["marker"]]},
    "sheet":   {"title": "현장 체크리스트 만들기", "steps": [["sheet"]]},
    "serve":   {"title": "촬영 앱 켜기", "steps": [["serve-app"]], "long": True},
    "ingest":  {"title": "진단", "steps": None},      # 폴더를 붙여서 만든다
    "demo":    {"title": "데모 번들 만들기", "steps": [["demo-bundle"]]},
    "web":     {"title": "웹서버용 내보내기", "steps": [["web"]]},
    "selfcheck": {"title": "자기 검사", "steps": [["self-check"]]},
    "manual":  {"title": "설명서 다시 만들기", "steps": [["manual"]]},
    "gaps":    {"title": "구멍 점검", "steps": [["gaps"]]},
    "rules":   {"title": "룰 DB 검증", "steps": [["rules"]]},
}


# ── 자식 프로세스 ────────────────────────────────────────────────────────
def self_cmd(args: list[str]) -> list[str]:
    """자기 자신을 다시 부른다. 실행 파일이면 sys.executable 이 곧 KFA.exe 다."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + args
    return [sys.executable, str(app_dir() / "kfa.py")] + args


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # **이게 없으면 화면이 멈춘 것처럼 보인다.**
    # 자식의 stdout 이 파이프면 파이썬은 블록 버퍼링을 쓴다. 그래서 점검(60초)
    # 내내 로그가 한 줄도 안 나오다가 끝나는 순간 한꺼번에 쏟아진다.
    # 사용자는 그 60초 동안 얼어붙은 화면을 본다.
    env["PYTHONUNBUFFERED"] = "1"
    env["KFA_NO_PANEL"] = "1"          # 자식이 다시 조작판을 열지 않도록
    env["KFA_UI"] = "panel"            # 자식이 Ctrl+C 대신 화면 버튼을 안내하도록
    return env


CREATE_NO_WINDOW = 0x08000000
# 이스케이프 문자는 눈에 보이지 않는다. 소스에 날것으로 두면 편집기가 지워도 모른다.
ANSI = re.compile(chr(27) + r"\[[0-9;]*[A-Za-z]")


class Job:
    """실행 중인 작업 하나. 줄 단위로 쌓아 두고 화면이 가져간다."""

    def __init__(self, key: str, steps: list[list[str]], title: str):
        self.key = key
        self.title = title
        self.steps = steps
        self.lines: list[str] = []
        self.state = "running"
        self.rc = 0
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = False

    def emit(self, s: str) -> None:
        # 색 코드를 걷어낸다. 터미널에서는 색이지만 브라우저에서는 '[32m' 같은
        # 쓰레기 글자로 보인다 — RapidOCR 로그가 실제로 그렇게 나왔다.
        s = ANSI.sub("", s)
        with self._lock:
            self.lines.append(s)
            if len(self.lines) > 4000:          # 오래 켜 두는 서버 로그 대비
                del self.lines[:1000]

    def since(self, n: int) -> tuple[list[str], int]:
        with self._lock:
            return self.lines[n:], len(self.lines)

    def run(self) -> None:
        for i, argv in enumerate(self.steps):
            if self._stop:
                break
            head = f"{i + 1}/{len(self.steps)}  " if len(self.steps) > 1 else ""
            self.emit(f"\n───── {head}{' '.join(argv)} " + "─" * 20)
            if argv[0] == "check":
                # 점검은 1분쯤 걸린다. 아무 말도 없이 1분을 보내면 멈춘 줄 안다.
                self.emit("  (테스트까지 돌리므로 1분쯤 걸립니다. 기다리세요)")
            kw = {}
            if sys.platform == "win32":
                kw["creationflags"] = CREATE_NO_WINDOW
            try:
                self.proc = subprocess.Popen(
                    self_cmd(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, env=_child_env(), cwd=str(app_dir()),
                    text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
            except OSError as exc:
                self.emit(f"[실패] 실행할 수 없습니다: {exc}")
                self.state = "failed"
                return
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.emit(line.rstrip("\n"))
            self.rc = self.proc.wait()
            if self.rc != 0 and not self._stop:
                self.state = "failed"
                self.emit(f"\n[중단] 위 단계가 오류로 끝났습니다 (코드 {self.rc}).")
                return
        self.state = "stopped" if self._stop else "done"

    def stop(self) -> None:
        self._stop = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


STATE: dict[str, Job | None] = {"job": None}


def start_job(key: str, extra: list[str] | None = None) -> tuple[Job | None, str]:
    """작업을 건다. 실패하면 **왜 실패했는지** 함께 돌려준다.

    예전에는 셋 다 None 을 돌려주었고 화면은 언제나 '이미 다른 작업이 돌고 있습니다'
    라고 말했다. 폴더를 못 고른 경우에도, 없는 작업을 부른 경우에도 그렇게 말했다.
    틀린 이유를 알려주는 화면은 아무 말도 안 하는 화면보다 나쁘다.
    """
    old = STATE["job"]
    if old is not None and old.state == "running":
        return None, "이미 다른 작업이 돌고 있습니다. 끝나거나 중지한 뒤에 다시 하세요."
    spec = JOBS.get(key)
    if spec is None:
        return None, f"알 수 없는 작업입니다: {key}"
    steps = spec["steps"]
    if steps is None:                    # ingest — 폴더가 붙는다
        if not extra:
            return None, "진단할 촬영을 먼저 고르세요."
        if len(extra) != 1:
            return None, '촬영 폴더 또는 ZIP 하나를 선택하세요.'
        target = Path(extra[0]).resolve()
        try:
            if target.is_file() and target.suffix.lower() == '.zip':
                target = easy.extract(target)
            if not (target / 'manifest.json').is_file():
                return None, '촬영 manifest.json이 없는 폴더입니다.'
        except (OSError, easy.UnsafeZip) as exc:
            return None, str(exc)
        steps = [["ingest", str(target)]]
    job = Job(key, steps, spec["title"])
    STATE["job"] = job
    threading.Thread(target=job.run, daemon=True).start()
    return job, ""


# ── QR ───────────────────────────────────────────────────────────────────
def qr_png(text: str, scale: int = 8, quiet: int = 3) -> bytes | None:
    """주소를 QR 로 만든다.

    폰에 `http://192.168.0.42:8099/` 를 손으로 치게 하는 것은 현장에서 실제로
    가장 자주 실패하는 대목이다. 카메라로 찍으면 끝나는 일을 자판으로 시킬 이유가 없다.
    """
    try:
        import cv2
        import numpy as np
        enc = cv2.QRCodeEncoder_create()
        m = enc.encode(text)
        if m is None:
            return None
        m = np.asarray(m, dtype=np.uint8)
        if m.ndim == 3:
            m = m[:, :, 0]
        pad = np.pad(m, quiet, constant_values=255)
        big = np.kron(pad, np.ones((scale, scale), dtype=np.uint8))
        ok, buf = cv2.imencode(".png", big)
        return buf.tobytes() if ok else None
    except Exception:                                       # noqa: BLE001
        return None


# ── 바탕화면 바로가기 ────────────────────────────────────────────────────
def make_shortcut() -> tuple[bool, str]:
    """바탕화면에 바로가기를 만든다.

    '압축을 풀고 → 폴더를 찾아 들어가서 → exe 를 더블클릭' 은 세 단계다.
    한 번 만들어 두면 그다음부터는 한 번이다.
    """
    if sys.platform != "win32":
        return False, "윈도우에서만 됩니다"
    target = sys.executable if getattr(sys, "frozen", False) else str(app_dir() / "kfa.py")
    home = Path(os.path.expanduser("~"))
    desk = next((d for d in (home / "Desktop", home / "바탕 화면",
                             home / "OneDrive" / "Desktop",
                             home / "OneDrive" / "바탕 화면") if d.is_dir()), None)
    if desk is None:
        return False, "바탕화면 폴더를 찾지 못했습니다"
    lnk = desk / "키오스크 접근성 진단.lnk"
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{tgt}';$s.WorkingDirectory='{wd}';"
        "$s.Description='키오스크 현장 접근성 진단';$s.Save()"
    ).format(lnk=str(lnk).replace("'", "''"), tgt=target.replace("'", "''"),
             wd=str(app_dir()).replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if lnk.exists():
        return True, str(lnk)
    return False, (r.stderr or b"").decode("utf-8", "replace")[:200] or "만들지 못했습니다"


# ── 화면 ─────────────────────────────────────────────────────────────────
def _root_tokens() -> str:
    """수집 앱 CSS 의 :root 를 그대로 가져온다.

    색을 여기서 새로 정하지 않는 이유 — self-check 가 검사하는 것은 그 파일이다.
    같은 토큰을 쓰면 조작판도 같은 검사를 통과한 색만 쓴다.
    """
    try:
        css = (resource_dir() / "app" / "style.css").read_text(encoding="utf-8")
        i = css.index(":root {")
        return css[i:css.index("}", i) + 1]
    except (OSError, ValueError):
        return (":root{--paper:#EEF2F6;--surface:#FFF;--surface-2:#F6F8FA;--ink:#131A22;"
                "--ink-2:#33404E;--muted:#5D6B79;--faint:#5A6773;--line:#D7DFE7;"
                "--line-soft:#E6ECF2;--accent:#F2B705;--deep:#17385C;--deep-ink:#0E2439;"
                "--a:#1B579B;--a-bg:#E7EFF9;--b:#24705A;--b-bg:#E3F0EB;--c:#99451F;"
                "--c-bg:#F7E9E2;--ok:#1E7A4D;--ok-bg:#E3F2EA;--bad:#B3261E;--bad-bg:#FBE9E7;"
                "--warn:#8C5200;--warn-bg:#FDF1DC;--tap:48px;"
                '--f:"Malgun Gothic","맑은 고딕",sans-serif;--mono:ui-monospace,monospace;}')


PAGE_CSS = """
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--paper);color:var(--ink);
  font-family:var(--f);font-size:17px;line-height:1.6;word-break:keep-all}
header{background:var(--deep-ink);color:#fff;padding:20px 24px;
  display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
header h1{margin:0;font-size:22px;letter-spacing:-.01em}
header .v{font-family:var(--mono);font-size:12px;color:var(--accent);letter-spacing:.12em}
main{max-width:940px;margin:0 auto;padding:28px 20px 60px}
h2{font-size:20px;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 22px}
.cards{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:22px;display:flex;flex-direction:column;gap:10px;text-align:left}
.card .n{font-family:var(--mono);font-size:13px;letter-spacing:.14em;color:var(--faint)}
.card h3{margin:0;font-size:21px}
.card p{margin:0;color:var(--ink-2);flex:1}
button,.btn{font:inherit;font-weight:700;border-radius:10px;cursor:pointer;
  min-height:var(--tap);padding:12px 20px;border:2px solid transparent;
  display:inline-flex;align-items:center;justify-content:center;gap:8px;text-decoration:none}
.primary{background:var(--deep-ink);color:#fff}
.primary:hover{background:var(--deep)}
.ghost{background:var(--surface);color:var(--ink);border-color:var(--line)}
.ghost:hover{background:var(--surface-2)}
.warnbtn{background:var(--accent);color:var(--deep-ink)}
button:focus-visible,.btn:focus-visible,a:focus-visible{outline:3px solid var(--a);outline-offset:2px}
button[disabled]{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
.tools{margin-top:30px;padding-top:20px;border-top:1px solid var(--line);
  display:flex;gap:10px;flex-wrap:wrap}
.tools .btn{min-height:44px;padding:10px 16px;font-weight:600;font-size:15px}
#log{background:var(--deep-ink);color:#E4ECF4;font-family:var(--mono);font-size:13px;
  line-height:1.55;padding:16px;border-radius:12px;height:46vh;min-height:260px;
  overflow:auto;white-space:pre-wrap;margin-top:16px}
.state{display:inline-block;font-family:var(--mono);font-size:12px;padding:5px 10px;
  border-radius:6px;letter-spacing:.06em}
.s-running{background:var(--a-bg);color:var(--a)}
.s-done{background:var(--ok-bg);color:var(--ok)}
.s-failed{background:var(--bad-bg);color:var(--bad)}
.s-stopped{background:var(--warn-bg);color:var(--warn)}
.list{display:flex;flex-direction:column;gap:10px;margin:18px 0}
.item{background:var(--surface);border:2px solid var(--line);border-radius:12px;
  padding:16px 18px;text-align:left;width:100%;flex-direction:column;align-items:flex-start;
  gap:4px;min-height:0}
.item:hover{border-color:var(--a);background:var(--a-bg)}
.item .t{font-size:18px;font-weight:700}
.item .m{font-family:var(--mono);font-size:12.5px;color:var(--muted);font-weight:400}
.addr{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:20px;margin-top:18px;display:flex;gap:22px;align-items:center;flex-wrap:wrap}
.addr img{width:190px;height:190px;image-rendering:pixelated;border:1px solid var(--line)}
.addr .u{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--deep);
  word-break:break-all}
.note{background:var(--warn-bg);color:var(--warn);border-radius:10px;padding:14px 16px;
  margin-top:16px;font-size:15.5px}
.okbox{background:var(--ok-bg);color:var(--ok);border-radius:10px;padding:14px 16px;
  margin-top:16px;font-size:15.5px;font-weight:700}
.hide{display:none}
"""


def page(token: str) -> str:
    return f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>키오스크 접근성 진단</title>
<style>{_root_tokens()}{PAGE_CSS}</style></head><body>
<header>
  <h1>키오스크 접근성 진단</h1>
  <span class="v">KFA {VERSION}</span>
</header>
<main>
  <section id="home">
    <h2>무엇을 하시겠습니까?</h2>
    <p class="sub">순서대로 하시면 됩니다. 처음이라면 ① 부터.</p>
    <div class="cards">
      <div class="card">
        <span class="n">STEP 1</span><h3>준비하기</h3>
        <p>프로그램을 점검하고, 현장에 들고 갈 인쇄물 두 장을 만듭니다.</p>
        <button class="primary" onclick="job('prepare')">준비 시작</button>
      </div>
      <div class="card">
        <span class="n">STEP 2</span><h3>촬영하기</h3>
        <p>폰으로 키오스크를 찍습니다. 인터넷이 되는 곳에서는 공개된 HTTPS 앱을 바로 엽니다.</p>
        <button class="primary" onclick="webqr()">촬영 앱 QR 열기</button>
        <button class="btn ghost" onclick="serve()">같은 와이파이에서 PC로 열기</button>
      </div>
      <div class="card">
        <span class="n">STEP 3</span><h3>진단하기</h3>
        <p>찍어 온 사진에서 판정과 리포트를 만듭니다. ZIP 그대로 넣어도 됩니다.</p>
        <button class="primary" onclick="pick()">사진 고르기</button>
      </div>
    </div>
    <div class="tools">
      <button class="btn primary" onclick="location.href='/review?t='+T">검토 · 실측 입력</button>
      <button class="btn ghost" onclick="openp('manual')">설명서 열기</button>
      <button class="btn ghost" onclick="openp('output')">결과 폴더 열기</button>
      <button class="btn ghost" onclick="shortcut()">바탕화면에 바로가기 만들기</button>
      <button class="btn ghost" onclick="job('web')">촬영 앱을 웹서버용으로 내보내기</button>
      <button class="btn ghost" onclick="more()">그 밖의 기능</button>
    </div>
    <div id="msg"></div>
  </section>

  <section id="work" class="hide">
    <h2 id="wt">작업</h2>
    <p class="sub"><span id="ws" class="state s-running">진행 중</span></p>
    <div id="extra"></div>
    <div id="log"></div>
    <div class="row">
      <button class="btn ghost" id="back" onclick="home()">처음으로</button>
      <button class="btn warnbtn hide" id="stop" onclick="stop()">중지</button>
      <button class="btn ghost hide" id="ofolder" onclick="openp('output')">결과 폴더 열기</button>
    </div>
  </section>

  <section id="picker" class="hide">
    <h2>어느 촬영을 진단할까요?</h2>
    <p class="sub">다운로드·바탕화면·문서 폴더와 이 프로그램 폴더에서 찾은 것입니다.</p>
    <div id="items" class="list"></div>
    <div class="row">
      <button class="btn ghost" onclick="home()">처음으로</button>
      <button class="btn ghost" onclick="pick()">다시 찾기</button>
      <button class="btn ghost" onclick="job('demo')">데모 번들 만들기</button>
    </div>
  </section>

  <section id="more" class="hide">
    <h2>그 밖의 기능</h2>
    <p class="sub">검증·개발용입니다. 현장에서는 쓸 일이 없습니다.</p>
    <div class="list">
      <button class="item" onclick="job('check')"><span class="t">설치·동작 점검</span><span class="m">check</span></button>
      <button class="item" onclick="job('selfcheck')"><span class="t">자기 검사 — 우리 화면이 우리 기준을 통과하는가</span><span class="m">self-check</span></button>
      <button class="item" onclick="job('rules')"><span class="t">룰 DB 검증 — 별표5 40항목 정합성</span><span class="m">rules</span></button>
      <button class="item" onclick="job('gaps')"><span class="t">구멍 점검 — 무엇이 아직 없는가</span><span class="m">gaps</span></button>
      <button class="item" onclick="job('marker')"><span class="t">마커 카드만 다시 만들기</span><span class="m">marker</span></button>
      <button class="item" onclick="job('sheet')"><span class="t">현장 체크리스트만 다시 만들기</span><span class="m">sheet</span></button>
      <button class="item" onclick="job('manual')"><span class="t">설명서 다시 만들기</span><span class="m">manual</span></button>
    </div>
    <div class="row"><button class="btn ghost" onclick="home()">처음으로</button></div>
  </section>
</main>
<script>
const T = "{token}";
let seen = 0, timer = null, cur = null;

function api(p, body) {{
  return fetch(p + (p.includes('?') ? '&' : '?') + 't=' + T,
    body ? {{method:'POST', headers:{{'Content-Type':'application/json'}},
             body: JSON.stringify(body)}} : {{}}).then(r => r.json());
}}
function show(id) {{
  for (const s of ['home','work','picker','more']) document.getElementById(s).classList.toggle('hide', s !== id);
  window.scrollTo(0,0);
}}
function home() {{ if (timer) clearInterval(timer); timer = null; show('home'); }}
function more() {{ show('more'); }}
function msg(html) {{ document.getElementById('msg').innerHTML = html; }}

function job(key, extra) {{
  seen = 0; cur = key;
  document.getElementById('log').textContent = '';
  document.getElementById('extra').innerHTML = '';
  document.getElementById('ofolder').classList.add('hide');
  api('/run', {{job:key, extra:extra||[]}}).then(r => {{
    if (!r.ok) {{ alert(r.error || '시작하지 못했습니다'); return; }}
    document.getElementById('wt').textContent = r.title;
    document.getElementById('stop').classList.toggle('hide', !r.long);
    show('work');
    if (timer) clearInterval(timer);
    timer = setInterval(poll, 600);
    poll();
  }});
}}
function poll() {{
  api('/log?since=' + seen).then(r => {{
    if (r.lines && r.lines.length) {{
      const el = document.getElementById('log');
      const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
      el.textContent += r.lines.join('\\n') + '\\n';
      seen = r.next;
      if (stick) el.scrollTop = el.scrollHeight;
    }}
    const s = document.getElementById('ws');
    const label = {{running:'진행 중', done:'완료', failed:'오류', stopped:'중지됨'}}[r.state] || r.state;
    s.textContent = label; s.className = 'state s-' + r.state;
    if (r.urls && r.urls.length) showAddr(r.urls);
    if (r.state !== 'running') {{
      clearInterval(timer); timer = null;
      document.getElementById('stop').classList.add('hide');
      if (r.state === 'done') finish();
    }}
  }});
}}
function showAddr(urls) {{
  const box = document.getElementById('extra');
  if (box.dataset.addr === urls[0]) return;
  box.dataset.addr = urls[0];
  box.innerHTML = '<div class="addr"><img alt="접속 주소 QR 코드" src="/qr?t=' + T +
    '&u=' + encodeURIComponent(urls[0]) + '"><div><div class="u">' + urls[0] + '</div>' +
    '<p style="margin:10px 0 0;color:var(--ink-2)">폰 카메라로 QR 을 비추면 촬영 앱이 열립니다.<br>' +
    '폰과 이 PC 가 <b>같은 와이파이</b>에 있어야 합니다.</p></div></div>' +
    '<div class="note">촬영이 끝나면 폰에서 ZIP 을 내보내 이 PC 로 옮긴 뒤, ③ 진단하기로 가세요.<br>'+
    '이 방식은 폰이 <b>같은 와이파이</b> 안에 있어야 하고, 와이파이를 벗어나면 앱이 다시 열리지 않습니다. '+
    '자주 쓰신다면 촬영 앱을 <b>웹서버에 올려 두는 편</b>이 훨씬 편합니다 — 처음 화면의 '+
    '‘촬영 앱을 웹서버용으로 내보내기’ 를 보세요.</div>';
}}
function finish() {{
  const box = document.getElementById('extra');
  if (cur === 'prepare') {{
    box.innerHTML = '<div class="okbox">준비 끝 — 인쇄물 두 장을 인쇄하세요.</div>' +
      '<div class="row"><button class="btn primary" onclick="openp(\\'marker\\')">마커 카드 열기 (인쇄)</button>' +
      '<button class="btn primary" onclick="openp(\\'sheet\\')">현장 체크리스트 열기 (인쇄)</button></div>' +
      '<div class="note">인쇄 배율은 반드시 <b>실제 크기 100%</b>. \\'용지에 맞춤\\'을 쓰면 모든 치수가 틀어집니다.<br>' +
      '인쇄 후 카드의 20mm 눈금에 자를 대어 확인하세요.</div>';
  }} else if (cur === 'ingest') {{
    box.innerHTML = '<div class="okbox">진단 완료 — 리포트를 만들었습니다.</div>' +
      '<div class="row"><button class="btn primary" onclick="openp(\\'reportA\\')">리포트 A · 점주용 열기</button>' +
      '<button class="btn primary" onclick="openp(\\'reportB\\')">리포트 B · 담당자용 열기</button></div>' +
      '<div class="note">브라우저에서 <b>Ctrl+P → 대상: PDF 로 저장</b> 하면 제출본이 됩니다.<br>' +
      '이 리포트는 법정 적합성 인증이 아닙니다.</div>';
  }} else if (cur === 'web') {{
    box.innerHTML = '<div class="okbox">웹서버에 올릴 파일을 만들었습니다.</div>' +
      '<div class="row"><button class="btn primary" onclick="openp(\\'web\\')">폴더 열기</button></div>' +
      '<div class="note">폴더를 통째로 올리세요. 서버에서 도는 코드는 없습니다. ' +
      '<b>반드시 https</b> 여야 카메라와 오프라인 저장이 됩니다.<br>' +
      '자세한 것은 폴더 안 \\'올리는 법.txt\\' 에 있습니다. 사진은 서버로 가지 않습니다.</div>';
  }} else if (cur === 'demo') {{
    box.innerHTML = '<div class="okbox">데모 번들을 만들었습니다.</div>' +
      '<div class="row"><button class="btn primary" onclick="pick()">이걸로 진단해보기</button></div>';
  }}
  document.getElementById('ofolder').classList.remove('hide');
}}
function stop() {{ api('/stop', {{}}).then(poll); }}
function serve() {{ job('serve'); }}
function pick() {{
  show('picker');
  const el = document.getElementById('items');
  el.innerHTML = '<p class="sub">찾는 중…</p>';
  api('/bundles').then(r => {{
    if (!r.items.length) {{
      el.innerHTML = '<div class="note">촬영 번들을 찾지 못했습니다.<br>' +
        '폰에서 내보낸 ZIP 을 이 PC 의 다운로드·바탕화면 폴더에 두고 <b>다시 찾기</b>를 누르세요.<br>' +
        '먼저 과정을 보고 싶으면 <b>데모 번들 만들기</b>를 누르세요.</div>';
      return;
    }}
    el.innerHTML = '';
    for (const b of r.items) {{
      const btn = document.createElement('button');
      btn.className = 'item';
      const title = document.createElement('span');
      title.className = 't'; title.textContent = b.label;
      const detail = document.createElement('span');
      detail.className = 'm'; detail.textContent = b.kind + ' · ' + b.when + ' · ' + b.path;
      btn.append(title, detail);
      btn.onclick = () => job('ingest', [b.path]);
      el.appendChild(btn);
    }}
  }});
}}
function webqr() {{
  api('/weburl').then(r => {{
    const u = r.url;
    if (!u) {{ msg('<div class="note">촬영 앱 주소가 없습니다.</div>'); return; }}
    msg('<div class="addr"><img alt="촬영 앱 접속 QR 코드" src="/qr?t=' + T +
      '&u=' + encodeURIComponent(u) + '"><div><div class="u">' + u + '</div>' +
      '<p style="margin:10px 0;color:var(--ink-2)">이것은 <b>앱 접속용 QR</b>입니다. 측정 마커와 다릅니다.<br>' +
      '폰 카메라로 비추거나 아래 버튼을 눌러 앱을 여세요.</p>' +
      '<a class="btn primary" target="_blank" rel="noopener" href="' + u + '">이 PC에서 촬영 앱 확인</a></div></div>' +
      (u.startsWith('https://') ? '' :
       '<div class="note"><b>http 주소입니다.</b> 카메라 권한과 오프라인 저장이 동작하지 않습니다. https 로 올리세요.</div>'));
  }});
}}
function openp(what) {{ api('/open', {{what:what}}).then(r => {{
  if (!r.ok) alert(r.error || '열지 못했습니다');
}}); }}
function shortcut() {{ api('/shortcut', {{}}).then(r => {{
  msg('<div class="' + (r.ok ? 'okbox' : 'note') + '">' +
      (r.ok ? '바탕화면에 <b>키오스크 접근성 진단</b> 바로가기를 만들었습니다.'
            : '만들지 못했습니다 — ' + r.error) + '</div>');
}}); }}
</script></body></html>"""


# ── 서버 ─────────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "KFA-panel"
    token = ""
    served = 0                  # 화면이 한 번이라도 열렸는가 (아래 감시용)

    def log_message(self, fmt, *args):          # 조용히
        pass

    # -- 도우미 --------------------------------------------------------
    def _q(self) -> dict:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _authed(self) -> bool:
        """토큰 확인 — 다른 웹페이지가 localhost 로 던지는 요청을 막는다."""
        return self._q().get("t", [""])[0] == self.token

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 1024 * 1024:
                return None
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, OSError):
            return {}

    # -- GET -----------------------------------------------------------
    def do_GET(self):                                        # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            if not self._authed():
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            Handler.served += 1
            self._send(200, page(self.token).encode("utf-8"), "text/html; charset=utf-8")
            return
        if not self._authed():
            self._json({"ok": False, "error": "token"}, 403)
            return
        if path == "/log":
            self._json(self._log_state())
        elif path == "/bundles":
            self._json({"items": _bundle_list()})
        elif path == "/review":
            self._send(200, (resource_dir() / 'app' / 'review.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/review/media':
            from tools import review_web
            try:
                q = self._q()
                session = review_web.SESSIONS[q.get('session', [''])[0]]
                index = int(q.get('index', ['-1'])[0])
                if index < 0:
                    raise ValueError('index')
                photo = session['shots'][index]
                mime = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}[photo.suffix.lower()]
                self._send(200, photo.read_bytes(), mime)
            except (KeyError, IndexError, ValueError, OSError):
                self._json({'ok': False, 'error': '사진을 찾을 수 없습니다.'}, 404)
        elif path == "/weburl":
            self._json({"ok": True, "url": _web_url_load()})
        elif path == "/qr":
            png = qr_png(self._q().get("u", [""])[0])
            if png:
                self._send(200, png, "image/png")
            else:
                self._send(404, b"", "image/png")
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    # -- POST ----------------------------------------------------------
    def do_POST(self):                                       # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._authed():
            self._json({"ok": False, "error": "token"}, 403)
            return
        b = self._body()
        if not isinstance(b, dict):
            self._json({'ok': False, 'error': '객체 형식의 입력이 필요합니다.'}, 400)
            return
        if path == '/review/compare':
            from engine import rules
            from engine.measurement import evaluate
            try:
                rec = b.get('record')
                if not isinstance(rec,dict): raise ValueError('측정값을 입력하세요.')
                result = evaluate(rules.load()[b.get('id')],rec)
                self._json({'ok':True,'result':result.to_row()})
            except (ValueError,KeyError,TypeError) as exc:
                self._json({'ok':False,'error':str(exc)},400)
            return
        if path in ('/review/load', '/review/save'):
            from tools import review_web
            try:
                j = STATE['job']
                if j and j.state == 'running':
                    raise ValueError('진행 중인 작업이 끝난 뒤 검토하세요.')
                if path == '/review/load':
                    selected = next((f for f in easy.find_bundles() if str(f.path) == b.get('path')), None)
                    if selected is None:
                        raise ValueError('촬영 목록에서 대상을 다시 선택하세요.')
                    target = easy.extract(selected.path) if selected.is_zip else selected.path
                    result = review_web.load(target)
                else:
                    result = review_web.save(b)
                self._json(result)
            except (ValueError, OSError, easy.UnsafeZip) as exc:
                self._json({'ok': False, 'error': str(exc)}, 400)
            return
        if path == "/run":
            key = str(b.get("job", ""))
            extra = [str(x) for x in (b.get("extra") or [])]
            job, why = start_job(key, extra)
            if job is None:
                self._json({"ok": False, "error": why})
                return
            self._json({"ok": True, "title": job.title,
                        "long": bool(JOBS[key].get("long"))})
        elif path == "/stop":
            j = STATE["job"]
            if j:
                j.stop()
            self._json({"ok": True})
        elif path == "/open":
            ok, err = _open_what(str(b.get("what", "")))
            self._json({"ok": ok, "error": err})
        elif path == "/weburl":
            ok, val = _web_url_save(str(b.get("url", "")))
            self._json({"ok": ok, "url": val if ok else "", "error": "" if ok else val})
        elif path == "/shortcut":
            ok, info = make_shortcut()
            self._json({"ok": ok, "error": "" if ok else info, "path": info if ok else ""})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    # -- 상태 ----------------------------------------------------------
    def _log_state(self) -> dict:
        j = STATE["job"]
        if j is None:
            return {"state": "done", "lines": [], "next": 0, "urls": []}
        since = int(self._q().get("since", ["0"])[0] or 0)
        lines, nxt = j.since(since)
        return {"state": j.state, "lines": lines, "next": nxt,
                "urls": _urls_in(j) if j.key == "serve" else []}


def _urls_in(job: Job) -> list[str]:
    """서버 로그에서 폰이 쓸 주소를 뽑는다. localhost 는 폰에서 못 쓴다."""
    out = []
    for ln in job.lines[:80]:
        s = ln.strip()
        if s.startswith(("http://", "https://")) and "localhost" not in s and "127.0.0.1" not in s:
            out.append(s)
    return out[:1]


def _bundle_list() -> list[dict]:
    items = []
    for f in easy.find_bundles():
        items.append({"label": f.label, "kind": "ZIP" if f.is_zip else "폴더",
                      "when": f.when, "path": str(f.path)})
    return items


WEB_URL_FILE = "web-url.txt"


def _web_url_load() -> str:
    f = output_dir() / WEB_URL_FILE
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_WEB_URL


def _web_url_save(url: str) -> tuple[bool, str]:
    """올려 둔 촬영 앱 주소를 기억한다. 현장마다 다시 물어볼 이유가 없다."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False, "주소는 http:// 또는 https:// 로 시작해야 합니다."
    if len(url) > 300 or any(c in url for c in "\r\n"):
        return False, "주소가 올바르지 않습니다."
    try:
        (output_dir() / WEB_URL_FILE).write_text(url, encoding="utf-8")
    except OSError as exc:
        return False, f"저장하지 못했습니다: {exc}"
    return True, url


def _newest_report() -> Path | None:
    try:
        ds = sorted(output_dir().glob("report_*"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return ds[0] if ds else None


def _marker_sheet() -> Path | None:
    """인쇄용 마커를 찾고, 없으면 사용자가 누른 자리에서 바로 만든다.

    배포 ZIP 의 `인쇄물`과 실행 뒤 생성되는 `assets`는 위치가 다르다. 예전에는
    assets만 찾아서 동봉된 인쇄물이 있어도 '아직 만들어지지 않았습니다'라고 했다.
    """
    candidates = [
        output_dir() / "BFK-MARK-A_A4_sheet.png",
        app_dir() / "인쇄물" / "1_마커카드_A4.png",
        app_dir() / "1_마커카드_A4.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    try:
        from tools import make_marker
        cards = [make_marker.build(i) for i in (0, 1, 2, 3)]
        target = candidates[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        if make_marker.cv2.imwrite(str(target), make_marker.sheet(cards)):
            return target
    except Exception:                         # 화면에는 아래의 구체적 복구 안내를 낸다
        return None
    return None


def _open_what(what: str) -> tuple[bool, str]:
    """화면이 열 수 있는 것은 여기 적힌 것뿐이다 — 경로를 화면에서 받지 않는다."""
    if what == "output":
        target: Path | None = output_dir()
    elif what == "web":
        target = output_dir() / "web"
    elif what == "marker":
        target = _marker_sheet()
    elif what == "sheet":
        target = output_dir() / "field-sheet.html"
    elif what == "manual":
        target = next((b / n for n in ("사용자매뉴얼.pdf", "사용자매뉴얼.html")
                       for b in (app_dir(), output_dir()) if (b / n).exists()), None)
    elif what in ("reportA", "reportB"):
        rep = _newest_report()
        name = "리포트A_점주용.html" if what == "reportA" else "리포트B_담당자용.html"
        target = (rep / name) if rep else None
    else:
        return False, "알 수 없는 대상"
    if target is None or not Path(target).exists():
        if what == "marker":
            return False, "마커 카드를 만들지 못했습니다. '준비 시작'을 다시 누르고 오류 내용을 확인하세요."
        return False, "아직 만들어지지 않았습니다"
    return (True, "") if easy.open_path(Path(target)) else (False, "열지 못했습니다")


def _show_console() -> None:
    """숨겨 둔 검은 창을 도로 보여 준다.

    kfa.launch() 가 더블클릭한 사용자의 콘솔을 숨긴다. 그 상태에서 브라우저가
    열리지 않으면 **사용자 눈에는 아무 일도 일어나지 않는다.** 프로그램이 죽은 줄
    안다. 그래서 화면이 안 열렸다고 판단되면 창을 도로 꺼내 주소를 보여 준다.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)     # SW_SHOW
    except Exception:                                    # noqa: BLE001
        pass


def _watch_first_open(url: str) -> None:
    """화면이 한 번도 안 열렸으면 검은 창을 꺼내 주소를 크게 보여 준다."""
    if Handler.served:
        return
    _show_console()
    bang = "!" * 66
    print(f"\n{bang}")
    print("  브라우저가 열리지 않은 것 같습니다.")
    print("  아래 주소를 브라우저 주소창에 붙여 넣으세요.\n")
    print(f"    {url}\n")
    print("  (기본 브라우저가 정해져 있지 않거나, 보안 프로그램이 막았을 수 있습니다)")
    print(f"{bang}\n", flush=True)


def free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="조작판 — 브라우저 화면으로 쓴다")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않는다")
    a = ap.parse_args()

    port = free_port(a.port)
    if port == 0:
        print(f"  [실패] {a.port} 부터 20개 포트가 모두 막혀 있습니다.")
        return 2

    Handler.token = secrets.token_urlsafe(16)
    url = f"http://127.0.0.1:{port}/?t={Handler.token}"

    # 127.0.0.1 에만 연다. 이 화면은 명령을 실행하므로 LAN 에 내놓지 않는다.
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    print("=" * 66)
    print("  키오스크 접근성 진단 — 조작판을 열었습니다")
    print("=" * 66)
    print("\n  브라우저 창에서 계속하세요. 이 검은 창은 그대로 두시면 됩니다.")
    print(f"\n  주소  {url}")
    print("\n  (창이 안 열리면 위 주소를 브라우저에 붙여 넣으세요)")
    print("  끝내려면 이 창을 닫거나 Ctrl+C.\n")

    if not a.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
        threading.Timer(12.0, lambda: _watch_first_open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  조작판을 닫았습니다.")
    finally:
        j = STATE["job"]
        if j:
            j.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
