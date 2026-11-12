---
name: review-change
description: 구현이 끝난 변경을 reviewer 서브에이전트로 검토하고, 결과를 커밋 메시지에 남길 수 있게 정리한다. 커밋 전에 쓴다.
---
1. reviewer 서브에이전트에게 승인된 계획과 현재 변경(git diff)을 비교해 검토하게 한다.
2. "수정 필요"가 나오면 지적을 고친 뒤 다시 reviewer를 돌린다. 계획 밖 수정이 필요하면 planner로 계획부터 고친다.
3. "통과"면 검토 요약 한 줄과 돌린 테스트를 사람에게 보고한다. 커밋 메시지에 `Reviewed-by: reviewer subagent` 줄을 넣는다.
