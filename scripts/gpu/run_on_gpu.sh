#!/usr/bin/env bash
# 인수 시험 판정 측정: 임대 GPU 1장(H100 80GB급)에서 vLLM으로 K3 3회와 K4 용량을 잰다 (설계 7.2, 4절). 합성 데이터만.
# 사용: bash scripts/gpu/run_on_gpu.sh [별칭 ...]   (기본: K3 대리 측정 통과 후보 3개)
# 서버에는 이 저장소 묶음만 올린다. 키·병원 데이터·현장조사 문서는 올리지 않는다.
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG=${TAG:-2026-11-11-gpu}
VLLM_VERSION=0.19.0
MODELS=("$@"); [ ${#MODELS[@]} -eq 0 ] && MODELS=(rental-gpt-oss-120b rental-gpt-oss-20b rental-gemma-4-26b)
declare -A HF=([rental-gpt-oss-120b]=openai/gpt-oss-120b [rental-gpt-oss-20b]=openai/gpt-oss-20b [rental-gemma-4-26b]=google/gemma-4-26B-A4B-it)
declare -A REV=([rental-gpt-oss-120b]=b5c939de8f754692c1647ca79fbf85e8c1e70f8a [rental-gpt-oss-20b]=6cee5e81ee83917806bbde320786a8fb61efebee [rental-gemma-4-26b]=4d7ae4984b7db7de8f8457170b3f1a419ee76d52)
OUT=evals/results/kit; mkdir -p "$OUT" logs
export TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only PYTHONIOENCODING=utf-8

python3 -m pip install -q "vllm==${VLLM_VERSION}" pydantic pyyaml rdflib pyshacl requests 2>&1 | tail -2
python3 -m pip install -q -e . 2>&1 | tail -1

serve() {  # $1 별칭, $2 추가 인자
  local hf=${HF[$1]} rev=${REV[$1]}
  pkill -f "vllm serve" 2>/dev/null || true; sleep 5
  nohup vllm serve "$hf" --revision "$rev" --host 127.0.0.1 --port 8000 --max-model-len 4096 --max-num-seqs 16 \
      --gpu-memory-utilization 0.90 $2 > "logs/vllm_$1$3.log" 2>&1 &
  for i in $(seq 1 180); do curl -sf http://127.0.0.1:8000/v1/models >/dev/null && return 0; sleep 10; done
  echo "vLLM 기동 실패: $1" >&2; tail -50 "logs/vllm_$1$3.log" >&2; return 1
}

server_manifest() {  # $1 별칭, $2 조건 이름, $3 vLLM 추가 인자
  python3 - "$1" "$2" "$3" "${HF[$1]}" "${REV[$1]}" <<'PY'
import json, subprocess, sys, platform, importlib.metadata as md
alias, cond, extra, hf, rev = sys.argv[1:]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
    except Exception as e: return str(e)
m = {"alias": alias, "condition": cond, "hf_repo": hf, "hf_revision": rev, "vllm": md.version("vllm"),
     "vllm_args": f"--max-model-len 4096 --max-num-seqs 16 --gpu-memory-utilization 0.90 {extra}".strip(),
     "gpu": sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader"),
     "cuda": sh("nvcc --version | tail -1"), "torch": md.version("torch"), "python": platform.python_version(),
     "models_api": json.loads(sh("curl -s http://127.0.0.1:8000/v1/models") or "{}"),
     "region_hint": sh("curl -s https://ipinfo.io/country")}
open(f"evals/results/kit/server_manifest_{alias}_{cond}.json", "w").write(json.dumps(m, ensure_ascii=False, indent=1))
PY
}

for a in "${MODELS[@]}"; do
  echo "=== $a K3 $(date +%T)"
  serve "$a" "" ""
  server_manifest "$a" k3 ""
  python3 scripts/run_kit.py --part K2 --models "kit:$a" --tag "$TAG" | tail -5
  python3 scripts/run_kit.py --part K3 --models "kit:$a" --tag "$TAG" 2>&1 | grep -v "/300$" | tail -3
  python3 scripts/run_kit.py --part K3 --models rule,escalate --tag "$TAG" --resume >/dev/null 2>&1 || true
  PASS=$(python3 scripts/run_kit.py --summarize --models "rule,escalate,kit:$a" --tag "$TAG" \
         | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['kit:$a'].get('k3_pass'))")
  echo "K3 판정 $a: $PASS"
  if [ "$PASS" = "True" ]; then  # K4: 통과 후보만. 캐시 켬·끔 × 전용·공유
    for cache in on off; do
      [ $cache = off ] && { serve "$a" "--no-enable-prefix-caching" "_nocache"; server_manifest "$a" k4_nocache "--no-enable-prefix-caching"; }
      for bg in 0 2; do
        label="cache${cache}_$([ $bg = 0 ] && echo dedicated || echo shared)"
        python3 scripts/replay_requests.py --models "kit:$a" --requests "$OUT/requests_2026-11-11.jsonl" --scale 5 \
            --background-rps $bg --tick-timeout 60 --tag "$TAG" --label "$label" | tail -12
      done
    done
  fi
done
pkill -f "vllm serve" 2>/dev/null || true
python3 scripts/run_kit.py --summarize --models "rule,escalate,$(printf 'kit:%s,' "${MODELS[@]}" | sed 's/,$//')" --tag "$TAG" > "$OUT/K3_${TAG}_summary_print.json"
tar czf "results_${TAG}.tgz" "$OUT" logs
echo "=== 끝 $(date +%T): results_${TAG}.tgz"
