"""임대 GPU 서버 정보 기록 (설계 7.2): GPU, vLLM 버전, 고정 커밋, 서빙 인자, 서버가 알려 준 모델 목록."""
import importlib.metadata as md
import json
import platform
import subprocess
import sys


def sh(c: str) -> str:
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()


alias, cond, extra, hf, rev = sys.argv[1:6]
try:
    models_api = json.loads(sh("curl -s http://127.0.0.1:8000/v1/models") or "{}")
except json.JSONDecodeError:
    models_api = None
m = {"alias": alias, "condition": cond, "hf_repo": hf, "hf_revision": rev, "vllm": md.version("vllm"),
     "vllm_args": f"--max-model-len 4096 --max-num-seqs 16 --gpu-memory-utilization 0.90 {extra}".strip(),
     "gpu": sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader"),
     "torch": md.version("torch"), "python": platform.python_version(), "models_api": models_api,
     "country": sh("curl -s https://ipinfo.io/country")}
with open(f"evals/results/kit/server_manifest_{alias}_{cond}.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(m, ensure_ascii=False, indent=1))
