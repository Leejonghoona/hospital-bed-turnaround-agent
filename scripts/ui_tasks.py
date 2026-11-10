"""화면 과업 시험 실행: API(빌드된 화면 포함)를 띄우고 tests/ui_tasks/tasks.cjs로 과업을 돌린다. 합성 데이터.

python scripts/ui_tasks.py [--only F1,W1] [--out docs/화면재설계/runs/latest]
결과: 과업별 통과/실패와 스크린샷. 먼저 `cd web && npm run build`.
"""

from __future__ import annotations

import argparse
import json
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


def run(only: str | None, out: Path) -> list[dict]:
    if not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("먼저 화면을 빌드하세요: cd web && npm run build")
    out.mkdir(parents=True, exist_ok=True)
    port = _port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "turnaround.api.app:app", "--port", str(port)], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        r = subprocess.run(["node", str(ROOT / "tests/ui_tasks/tasks.cjs"), f"http://127.0.0.1:{port}", str(out), only or ""],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
    finally:
        srv.kill()
    if r.returncode:
        raise SystemExit(r.stderr[-1500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default=str(ROOT / "docs/화면재설계/runs/latest"))
    a = ap.parse_args()
    res = run(a.only, Path(a.out))
    for t in res:
        print(("통과 " if t["pass"] else "실패 ") + t["id"])
        for c in t["checks"]:
            print(f"   {'o' if c['ok'] else 'x'} {c['name']}" + (f"  ({c['detail']})" if c.get("detail") else ""))
    print(f"{sum(t['pass'] for t in res)}/{len(res)} 통과")
