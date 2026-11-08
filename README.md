# 병실관리AI

퇴원 확정부터 다음 환자 입실까지 병상이 비어 있는 시간을 줄이는 병원 운영 에이전트 데모입니다. 가상 병원 데이터로만 동작합니다.

- 설계 문서: `docs/병상턴어라운드_설계.html` (브라우저로 열기)
- 공유 링크: https://claude.ai/artifact/5x2WpmAxeuuBjvNe5gJmky
- 작업 지침: `CLAUDE.md`

## 구조 요약
이벤트 저장소 → 사례 워크플로(사가) → 계획 에이전트와 도구 → 실행 게이트웨이 → 부서 화면 공유, 병동 회신, 병상관리 검토, 환경 청소 요청

## 상태
설계 완료. 구현 1단계(합성 데이터), 1.5단계(평가 골격), 2단계(이벤트 저장소) 완료. 진행 현황은 `docs/RESULTS.md`.

## 실행
```bash
pip install -e '.[dev]'
pytest
python -m turnaround.sim --seed 42 --out data/synthetic_seed42.json
python -m turnaround.evaluation.board --seeds 0-199 --write   # 점수판 기록
```
