# WardFlow

퇴원부터 다음 환자 입실까지, 병상 준비 과정을 맡는 병원 운영 AI 에이전트입니다.
**LLM은 판단만 하고, 실행은 규칙과 게이트웨이가 다시 검사한 뒤에만 합니다.** 모든 데이터는 합성(가상 병원)입니다.

<p>
  <img src="docs/화면재설계/runs/latest/A3.png" width="49%" alt="원무팀 화면">
  <img src="docs/화면재설계/runs/latest/N1.png" width="49%" alt="병동 화면">
</p>

## 핵심 결과

| 항목 | 결과 | 조건 |
|---|---|---|
| 실행 안전 위반 | **0 / 761** | 실행된 배정·청소·입실 호출을 독립 평가기로 사후 점검, 합성 병원 100곳 |
| 사람 손 없이 확정 | **72.4%** | 확정 203건 중 147건 (입원 304건 대비 48%), 합성 병원 20곳 |
| 수속 → 입실 대기 | **90.1 → 78.4분** | 같은 날 짝 비교 48곳, 차이 95% 구간 −16.8 ~ −7.0분. 지연 크기는 설계 가정 |
| 원내 LLM 도입 시험 | **판정 4종 중 3종 통과** | H100 1장, vLLM 0.19.0, 300문항 × 3회. 대표 Gemma 4 26B, 실행된 금지 행동 0 |
| LLM 판단의 규칙표 반영 | 구현 | 담당자가 고친 답이 LLM 답과 같은 사례가 쌓이면 규칙 수정안, 책임자 승인 뒤 반영 |

## 구조

```
병원 사건 → 사건 저장소 → 사례 흐름(사가) → 판단(병상: 점수 규칙 / 예외: 규칙표, LLM은 기록·제안)
        → 실행 게이트웨이(제안 때·확정 직전 재검사) → 원무팀 · 병동 · 감염관리실 · 청소
```

- **판단과 실행 분리:** 병상은 점수 규칙으로 정하고, 모든 실행은 게이트웨이를 거칩니다. 연락·확정 뒤 재배정 같은 행동은 코드 상수로 잠겨 병원 설정으로도 풀 수 없습니다.
- **단계별 맡김:** 사람 결정 25건 이상, 일치율 신뢰 하한 85% 이상인 유형만 에이전트가 확정하고, 최근 20건 중 3건이 고쳐지면 다시 사람에게 돌아갑니다.
- **휴먼 인 더 루프:** LLM 예외 판단은 실행하지 않고 기록합니다. 담당자가 규칙표 답을 고쳤고 그 답이 LLM과 같은 사례만 세어 규칙표 수정안을 만듭니다(`src/turnaround/agent/rule_learning.py`).
- **병원 데이터 연결:** 열 이름이 다른 EMR 표를 공통 사전(온톨로지)에 연결하고, 모르는 열은 추측하지 않고 묻습니다.

## 실행

```bash
docker build -t wardflow . && docker run -p 8000:8000 -v wardflow-data:/data wardflow
```

http://localhost:8000 에서 **시연 화면**(대표 사례 3개)이나 **운영 화면**(역할 계정, 데모 비밀번호 `demo1234`)을 고릅니다.
기본은 API 키 없이 도는 결정적 규칙표입니다. 실제 모델은 `TURNAROUND_LLM=openai|anthropic|local`로 붙입니다.

```bash
pip install -e ".[dev,api]" && python -m pytest -q   # 전체 시험
```

## 한계

- 합성 데이터만 썼습니다. 단계별 소요 시간과 행동 효과 크기는 공개 자료를 참고한 설계 가정이고, 현직자 검증 전입니다.
- 담당자 결정은 모의 모형입니다. 로그인·신원 인증은 없습니다.
- HL7 v2 ADT 입력은 직접 쓴 표본 메시지 36건으로만 시험했고(틀린 연결 0, 질문 7건), FHIR 내보내기는 R4 구조 검증까지만 했습니다. 실제 병원 메시지와 KR Core 프로필 검증은 하지 않았습니다.
- 원내 LLM 시험은 원내 조건을 재현한 임대 GPU에서 쟀습니다. 정답 차이는 사전 등록 기준 보정 후 유의하지 않습니다.

## 근거 문서

| 문서 | 내용 |
|---|---|
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 설계 결정과 이유 (날짜순) |
| [`docs/RESULTS.md`](docs/RESULTS.md) | 측정 기록과 재현 조건 |
| [`docs/검토/LLM도입_인수시험_설계_초안.md`](docs/검토/LLM도입_인수시험_설계_초안.md) | 원내 LLM 도입 시험 사전 등록과 GPU 판정(16절) |
| [`docs/AI-DD.md`](docs/AI-DD.md) | AI·규칙·사람의 역할 분담 |

<details>
<summary>MCP 연결, 에이전트 추적</summary>

조회 전용 도구 6개를 MCP 서버로 엽니다(상태를 바꾸는 도구는 없음).

```bash
pip install -e ".[api,mcp]"
python -m turnaround.mcp_server          # stdio
claude mcp add wardflow -- python -m turnaround.mcp_server
```

LLM 호출, 판단, 게이트웨이 판정을 OpenTelemetry span으로 남깁니다.

```bash
pip install -e ".[trace]"
TURNAROUND_TRACE=1 TURNAROUND_TRACE_FILE=traces.jsonl uvicorn turnaround.api.app:app
```
</details>
