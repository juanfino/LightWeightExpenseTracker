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
    missing = [v for v in ("TELEGRAM_TOKEN", "USERS_JSON") if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Variables de entorno requeridas no definidas: {', '.join(missing)}"
        )

    users_raw = os.environ["USERS_JSON"]
    try:
        users = json.loads(users_raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"USERS_JSON no es JSON válido: {e}") from e

    return {
        "telegram_token": os.environ["TELEGRAM_TOKEN"],
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "users": users,
    }


def parse_users(users_list: list) -> dict:
    """Convierte lista de usuarios a {telegram_id_str: name}."""
    try:
        return {str(u["telegram_id"]): u["name"] for u in users_list}
    except Exception as e:
        logger.warning("No se pudo parsear usuarios: %s", e)
        return {}


def main():
    # 1. Cargar configuración
    config = load_config()

    token   = config.get("telegram_token", "")
    db_path = os.getenv("DB_PATH", "/data/gastos.db")

    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no está definido.")

    # 2. Parsear usuarios
    users = parse_users(config.get("users", []))
    logger.info("Usuarios configurados: %s", list(users.values()) or "(ninguno)")

    # 3. Inicializar DB
    import db as database
    database.DB_PATH = db_path
    database.init_db(users)
    logger.info("Base de datos lista en %s", db_path)

    # 4. Configurar módulo de backup y programar envío diario
    import backup as backup_module
    backup_module.TELEGRAM_TOKEN = token
    backup_module.USERS = users
    backup_module.DB_PATH = db_path

    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    # 21:00 ART = 00:00 UTC
    scheduler.add_job(backup_module.send_db_backup, "cron", hour=0, minute=0)
    scheduler.start()
    logger.info("Scheduler de backup iniciado (21:00 ART diario)")

    # 5. Iniciar Flask en thread daemon
    import dashboard
    dash_thread = threading.Thread(target=dashboard.run_dashboard, daemon=True, name="dashboard")
    dash_thread.start()
    logger.info("Dashboard iniciado en puerto 5000")

    # 6. Iniciar bot Telegram (bloquea el hilo principal)
    import bot
    bot.TELEGRAM_TOKEN = token
    bot.USERS = users
    app = bot.build_app()
    app.bot_data["anthropic_api_key"] = config.get("anthropic_api_key", "")
    logger.info("Bot Telegram iniciando (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()