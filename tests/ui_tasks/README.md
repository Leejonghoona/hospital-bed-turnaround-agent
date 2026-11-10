# 화면 과업 시험 (Playwright)

`docs/화면재설계/tasks.json`의 과업을 브라우저로 직접 수행하고 합격 조건을 확인한다. 점수가 아니라 과업별 통과/실패.

```bash
cd web && npm run build && cd ..
python scripts/ui_tasks.py            # API+화면을 띄우고 과업을 돌려 결과와 스크린샷을 남긴다
```
