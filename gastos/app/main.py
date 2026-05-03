import json
import logging
import os
import threading

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _parse_users(users_json: str) -> dict:
    """
    Convierte el JSON de usuarios de HA a {telegram_id: name}.
    El valor puede ser una lista de objetos o un string JSON.
    """
    try:
        if isinstance(users_json, str):
            data = json.loads(users_json)
        else:
            data = users_json
        return {str(u["telegram_id"]): u["name"] for u in data}
    except Exception as e:
        logger.warning("No se pudo parsear USERS_JSON: %s", e)
        return {}


def main():
    # 1. Variables de entorno
    token      = os.environ.get("TELEGRAM_TOKEN", "")
    users_json = os.environ.get("USERS_JSON", "[]")
    db_path    = os.environ.get("DB_PATH", "/data/gastos.db")

    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no está definido.")

    # 2. Parsear usuarios
    users = _parse_users(users_json)
    logger.info("Usuarios configurados: %s", list(users.values()) or "(ninguno)")

    # 3. Inicializar DB (crea tablas + seed si es nueva + sincroniza usuarios)
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
    app = bot.build_app()
    logger.info("Bot Telegram iniciando (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
