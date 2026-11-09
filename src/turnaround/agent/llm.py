"""교체 가능한 LLM 인터페이스 (AGT-1).

기본은 API 키 없이 도는 결정적 가짜 LLM이다. 실제 모델은 환경 변수가 모두 있을 때만 붙는다.
- TURNAROUND_LLM=openai | anthropic | xai | local | claudesub(구독 계정 Claude Code, 개인 평가용)
- TURNAROUND_LLM_MODEL=<모델 ID>
- TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only  (외부 API에는 합성 데이터만 보낸다는 확인. 운영 정책은 deny)
- OPENAI_API_KEY 또는 ANTHROPIC_API_KEY

LLM이 하는 일은 판단(후보 중 선택), 설명, 자유 서술 추출, 질문 해석뿐이다. 숫자 계산과 배정 규칙은 도구가 한다.
모든 호출은 JSON 하나를 돌려받는다. 형식이 틀리면 호출한 쪽이 규칙 모드로 내려간다.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests


class LLMError(RuntimeError):
    """모델 장애, 형식 오류. 호출한 쪽은 규칙 모드로 내려간다 (NFR-4)."""


class QuotaExceeded(LLMError):
    """계정 잔액·한도 소진 (HTTP 402·429 중 잔액 관련). 평가에서는 오답이 아니라 중단으로 센다."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class LLM(Protocol):
    name: str
    usage: Usage

    def complete_json(self, task: str, system: str, user: str) -> dict: ...


# ---------- 외부 전송 관문 (한 곳, 2026-11-09 최고점 루프 1회차) ----------

def guard_external(llm, data_class: str, policy=None) -> None:
    """외부 모델로 보내기 전에 꼭 지나는 관문. 정책 external_llm이 deny이면 합성 데이터만 보낸다.
    외부가 아닌 모델(가짜 LLM, 원내 모델)은 그대로 통과한다. 매핑·자유 서술 추출이 모두 이 함수를 쓴다."""
    if not getattr(llm, "external", False):
        return
    if policy is None:
        from turnaround.gateway import load_policy
        policy = load_policy()
    if data_class != "synthetic" and policy.external_llm == "deny":
        raise PermissionError("정책 external_llm=deny: 병원 데이터는 외부 모델로 보내지 않는다(원내 모델이나 가짜 LLM을 쓴다)")


_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-\s.]?\d{3,4}(?:[-\s.]?\d{4})?(?!\d)")
_LONG_NO = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_HONORIFIC_NAME = re.compile(r"([가-힣]{2,4})(?=\s*(?:님|씨|환자|보호자분|어르신))")
# 호칭 앞에 와도 사람 이름이 아닌 말 (설계 가정)
_ROLE_WORDS = {"보호자", "환자", "간병인", "간호사", "선생", "기사", "기사님", "담당", "담당자", "교수", "의사", "주치의",
               "어머", "아버", "어머니", "아버지", "따", "아드", "할머", "할아버", "할머니", "할아버지", "고객", "가족", "병동",
               "원무", "수납", "직원", "여사", "사모"}


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_INTL_PHONE = re.compile(r"(?<!\d)\+?82[-\s.]?(?:\(0\))?0?1\d[-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
# 진단 낱말·코드 (민감정보). 감염관리 낱말(VRE·CRE·결핵 등)은 격리 판단에 쓰는 업무 정보라 가리지 않는다 (설계 가정)
_DIAGNOSIS = re.compile(r"(?<![A-Za-z0-9])(?:HIV|AIDS|B2[0-4]|F2[05]|F3[1-3])(?:\.\d{1,2})?(?![A-Za-z0-9])"
                        r"|에이즈|조현병|정신분열(?:증|병)?|우울증|양극성|조울증", re.I)
_ROLE_BEFORE = re.compile(r"(?<![가-힣])(?:보호자|환자|이름|성명)[은는이가]?(?:\s*[:：]\s*|\s+)([가-힣]+)")
_ROLE_AFTER = re.compile(r"([가-힣]{2,4})(\s*[(（]\s*(?:보호자|환자|가족|배우자|아들|딸|본인|간병인)\s*[)）])")
_PARTICLES = ("에게서", "에게", "께서", "한테", "이가", "이는", "이를", "은", "는", "이", "가", "을", "를", "의", "께",
              "와", "과", "도", "에", "로", "만")
# 역할어 뒤에 와도 이름이 아닌 업무 낱말 (앞부분이 이것이면 이름으로 보지 않는다, 설계 가정)
_WORK_WORDS = ("연락", "도착", "지연", "동의", "확인", "요청", "대기", "출발", "퇴원", "입원", "이송", "상태", "정보", "없",
               "미정", "부재", "상담", "면담", "설명", "교육", "수납", "정산", "배정", "이동", "준비", "식사", "보행", "거동",
               "의식", "안정", "불안", "요구", "희망", "거부", "동반", "변경", "취소", "본인", "가족", "관련", "전화", "문의",
               "방문", "귀가", "사망", "격리", "감염", "검사", "진료", "처방", "투약", "수술", "회진", "퇴실", "입실", "침상",
               "병상", "병실", "청소", "이름", "성명", "보호자", "환자", "늦", "도움", "부축", "휠체어", "침대", "대신", "직접",
               "모두", "아직", "현재", "오늘", "내일", "어제", "오전", "오후", "다시", "이미", "미도착", "통화", "응답", "연결",
               "사정", "사유", "인계", "담당", "번호", "연락처", "주소", "메일", "이메일", "측", "쪽", "분", "용", "등록", "명단",
               "목록", "수속", "카드", "안내", "도착함", "오심", "옴", "왔", "올", "와서", "전달", "통보", "서명", "신분", "나이",
               "성별", "연령", "보험", "진단", "의뢰", "협진", "외출", "외박", "면회", "간병", "식이", "산소", "낙상", "욕창",
               "통증", "발열", "혈압", "체온", "호흡", "투석", "정맥", "주사", "수액", "약", "중", "예정", "신규", "중증", "소아",
               "고령", "해당", "다른", "같은", "기존", "응급", "외래", "재원", "전동", "일반", "내과", "외과", "병동", "모든", "간호",
               "정오", "한시", "한낮", "조금", "한참", "이따", "이후", "이번", "이상", "이전", "오늘", "저녁", "점심", "아침", "새벽",
               "내원", "검토", "비용", "차가", "기다", "곧바", "바로", "나중", "주말", "다음", "지금", "방금", "어려")


# 역할어 뒤 이름은 성씨로 시작하는 3자(복성은 4자) 낱말만 본다. 업무 문장("보호자 정오쯤", "환자는 검토 없이")을
# 가리지 않으려는 것이다. 2자 이름·드문 성씨는 놓친다(한계, 설계 가정)
_SURNAMES = set("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민나진지엄채원천방공현함"
                "변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목형피두감호")
_DOUBLE_SURNAMES = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")


def _name_shape(name: str) -> bool:
    return (len(name) == 3 and name[0] in _SURNAMES) or (len(name) == 4 and name[:2] in _DOUBLE_SURNAMES)


def _strip_particle(tok: str) -> tuple[str, str]:
    for p in _PARTICLES:
        if tok.endswith(p) and 2 <= len(tok) - len(p) <= 4:
            return tok[: -len(p)], p
    return tok, ""


def _work_word(tok: str) -> bool:
    return tok in _ROLE_WORDS or any(tok.startswith(w) for w in _WORK_WORDS if len(w) > 1) or tok in _WORK_WORDS


def _mask_honorific(m) -> str:
    tok = m.group(1)
    name, rest = _strip_particle(tok)
    if tok in _ROLE_WORDS or name in _ROLE_WORDS or _work_word(tok) or _work_word(name):
        return tok
    return "○" * len(tok)


def _mask_role_before(text: str) -> str:
    out, pos = [], 0
    i = 0
    while True:
        m = _ROLE_BEFORE.search(text, i)
        if not m:
            break
        tok = m.group(1)
        name, rest = _strip_particle(tok)
        if _name_shape(name) and not _work_word(name) and not _work_word(tok):
            out.append(text[pos:m.start(1)] + "○" * len(name) + rest)
            pos = i = m.end(1)
        else:
            i = m.start(1)  # 역할어 다음 낱말이 다시 역할어일 수 있다("환자 이름 박영수")
            if i <= m.start():
                i = m.start() + 1
    out.append(text[pos:])
    return "".join(out)


def redact_for_external(text: str) -> str:
    """외부 모델로 보내는 자유 서술에서 이메일·전화번호(국제 번호 포함)·긴 번호(등록번호 등)·진단 낱말·이름(호칭 앞,
    역할어 뒤, 괄호 역할어 앞)을 가린다. 시각·지연 사유·병실 종류·감염관리 낱말은 남긴다 (규칙은 설계 가정)."""
    text = _EMAIL.sub("[이메일]", text)
    text = _INTL_PHONE.sub("[전화]", text)
    text = _PHONE.sub("[전화]", text)
    text = _LONG_NO.sub("[번호]", text)
    text = _DIAGNOSIS.sub("[진단]", text)
    text = _HONORIFIC_NAME.sub(_mask_honorific, text)
    text = _ROLE_AFTER.sub(lambda m: (m.group(1) if _work_word(m.group(1)) else "○" * len(m.group(1))) + m.group(2),
                           text)
    return _mask_role_before(text)


def _redact_obj(obj):
    """JSON 값 중 문자열만 가린다. 키·숫자·참거짓은 그대로 둔다(형식이 깨지지 않게)."""
    if isinstance(obj, str):
        return redact_for_external(obj)
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    return obj


class GuardedLLM:
    """외부 모델을 감싸는 관문 (2026-11-09 개인정보·IT 2회차). 모든 호출의 첫 줄에서 정책을 검사하고 가린다.
    data_class 기본은 병원 데이터(fail-closed). 평가·측정 경로는 합성(synthetic)을 명시한다."""

    external = True

    def __init__(self, inner, data_class: str = "hospital", policy=None):
        if isinstance(inner, GuardedLLM):
            inner = inner.inner
        self.inner, self.data_class, self.policy = inner, data_class, policy

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def usage(self):
        return self.inner.usage

    def __getattr__(self, item):  # model, api_equiv_usd 같은 속성은 안쪽 모델 것
        if item == "inner":
            raise AttributeError(item)
        return getattr(self.inner, item)

    def __setattr__(self, key, value):  # timeout_s, reasoning_effort 같은 설정도 안쪽 모델에 건다
        if key in ("inner", "data_class", "policy"):
            object.__setattr__(self, key, value)
        else:
            setattr(self.inner, key, value)

    def complete_json(self, task: str, system: str, user: str) -> dict:
        guard_external(self, self.data_class, self.policy)
        try:
            user = json.dumps(_redact_obj(json.loads(user)), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            user = redact_for_external(user)
        return self.inner.complete_json(task, system, user)


def bind_data_class(llm, data_class: str | None = None, policy=None):
    """모델을 데이터 분류에 묶는다. 외부가 아닌 모델은 그대로. data_class가 없으면 이미 감싼 모델은 그대로 두고,
    감싸지 않은 외부 모델은 병원 데이터로 감싼다(fail-closed)."""
    if llm is None or not getattr(llm, "external", False):
        return llm
    if isinstance(llm, GuardedLLM):
        if data_class is None or (data_class == llm.data_class and policy is None):
            return llm
        return GuardedLLM(llm.inner, data_class, policy if policy is not None else llm.policy)
    return GuardedLLM(llm, data_class or "hospital", policy)


# ---------- 결정적 가짜 LLM ----------

_TIME = re.compile(r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?")


def _first_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("JSON 없음")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON 형식 오류: {e}") from e


@dataclass
class FakeLLM:
    """테스트용 결정적 LLM. 같은 입력이면 같은 출력 (NFR-2). 규칙은 단순하게 둔다."""

    name: str = "fake"
    usage: Usage = field(default_factory=Usage)
    fail: bool = False  # 장애 주입 (NFR-4 시험)

    def complete_json(self, task: str, system: str, user: str) -> dict:
        self.usage.calls += 1
        if self.fail:
            raise LLMError("가짜 장애")
        data = _first_json(user)
        if task == "choose_bed":
            cands = data.get("candidates") or []
            if not cands:
                return {"bed_id": None, "rationale": "조건에 맞는 병상 없음"}
            best = max(cands, key=lambda c: (c["score"], -cands.index(c)))
            return {"bed_id": best["bed_id"], "rationale": f"점수 1순위 {best['bed_id']} ({best['score']:.1f})"}
        if task == "choose_exception_action":
            return _fake_exception_action(json.loads(user))
        if task == "explain_choice":
            c = next((x for x in data.get("candidates", []) if x["bed_id"] == data.get("chosen_bed_id")), None)
            if c is None:
                raise LLMError("선택 병상 없음")
            parts = ", ".join(f"{k} {v:+.0f}" for k, v in (c.get("parts") or {}).items()) or "감점 없음"
            return {"rationale": f"{c['bed_id']}: 점수 {c['score']:.1f} ({parts})"}
        if task == "extract":
            return _fake_extract(data.get("text", ""), data.get("reference_time"))
        if task == "parse_question":
            return _fake_parse(data.get("question", ""))
        raise LLMError(f"모르는 작업: {task}")


def _fake_exception_action(ctx: dict) -> dict:
    """가짜 LLM의 예외 행동 결정표 (설계 개정 4.5). 같은 입력에 같은 출력. 실제 모델 평가의 비교선."""
    ex = ctx.get("exception", {})
    allowed = [a["id"] for a in ctx.get("allowed_actions", [])]
    st0 = ctx.get("case_state") or {}
    if ex.get("stage") in ("after_contact", "after_commit", "after_arrival"):
        # 목표 함수 2항: 연락·확정·입실 뒤에는 매칭을 스스로 바꾸지 않는다 (트리거와 상관없이).
        # 다시 찾기는 추천안으로 사람 승인을 받는다 (사용자 검토 XD-028). 입실 뒤는 사람에게
        if "propose_rematch" in allowed and ex.get("stage") != "after_arrival":
            return {"action": "propose_rematch", "params": {}, "rationale": "연락 뒤: 다시 찾은 추천안을 사람이 승인, 재연락은 원무"}
        return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "연락 뒤: 사람에게"}
    if (st0.get("scarce_room") or st0.get("precaution") == "airborne") and "propose_rematch" in allowed:
        # 음압실처럼 부족한 자원: 자동으로 잡지 않고 추천안으로 승인 (사용자 검토 XD-007·012)
        return {"action": "propose_rematch", "params": {}, "rationale": "부족 자원: 추천안을 사람이 승인"}
    trig, st = ex.get("trigger"), ctx.get("case_state") or {}
    if trig == "cleaning_overdue":
        # 1차: 같은 등급으로 다시 요청(독촉 1회). 2차: 기다리는 입원이 있으면 추천안(사람 승인), 없으면 병동에 올림
        if ex.get("stage") != "second" and "re_request_cleaning" in allowed:
            return {"action": "re_request_cleaning", "params": {}, "rationale": "청소 지연: 같은 등급으로 재요청"}
        if (st.get("next_admission_waiting") or st.get("waiting_matches")) and "propose_rematch" in allowed:
            return {"action": "propose_rematch", "params": {}, "rationale": "재요청 뒤에도 지연: 다른 병상 추천안을 사람 승인"}
        return {"action": "escalate", "params": {"role": "ward"}, "rationale": "재요청 뒤에도 지연: 병동에 올림"}
    if trig == "sequence_stalled":
        if ex.get("attempts", 0) == 0 and "remind_owner" in allowed:
            return {"action": "remind_owner", "params": {}, "rationale": "단계 지연: 담당에게 알림 1회"}
        return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "알림 뒤에도 지연: 올림"}
    if trig == "discharge_date_changed":
        if (ctx.get("tool_outputs") or {}).get("rematch_candidates") and "rematch_now" in allowed:
            return {"action": "rematch_now", "params": {}, "rationale": "연락 전 퇴원 연기: 규칙으로 다시 찾음"}
        return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "후보 없음: 원무에"}
    if trig == "info_changed_pending":
        if st.get("correction_direction") == "loosen" and not st.get("correction_confirmed"):
            return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "안전을 푸는 정정: 확인 먼저"}
        if "replan_now" in allowed:
            return {"action": "replan_now", "params": {}, "rationale": "정정된 값으로 다시 찾음"}
    if trig == "promise_at_risk":
        # 원무 관점 (2단계 현장 검수 H4): 퇴실 전이면 재연락, 퇴실 뒤 청소 전이면 재요청 1회, 그 뒤에도 위험이면 재연락
        if ex.get("stage") != "occupant_not_departed" and ex.get("attempts", 0) == 0:
            if not st.get("cleaning_started") and "re_request_cleaning" in allowed:
                return {"action": "re_request_cleaning", "params": {}, "rationale": "약속 위험: 청소 재요청"}
            if not st.get("isolation_discharge") and "request_bed_prep_first" in allowed:
                return {"action": "request_bed_prep_first", "params": {}, "rationale": "청소 중: 침상 정리 먼저"}
        if "request_recontact" in allowed:
            return {"action": "request_recontact", "params": {}, "rationale": "약속을 지키기 어려움: 원무 재연락"}
    if trig == "call_up_stalled":
        if ex.get("attempts", 0) == 0 and "remind_ward" in allowed:
            return {"action": "remind_ward", "params": {}, "rationale": "호출 정체: 병동에 알림 1회"}
        return {"action": "escalate", "params": {"role": "ward"}, "rationale": "알림 뒤에도 정체: 병동 책임자에게"}
    if trig == "no_bed":
        if st.get("from_icu"):
            return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "중환자실 하향: 원무에 근거 카드와 함께"}
        if "wait_for_next_ready" in allowed:
            return {"action": "wait_for_next_ready", "params": {}, "rationale": "곧 준비될 병상: 늦은 퇴원 재촉과 함께 기다림"}
    if trig == "sequence_stalled" and "remind_owner" in allowed:
        return {"action": "remind_owner", "params": {}, "rationale": "처방 없음: 병동·주치의에게 재촉"}
    if trig == "guardian_delay_same_day":
        if (st.get("next_admission_waiting") or st.get("waiting_matches")) and "offer_lounge" in allowed \
                and not st.get("isolation_discharge") and not st.get("mobility_aid"):
            return {"action": "offer_lounge", "params": {}, "rationale": "다음 입원 대기: 라운지 제안으로 병상 먼저 비움"}
        if "wait" in allowed:
            return {"action": "wait", "params": {}, "rationale": "기다리는 입원 없음: 보호자 도착까지 둠"}
    if "rematch_now" in allowed:
        return {"action": "rematch_now", "params": {}, "rationale": "연락 전: 규칙 모드로 다시 찾음"}
    return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "허용 목록 밖: 사람에게"}


def _fake_extract(text: str, ref: str | None) -> dict:
    out: dict = {}
    m = _TIME.search(text)
    if m and ("도착" in text or "온다" in text or "오신다" in text):
        hh = int(m.group(2))
        if m.group(1) == "오후" and hh < 12 or (m.group(1) is None and 1 <= hh <= 7):
            hh += 12  # "2시 도착"은 오후로 본다 (병원 낮 시간 가정)
        mm = 30 if m.group(4) else int(m.group(3) or 0)
        out["guardian_eta"] = f"{hh:02d}:{mm:02d}"
    for code, words in (("guardian", ("보호자가", "출발이 늦")), ("billing", ("수납", "원무과", "정산")),
                        ("medication", ("약",)), ("transport", ("구급차", "이송차", "택시"))):
        if any(w in text for w in words) and ("늦" in text or "지연" in text or "대기" in text or "밀림" in text):
            out.setdefault("delay_reason", code)
    if re.search(r"휠체어|부축", text):
        out["mobility_aid"] = True
    if re.search(r"1인실\s*(희망|원)", text):
        out["single_room_wish"] = True
    return out


def _fake_parse(q: str) -> dict:
    case = re.search(r"(?<![A-Za-z0-9])([A-Z]{1,3}-?[A-Z]?\d{1,4})(?![A-Za-z0-9])", q)  # 한글 조사 앞도 잡는다
    if "수술" in q and "퇴원" in q:
        return {"intent": "discharges_with_attending_surgery", "day": "tomorrow" if "내일" in q else "today",
                "period": "am" if "오전" in q else "any"}
    if "재입원" in q:
        return {"intent": "readmission_plan", "case": case.group(1) if case else None}
    if "청소" in q and case:
        return {"intent": "cleaning_status", "case": case.group(1)}
    return {"intent": "unknown"}


# ---------- 실제 모델 (환경 변수가 있을 때만) ----------

@dataclass
class _HttpLLM:
    external = True  # 국외 서버로 간다
    model: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    timeout_s: float = 60.0

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        t0 = time.monotonic()
        try:
            r = requests.post(url, headers=headers, json=body, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise LLMError(f"연결 실패: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        if r.status_code in (402, 429) and any(w in r.text.lower() for w in ("quota", "credit", "balance", "billing", "spend")):
            raise QuotaExceeded(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


@dataclass
class OpenAILLM(_HttpLLM):
    base_url: str = "https://api.openai.com/v1"
    reasoning_effort: str | None = None  # 추론 모델만 (low 등). 기본은 공급사 기본값

    def complete_json(self, task: str, system: str, user: str) -> dict:
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        if not self.model.startswith(("gpt-5", "o1", "o3", "o4")):
            body["temperature"] = 0  # 반복 일관성. 추론 모델은 온도 설정을 받지 않는다
        elif self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        res = self._post(f"{self.base_url}/chat/completions",
                         {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}, body)
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        return _first_json(res["choices"][0]["message"]["content"] or "")


@dataclass
class AnthropicLLM(_HttpLLM):
    base_url: str = "https://api.anthropic.com/v1"

    def body(self, system: str, user: str) -> dict:
        body = {"model": self.model, "max_tokens": 8192 if len(user) > 6000 else 1024,
                "system": system + "\nJSON 객체 하나만 출력한다.",
                "messages": [{"role": "user", "content": user}]}
        if re.match(r"claude-(3|[a-z]+-4)", self.model):
            body["temperature"] = 0  # 반복 일관성. Claude 5 계열은 temperature를 받지 않는다(HTTP 400)
        return body

    def complete_json(self, task: str, system: str, user: str) -> dict:
        res = self._post(f"{self.base_url}/messages",
                         {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"},
                         self.body(system, user))
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("input_tokens", 0)
        self.usage.output_tokens += u.get("output_tokens", 0)
        text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        return _first_json(text)


@dataclass
class LocalLLM:
    """원내(온프레미스) 모델: llama.cpp로 GGUF 양자화 모델을 CPU·GPU에서 돌린다. 외부 전송 없음."""

    model_path: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    n_ctx: int = 4096
    n_threads: int | None = None
    _llm: object = None

    def __post_init__(self):
        from llama_cpp import Llama  # 선택 의존성

        self.name = self.name or f"local:{Path(self.model_path).name}"
        self._llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads, verbose=False)

    def complete_json(self, task: str, system: str, user: str) -> dict:
        t0 = time.monotonic()
        try:
            res = self._llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"}, temperature=0.0, max_tokens=256)
        except Exception as e:  # noqa: BLE001  모델 런타임 오류는 모두 장애로 본다
            raise LLMError(f"로컬 모델 오류: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        return _first_json(res["choices"][0]["message"]["content"] or "")


@dataclass
class ClaudeSubLLM:
    external = True
    """구독(Pro·Max) 계정으로 로그인한 Claude Code를 claude-agent-sdk로 부른다 (개인 평가용, 대리 측정).

    사용자가 약관에서 개인 평가 용도 허용을 확인함(2026-11-09). 한계: 온도를 고정할 수 없고,
    지연에 Claude Code 기동 시간이 섞여 지연·비용 수치는 쓰지 않는다. 정확도 비교에만 쓴다.
    `ANTHROPIC_API_KEY`가 잡혀 있으면 구독이 아니라 API로 과금되므로 실행하지 않는다.
    """

    model: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    api_equiv_usd: float = 0.0  # API 정가 환산(실제 청구 아님)

    def __post_init__(self):
        self.name = self.name or f"claudesub:{self.model}"

    def complete_json(self, task: str, system: str, user: str) -> dict:
        if os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY가 잡혀 있어 구독 대신 API로 과금된다. 변수를 지우고 다시 실행")
        import asyncio

        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        except ImportError as e:  # 선택 의존성
            raise LLMError("claude-agent-sdk가 없다: pip install claude-agent-sdk") from e

        opts = ClaudeAgentOptions(model=self.model, system_prompt=system + "\nJSON 객체 하나만 출력한다.",
                                  tools=[], max_turns=1, setting_sources=[], strict_mcp_config=True,
                                  extra_args={"no-session-persistence": None})

        async def run():
            res = None
            async for m in query(prompt=user, options=opts):
                if isinstance(m, ResultMessage):
                    res = m
            return res

        t0 = time.monotonic()
        try:
            res = asyncio.run(run())
        except Exception as e:  # noqa: BLE001  SDK·CLI 오류는 모두 장애로 본다
            raise LLMError(f"구독 경유 호출 실패: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        if res is None or res.is_error:
            msg = getattr(res, "subtype", "응답 없음")
            if res is not None and any(w in str(res.result or "").lower() for w in ("limit", "quota")):
                raise QuotaExceeded(f"구독 한도: {res.result}")
            raise LLMError(f"구독 경유 오류: {msg}")
        u = res.usage or {}
        self.usage.input_tokens += sum(u.get(k, 0) or 0 for k in
                                       ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.usage.output_tokens += u.get("output_tokens", 0) or 0
        self.api_equiv_usd += res.total_cost_usd or 0.0
        return _first_json(res.result or "")


def get_llm(env: dict | None = None, data_class: str = "hospital", policy=None) -> LLM:
    """환경 변수가 모두 갖춰졌을 때만 실제 모델. 아니면 가짜 LLM.
    외부 모델은 `GuardedLLM`으로 감싸 돌려준다(data_class 기본 병원 데이터, fail-closed)."""
    env = os.environ if env is None else env
    llm = _make_llm(env)
    return GuardedLLM(llm, data_class, policy) if getattr(llm, "external", False) else llm


def _make_llm(env) -> LLM:
    kind, model = env.get("TURNAROUND_LLM", "fake"), env.get("TURNAROUND_LLM_MODEL")
    if kind == "fake" or not model:
        return FakeLLM()
    if kind == "local":
        return LocalLLM(model_path=model)  # 원내 실행: 외부 전송이 없어 확인 값이 필요 없다
    if env.get("TURNAROUND_EXTERNAL_LLM_ACK") != "synthetic-only":
        raise LLMError("외부 LLM은 합성 데이터 확인(TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only)이 있어야 쓴다")
    if kind == "claudesub":  # 구독 경유도 데이터가 국외 서버로 간다: 합성 데이터 확인 필요
        return ClaudeSubLLM(model=model)
    if kind == "openai" and env.get("OPENAI_API_KEY"):
        return OpenAILLM(model=model, name=f"openai:{model}")
    if kind == "xai":  # OpenAI 호환 API. 키는 환경 프록시가 넣는다
        return OpenAILLM(model=model, name=f"xai:{model}", base_url="https://api.x.ai/v1")
    if kind == "anthropic" and env.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(model=model, name=f"anthropic:{model}")
    raise LLMError(f"{kind} 키가 없다")
