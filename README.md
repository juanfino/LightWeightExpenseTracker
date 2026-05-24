# LightWeightExpenseTracker

A family expense tracker: send a message like `Supermercado 150000` to a Telegram bot and it records, categorizes, and displays your spending in a web dashboard. Runs as a Docker container on a Raspberry Pi 4.

## Deployment

### Making a change

1. Code your change locally
2. Push to `main` (or merge a PR to `main`)
3. GitHub Actions builds and pushes the image automatically (~2-3 min)
4. Check the Actions tab to confirm the run is green

The image is published to `ghcr.io/juanfino/lightweightexpensetracker:latest` (multi-arch: `linux/arm64`, `linux/amd64`). The same commit is also tagged with its git SHA for traceability.

### Deploying to the Pi

SSH into the Pi and run:

```bash
ssh juanfino@192.168.68.72 "docker compose pull gastos && docker compose up -d gastos"
ssh juanfino@192.168.68.72 "docker logs -f gastos"
```

### Verifying

- Logs show "Bot Telegram iniciando" and polling requests
- Dashboard available at https://expenses.juampifinochietto.com
- Bot responds in Telegram

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

The dashboard runs on port 5000. A Cloudflare Tunnel routes `expenses.juampifinochietto.com → localhost:5000` on the Pi.
