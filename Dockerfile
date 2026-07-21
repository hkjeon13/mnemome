FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 mnemome \
    && useradd --system --uid 10001 --gid mnemome --home-dir /app mnemome

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY vendor ./vendor
ARG REQUIRE_LOTTE_AGENT=0
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[service]" \
    && python -m nltk.downloader -d /usr/local/share/nltk_data punkt_tab averaged_perceptron_tagger_eng \
    && lotte_wheel="$(find ./vendor -maxdepth 1 -name 'lotte_agent-*.whl' -print -quit)" \
    && if [ -n "$lotte_wheel" ]; then python -m pip install "$lotte_wheel"; \
       elif [ "$REQUIRE_LOTTE_AGENT" = "1" ]; then echo "Lotte Agent wheel is required" >&2; exit 1; fi

RUN mkdir -p /data && chown -R mnemome:mnemome /app /data
USER mnemome

EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=2)"

CMD ["mnemome-api"]
