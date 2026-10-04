FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cron tzdata util-linux passwd ca-certificates \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --system radar \
    && useradd --system --gid radar --home-dir /app --shell /usr/sbin/nologin radar

COPY --chown=radar:radar . /app

RUN mkdir -p /app/dados /app/logs /app/saida \
    && chown -R radar:radar /app \
    && install -m 0644 /app/deploy/radar.cron /etc/cron.d/radar \
    && chmod 0755 /app/deploy/entrypoint.sh

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["cron", "-f"]
