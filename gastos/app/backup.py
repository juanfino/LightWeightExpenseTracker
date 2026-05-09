import asyncio
import io
import logging
import os
from datetime import datetime, timezone

from telegram import Bot

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = ""
USERS: dict = {}   # {telegram_id_str: name}
DB_PATH = "/data/gastos.db"
LAST_BACKUP_PATH = "/data/last_backup.txt"


async def _send_backup_async() -> int:
    bot = Bot(token=TELEGRAM_TOKEN)
    async with bot:
        with open(DB_PATH, "rb") as f:
            data = f.read()
        sent = 0
        for chat_id in USERS:
            try:
                await bot.send_document(
                    chat_id=int(chat_id),
                    document=io.BytesIO(data),
                    filename="gastos.db",
                    caption="🗄 Backup de la base de datos",
                )
                sent += 1
            except Exception as e:
                logger.error("Error enviando backup a %s: %s", chat_id, e)
    return sent


def send_db_backup() -> str | None:
    """Sends gastos.db to all configured users via Telegram.

    Returns the ISO UTC timestamp on success, None on failure.
    """
    if not TELEGRAM_TOKEN or not USERS:
        logger.warning("Backup: token o usuarios no configurados")
        return None
    if not os.path.exists(DB_PATH):
        logger.warning("Backup: DB no encontrada en %s", DB_PATH)
        return None
    try:
        sent = asyncio.run(_send_backup_async())
    except Exception as e:
        logger.error("Error en send_db_backup: %s", e)
        return None

    ts = datetime.now(timezone.utc).isoformat()
    try:
        with open(LAST_BACKUP_PATH, "w") as f:
            f.write(ts)
    except Exception as e:
        logger.warning("No se pudo escribir last_backup.txt: %s", e)

    logger.info("Backup enviado a %d usuario(s). Timestamp: %s", sent, ts)
    return ts
