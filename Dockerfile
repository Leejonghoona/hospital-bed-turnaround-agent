# WardFlow 한 컨테이너 배포 (합성 데이터 전용). 화면(React)을 빌드해 FastAPI가 같은 주소에서 내준다.
# 기본은 결정적 가짜 LLM이라 API 키가 없어도 돈다. 접속기록은 /data에 파일로 남는다(볼륨으로 보존).

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src WARDFLOW_DATA_DIR=/data PORT=8000
COPY pyproject.toml ./
COPY src/ src/
COPY config/ config/
RUN pip install --no-cache-dir ".[api]" && pip uninstall -y turnaround
COPY --from=web /web/dist web/dist
RUN useradd -m wardflow && mkdir -p /data && chown wardflow /data
USER wardflow
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"
CMD ["sh", "-c", "uvicorn turnaround.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
