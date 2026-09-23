"""Launch the packaged capture server and fetch its real resources."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def check(exe):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen([str(Path(exe).resolve()), 'serve-app', '--port', str(port)],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            base = f'http://127.0.0.1:{port}/'
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if p.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read().decode('utf-8', errors='replace'))
                try:
                    with urllib.request.urlopen(base, timeout=1) as r:
                        assert r.status == 200
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError('Capture server did not start within 30 seconds')
            for path in ('index.html','app.js','zip.js','style.css','sw.js','icon.svg','manifest.webmanifest','protocol.json'):
                with urllib.request.urlopen(base+path, timeout=3) as r:
                    content=r.read()
                    assert r.status == 200 and content, path
                if path == 'protocol.json':
                    assert isinstance(json.loads(content),dict)
                print(f'PASS HTTP 200 {path}')
        finally:
            if p.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:
                    p.terminate()
            p.wait(timeout=10)


if __name__ == '__main__':
    check(sys.argv[1])
