"""현장 조사용 유튜브 자막 받기 (노트북에서 실행). 클라우드 세션 IP는 유튜브가 막아서 사용자 PC에서 받는다.

사용 (저장소 폴더에서):  python scripts/fetch_yt_transcripts.py
- 대상: docs/검토/현장조사/유튜브_대상.txt 의 영상 ID (한 줄에 하나)
- 결과: docs/검토/현장조사/유튜브_자막/<영상ID>.txt  (한국어 자막 우선, 없으면 영어. 자동 생성 자막 포함)
- 이미 받은 영상은 건너뛴다. 끝나면 결과 폴더만 커밋·푸시한다.
- 개인 연구용으로 공개 영상의 자막을 읽기 위한 것이다. 요약을 만든 뒤 원문 자막 폴더는 지운다.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "docs" / "검토" / "현장조사" / "유튜브_대상.txt"
OUT = ROOT / "docs" / "검토" / "현장조사" / "유튜브_자막"
BRANCH = "claude/gallant-keller-0lrkwf"


def fetch(vid: str) -> str | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    try:
        t = api.fetch(vid, languages=["ko", "en"])
    except Exception as e:  # noqa: BLE001  자막 없음·비공개·차단은 모두 건너뛴다
        print(f"  {vid}: 자막 없음 ({type(e).__name__})")
        return None
    lines = [f"[{int(s.start // 60):02d}:{int(s.start % 60):02d}] {s.text}" for s in t]
    return f"https://www.youtube.com/watch?v={vid}\n" + "\n".join(lines) + "\n"


def git(*a: str, check=True):
    return subprocess.run(["git", *a], cwd=ROOT, text=True, capture_output=True, check=check)


def main() -> int:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "youtube-transcript-api"], check=False)
    git("pull", "-q", "origin", BRANCH, check=False)
    OUT.mkdir(parents=True, exist_ok=True)
    ids = [x.strip() for x in LIST.read_text(encoding="utf-8").splitlines() if x.strip()]
    got = 0
    for i, vid in enumerate(ids, 1):
        f = OUT / f"{vid}.txt"
        if f.exists():
            continue
        print(f"[{i}/{len(ids)}] {vid}")
        text = fetch(vid)
        if text:
            f.write_text(text, encoding="utf-8")
            got += 1
        time.sleep(2)  # 천천히 받는다
    print(f"새로 받은 자막 {got}개, 전체 {len(list(OUT.glob('*.txt')))}개")
    if got:
        git("add", str(OUT.relative_to(ROOT)))
        git("commit", "-q", "-m", f"현장 조사용 유튜브 자막 {got}개 받음")
        for _ in range(4):
            if git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False).returncode == 0:
                print("푸시 완료. 채팅에 '자막 올렸어'라고 알려 주세요.")
                return 0
            git("pull", "-q", "--rebase", "origin", BRANCH, check=False)
        print("푸시 실패: 폴더를 압축해 채팅에 첨부해도 됩니다.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
