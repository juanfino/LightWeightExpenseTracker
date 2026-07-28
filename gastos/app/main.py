import json
import logging
import os
import threading

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Lee la configuración desde variables de entorno."""
    missing = [
        v for v in (
            "TELEGRAM_TOKEN", "USERS_JSON", "AUTH_SECRET_KEY",
            "TELEGRAM_BOT_USERNAME",
            "AUTH_BOOTSTRAP_EMAIL", "SUPERADMIN_EMAIL",
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            "RESEND_API_KEY", "TURNSTILE_SECRET",
        )
        if not os.environ.get(v)
    ]
    if missing:
        raise RuntimeError(
            f"Variables de entorno requeridas no definidas: {', '.join(missing)}"
        )

    users_raw = os.environ["USERS_JSON"]
    try:
        users = json.loads(users_raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"USERS_JSON no es JSON válido: {e}") from e

    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_api_key:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "OPENAI_API_KEY no configurada — los mensajes de voz no serán procesados"
        )

    return {
        "telegram_token": os.environ["TELEGRAM_TOKEN"],
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai_api_key": openai_api_key,
        "users": users,
    }


def parse_users(users_list: list) -> dict:
    """Convierte lista de usuarios a {telegram_id_str: name}."""
    try:
        return {str(u["telegram_id"]): u["name"] for u in users_list}
    except Exception as e:
        logger.warning("No se pudo parsear usuarios: %s", e)
        return {}


def parse_user_emails(users_list: list) -> dict:
    """Return optional legacy Telegram → web email identity mappings."""
    result = {}
    for user in users_list:
        telegram_id = str(user.get("telegram_id", "")).strip()
        email = str(user.get("email", "")).strip().casefold()
        if telegram_id and email:
            result[telegram_id] = email
    return result


def main():
    # 1. Cargar configuración
    config = load_config()

    token   = config.get("telegram_token", "")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no está definido.")

    # 2. Parsear usuarios
    users = parse_users(config.get("users", []))
    user_emails = parse_user_emails(config.get("users", []))
    logger.info("Usuarios configurados: %s", list(users.values()) or "(ninguno)")

    # 3. Inicializar DB
    import db as database
    database.init_db(users, user_emails=user_emails)
    logger.info("Base de datos PostgreSQL lista")

    # 4. Configurar módulo de backup y programar dump local diario
    import backup as backup_module

    from apscheduler.schedulers.background import BackgroundScheduler
    from zoneinfo import ZoneInfo
    scheduler = BackgroundScheduler(timezone=ZoneInfo("America/Argentina/Buenos_Aires"))
    scheduler.add_job(backup_module.create_backup, "cron", hour=21, minute=0)
    scheduler.start()
    logger.info("Scheduler de backup PostgreSQL → R2 iniciado (21:00 ART diario)")

    # 5. Iniciar Flask en thread daemon
    import dashboard
    dash_thread = threading.Thread(target=dashboard.run_dashboard, daemon=True, name="dashboard")
    dash_thread.start()
    dash_port = int(os.environ.get("DASHBOARD_PORT", 5000))
    logger.info("Dashboard iniciado en puerto %d", dash_port)

    # 6. Iniciar bot Telegram (bloquea el hilo principal)
    import bot
    bot.TELEGRAM_TOKEN = token
    bot.USERS = users
    app = bot.build_app()
    app.bot_data["anthropic_api_key"] = config.get("anthropic_api_key", "")
    app.bot_data["openai_api_key"] = config.get("openai_api_key", "")
    logger.info("Bot Telegram iniciando (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
