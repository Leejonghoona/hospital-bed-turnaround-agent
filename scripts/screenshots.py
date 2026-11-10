"""화면 스크린샷 (08 계획 4단계). Streamlit을 띄우고 브라우저로 시연 대본 3장면을 눌러 단계마다 찍는다.

실행: python scripts/screenshots.py [--out docs/screens] [--scale 2]
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(port: int) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "src/turnaround/ui/app.py"),
                          "--server.headless", "true", "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return p
        except OSError:
            time.sleep(0.5)
    p.kill()
    raise RuntimeError("streamlit이 뜨지 않았다")


def shoot(out: Path, scale: int = 2, only: str | None = None) -> list[dict]:
    """브라우저(Playwright, Node)로 장면을 눌러 찍는다. 단계마다 {file, step, ok}."""
    import json
    out.mkdir(parents=True, exist_ok=True)
    port = _port()
    srv = serve(port)
    try:
        r = subprocess.run(["node", str(ROOT / "scripts/shoot.cjs"), str(port), str(out), str(scale), only or ""],
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
    finally:
        srv.kill()
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
    return [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/screens"))
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args()
    for f in shoot(Path(a.out), a.scale):
        print(f["file"], "ok" if f["ok"] else "확인 필요")
