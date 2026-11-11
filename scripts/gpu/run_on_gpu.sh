#!/usr/bin/env bash
# 인수 시험 GPU 측정: 임대 GPU 1장(H100 80GB급)에서 vLLM으로 잰다 (설계 7.2, 4절). 합성 데이터만.
#   판정: 호스팅 대리 K3 통과 후보 3개 -> K2, K3 3회, K3 통과 시 K4(캐시 켬/끔 x 전용/공유)
#   탐색(판정 밖): Gemma 4 31B, Kanana-2 30B, HyperCLOVA X SEED 14B -> K2, K3 3회
# 사용: bash scripts/gpu/run_on_gpu.sh   (모델 하나가 뜨지 않아도 다음 모델로 넘어간다)
# 서버에는 이 저장소 묶음만 올린다. 키, 병원 데이터, 현장조사 문서는 올리지 않는다.
set -uo pipefail
cd "$(dirname "$0")/../.."
TAG=${TAG:-2026-11-11-gpu}
TAGX=${TAGX:-2026-11-11-gpu-explore}
VLLM_VERSION=0.19.0
JUDGE=(rental-gpt-oss-120b rental-gpt-oss-20b rental-gemma-4-26b)
EXPLORE=(rental-gemma-4-31b rental-kanana-2-30b rental-hcx-seed-14b)
declare -A HF=(
  [rental-gpt-oss-120b]=openai/gpt-oss-120b
  [rental-gpt-oss-20b]=openai/gpt-oss-20b
  [rental-gemma-4-26b]=google/gemma-4-26B-A4B-it
  [rental-gemma-4-31b]=google/gemma-4-31B-it
  [rental-kanana-2-30b]=kakaocorp/kanana-2-30b-a3b-instruct
  [rental-hcx-seed-14b]=naver-hyperclovax/HyperCLOVAX-SEED-Think-14B
)
declare -A REV=(
  [rental-gpt-oss-120b]=b5c939de8f754692c1647ca79fbf85e8c1e70f8a
  [rental-gpt-oss-20b]=6cee5e81ee83917806bbde320786a8fb61efebee
  [rental-gemma-4-26b]=4d7ae4984b7db7de8f8457170b3f1a419ee76d52
  [rental-gemma-4-31b]=842da3794eaa0b77d5f08bae87a17459d91ff475
  [rental-kanana-2-30b]=bf0054a93f220c3da6a429450bac1fbfa64989cc
  [rental-hcx-seed-14b]=9b74e35d4c7e4ffec489f4171273caca8948a2b9
)
declare -A XARGS=([rental-hcx-seed-14b]="--trust-remote-code --dtype bfloat16")
OUT=evals/results/kit
mkdir -p "$OUT" logs
export TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only PYTHONIOENCODING=utf-8 HF_HUB_ENABLE_HF_TRANSFER=1
find scripts src config -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yaml" \) -exec sed -i 's/\r$//' {} +

python3 -m pip install -q "vllm==${VLLM_VERSION}" hf_transfer 2>&1 | tail -2
python3 -m pip install -q -e . 2>&1 | tail -1

serve() {  # $1 alias, $2 extra args, $3 log suffix
  local hf=${HF[$1]} rev=${REV[$1]}
  pkill -f "vllm serve" 2>/dev/null
  sleep 5
  nohup vllm serve "$hf" --revision "$rev" --host 127.0.0.1 --port 8000 --max-model-len 4096 --max-num-seqs 16 \
      --gpu-memory-utilization 0.90 ${XARGS[$1]:-} $2 > "logs/vllm_$1$3.log" 2>&1 &
  for i in $(seq 1 240); do
    curl -sf http://127.0.0.1:8000/v1/models >/dev/null && return 0
    pgrep -f "vllm serve" >/dev/null || break
    sleep 10
  done
  echo "vLLM start failed: $1" | tee -a logs/failures.txt
  tail -40 "logs/vllm_$1$3.log" >> logs/failures.txt
  return 1
}

server_manifest() {  # $1 alias, $2 condition, $3 extra args
  python3 scripts/gpu/server_manifest.py "$1" "$2" "${XARGS[$1]:-} $3" "${HF[$1]}" "${REV[$1]}"
}

k3() {  # $1 alias, $2 tag -> prints K3 verdict (True/False/None) on last line
  python3 scripts/run_kit.py --part K2 --models "kit:$1" --tag "$2" | tail -4
  python3 scripts/run_kit.py --part K3 --models "kit:$1" --tag "$2" 2>&1 | grep -v "/300$" | tail -3
  [ -f "$OUT/K3_$2_rule.jsonl" ] || python3 scripts/run_kit.py --part K3 --models rule,escalate --tag "$2" >/dev/null
  python3 scripts/run_kit.py --summarize --models "rule,escalate,kit:$1" --tag "$2" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['kit:$1'].get('k3_pass'))"
}

for a in "${JUDGE[@]}"; do
  echo "=== judge $a $(date +%T)"
  serve "$a" "" "" || continue
  server_manifest "$a" k3 ""
  PASS=$(k3 "$a" "$TAG" | tail -1)
  echo "K3 verdict $a: $PASS"
  if [ "$PASS" = "True" ]; then
    for cache in on off; do
      if [ "$cache" = off ]; then
        serve "$a" "--no-enable-prefix-caching" "_nocache" || break
        server_manifest "$a" k4_nocache "--no-enable-prefix-caching"
      fi
      for bg in 0 2; do
        if [ "$bg" = 0 ]; then mode=dedicated; else mode=shared; fi
        python3 scripts/replay_requests.py --models "kit:$a" --requests "$OUT/requests_2026-11-11.jsonl" --scale 5 \
            --background-rps "$bg" --tick-timeout 60 --tag "$TAG" --label "cache${cache}_${mode}" | tail -12
      done
    done
  fi
done

for a in "${EXPLORE[@]}"; do
  echo "=== explore $a $(date +%T)"
  serve "$a" "" "" || continue
  server_manifest "$a" k3 ""
  echo "K3 explore $a: $(k3 "$a" "$TAGX" | tail -1)"
done

pkill -f "vllm serve" 2>/dev/null
J=$(printf 'kit:%s,' "${JUDGE[@]}"); X=$(printf 'kit:%s,' "${EXPLORE[@]}")
python3 scripts/run_kit.py --summarize --models "rule,escalate,${J%,}" --tag "$TAG" > /dev/null
python3 scripts/run_kit.py --summarize --models "rule,escalate,${X%,}" --tag "$TAGX" > /dev/null
tar czf results_gpu.tgz "$OUT" logs
echo "=== done $(date +%T): results_gpu.tgz"
