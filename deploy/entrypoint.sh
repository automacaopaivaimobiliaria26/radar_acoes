#!/bin/sh
set -eu

if [ "${RADAR_INIT_STORAGE:-1}" = "1" ]; then
    mkdir -p /app/dados /app/logs /app/saida
    chown -R radar:radar /app/dados /app/logs /app/saida
fi

exec "$@"
