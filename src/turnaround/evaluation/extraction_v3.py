"""추출 평가 세트 v3 초안 (docs/검토/라벨절차_검수결과.md 2절 4항의 형식 확장).

v2에서 빠진 메모 형식을 넣는다. 현장 방식과 무관한 것부터 만든다:
- 날짜 넘김: 전날 저녁에 받은 "내일 11시" (상대 날짜 U3: guardian_eta_day)
- 근무조별 기록 시각이 붙은 여러 줄 메모, 정정·번복("2시 → 3시로 변경")
- 부정 표현("오늘 못 오신대요"), 표기 다양화(14시, PM 2시, 오후 2시)
- 메모 주체(U5): 1인실 희망은 입원 대기 메모에만
- 검수에서 고친 라벨 규칙: 휠체어 택시·사설 구급차 → 이동 보조 필요, "보호자 연락 안 됨" → 지연 사유 보호자

지연 사유 코드(U2)와 범위 표현 처리(U4)는 현직자 설문 뒤 확정한다. 코드는 정책 파일에서 읽고,
범위 표현 문항에는 "u4_pending" 표시를 붙여 나중에 정답을 다시 정할 수 있게 한다.
상태는 모두 draft. 생성: python -m turnaround.evaluation.extraction_v3
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evals" / "model_eval" / "extraction_v3_draft.jsonl"

WHO = ["보호자", "따님", "아드님", "배우자", "보호자분", "간병인"]
SHIFT_AT = {"D": (9, 30), "E": (19, 30), "N": (23, 40)}
DELAY_TEXT = {"guardian": ["보호자 연락 안 됨", "보호자 차가 막혀 늦는다고 함", "보호자 오늘 못 오신대요"],
              "billing": ["수납 창구 대기 김", "진료비 심사 아직"], "medication": ["퇴원약 아직 안 올라옴", "약 재조제 중"],
              "transport": ["사설 구급차 배차 늦어짐", "휠체어 택시 예약 시간 밀림"]}


def _fmt_time(rng: random.Random, h: int, m: int) -> str:
    pm = h >= 12
    h12 = h - 12 if h > 12 else h
    forms = [f"{h}시" + (f" {m}분" if m else ""), f"{'PM' if pm else 'AM'} {h12}시" + (" 반" if m == 30 else ""),
             f"{'오후' if pm else '오전'} {h12}시" + (f" {m}분" if m else ""), f"{h:02d}:{m:02d}"]
    if 1 <= h12 <= 7 and pm:
        forms.append(f"{h12}시" + (" 반" if m == 30 else ""))  # 생략형: 1~7시는 오후로 본다(설계 가정)
    return rng.choice(forms)


def _item(rng: random.Random, i: int, codes: list[str]) -> dict:
    kind = rng.choice(["date_cross", "correction", "negation", "plain", "shift_lines", "admission", "range"])
    gold: dict = {}
    flags: list[str] = []
    case_kind = "discharge"
    ref = datetime(2026, 3, 2, 9, 30)
    if kind == "date_cross":
        ref = datetime(2026, 3, 2, *SHIFT_AT["E"])
        h, m = rng.choice([10, 11, 13, 14]), rng.choice([0, 30])
        text = f"{rng.choice(WHO)} 내일 {_fmt_time(rng, h, m)} 도착 예정"
        gold.update(guardian_eta=f"{h:02d}:{m:02d}", guardian_eta_day=1)
    elif kind == "correction":
        h1, h2 = rng.sample([13, 14, 15, 16], 2)
        text = rng.choice([f"보호자 {h1 - 12}시 도착 예정 → {h2 - 12}시로 변경", f"{h1}시 온다고 했다가 {h2}시로 늦춤",
                           f"보호자 {h1 - 12}시 예정이었으나 정정: {h2 - 12}시"])
        gold.update(guardian_eta=f"{h2:02d}:00")
    elif kind == "negation":
        text = rng.choice(["보호자 오늘 못 오신대요, 내일 다시 연락 주기로", "보호자 연락 안 됨", "오늘 보호자 내원 어렵다고 함"])
        if "guardian" in codes:
            gold["delay_reason"] = "guardian"
    elif kind == "shift_lines":
        ref = datetime(2026, 3, 3, *SHIFT_AT["D"])
        h = rng.choice([10, 11, 14])
        lines = [f"[E 19:30] 보호자 내일 {_fmt_time(rng, h, 0)} 온다고 함",
                 f"[N 02:10] 특이사항 없음", f"[D 08:20] 보호자 통화, 예정대로 오신다고 함"]
        text = " / ".join(lines)
        gold.update(guardian_eta=f"{h:02d}:00")  # D 근무 기준(ref)으로는 오늘
        flags.append("multi_author")
    elif kind == "admission":
        case_kind = "admission"
        who = rng.choice(["환자분", "보호자", "따님", "배우자"])
        wish = rng.random() < 0.5
        text = rng.choice([f"입원 예정, {who}이(가) 가능하면 1인실 원하심(차액 설명함)", f"{who} 1인실 자리 나면 옮기고 싶다고 함",
                           f"입원 대기, {who} 1인실 희망, 없으면 2인실도 괜찮다고"]) if wish else rng.choice([
                           f"입원 대기, {who} 다인실 괜찮다고 함", "MRSA 이력으로 격리실 필요, 1인실 희망 아님",
                           f"{who} 비용 때문에 다인실 원함", "입원 예정, 특이사항 없음"])
        if wish:
            gold["single_room_wish"] = True
    elif kind == "range":
        h = rng.choice([1, 2, 3])
        text = f"보호자 {h}~{h + 1}시 사이 도착"
        flags.append("u4_pending")  # 범위 표현: 지금은 시각 없음, 늦은 쪽 기록 여부는 설문 뒤
    else:
        h, m = rng.choice([13, 14, 15, 16]), rng.choice([0, 30])
        text = f"{rng.choice(WHO)} {_fmt_time(rng, h, m)} 도착 예정"
        gold.update(guardian_eta=f"{h:02d}:{m:02d}")
    if case_kind == "discharge" and rng.random() < 0.3:
        code = rng.choice([c for c in codes if c in DELAY_TEXT] or ["guardian"])
        d = rng.choice(DELAY_TEXT[code])
        text += f", {d}"
        if "delay_reason" not in gold:
            gold["delay_reason"] = code
        elif gold["delay_reason"] != code:
            gold["delay_reason_allowed"] = sorted({gold["delay_reason"], code})  # 다중 사유: 둘 다 정답
            flags.append("multi_reason")
        if "휠체어 택시" in d:
            gold["mobility_aid"] = True  # 검수 결과: 휠체어 택시는 이동 보조 필요
    if case_kind == "discharge" and rng.random() < 0.2:
        text += ", " + rng.choice(["휠체어 필요", "부축 필요", "w/c 이동"])
        gold["mobility_aid"] = True
    return {"id": f"V3-{i:03d}", "text": text, "case_kind": case_kind, "reference_time": ref.isoformat(timespec="minutes"),
            "form": kind, "flags": flags, "gold": gold, "generator": "template-v3", "status": "draft"}


def build(n: int = 200, seed: int = 303) -> list[dict]:
    from turnaround.gateway import load_policy
    codes = load_policy().extraction.delay_codes
    rng = random.Random(seed)
    items, seen = [], set()
    i = 0
    while len(items) < n:
        it = _item(rng, i + 1, codes)
        i += 1
        if it["text"] in seen:
            continue  # 같은 문장은 넣지 않는다 (v2 검수: 중복 25문항)
        seen.add(it["text"])
        it["id"] = f"V3-{len(items) + 1:03d}"
        items.append(it)
    return items


if __name__ == "__main__":
    items = build()
    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")
    from collections import Counter
    print(len(items), Counter(x["form"] for x in items))
