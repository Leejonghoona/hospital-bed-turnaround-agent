#!/usr/bin/env bash
# HyperCLOVA X SEED 세 번째 시도(2026-11-11 05:02). vLLM 0.19.0은 HyperCLOVAXConfig를 포함하지만 설정 레지스트리에 등록하지 않는다.
# 그래서 vLLM의 vllm/transformers_utils/configs/hyperclovax.py를 고정 커밋 로컬 사본(/root/hcx14b)에 configuration_hyperclovax.py로 넣고
# config.json auto_map을 AutoConfig 하나로 바꾼 뒤 --trust-remote-code로 서빙했다. 모델 구현은 vLLM 내장(HyperCLOVAXForCausalLM), 가중치는 그대로.
V=/root/venv/lib/python3.12/site-packages/vllm/transformers_utils/configs/hyperclovax.py
cp $V /root/hcx14b/configuration_hyperclovax.py
/root/venv/bin/python -c "import json;p='/root/hcx14b/config.json';c=json.load(open(p));c['auto_map']={'AutoConfig':'configuration_hyperclovax.HyperCLOVAXConfig'};json.dump(c,open(p,'w'),indent=1)"
cd /root/kit
sed -i 's/XARGS=(\[rental-hcx-seed-14b\]="--dtype bfloat16/XARGS=([rental-hcx-seed-14b]="--trust-remote-code --dtype bfloat16/' scripts/gpu/retry_hcx2.sh
PATH=/root/venv/bin:$PATH HF_HOME=/root/hf bash scripts/gpu/retry_hcx2.sh
