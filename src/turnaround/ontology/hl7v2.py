"""HL7 v2 메시지 파서 (파이프 구분, v2.5 ADT 평가용 최소판).

생성기(`sim/exports.py`)와 무관한 표준 외부 형식으로 온보딩 평가 경로를 하나 더 두기 위한 것이다(DECISIONS 2026-11-12).

- 구분자는 메시지마다 MSH-1(필드)과 MSH-2(성분·반복·확장·부성분 순서)에서 읽는다. 고정값을 가정하지 않는다.
- 세그먼트 구분은 표준의 CR(\\r)이지만 파일로 오가며 LF·CRLF로 바뀌는 일이 흔해 셋 다 받는다.
- 필드 번호는 표준 표기(MSH-9, PV1-3)를 따른다. MSH는 MSH-1이 필드 구분자 자체라 번호가 하나 밀린다.
- 확장 문자(\\F\\, \\S\\, \\T\\, \\R\\, \\E\\)는 값을 꺼낼 때 푼다. 그 밖의 확장(\\H\\, \\X..\\ 등)은 그대로 둔다.
- 의미 해석(어느 필드가 무슨 사건인지)은 여기서 하지 않는다. 어댑터(`hl7_adapter.py`)가 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class HL7ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Encoding:
    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    def unescape(self, s: str) -> str:
        if self.escape not in s:
            return s
        e = re.escape(self.escape)
        table = {"F": self.field, "S": self.component, "T": self.subcomponent, "R": self.repetition, "E": self.escape}
        return re.sub(f"{e}([FSTRE]){e}", lambda m: table[m.group(1)], s)


@dataclass
class Segment:
    name: str
    fields: list[str]  # fields[n] = 표준 번호 n의 원문(확장 문자 그대로). fields[0] = 세그먼트 이름


@dataclass
class Message:
    segments: list[Segment]
    enc: Encoding
    raw: str = ""
    index: int = 0  # 묶음 안 순서(도착 순서)

    def all(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    def seg(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.name == name), None)

    def field(self, seg: Segment | str | None, n: int, comp: int | None = None, rep: int = 0, sub: int | None = None) -> str:
        """표준 번호로 값을 꺼낸다. comp·sub는 1부터. 없는 위치는 빈 문자열."""
        if isinstance(seg, str):
            seg = self.seg(seg)
        if seg is None or n >= len(seg.fields):
            return ""
        raw = seg.fields[n]
        if seg.name == "MSH" and n in (1, 2):
            return raw
        reps = raw.split(self.enc.repetition)
        if rep >= len(reps):
            return ""
        val = reps[rep]
        if comp is not None:
            comps = val.split(self.enc.component)
            val = comps[comp - 1] if comp - 1 < len(comps) else ""
            if sub is not None:
                subs = val.split(self.enc.subcomponent)
                val = subs[sub - 1] if sub - 1 < len(subs) else ""
        return self.enc.unescape(val)

    def repetitions(self, seg: Segment | str | None, n: int) -> int:
        if isinstance(seg, str):
            seg = self.seg(seg)
        if seg is None or n >= len(seg.fields) or seg.fields[n] == "":
            return 0
        return len(seg.fields[n].split(self.enc.repetition))

    def get(self, spec: str, seg: Segment | None = None) -> str:
        """'PV1-3.1' 꼴 위치 표기로 값을 꺼낸다(seg를 주면 그 세그먼트 인스턴스에서)."""
        m = re.fullmatch(r"([A-Z][A-Z0-9]{2})-(\d+)(?:\.(\d+))?(?:\.(\d+))?", spec)
        if not m:
            raise ValueError(f"위치 표기를 모른다: {spec}")
        name, n, c, s = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        target = seg if seg is not None and seg.name == name else self.seg(name)
        return self.field(target, n, int(c) if c else None, 0, int(s) if s else None)

    @property
    def control_id(self) -> str:
        return self.field("MSH", 10)

    @property
    def trigger(self) -> str:
        """MSH-9.2 (예: A01). 옛 형식으로 EVN-1에만 있는 경우도 받는다."""
        return self.field("MSH", 9, 2) or self.field("EVN", 1)

    @property
    def message_type(self) -> str:
        return self.field("MSH", 9, 1)


def _segment_lines(text: str) -> list[str]:
    return [ln for ln in re.split(r"\r\n|\r|\n", text) if ln.strip()]


def parse_message(text: str, index: int = 0) -> Message:
    lines = _segment_lines(text)
    if not lines or not lines[0].startswith("MSH"):
        raise HL7ParseError("메시지는 MSH 세그먼트로 시작해야 한다")
    head = lines[0]
    if len(head) < 8:
        raise HL7ParseError("MSH가 너무 짧다(구분자를 읽을 수 없다)")
    fsep = head[3]
    enc_end = head.find(fsep, 4)
    enc_chars = head[4:enc_end] if enc_end != -1 else head[4:]
    if len(enc_chars) < 4 or fsep in enc_chars or fsep.isalnum():
        raise HL7ParseError(f"MSH-2 구분자 정의가 잘못됐다: {enc_chars!r}")
    enc = Encoding(field=fsep, component=enc_chars[0], repetition=enc_chars[1], escape=enc_chars[2],
                   subcomponent=enc_chars[3] if len(enc_chars) > 3 else "&")
    segs = []
    for ln in lines:
        name = ln[:3]
        if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", name):
            raise HL7ParseError(f"세그먼트 이름이 아니다: {ln[:10]!r}")
        if name == "MSH":
            rest = ln[4:].split(fsep)
            fields = ["MSH", fsep] + rest  # MSH-1 = 필드 구분자, MSH-2 = 구분자 정의
        else:
            fields = ln.split(fsep)
        segs.append(Segment(name=name, fields=fields))
    return Message(segments=segs, enc=enc, raw=text, index=index)


def split_batch(text: str) -> list[str]:
    """여러 메시지를 이어 붙인 파일을 MSH 기준으로 나눈다. 묶음 헤더(FHS/BHS/BTS/FTS)와 주석(#)은 버린다."""
    out, cur = [], []
    for ln in _segment_lines(text):
        if ln.startswith("#") or ln[:3] in ("FHS", "BHS", "BTS", "FTS"):
            continue
        if ln.startswith("MSH") and cur:
            out.append("\r".join(cur))
            cur = []
        cur.append(ln)
    if cur:
        out.append("\r".join(cur))
    return out


def parse_batch(text: str) -> tuple[list[Message], list[tuple[int, str]]]:
    """(읽은 메시지, (순서, 오류)) 목록. 한 메시지가 깨져도 나머지는 읽는다."""
    msgs, errors = [], []
    for i, chunk in enumerate(split_batch(text)):
        try:
            msgs.append(parse_message(chunk, index=i))
        except HL7ParseError as e:
            errors.append((i, str(e)))
    return msgs, errors


__all__ = ["Encoding", "HL7ParseError", "Message", "Segment", "parse_batch", "parse_message", "split_batch"]
