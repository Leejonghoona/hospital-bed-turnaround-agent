#!/usr/bin/env bash
# Gemma 4 31B 재시도: 내려받기 오류(파일 재구성 실패) -> 가중치를 먼저 받아 두고 같은 환경(transformers 5.5.4)에서 측정
cd /root/kit
export HF_HOME=/root/hf
for i in 1 2 3; do
  HF_HUB_ENABLE_HF_TRANSFER=0 /root/venv2/bin/python -c "from huggingface_hub import snapshot_download as s; s('google/gemma-4-31B-it', revision='842da3794eaa0b77d5f08bae87a17459d91ff475')" && break
  echo "download retry $i"; sleep 20
done
while ! grep -q "=== all done" /root/retry2.log; do sleep 30; done
echo "=== retry gemma-4-31b $(date +%T) predownloaded"
sed -e "s/^JUDGE=.*/JUDGE=()/" -e "s/^EXPLORE=.*/EXPLORE=(rental-gemma-4-31b)/" -e '/pip install -q "vllm==/d' \
    scripts/gpu/run_on_gpu.sh > scripts/gpu/retry_g31.sh
PATH=/root/venv2/bin:$PATH bash scripts/gpu/retry_g31.sh
pkill -f "vllm serve"
tar czf /root/results_all.tgz evals/results/kit logs /root/run.log /root/retry.log /root/retry2.log /root/retry3.log
echo "=== final done $(date +%T)"
