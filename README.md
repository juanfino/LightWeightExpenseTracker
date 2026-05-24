# LightWeightExpenseTracker

A family expense tracker: send a message like `Supermercado 150000` to a Telegram bot and it records, categorizes, and displays your spending in a web dashboard. Runs as a Docker container on a Raspberry Pi 4.

## Deployment

### Continuous delivery

Every push to `main` builds a multi-arch image (`linux/arm64`, `linux/amd64`) and pushes it to:

```
ghcr.io/juanfino/lightweightexpensetracker:latest
```

The same commit is also tagged with its git SHA for traceability.

### Deploy / update on the Pi

```bash
docker compose pull gastos && docker compose up -d gastos
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token from @BotFather |
| `USERS_JSON` | Yes | JSON array of authorized users, e.g. `[{"telegram_id":"123","name":"Juampi"}]` |
| `ANTHROPIC_API_KEY` | No | Enables OCR of ticket photos via Claude |
| `DB_PATH` | No | Path to SQLite file (default: `/data/gastos.db`) |

Copy `.env.example` to `~/.env` on the Pi and fill in the values.

### Persistent data

The SQLite database is mounted from `~/gastos-data/gastos.db` on the Pi into `/data/gastos.db` inside the container.

### Exposing the dashboard

The dashboard runs on port 5000. Configure a Cloudflare Tunnel entry pointing `expenses.yourdomain.com → localhost:5000`.
