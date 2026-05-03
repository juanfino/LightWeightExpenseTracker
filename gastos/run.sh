#!/usr/bin/with-contenv bashio

export TELEGRAM_TOKEN=$(bashio::config 'telegram_token')
export USERS_JSON=$(bashio::config 'users')
export DB_PATH="/data/gastos.db"

python3 /app/main.py
