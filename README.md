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
- An anonymous visit reaches the application's own login, not Cloudflare Access
- Google always shows the account selector; email OTP reaches the verified sender
- `/dashboard` redirects to `/login` without a session and private APIs return `401`
- Bot responds in Telegram

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_BOT_USERNAME` | Yes | Bot username without `@`; used to build one-tap linking URLs |
| `PUBLIC_DASHBOARD_URL` | No | Public dashboard base URL used in linking help |
| `USERS_JSON` | Yes | Authorized users; optional `email` links a legacy Telegram identity to web login without duplication |
| `AUTH_SECRET_KEY` | Yes | Random secret used to sign short-lived OAuth/pre-auth state |
| `AUTH_BOOTSTRAP_EMAIL` | Yes | Email attached once to the existing family-1 owner |
| `SUPERADMIN_EMAIL` | Yes | Sole superadmin email; applied at startup and never writable through HTTP |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth web client credentials |
| `RESEND_API_KEY` | Yes | Sends six-digit login/registration codes |
| `RESEND_FROM_EMAIL` | No | Verified sender; defaults to `acceso@juampifinochietto.com` |
| `TURNSTILE_SECRET` | Yes | Private Cloudflare Turnstile siteverify secret |
| `ANTHROPIC_API_KEY` | No | Enables OCR of ticket photos, voice/dollar extraction, and the natural-language intent layer |
| `OPENAI_API_KEY` | No | Enables voice message transcription (Whisper) |
| `DATABASE_URL` | Yes | PostgreSQL connection URL |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL service password |
| `R2_ENDPOINT`, `R2_BUCKET` | Yes | Private Cloudflare R2 backup destination |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Yes | Bucket-scoped R2 credentials |
| `DASHBOARD_PORT` | No | Dashboard port (default: `5000`; the Pi sets this to `8090` to free up 5000 for Frigate) |

Copy `.env.example` to `~/.env` on the Pi and fill in the values.

### Persistent data

PostgreSQL is persisted at `~/postgres-data`. Daily dumps are stored off-device
in private Cloudflare R2 with 90-day retention; see `docs/RUNBOOK.md`.

### Exposing the dashboard

The dashboard's own default port is 5000, but the Pi's `~/.env` sets `DASHBOARD_PORT=8090` (freeing port 5000 for Frigate). A Cloudflare Tunnel routes `expenses.juampifinochietto.com → localhost:8090` on the Pi. Cloudflare Access was removed after the application-owned authentication passed the Phase 3 production checks; the Tunnel and Turnstile remain active and must not be removed with it.
