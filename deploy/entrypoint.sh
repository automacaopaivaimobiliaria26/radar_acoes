#!/bin/sh
set -eu

mkdir -p /app/dados /app/logs /app/saida
chown -R radar:radar /app/dados /app/logs /app/saida

exec "$@"
