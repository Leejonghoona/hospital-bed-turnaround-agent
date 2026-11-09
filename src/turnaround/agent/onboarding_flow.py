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
    if qid.startswith(("col:", "evt:")):
        return "column"
    return "code"


@dataclass
class QuestionGroup:
    stage: str
    questions: list[Question]
    round: int = 1


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

    def verify(self) -> bool:
        return _version(self.mapping, self.profile_yaml, self.ignored) == self.version

    def approve(self, by: str) -> "OnboardingPackage":
        if not self.proof.get("passed"):
            raise OnboardingError(f"재생 증명을 통과하지 못해 승인할 수 없다: {self.proof.get('failures')}")
        if not self.verify():
            raise OnboardingError("버전 지문이 내용과 맞지 않는다(고정 뒤 바뀜)")
        return replace(self, status="approved", approved_by=by)


def _version(mapping, profile_yaml, ignored) -> str:
    blob = json.dumps({"mapping": mapping.model_dump(mode="json"), "profile": profile_yaml,
                       "ignored": {t: sorted(v) for t, v in sorted(ignored.items())}}, sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class OnboardingSession:
    def __init__(self, tables: dict[str, list[dict]], llm=None, hospital: str = "draft", lexicon=None):
        self.tables, self.llm, self.hospital, self.lexicon = tables, llm, hospital, lexicon
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
                                     lexicon=self.lexicon)
        self.draft.questions = _dedupe(self.draft.questions)

    @classmethod
    def revise(cls, pkg: OnboardingPackage, tables: dict[str, list[dict]]) -> "OnboardingSession":
        """승인된 버전을 바탕으로 변화한 내보내기만 다시 묻는다."""
        s = cls.__new__(cls)
        s.tables, s.llm, s.hospital, s.lexicon = tables, None, pkg.hospital, None
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
            self.mapping = base
        return None

    @property
    def done(self) -> bool:
        return self.current() is None

    def submit(self, answers: dict[str, object]) -> None:
        g = self.current()
        if g is None:
            raise OnboardingError("더 물을 것이 없다")
        ids = {q.id for q in g.questions}
        missing = ids - set(answers)
        if missing:
            raise OnboardingError(f"이 묶음의 질문에 모두 답해야 다음으로 간다: {sorted(missing)[:3]}")
        for q in g.questions:
            a = answers[q.id]
            if q.id.startswith("col:") and a is None:
                a = IGNORE
            self.answers[q.id] = a
            self.asked.append(q)
        self.history.append((g.stage, g.round, sorted(ids)))
        if self._revise:
            return
        if g.stage == "structure":
            lay = answers.get("layout")
            if lay:
                self.hints["layout"] = (lay[0], tuple(lay[1]))
            if answers.get("bed_parts"):
                t = next(t for t, tm in self.draft.mapping.tables.items() if tm.kind == "layout")
                self.hints["layout"] = (t, tuple(answers["bed_parts"]))
            self.layout_answered = True
            self._repropose()
        elif g.stage == "format":
            for qid, hk in (("fmt:time", "time_format"), ("fmt:sep", "list_sep"), ("fmt:bool", "bool_values")):
                if answers.get(qid):
                    self.hints[hk] = answers[qid]
            self._repropose()

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
        proof, profile_yaml, pq = replay_proof(self.mapping, self.tables, day=day, seed=seed, until=until, policy=policy)
        ignored = self.ignored()
        frozen_ignored = {t: tuple(sorted(v)) for t, v in ignored.items()}
        return OnboardingPackage(hospital=self.hospital, version=_version(self.mapping, profile_yaml, frozen_ignored),
                                 mapping=self.mapping, profile_yaml=profile_yaml, profile_questions=pq, proof=proof,
                                 ignored=frozen_ignored, answers=dict(self.answers), parent=self.base_version)


def _dedupe(qs: list[Question]) -> list[Question]:
    seen, out = set(), []
    for q in qs:
        if q.id not in seen:
            seen.add(q.id)
            out.append(q)
    return out


# ---------------- 재생 증명 ----------------

def replay_proof(mapping, tables, day: int = 3, seed: int = 0, until: time = time(23, 45), policy=None):
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
                 "safety_violations": None, "commits": 0}, {}, [])
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


__all__ = ["STAGES", "OnboardingError", "OnboardingPackage", "OnboardingSession", "QuestionGroup", "detect_drift",
           "oracle", "replay_proof"]
