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
    """
    Lee la configuración desde /data/options.json (escrito por HA Supervisor).
    Fallback a variables de entorno para desarrollo local.
    """
    options_path = os.getenv("OPTIONS_PATH", "/data/options.json")

    if os.path.exists(options_path):
        with open(options_path) as f:
            config = json.load(f)
        logger.info("Configuración cargada desde %s", options_path)
        return config

    # Fallback para desarrollo local
    logger.warning("options.json no encontrado, usando variables de entorno")
    users_raw = os.environ.get("USERS_JSON", "[]")
    try:
        users = json.loads(users_raw) if isinstance(users_raw, str) else users_raw
    except Exception:
        users = []

    return {
        "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
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
        raise RuntimeError("telegram_token no está definido en la configuración.")

    # 2. Parsear usuarios
    users = parse_users(config.get("users", []))
    logger.info("Usuarios configurados: %s", list(users.values()) or "(ninguno)")

    # 3. Inicializar DB
    import db as database
    database.DB_PATH = db_path
    database.init_db(users)
    logger.info("Base de datos lista en %s", db_path)

    # 4. Iniciar Flask en thread daemon
    import dashboard
    dash_thread = threading.Thread(target=dashboard.run_dashboard, daemon=True, name="dashboard")
    dash_thread.start()
    logger.info("Dashboard iniciado en puerto 5000")

    # 5. Iniciar bot Telegram (bloquea el hilo principal)
    import bot
    bot.TELEGRAM_TOKEN = token
    bot.USERS = users
    app = bot.build_app()
    app.bot_data["anthropic_api_key"] = config.get("anthropic_api_key", "")
    logger.info("Bot Telegram iniciando (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()