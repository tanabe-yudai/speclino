# test.run のランナーイメージ（ネットワーク無しで pytest を実行する最小構成）。
# 依存はここに同梱したものだけ。pip install は実行時にできない（--network none）。
FROM python:3.12-slim
# 本番の rootfs（Cloud Run sandboxes は本番イメージをそのまま使う）と揃える。
# ここに無い依存を import するテストは収集時に落ちるので、宣言済みテストが必要とするものを足す。
RUN pip install --no-cache-dir "pytest>=8" "fastapi>=0.133" "pydantic>=2.10" "PyYAML>=6" \
    && useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin sandbox || true
WORKDIR /work
USER 65534
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1 PYTHONPATH=/work/head
ENTRYPOINT ["python", "-m", "pytest"]
