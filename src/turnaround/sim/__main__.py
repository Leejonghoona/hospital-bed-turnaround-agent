"""합성 데이터를 JSON으로 저장한다.

    python -m turnaround.sim --seed 42 --out data/synthetic_seed42.json
"""

import argparse
from pathlib import Path

from .generator import generate


def main() -> None:
    p = argparse.ArgumentParser(description="합성 병원 데이터 생성 (가상 데이터 전용)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=None, help="저장 경로. 없으면 요약만 출력")
    a = p.parse_args()
    h = generate(seed=a.seed)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(h.model_dump_json(indent=2), encoding="utf-8")
    print(f"seed={h.seed} 병동 {len(h.wards)} 병상 {len(h.beds)} 퇴원 {len(h.discharges)} 입원 대기 {len(h.admissions)}"
          + (f" -> {a.out}" if a.out else ""))


if __name__ == "__main__":
    main()
