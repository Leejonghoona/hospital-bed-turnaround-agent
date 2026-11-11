#!/usr/bin/env bash
# 사후 추가 측정(2026-11-11, 설계 15절). 대표 선정(Gemma 4 26B)은 바꾸지 않는다. 합성 데이터만.
#  1) Qwen3.8 27B: 등록 후보 4개 중 GPU에서 못 잰 하나 -> K2, K3 3회, K3 통과 시 K4
#  2) Gemma 4 31B(탐색): K4만(K3는 본 측정에서 끝남), 판정 밖 참고
# 환경: 본 측정에서 겪은 문제를 처음부터 피한다(venv, Gemma용 transformers 5.5.4, expandable_segments).
set -uo pipefail
cd "$(dirname "$0")/../.."
OUT=evals/results/kit; mkdir -p "$OUT" logs
export TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only PYTHONIOENCODING=utf-8 HF_HOME=/root/hf PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
find scripts src config -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yaml" \) -exec sed -i 's/\r$//' {} +
python3 -m venv /root/venv && /root/venv/bin/python -m pip install -q vllm==0.19.0 hf_transfer 2>&1 | tail -1
/root/venv/bin/python -m pip install -q -e . 2>&1 | tail -1
cp -a /root/venv /root/venv2 && sed -i "s#/root/venv\b#/root/venv2#g" /root/venv2/bin/activate /root/venv2/bin/pip* /root/venv2/bin/vllm /root/venv2/pyvenv.cfg
/root/venv2/bin/python -m pip install -q "transformers==5.5.4" 2>&1 | grep -v -E "WARNING|conflict|requires" | tail -1

serve() {  # $1 venv, $2 hf, $3 rev, $4 log, $5 extra
  pkill -f "vllm serve" 2>/dev/null; sleep 5
  nohup "$1/bin/vllm" serve "$2" --revision "$3" --host 127.0.0.1 --port 8000 --max-model-len 4096 --max-num-seqs 16 \
      --gpu-memory-utilization 0.90 $5 > "logs/$4.log" 2>&1 &
  for i in $(seq 1 240); do
    curl -sf http://127.0.0.1:8000/v1/models >/dev/null && return 0
    pgrep -f "vllm serve" >/dev/null || break; sleep 10
  done
  echo "vLLM start failed: $4" | tee -a logs/failures.txt; tail -40 "logs/$4.log" >> logs/failures.txt; return 1
}
man() { /root/venv/bin/python scripts/gpu/server_manifest.py "$1" "$2" "$3" "$4" "$5"; }
k4() {  # $1 alias, $2 tag, $3 cache label
  for bg in 0 2; do
    if [ "$bg" = 0 ]; then mode=dedicated; else mode=shared; fi
    /root/venv/bin/python scripts/replay_requests.py --models "kit:$1" --requests "$OUT/requests_2026-11-11.jsonl" --scale 5 \
        --background-rps "$bg" --tick-timeout 60 --tag "$2" --label "cache${3}_${mode}" | tail -3
  done
}
P=/root/venv/bin/python

Q=rental-qwen3.8-27b; QH=Qwen/Qwen3.8-27B; QR=37561816e66b5b9153b1b64c10968c18c1e6f4a0; QX='--limit-mm-per-prompt {"image":0,"video":0}'
TAG=2026-11-11-gpu
echo "=== qwen $(date +%T)"
if serve /root/venv "$QH" "$QR" "vllm_$Q" "$QX"; then
  man "$Q" k3 "$QX" "$QH" "$QR"
  $P scripts/run_kit.py --part K2 --models "kit:$Q" --tag "$TAG" | tail -2
  $P scripts/run_kit.py --part K3 --models "kit:$Q" --tag "$TAG" 2>&1 | grep -v "/300$" | tail -2
  PASS=$($P scripts/run_kit.py --summarize --models "rule,escalate,kit:$Q" --tag "$TAG" | $P -c "import json,sys; print(json.load(sys.stdin)['kit:$Q'].get('k3_pass'))")
  echo "K3 verdict $Q: $PASS"
  if [ "$PASS" = "True" ]; then
    k4 "$Q" "$TAG" on
    serve /root/venv "$QH" "$QR" "vllm_${Q}_nocache" "$QX --no-enable-prefix-caching" && { man "$Q" k4_nocache "$QX --no-enable-prefix-caching" "$QH" "$QR"; k4 "$Q" "$TAG" off; }
  fi
fi

G=rental-gemma-4-31b; GH=google/gemma-4-31B-it; GR=842da3794eaa0b77d5f08bae87a17459d91ff475
TAGX=2026-11-11-gpu-explore
echo "=== gemma31 K4 $(date +%T)"
serve /root/venv2 "$GH" "$GR" "vllm_${G}_k4" "" && { man "$G" k4 "" "$GH" "$GR"; k4 "$G" "$TAGX" on; }
serve /root/venv2 "$GH" "$GR" "vllm_${G}_nocache" "--no-enable-prefix-caching" && { man "$G" k4_nocache "--no-enable-prefix-caching" "$GH" "$GR"; k4 "$G" "$TAGX" off; }

pkill -f "vllm serve" 2>/dev/null
tar czf /root/results_extra.tgz "$OUT" logs
echo "=== extra done $(date +%T)"
