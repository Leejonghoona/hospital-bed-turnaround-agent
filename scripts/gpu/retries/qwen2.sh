#!/usr/bin/env bash
# Qwen3.8 27B 재시도: 첫 시도는 vLLM을 전체 경로로 띄워 PATH에 ninja가 없어 선형 어텐션 커널 빌드 실패 -> 엔진 종료, 300건 연결 실패(무효 회차).
cd /root/kit
while ! grep -q "=== extra done" /root/extra.log; do sleep 20; done
mkdir -p evals/results/kit/invalid_qwen_try1 && mv evals/results/kit/*qwen3.8-27b* evals/results/kit/invalid_qwen_try1/ && mv logs/vllm_rental-qwen3.8-27b.log evals/results/kit/invalid_qwen_try1/
export PATH=/root/venv/bin:$PATH
sed -n '1,/^P=\/root\/venv\/bin\/python$/p' scripts/gpu/run_extra.sh | grep -vE "pip install|venv2|^python3 -m venv|^/root/venv/bin/python -m pip|^cp -a" > scripts/gpu/qwen_retry.sh
sed -n '/^Q=rental-qwen3.8-27b/,/^G=rental-gemma-4-31b/p' scripts/gpu/run_extra.sh | grep -v "^G=" >> scripts/gpu/qwen_retry.sh
echo "=== qwen retry $(date +%T)"
bash scripts/gpu/qwen_retry.sh
pkill -f "vllm serve"
tar czf /root/results_extra.tgz evals/results/kit logs /root/extra.log /root/qwen2.log
echo "=== qwen2 done $(date +%T)"
