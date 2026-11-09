"""측정 결과 파일에 남기는 재현 지문 (5단계 재현성 검수): 커밋, 작업 트리 변경 여부, 시각, 파이썬·주요 패키지, 정책 지문."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - git이 없으면 비워 둔다
        return ""


def stamp(policy=None) -> dict:
    pkgs = {}
    for p in ("pydantic", "rdflib", "pyshacl", "streamlit"):
        try:
            pkgs[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pkgs[p] = None
    pol = None
    if policy is not None:
        pol = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()[:12]
    # 결과 파일(evals/)과 문서 변경은 측정 코드 변경이 아니다: 소스·설정·스크립트만 보고, 그 차이의 지문을 남긴다
    src = ("src", "config", "scripts", "pyproject.toml")
    diff = _git("diff", "HEAD", "--", *src)
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *src)
    return {"commit": _git("rev-parse", "--short", "HEAD"), "dirty": bool(_git("status", "--porcelain")),
            "source_dirty": bool(diff or untracked),
            "source_diff_sha": hashlib.sha256((diff + untracked).encode()).hexdigest()[:12] if (diff or untracked) else None,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "python": platform.python_version(),
            "packages": pkgs, "policy_fingerprint": pol}


__all__ = ["stamp"]
