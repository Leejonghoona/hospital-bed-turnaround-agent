# 병실관리AI

퇴원 확정부터 다음 환자 입실까지 병상이 비어 있는 시간을 줄이는 병원 운영 에이전트 데모입니다. 가상 병원 데이터로만 동작합니다.

## 엔드픽처

> **퇴원부터 다음 입원까지, 사람은 책임질 순간에만 개입한다.**
> **그리고 어느 병원이든, 데이터만 넣으면 그 병원에 맞게 스스로 세팅된다.**

| | 1단계: 한 병원에서 증명 | 2단계: 어느 병원이든 |
|---|---|---|
| 무엇을 | 퇴원→입원 사이클 전체를 에이전트화 | 병원 데이터를 넣으면 그 병원용 설정(데이터 연결, 규칙, 소요시간)을 AI가 자동으로 만듦 |
| 사람은 | 공식 확정, 환자 연락, 안전 예외 세 곳만 | 만들어진 설정을 한 번 승인 |
| 증명하는 숫자 | 사례당 사람 개입 수, 완전 자동 처리 비율, 안전 위반 0건 | 처음 보는 가상 병원에서 자동 세팅 성공, 시나리오 통과 |

- 설계 문서: `docs/병상턴어라운드_설계.html` (브라우저로 열기)
- 공유 링크: https://claude.ai/artifact/5x2WpmAxeuuBjvNe5gJmky
- 작업 지침: `CLAUDE.md`

## 구조 요약
이벤트 저장소 → 사례 워크플로(사가) → 계획 에이전트와 도구 → 실행 게이트웨이 → 부서 화면 공유, 병동 회신, 병상관리 검토, 환경 청소 요청

## 상태
1단계 MVP. 구현 우선순위 1~9단계 모두 동작한다: 합성 데이터, 이벤트 저장소, 사가(교대·마감·사망 경로 포함), 실행 게이트웨이(안전 예외 3단계, 병실 사용 중지), 온톨로지, 계획 에이전트·추출·질의 응답(가짜 LLM 기본), 몬테카를로 가용 병상 추정, Streamlit 목업, FHIR R4 경계 어댑터.
- 회귀 시나리오 30/30, 홀드아웃 2회차 10/10 (블라인드 한계는 `docs/RESULTS.md`)
- 안전: 배정 기준 위반 0/6000, 종말소독 누락 0/600, 미래 정보 누수 0/3000 (합성 데이터 기준)
- 모델 선택 근거: `docs/모델_성능점검표.md`, 개발 과정 통제: `docs/AI-DD.md`, MVP 뒤 검토할 가정: `docs/DECISIONS.md`의 MA 항목
- 2단계(온보딩 에이전트)는 스펙 초안만: `docs/2단계_온보딩_스펙.md`

## 실행
```bash
pip install -e '.[dev]'
pytest
python -m turnaround.sim --seed 42 --out data/synthetic_seed42.json
python -m turnaround.evaluation.board --seeds 0-199 --write   # 점수판 기록
pytest -m slow                                                  # 긴 검사 (안전 스윕 1000, 경계 전수, 퍼징, 뮤턴트 등)
streamlit run src/turnaround/ui/app.py                          # 화면 목업 (pip install -e '.[ui]')
python -m turnaround.evaluation.model_check --models fake,rule   # 모델 점검 (실제 모델은 아래 환경 변수)
```
실제 모델: `TURNAROUND_LLM=openai|anthropic|local`, `TURNAROUND_LLM_MODEL=<모델 ID 또는 GGUF 경로>`, 외부 API는 `TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only`와 키가 있어야 붙는다.
