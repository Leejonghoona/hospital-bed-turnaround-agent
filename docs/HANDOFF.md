# 새 세션 인수인계

이전 대화를 읽지 않고 이어서 작업하기 위한 요약이다. 정답 문서는 CLAUDE.md, `docs/병상턴어라운드_설계.html`, `docs/SPEC.md`다.

## 지금 상태
- 1단계 MVP 동작. 회귀 30/30, 홀드아웃 2회차 10/10. 상세는 README "상태", `docs/RESULTS.md`.
- 결정과 MVP 뒤 검토할 가정: `docs/DECISIONS.md` (MA-01 ~ MA-14).
- 모델 점검: `docs/모델_성능점검표.md` (결과표는 `python -m turnaround.evaluation.model_report`가 `evals/results/model_check/*.json`에서 만든다).

## 남은 일
1. **Claude 측정**: 환경 변수 `ANTHROPIC_API_KEY`가 있으면 아래를 돌리고 결과표를 다시 만든다. 키는 저장소에 넣지 않는다.
   ```bash
   export TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only
   python -m turnaround.evaluation.model_check --models anthropic:<모델 ID> --reps 3 --split dev
   python -m turnaround.evaluation.model_check --models anthropic:<모델 ID> --reps 0 --split holdout
   python -m turnaround.evaluation.model_report
   ```
2. 원내 모델 실측(EXAONE 홀드아웃, Qwen 3B·7B)이 결과 폴더에 없으면 다시 돈다. llama.cpp 빌드(`pip install llama-cpp-python`)와 GGUF 다운로드(`models/`, 저장소 제외)가 필요하다. Hugging Face 파일은 `us.aws.cdn.hf.co`를 거친다(환경 허용 호스트에 추가됨).
3. 점검표 5절(원내 장비 추정)과 결론 작성. 인젝션 짝 변화가 무작위성인지 문항별 대조.
4. 사용자 요청: 전문가 PM·PO 시각으로 기획 단계 산출물(요구사항 명세서, PRD 등)을 분석하고, 잘 쓴 공개 예시를 인용해 이 프로젝트에 맞춘 문서를 `docs/`에 작성.
5. MVP 뒤 사용자 검토: MA 가정 목록, 홀드아웃 H06~H10 라벨, 추출 문장 200 라벨.

## 규칙 요약
브랜치 `claude/gallant-keller-0lrkwf`에만 푸시. 테스트 먼저, 작게 커밋, 한국어 한 줄 커밋 메시지. 합성 데이터만. 외부 API에는 합성 데이터만 보낸다. Grok 키는 환경 프록시가 넣는다(`xai:<모델>`).
