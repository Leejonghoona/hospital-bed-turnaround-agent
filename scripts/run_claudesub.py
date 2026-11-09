"""구독 경유 Claude 측정 한 번에 돌리기: 최신 코드 받기 → 측정 → 결과 저장 → 커밋·푸시.

사용 (노트북, 저장소 폴더에서):
    python scripts/run_claudesub.py            # 세 모델, 인젝션·질의·계획
    python scripts/run_claudesub.py --dry-run  # 5문항만 시험 (커밋 안 함)

- 구독 한도에 걸려 멈춘 모델은 다음 실행 때 다시 잰다. 끝까지 잰 모델은 건너뛴다.
- ANTHROPIC_API_KEY가 있으면 API로 과금되므로 이 프로세스에서만 지운다.
- 외부로는 합성 데이터만 나간다(TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only).
- 개인 평가 용도 (DECISIONS "구독 경유 Claude 측정").
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "results" / "model_check_v2"
BRANCH = "claude/gallant-keller-0lrkwf"
MODELS = ["claudesub:claude-haiku-5-5", "claudesub:claude-sonnet-5-5", "claudesub:claude-opus-5-5"]
PARTS = "injection,qa,planning"  # 추출은 설문 뒤 라벨 확정 후


def _safe(spec: str) -> str:
    return spec.replace(":", "_").replace("/", "_")


def _stopped(r: dict) -> bool:
    return any(isinstance(v, dict) and v.get("stopped") for v in r.values())


def done_models(outdir: Path, parts: list[str]) -> set[str]:
    """요청한 부분을 모두 끝까지 잰 결과 파일이 있는 모델."""
    done = set()
    for f in outdir.glob("*_claudesub_*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if all(p in r for p in parts) and not _stopped(r) and r.get("suite") == "v2":
            done.add(r["model"])
    return done


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--parts", default=PARTS)
    ap.add_argument("--dry-run", action="store_true", help="5문항만, 저장·커밋 안 함")
    ap.add_argument("--no-git", action="store_true", help="pull·커밋·푸시를 하지 않는다")
    a = ap.parse_args(argv)

    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ["TURNAROUND_EXTERNAL_LLM_ACK"] = "synthetic-only"
    git = not (a.dry_run or a.no_git)
    if git:
        print("[1/4] 최신 코드 받기")
        _git("pull", "-q", "origin", BRANCH)

    sys.path.insert(0, str(ROOT / "src"))
    from turnaround.evaluation.model_check import check_v2

    parts = a.parts.split(",")
    todo = [m for m in a.models.split(",") if m and (a.dry_run or m not in done_models(OUT, parts))]
    print(f"[2/4] 측정할 모델: {todo or '없음 (모두 끝남)'}")
    OUT.mkdir(parents=True, exist_ok=True)
    saved, stopped = [], []
    for spec in todo:
        print(f"  - {spec} 측정 중 (몇십 분 걸릴 수 있음)")
        r = check_v2(spec, max_usd=1e9, parts=tuple(parts), limit=5 if a.dry_run else None)
        if a.dry_run:
            print(json.dumps({k: r.get(k) for k in parts}, ensure_ascii=False)[:800])
            continue
        f = OUT / f"{r.get('date', 'x')}_{_safe(spec)}.json"
        f.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(f)
        if _stopped(r):
            stopped.append(spec)
            print(f"    구독 한도에 걸려 멈춤. 나중에 같은 명령을 다시 실행하면 이어서 잰다")
            break  # 한도는 계정 단위라 다음 모델도 막힌다

    if git and saved:
        print("[3/4] 결과 커밋")
        _git("add", *[str(p.relative_to(ROOT)) for p in saved])
        names = ", ".join(s.partition(":")[2] for s in todo[: len(saved)])
        _git("commit", "-q", "-m", f"구독 경유 Claude 측정 결과: {names}" + (" (한도로 일부 중단)" if stopped else ""))
        print("[4/4] 푸시")
        for _ in range(4):
            if _git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False).returncode == 0:
                break
            _git("pull", "-q", "--rebase", "origin", BRANCH, check=False)
        else:
            print("푸시 실패: 결과 파일은 evals/results/model_check_v2/에 있다. 채팅에 첨부해도 된다")
            return 1
    print("끝." + (f" 한도로 멈춘 모델: {stopped}" if stopped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
