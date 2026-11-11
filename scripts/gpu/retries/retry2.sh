#!/usr/bin/env bash
# HyperCLOVA X SEED 재시도: vLLM 내장 구현 사용(--trust-remote-code 제거, bf16 유지)
cd /root/kit
while ! grep -q "=== retry done" /root/retry.log; do sleep 30; done
echo "=== retry hcx $(date +%T) without --trust-remote-code"
sed -e "s/^JUDGE=.*/JUDGE=()/" -e "s/^EXPLORE=.*/EXPLORE=(rental-hcx-seed-14b)/" -e '/pip install -q "vllm==/d' \
    -e 's/^declare -A XARGS=.*/declare -A XARGS=([rental-hcx-seed-14b]="--dtype bfloat16")/' \
    scripts/gpu/run_on_gpu.sh > scripts/gpu/retry_hcx.sh
PATH=/root/venv/bin:$PATH HF_HOME=/root/hf bash scripts/gpu/retry_hcx.sh
pkill -f "vllm serve"
tar czf /root/results_all.tgz evals/results/kit logs /root/run.log /root/retry.log /root/retry2.log
echo "=== all done $(date +%T)"
