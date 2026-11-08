"""점수판. 시나리오 통과 현황, 안전 지표, 기준선을 JSON으로 내고 RESULTS.md에 한 줄 추가한다.

    python -m turnaround.evaluation.board --seeds 0-199 --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

from turnaround.events import EventStore, NoiseConfig, hospital_to_events
from turnaround.sim import generate

from .baselines import no_agent_baseline
from .invariants import BedTurnover, check_assignment, check_turnover, zero_event_upper_bound
from .runners import RUNNERS, STAGE  # 시나리오 ID → 실행기, 현재 구현 단계
from .runners_gateway import HOLDOUT_RUNNERS
from .scenario import load_scenarios
from turnaround.gateway import load_policy

ROOT = Path(__file__).resolve().parents[3]
DEV_DIR = ROOT / "evals" / "scenarios" / "dev"
HEADER = "## 점수판 기록 v2"
COLUMNS = ["날짜", "커밋", "시나리오 세트", "분리", "LLM", "정책", "S1 위반/분모", "S2 위반/분모", "S4 누수/절단",
           "통과/실패/미구현", "퇴실→준비 P50·P90(분)", "가정"]



def scenario_set_hash(directory: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(directory).glob("*.yaml")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _safety(seeds: Iterable[int]) -> dict:
    """지금은 생성기 상태를 검사한다. 사가와 게이트웨이가 생기면 시스템이 실행한 상태로 바꾼다."""
    s1_v = s1_n = s2_v = s2_n = 0
    for seed in seeds:
        h = generate(seed=seed)
        for day in range(h.config.days):
            occ = {d.bed_id: d.patient for d in h.discharges if d.day == day}
            s1_v += len(check_assignment(h.rooms, h.beds, occ))
            s1_n += len(occ)
        for d in h.discharges:
            if not d.patient.isolation_order:
                continue
            s2_n += 1
            s2_v += len(check_turnover(BedTurnover(
                bed_id=d.bed_id, previous_isolation=True, cleaning_type=d.cleaning_type,
                cleaning_done_at=d.actual.cleaning_done_at, committed_at=None, vacant_at=d.actual.bed_ready_at,
                previous_precautions=d.patient.precautions, previous_organism=d.patient.organism,
                departed_at=d.actual.departed_at, cleaning_started_at=d.actual.cleaning_started_at,
                sporicidal_done=d.sporicidal, aerosol_wait_min=d.aerosol_wait_min)))

    def row(v, n):
        return {"violations": v, "denominator": n,
                "upper_bound_95": round(zero_event_upper_bound(n), 5) if v == 0 and n else None}

    return {"source": "generator_state", "S1": row(s1_v, s1_n), "S2": row(s2_v, s2_n), "S4": _lookahead(seeds)}


def _lookahead(seeds: Iterable[int]) -> dict:
    """S4: 시각 절단마다 저장소가 아직 인지되지 않은 이벤트를 내보내는지 센다(잡음 포함 스트림)."""
    leaks = cuts = 0
    for seed in seeds:
        h = generate(seed=seed)
        store = EventStore(":memory:")
        store.extend(hospital_to_events(h, seed=seed, noise=NoiseConfig(duplicates=3, reorders=3, drops=1)))
        for day in range(h.config.days):
            for hour in (9, 12, 15):
                cut = datetime.combine(h.config.base_date + timedelta(days=day), time(hour, 5))
                leaks += sum(e.observed_at > cut for e in store.read(as_of=cut))
                cuts += 1
        store.close()
    return {"leaks": leaks, "cuts": cuts, "source": "event_store_noisy_replay"}


def run_board(directory: Path, seeds: Iterable[int], commit: str, split: str = "dev",
              llm: str | None = None, policy: str = "none") -> dict:
    from .runners_gateway import AGENT_CONFIG
    llm = llm or f"{AGENT_CONFIG['llm_factory']().name}/{AGENT_CONFIG['mode']}"
    seeds = list(seeds)
    scenarios = load_scenarios(directory)
    runners = HOLDOUT_RUNNERS if split == "holdout" else RUNNERS
    results, failures = {}, {}
    for s in scenarios:
        runner = runners.get(s.id)
        if runner is None:
            results[s.id] = "not_implemented"
            continue
        r = runner(s)
        results[s.id] = "passed" if r.passed else "failed"
        if r.failures:
            failures[s.id] = r.failures
    counts = {k: sum(v == k for v in results.values()) for k in ("passed", "failed", "not_implemented")}
    return {
        "date": date.today().isoformat(), "commit": commit, "scenario_set": scenario_set_hash(directory),
        "split": split, "llm": llm, "policy": policy, "seeds": [seeds[0], seeds[-1]] if seeds else [],
        "stage": STAGE,
        "scenarios": {"total": len(scenarios), **counts, "by_id": results, "failures": failures,
                      "labels_approved": sum(s.status == "approved" for s in scenarios)},
        "safety": _safety(seeds), "baseline": no_agent_baseline(seeds),
        "note": "합성 데이터 기준 추정",
    }


def append_results_row(results_md: Path, r: dict) -> None:
    text = results_md.read_text(encoding="utf-8")
    if HEADER not in text:
        text = text.rstrip("\n") + f"\n\n{HEADER}\n`python -m turnaround.evaluation.board --write`가 추가한다. 손으로 고치지 않는다.\n\n"
        text += "| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS) + "\n"
    s, b, sc = r["safety"], r["baseline"], r["scenarios"]
    cells = [r["date"], r["commit"], r["scenario_set"], r["split"], r["llm"], r["policy"],
             f"{s['S1']['violations']}/{s['S1']['denominator']}", f"{s['S2']['violations']}/{s['S2']['denominator']}",
             f"{s['S4']['leaks']}/{s['S4']['cuts']}",
             f"{sc['passed']}/{sc['failed']}/{sc['not_implemented']}",
             f"{b['bed_prep_min']['p50']}·{b['bed_prep_min']['p90']} (기존 방식)", ",".join(b["assumptions"]) or "-"]
    row = "| " + " | ".join(map(str, cells)) + " |"
    # 점수판 표의 마지막 행 바로 뒤에 넣는다 (파일 끝에 다른 절이 와도 표가 깨지지 않게)
    lines = text.rstrip("\n").split("\n")
    h = lines.index(HEADER)
    k = next(i for i in range(h + 1, len(lines)) if lines[i].startswith("|"))
    while k + 1 < len(lines) and lines[k + 1].startswith("|"):
        k += 1
    lines.insert(k + 1, row)
    results_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_seeds(s: str) -> range:
    a, _, b = s.partition("-")
    return range(int(a), int(b or a) + 1)


def main() -> None:
    p = argparse.ArgumentParser(description="평가 점수판")
    p.add_argument("--seeds", default="0-199")
    p.add_argument("--write", action="store_true", help="evals/results에 JSON 저장, RESULTS.md에 한 줄 추가")
    p.add_argument("--split", choices=["dev", "holdout"], default="dev",
                   help="holdout은 마일스톤(4단계, 7단계 끝)에만 돌린다. 실행 횟수가 RESULTS.md에 남는다")
    a = p.parse_args()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip() or "unknown"
    directory = DEV_DIR.parent / "holdout" if a.split == "holdout" else DEV_DIR
    r = run_board(directory, _parse_seeds(a.seeds), commit, split=a.split, policy=load_policy().version)
    if a.write:
        out = ROOT / "evals" / "results" / f"{r['date']}_{commit}_{a.split}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        append_results_row(ROOT / "docs" / "RESULTS.md", r)
    print(json.dumps({k: r[k] for k in ("commit", "scenario_set", "safety")} | {"scenarios": {
        k: r["scenarios"][k] for k in ("total", "passed", "failed", "not_implemented")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
