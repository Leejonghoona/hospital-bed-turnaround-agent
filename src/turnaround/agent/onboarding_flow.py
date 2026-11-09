"""온보딩 흐름 (08 계획 3단계): 병원 내보내기 업로드부터 승인된 매핑·프로필 버전까지.

업로드 → 매핑 초안 → 질문 묶음(구조 → 형식 → 열 → 코드) → 답 → 값 풀이 검사(2차 코드 질문) → 매핑 확정
→ 절차 추론(M4 프로필 초안) → 재생 증명(사건 순서 위반 0, 하루 재생 안전 위반 0, 배정 발생) → 승인 → 버전 고정
→ 운영 중 변화 감지(새 열·사라진 열·새 코드·시각 형식 깨짐·새 사건 코드) → 재질문 → 새 버전(다시 승인).

묶음 순서의 이유: 구조(병상 표)를 알아야 열을 고를 수 있고, 형식(시각)을 알아야 사건 순서 검사로 애매한 열을 스스로
가릴 수 있으며, 열을 알아야 코드값을 어느 어휘로 읽을지 정해진다. 앞 묶음의 답은 초안을 다시 만드는 데 쓴다.
판정은 모두 결정적 코드다(CLAUDE.md 2번). LLM은 후보만 낸다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from types import SimpleNamespace

from .mapping_agent import IGNORE, MappingDraft, Question, _order_rel, answer, followup_questions, propose_mapping

STAGES = ("structure", "format", "column", "code")


class OnboardingError(RuntimeError):
    pass


def _stage(qid: str) -> str:
    if qid in ("layout", "bed_parts") or qid.startswith(("table:", "gone:")):
        return "structure"
    if qid.startswith("fmt:"):
        return "format"
    if qid.startswith(("col:", "evt:", "fill:")):
        return "column"
    return "code"


@dataclass
class QuestionGroup:
    stage: str
    questions: list[Question]
    round: int = 1


# 매핑 버전을 승인할 수 있는 역할 (설계 가정: 의료정보팀·원무 책임자·간호부·감염관리 책임자)
APPROVER_ROLES = frozenset({"medical_informatics", "admission_lead", "nursing_admin", "ipc_lead"})
# 이 키가 매핑에 없으면 서비스가 안전하게 돌 수 없다 (격리 주의, 병실 종류)
REQUIRED_SAFETY_KEYS = ("patient.precautions", "room_type")


@dataclass(frozen=True)
class OnboardingPackage:
    hospital: str
    version: str
    mapping: object
    profile_yaml: dict
    profile_questions: list
    proof: dict
    ignored: dict
    answers: dict
    status: str = "proposed"
    approved_by: str | None = None
    parent: str | None = None
    provenance: dict = field(default_factory=dict)  # 핵심 온톨로지·변환 코드·LLM·사전 지문
    stats: dict = field(default_factory=dict)  # 승인 때 열 채움 비율 (변화 감지 기준)
    audit: tuple = ()  # 감사 기록 (만듦·승인)
    approval_sig: str | None = None

    def verify(self) -> bool:
        """지문이 내용(매핑·프로필·증명·출처·기준 통계)과 맞고, 승인 기록이 그 지문에 묶여 있는가."""
        if _version(self.mapping, self.profile_yaml, self.ignored, self.proof, self.provenance, self.stats) != self.version:
            return False
        if self.status == "approved":
            return self.approval_sig == _sig(self.version, self.approved_by, self.audit)
        return True

    def approve(self, by: str, acknowledge_profile_questions: bool = False, at: datetime | None = None
                ) -> "OnboardingPackage":
        if not by or by not in APPROVER_ROLES:
            raise OnboardingError(f"승인할 수 있는 역할이 아니다: {by!r} (허용 {sorted(APPROVER_ROLES)})")
        if not self.proof.get("passed"):
            raise OnboardingError(f"재생 증명을 통과하지 못해 승인할 수 없다: {self.proof.get('failures')}")
        if not self.verify():
            raise OnboardingError("버전 지문이 내용과 맞지 않는다(고정 뒤 바뀜)")
        if self.profile_questions and not acknowledge_profile_questions:
            raise OnboardingError(f"프로필 질문 {len(self.profile_questions)}개가 남아 있다(답하거나 남은 채 승인함을 명시)")
        when = (at or datetime.now()).isoformat(timespec="seconds")
        audit = self.audit + ({"event": "approved", "by": by, "at": when, "version": self.version,
                               "profile_questions_open": len(self.profile_questions)},)
        return replace(self, status="approved", approved_by=by, audit=audit, approval_sig=_sig(self.version, by, audit))


def _version(mapping, profile_yaml, ignored, proof=None, provenance=None, stats=None) -> str:
    blob = json.dumps({"mapping": mapping.model_dump(mode="json"), "profile": profile_yaml,
                       "ignored": {t: sorted(v) for t, v in sorted(ignored.items())}, "proof": proof or {},
                       "provenance": provenance or {}, "stats": stats or {}}, sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _sig(version, by, audit) -> str:
    blob = json.dumps({"version": version, "by": by, "audit": list(audit)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _file_sha(*paths) -> str:
    from pathlib import Path
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:12]


def _provenance(llm, lexicon) -> dict:
    from pathlib import Path
    here = Path(__file__).resolve().parent
    onto = here.parent / "ontology"
    return {"core_ttl": _file_sha(onto / "core.ttl"),
            "code": _file_sha(onto / "mapping.py", here / "mapping_agent.py", here / "onboarding_flow.py"),
            "llm": getattr(llm, "name", "fake") if llm is not None else "fake",
            "lexicon": hashlib.sha256(lexicon.model_dump_json().encode()).hexdigest()[:12] if lexicon is not None
            else None}


def _fill_stats(mapping, tables) -> dict:
    out = {}
    for t, tm in mapping.tables.items():
        rows = tables.get(t) or []
        cols = list(tm.columns) if tm.kind != "events" else []
        out[t] = {c: round(sum(1 for r in rows if r.get(c) not in ("", None)) / max(1, len(rows)), 3) for c in cols}
    return out


class OnboardingSession:
    def __init__(self, tables: dict[str, list[dict]], llm=None, hospital: str = "draft", lexicon=None,
                 data_class: str = "hospital"):
        self.tables, self.llm, self.hospital, self.lexicon = tables, llm, hospital, lexicon
        self.data_class = data_class
        self.base_required: tuple = REQUIRED_SAFETY_KEYS
        self.hints: dict = {}
        self.answers: dict[str, object] = {}
        self.asked: list[Question] = []
        self.history: list[tuple[str, int, list[str]]] = []
        self.mapping = None
        self.base_version: str | None = None
        self.base_ignored: dict = {}
        self.layout_answered = False
        self._follow: list[Question] | None = None
        self._revise = False
        self._repropose()

    # ---------- 초안 ----------

    def _repropose(self) -> None:
        self.draft = propose_mapping(self.tables, llm=self.llm, hospital=self.hospital, hints=self.hints,
                                     lexicon=self.lexicon, data_class=self.data_class)
        self.draft.questions = _dedupe(self.draft.questions)

    @classmethod
    def revise(cls, pkg: OnboardingPackage, tables: dict[str, list[dict]]) -> "OnboardingSession":
        """승인된 버전을 바탕으로 변화한 내보내기만 다시 묻는다."""
        s = cls.__new__(cls)
        s.tables, s.llm, s.hospital, s.lexicon, s.data_class = tables, None, pkg.hospital, None, "hospital"
        s.base_required = REQUIRED_SAFETY_KEYS
        s.base_pairs = {(t, k) for t, tm in pkg.mapping.tables.items() for k in tm.columns.values()
                        if k in REQUIRED_SAFETY_KEYS}
        s.hints, s.answers, s.asked, s.history = {}, {}, [], []
        s.mapping, s.base_version, s.base_ignored = None, pkg.version, {t: set(v) for t, v in pkg.ignored.items()}
        s.layout_answered, s._follow, s._revise = False, None, True
        s.draft = MappingDraft(mapping=pkg.mapping, questions=detect_drift(pkg, tables), evidence={"drift": True},
                               columns={})
        return s

    # ---------- 질문 ----------

    def _pending(self) -> list[Question]:
        qs = self.draft.questions if self._follow is None else self._follow
        return [q for q in qs if q.id not in self.answers]

    def groups(self) -> list[QuestionGroup]:
        by: dict[str, list[Question]] = {}
        for q in self._pending():
            by.setdefault(_stage(q.id), []).append(q)
        rnd = 1 if self._follow is None else 2
        return [QuestionGroup(st, by[st], rnd) for st in STAGES if st in by]

    def open_questions(self) -> int:
        """제안 없는 질문 수(사람이 답을 찾아야 하는 질문). 제안 확인은 세지 않는다."""
        return sum(1 for g in self.groups() for q in g.questions if q.proposal is None)

    def current(self) -> QuestionGroup | None:
        gs = self.groups()
        if gs:
            return gs[0]
        if self._follow is None:
            m = answer(self.draft, self.answers)
            seen = set(self.answers)
            self._follow = [q for q in _dedupe(followup_questions(m, self.tables)) if q.id not in seen]
            self._premap = m
            gs = self.groups()
            if gs:
                return gs[0]
        if self.mapping is None:
            base = self._premap
            if self._follow:
                base = answer(MappingDraft(mapping=base, questions=self._follow, evidence={}, columns={}), self.answers)
            # 사라진 열: 대신할 열 이름을 답하면 옮기고, 아니면 매핑에서 뺀다(빈 열을 매핑에 남기지 않는다)
            tables = dict(base.tables)
            for qid, a in self.answers.items():
                if qid.startswith("gone:") and qid.count(":") == 2:
                    _, t, c = qid.split(":", 2)
                    if t in tables and c in tables[t].columns:
                        cols = dict(tables[t].columns)
                        key = cols.pop(c)
                        if a and a != IGNORE and isinstance(a, str) and a in {k for r in self.tables.get(t, []) for k in r}:
                            cols[a] = key
                        tables[t] = tables[t].model_copy(update={"columns": cols})
            self.mapping = base.model_copy(update={"tables": tables})
        return None

    @property
    def done(self) -> bool:
        return self.current() is None

    def submit(self, answers: dict[str, object]) -> None:
        """지금 묶음의 답을 저장한다. 일부만 답해도 저장되고, 묶음이 다 끝나야 다음 묶음으로 간다(부분 저장).
        제안을 확인하는 질문(안전 코드·관례 추정 등)과 형식 질문에 None은 '모름'이지 승인이 아니다: 답하지 않은 것으로 남긴다."""
        g = self.current()
        if g is None:
            raise OnboardingError("더 물을 것이 없다")
        took = []
        for q in g.questions:
            if q.id not in answers:
                continue
            a = answers[q.id]
            if a is None and (q.id.startswith(("fmt:", "fill:")) or q.id in ("layout", "bed_parts") or
                              (q.id.startswith("code:") and q.proposal is not None)):
                continue
            if q.id.startswith("col:") and a is None:
                a = IGNORE
            self.answers[q.id] = a
            self.asked.append(q)
            took.append(q.id)
        self.history.append((g.stage, g.round, sorted(took)))
        if self._revise or any(q.id not in self.answers for q in g.questions):
            return
        if g.stage == "structure":
            lay = self.answers.get("layout")
            if lay:
                self.hints["layout"] = (lay[0], tuple(lay[1]))
            if self.answers.get("bed_parts"):
                t = next(t for t, tm in self.draft.mapping.tables.items() if tm.kind == "layout")
                self.hints["layout"] = (t, tuple(self.answers["bed_parts"]))
            self.layout_answered = True
            self._repropose()
        elif g.stage == "format":
            for qid, hk in (("fmt:time", "time_format"), ("fmt:sep", "list_sep"), ("fmt:bool", "bool_values")):
                if self.answers.get(qid):
                    self.hints[hk] = self.answers[qid]
            self._repropose()

    def import_code_master(self, codes: dict[str, dict[str, str]]) -> int:
        """병원 코드표(정규 키 → {원천값: 정규값})를 한 번에 넣어 남은 코드 질문에 답한다 (3단계 의료정보 검수 A-3).
        안전 코드도 코드표가 곧 사람의 확인이다(코드표를 넣은 사람이 책임). 답한 질문 수를 돌려준다."""
        n = 0
        while (g := self.current()) is not None and g.stage == "code":
            got = {}
            for q in g.questions:
                _, k, v = q.id.split(":", 2)
                if v in codes.get(k, {}):
                    got[q.id] = codes[k][v]
            if not got:
                break
            self.submit(got)
            n += len(got)
        return n

    def import_column_dictionary(self, cols: dict[tuple[str, str], str | None]) -> int:
        """테이블 정의서((표, 열) → 정규 키, 쓰지 않으면 None)를 한 번에 넣는다."""
        g = self.current()
        if g is None or g.stage != "column":
            return 0
        got = {q.id: cols[tuple(q.id.split(":", 2)[1:])] for q in g.questions
               if q.id.startswith("col:") and tuple(q.id.split(":", 2)[1:]) in cols}
        if got:
            self.submit(got)
        return len(got)

    def ignored(self) -> dict[str, set]:
        out = {t: set(v) for t, v in self.base_ignored.items()}
        for qid, a in self.answers.items():
            if qid.startswith("col:") and a == IGNORE:
                _, t, c = qid.split(":", 2)
                out.setdefault(t, set()).add(c)
        return out

    # ---------- 확정 ----------

    def finalize(self, day: int = 3, seed: int = 0, until: time = time(23, 45), policy=None) -> OnboardingPackage:
        if not self.done:
            raise OnboardingError("답하지 않은 질문이 남아 있다")
        proof, profile_yaml, pq = replay_proof(self.mapping, self.tables, day=day, seed=seed, until=until, policy=policy,
                                               required=self.base_required)
        # 승인된 버전에서 표마다 있던 안전 속성 열이 빠지면 증명 실패 (3단계 의료정보 검수 A-2)
        now_pairs = {(t, k) for t, tm in self.mapping.tables.items() for k in tm.columns.values()}
        lost = sorted(getattr(self, "base_pairs", set()) - now_pairs)
        if lost:
            proof = {**proof, "passed": False,
                     "failures": proof["failures"] + [f"안전 속성 열이 빠졌다: {t}.{k}" for t, k in lost]}
        ignored = self.ignored()
        frozen_ignored = {t: tuple(sorted(v)) for t, v in ignored.items()}
        prov = _provenance(self.llm, self.lexicon)
        stats = _fill_stats(self.mapping, self.tables)
        version = _version(self.mapping, profile_yaml, frozen_ignored, proof, prov, stats)
        audit = ({"event": "proposed", "at": datetime.now().isoformat(timespec="seconds"), "version": version,
                  "parent": self.base_version, "answers": len(self.answers), "llm": prov["llm"]},)
        return OnboardingPackage(hospital=self.hospital, version=version, mapping=self.mapping, profile_yaml=profile_yaml,
                                 profile_questions=pq, proof=proof, ignored=frozen_ignored, answers=dict(self.answers),
                                 parent=self.base_version, provenance=prov, stats=stats, audit=audit)


def _dedupe(qs: list[Question]) -> list[Question]:
    seen, out = set(), []
    for q in qs:
        if q.id not in seen:
            seen.add(q.id)
            out.append(q)
    return out


# ---------------- 재생 증명 ----------------

def _plausibility(mapped) -> list[str]:
    """정답 없이 볼 수 있는 속성 사이 개연성 (3단계 온톨로지 검수 B-2: 뜻이 뒤바뀐 코드표를 잡는다). 기준은 설계 가정."""
    out = []
    bad_rooms = [r.id for r in mapped.rooms if (r.room_type.value == "multi") != (r.capacity >= 2)]
    if bad_rooms:
        out.append(f"병실 종류와 정원이 맞지 않는다 {len(bad_rooms)}실(다인실은 2인 이상, 1인실·음압실은 1인)")
    room = {r.id: r for r in mapped.rooms}
    bed_room = {b.id: room.get(b.room_id) for b in mapped.beds}
    n = bad = air = air_np = 0
    for e in mapped.events:
        if e.type.value != "discharge_decided":
            continue
        r = bed_room.get(e.payload.get("bed_id"))
        if r is None:
            continue
        g = getattr(r.gender, "value", r.gender)
        if g is not None and e.payload.get("gender"):
            n += 1
            bad += e.payload["gender"] != g
        if "airborne" in (e.payload.get("precautions") or []):
            air += 1
            air_np += r.room_type.value == "negative_pressure"
    if n and bad / n > 0.05:
        out.append(f"성별 병실에 다른 성별 환자 {bad}/{n}건(성별 코드표가 뒤바뀌었을 수 있다)")
    if air and air_np * 2 < air:
        out.append(f"공기주의 퇴원 {air}건 중 음압실 {air_np}건(격리 코드표가 뒤바뀌었을 수 있다)")
    return out


def replay_proof(mapping, tables, day: int = 3, seed: int = 0, until: time = time(23, 45), policy=None,
                 required=REQUIRED_SAFETY_KEYS):
    """매핑한 결과만으로 서비스를 하루 돌려 본다. 정답이 없는 새 병원에서도 쓸 수 있는 검사만 쓴다.
    재생의 사람 응답(원무 연락·병동 준비 등)은 합성 모형이다. (증명, 프로필 초안, 프로필 질문)"""
    from turnaround.agent.onboarding import infer_profile
    from turnaround.evaluation.safety_exec import executed_safety
    from turnaround.events import EventType
    from turnaround.gateway import load_policy
    from turnaround.ontology.mapping import apply_mapping
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    failures: list[str] = []
    try:
        mapped = apply_mapping(mapping, tables)
    except Exception as e:  # noqa: BLE001 - 변환 실패는 증명 실패로 기록
        return ({"passed": False, "failures": [f"변환 실패: {type(e).__name__}: {str(e)[:120]}"], "order_violations": None,
                 "safety_violations": None, "commits": 0, "plausibility": []}, {}, [])
    mapped_keys = {k for tm in mapping.tables.values() for k in tm.columns.values()}
    for k in required:
        if k not in mapped_keys:
            failures.append(f"안전 속성 열이 매핑에 없다: {k}")
    plaus = _plausibility(mapped)
    failures += plaus
    first: dict[tuple[str, str], datetime] = {}
    for e in mapped.events:
        k = (e.case_id, e.type.value)
        if k not in first or e.occurred_at < first[k]:
            first[k] = e.occurred_at
    order = 0
    cases = {c for c, _ in first}
    for a, b in _order_rel():
        for c in cases:
            ta, tb = first.get((c, a)), first.get((c, b))
            if ta and tb and ta > tb:
                order += 1
    if order:
        failures.append(f"사건 순서 위반 {order}건")
    st = build_demo(seed=seed, day=day, until=until, via_fhir=False, policy=policy or load_policy(profile="field_default"),
                    config=GeneratorConfig.realistic(), mapped=mapped)
    out = st.store.read()
    commits = sum(1 for e in out if e.type is EventType.MATCH_COMMITTED)
    s = executed_safety(st.gw, out)
    safety = sum(s[k]["violations"] for k in ("S1", "S2", "S3", "S4"))
    if safety:
        failures.append(f"안전 위반 {safety}건")
    if not commits:
        failures.append("하루 재생에서 배정이 하나도 없다")
    prof = infer_profile(list(mapped.events))
    proof = {"passed": not failures, "failures": failures, "order_violations": order, "safety_violations": safety,
             "plausibility": plaus,
             "commits": commits, "events": len(mapped.events), "beds": len(mapped.beds), "day": day}
    return proof, prof.profile_yaml(), prof.questions


# ---------------- 변화 감지 ----------------

def detect_drift(pkg: OnboardingPackage, tables: dict[str, list[dict]]) -> list[Question]:
    """승인된 매핑과 새 내보내기를 견준다. 결정적 검사만 쓴다."""
    m = pkg.mapping
    qs: list[Question] = []
    for t in tables:
        if t not in m.tables:
            qs.append(Question(f"table:{t}", f"표 {t}", "승인된 매핑에 없는 새 표다"))
    time_bad: list[str] = []
    for t, tm in m.tables.items():
        rows = tables.get(t)
        if rows is None:
            qs.append(Question(f"gone:{t}", f"표 {t}", "매핑한 표가 내보내기에서 사라졌다"))
            continue
        keys = {c for r in rows for c in r}
        filled = {c for r in rows for c, v in r.items() if v not in ("", None)}
        if tm.kind == "events":
            cc, kc, tc = tm.event_columns
            for code in sorted({r.get(kc) for r in rows} - set(tm.event_codes) - {None, ""}):
                qs.append(Question(f"evt:{code}", f"사건 코드 {code}", "승인된 매핑에 없는 새 사건 코드다"))
            known = {cc, kc, tc}
            time_cols = [tc]
        else:
            known = set(tm.columns) | set(m.bed_parts)  # 병상 ID 조합 열은 어느 표에서나 쓴다
            time_cols = [c for c, k in tm.columns.items() if k.startswith("t:") or k.split(".")[-1] in ("eta", "requested_at")]
            for c in sorted(set(tm.columns) - keys):
                qs.append(Question(f"gone:{t}:{c}", f"{t}.{c}", f"매핑한 열이 사라졌다({tm.columns[c]})"))
        for c in sorted(filled - known - set(pkg.ignored.get(t, ()))):
            qs.append(Question(f"col:{t}:{c}", f"{t}.{c}", "승인된 매핑에 없는 새 열이다"))
        for c in time_cols:
            for r in rows:
                v = r.get(c)
                if v:
                    try:
                        datetime.strptime(v, m.time_format)
                    except ValueError:
                        time_bad.append(str(v))
                        break
    from turnaround.ontology.mapping import _BOOLS
    bool_bad = sorted({str(r.get(c)) for t, tm in m.tables.items() for c, k in tm.columns.items()
                       if k.split(".")[-1] in _BOOLS for r in tables.get(t, []) if r.get(c) not in ("", None)}
                      - set(m.bool_values))
    if bool_bad:  # 참거짓 표기가 바뀌면 안전 플래그가 조용히 거짓이 된다 (3단계 의료정보 검수 A-1)
        qs.append(Question("fmt:bool", "참거짓 표기", f"승인된 표기 {tuple(m.bool_values)} 밖의 값이 있다: {bool_bad[:4]}",
                           None, bool_bad[:4]))
    for t, cols in (pkg.stats or {}).items():
        rows = tables.get(t) or []
        for c, base in cols.items():
            if not rows or not any(c in r for r in rows):
                continue
            now_fill = sum(1 for r in rows if r.get(c) not in ("", None)) / len(rows)
            if base >= 0.05 and now_fill < base * 0.2:  # 채움 비율이 크게 떨어졌다 (설계 가정 기준)
                qs.append(Question(f"fill:{t}:{c}", f"{t}.{c}", f"채움 비율이 {base:.0%} → {now_fill:.0%}로 떨어졌다"
                                   f"({m.tables[t].columns.get(c)}). 정말 비었는지 확인(답: confirmed_empty)"))
    if time_bad:
        qs.append(Question("fmt:time", "시각 형식", f"승인된 형식({m.time_format})으로 읽히지 않는 시각이 있다",
                           m.time_format, time_bad[:3]))
    qs += followup_questions(m, tables)
    return _dedupe(qs)


# ---------------- 평가용: 정답으로 답하는 사람 ----------------

def oracle(gold, group: QuestionGroup) -> dict:
    """정답 매핑으로 답한다(평가·데모용 사람 모형). 정답에 없는 열은 '쓰지 않음'."""
    from turnaround.evaluation.mapping_eval import gold_answers
    out = gold_answers(SimpleNamespace(questions=group.questions), gold)
    lay = next((t for t, tm in gold.tables.items() if tm.kind == "layout"), None)
    for q in group.questions:
        if q.id == "layout":
            out[q.id] = (lay, tuple(gold.bed_parts))
        elif q.id == "bed_parts":
            out[q.id] = tuple(gold.bed_parts)
        elif q.id not in out:
            out[q.id] = None
    return out


__all__ = ["APPROVER_ROLES", "REQUIRED_SAFETY_KEYS", "STAGES", "OnboardingError", "OnboardingPackage", "OnboardingSession", "QuestionGroup", "detect_drift",
           "oracle", "replay_proof"]
