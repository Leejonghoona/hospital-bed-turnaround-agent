# AI 개발 워크플로

WardFlow는 Claude Code(AI 코딩 에이전트)로 개발한다. 방향과 판정 기준은 사람이 정하고, 구현은 에이전트가 맡는다.
이 문서는 에이전트가 작업 범위를 벗어나거나 검증되지 않은 변경을 남기지 않도록 저장소에 걸어 둔 장치를 설명한다.
모든 장치는 `.claude/` 에 있고, 프롬프트 지시가 아니라 Claude Code 훅으로 매번 강제 실행된다.

## 작업 순서

```
researcher(조사, 읽기 전용) → planner(.claude/plans/ 에 계획) → 사람 '승인' 입력
→ 구현(main) → reviewer(검토, 읽기 전용) → Stop 훅 완료 검증 → 커밋
```

| 단계 | 담당 | 강제 장치 |
|---|---|---|
| 조사 | `researcher` (Haiku, 읽기 도구만) | 파일 수정 도구 제외, 훅이 쓰기 명령 차단 |
| 계획 | `planner` | 계획 문서 외 쓰기 차단 |
| 승인 | 사람 | `prompt_gate.py`: 사람이 입력한 '승인'만 승인 상태로 기록 |
| 구현 | main | `guard.py` G1: 승인된 계획 없이는 src·tests·docs 등 수정 차단, 승인 뒤 계획이 바뀌면 다시 차단 |
| 검토 | `reviewer` (읽기 + 테스트 실행) | 파일 수정 차단, 계획과 실제 변경 대조 |
| 완료 | Stop 훅 | `stop_check.py`: 바뀐 코드와 관련된 테스트를 돌려 실패하면 종료를 막고 실패 내용을 돌려줌 |

## 차단 규칙 (`.claude/hooks/guard.py`)

| 규칙 | 막는 것 | 이유 |
|---|---|---|
| G1 | 승인된 계획 없이 코드·문서 수정 | 작업 범위 이탈 방지 |
| G2 | AI가 승인 상태 파일을 쓰는 것 | 스스로 승인하는 것 방지 |
| G3 | researcher·reviewer의 수정, planner의 계획 외 쓰기 | 역할 분리 |
| G4 | 평가 정답 세트(`evals/model_eval/*.jsonl`, 홀드아웃)와 지난 측정 결과 수정 | 평가셋 오염 방지 |
| G5 | `.env`·키 파일 읽기·쓰기 | 비밀 정보 노출 방지 |
| G6 | 강제 푸시, hard reset, 재귀 삭제, 내려받은 스크립트 바로 실행 | 되돌리기 어려운 변경 방지 |

## 기록과 재현

| 파일 (`.claude/logs/`, 저장소에 올리지 않음) | 내용 |
|---|---|
| `prompts.jsonl` | 사람 입력 원문 앞부분과 해시 |
| `tools.jsonl` | 역할별 도구 호출, 대상, 성공 여부, 걸린 시간 |
| `guard.jsonl` | 차단된 행동과 규칙 |
| `approvals.jsonl` | 계획 초안·승인·반려 |
| `checks.jsonl` | 완료 검증 결과와 돌린 테스트 |

`python scripts/ai_log_report.py` (또는 `/ai-report`)로 집계한다. 세션을 시작하면 `session_context.py`가 브랜치, 최근 커밋, 계획 상태, 마지막 검증 결과를 자동으로 알려 준다.

## 비용과 속도

- 조사는 가벼운 모델(Haiku), 계획과 검토는 Sonnet에 맡긴다.
- Stop 훅은 전체 테스트(약 11분, CI 담당) 대신 바뀐 파일과 이어지는 테스트만 돌린다.

## 한계

- 훅은 Claude Code 안에서만 동작한다. 사람이 직접 고치는 변경은 막지 않는다.
- main 에이전트가 Bash로 파일을 쓰는 우회는 일부(STATE.json, .env)만 막는다. 나머지는 reviewer 검토와 CI가 잡는다.
- 훅 자체는 `tests/test_ai_hooks.py` 16개 시험으로 확인한다.
