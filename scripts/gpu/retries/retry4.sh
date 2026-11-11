#!/usr/bin/env bash
# 디스크 부족으로 끊긴 두 모델 재시도.
#  gpt-oss-120b: expandable_segments(메모리 부족 해결 확인됨) + 디스크 확보
#  HyperCLOVA X SEED: 고정 커밋을 로컬로 받아 config.json의 auto_map만 지우고 vLLM 내장 구현으로 서빙
cd /root/kit
export HF_HOME=/root/hf
while ! grep -q "=== final done" /root/retry3.log; do sleep 30; done
free_model() { d=/root/hf/hub/models--$1; [ -d "$d" ] || return; for l in $(find $d -type l); do rm -f "$(readlink -f $l)"; done; rm -rf "$d"; }
free_model google--gemma-4-31B-it; free_model google--gemma-4-26B-A4B-it; rm -rf /tmp/torchinductor_root
df -h / | tail -1
echo "=== retry gpt-oss-120b(2) $(date +%T)"
sed -e "s/^JUDGE=.*/JUDGE=(rental-gpt-oss-120b)/" -e "s/^EXPLORE=.*/EXPLORE=()/" -e '/pip install -q "vllm==/d' \
    scripts/gpu/run_on_gpu.sh > scripts/gpu/retry_120b.sh
PATH=/root/venv/bin:$PATH PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True bash scripts/gpu/retry_120b.sh
pkill -f "vllm serve"; sleep 5
free_model openai--gpt-oss-120b; rm -rf /tmp/torchinductor_root
echo "=== retry hcx(2) $(date +%T) local copy, auto_map removed"
L=/root/hcx14b
/root/venv/bin/python -c "from huggingface_hub import snapshot_download as s; s('naver-hyperclovax/HyperCLOVAX-SEED-Think-14B', revision='9b74e35d4c7e4ffec489f4171273caca8948a2b9', local_dir='$L')"
/root/venv/bin/python -c "import json;p='$L/config.json';c=json.load(open(p));c.pop('auto_map',None);json.dump(c,open(p,'w'),indent=1)"
sed -e "s/^JUDGE=.*/JUDGE=()/" -e "s/^EXPLORE=.*/EXPLORE=(rental-hcx-seed-14b)/" -e '/pip install -q "vllm==/d' \
    -e "s#\[rental-hcx-seed-14b\]=naver-hyperclovax/HyperCLOVAX-SEED-Think-14B#[rental-hcx-seed-14b]=$L#" \
    -e 's/^declare -A XARGS=.*/declare -A XARGS=([rental-hcx-seed-14b]="--dtype bfloat16 --served-model-name naver-hyperclovax\/HyperCLOVAX-SEED-Think-14B")/' \
    -e 's/--revision "\$rev" //' \
    scripts/gpu/run_on_gpu.sh > scripts/gpu/retry_hcx2.sh
PATH=/root/venv/bin:$PATH bash scripts/gpu/retry_hcx2.sh
pkill -f "vllm serve"
tar czf /root/results_all.tgz evals/results/kit logs /root/run.log /root/retry.log /root/retry2.log /root/retry3.log /root/retry4.log
echo "=== last done $(date +%T)"
