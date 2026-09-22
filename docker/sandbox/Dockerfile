# test.run のランナーイメージ（ネットワーク無しで pytest を実行する最小構成）。
# 依存はここに同梱したものだけ。pip install は実行時にできない（--network none）。
FROM python:3.12-slim
RUN pip install --no-cache-dir "pytest>=8" "fastapi>=0.133" "pydantic>=2.10" \
    && useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin sandbox || true
WORKDIR /work
USER 65534
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1 PYTHONPATH=/work/head
ENTRYPOINT ["python", "-m", "pytest"]
