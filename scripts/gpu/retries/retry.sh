#!/usr/bin/env bash
# 본 측정 뒤 재시도: Gemma 4 두 개(transformers 5.5.4 환경), gpt-oss-120b(메모리 할당 방식만 변경)
cd /root/kit
while ! grep -q "=== done" /root/run.log; do sleep 30; done
cp results_gpu.tgz /root/results_main.tgz
mk() {  # $1 judge list, $2 explore list -> /root/kit/scripts/gpu/retry_run.sh
  sed -e "s/^JUDGE=.*/JUDGE=($1)/" -e "s/^EXPLORE=.*/EXPLORE=($2)/" -e '/pip install -q "vllm==/d' \
      scripts/gpu/run_on_gpu.sh > scripts/gpu/retry_run.sh
}
echo "=== retry gemma $(date +%T) transformers 5.5.4"
mk "rental-gemma-4-26b" "rental-gemma-4-31b"
PATH=/root/venv2/bin:$PATH HF_HOME=/root/hf bash scripts/gpu/retry_run.sh
echo "=== retry gpt-oss-120b $(date +%T) PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"
mk "rental-gpt-oss-120b" ""
PATH=/root/venv/bin:$PATH HF_HOME=/root/hf PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True bash scripts/gpu/retry_run.sh
pkill -f "vllm serve"
tar czf /root/results_all.tgz evals/results/kit logs /root/run.log /root/retry.log
echo "=== retry done $(date +%T)"
