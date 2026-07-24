import logging
import os
import shutil
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = "/data/gastos.db"
LAST_BACKUP_PATH = "/data/last_backup.txt"
RETENTION_DAYS = 7


def _backup_dir() -> str:
    return os.path.join(os.path.dirname(DB_PATH) or ".", "backups")


def _prune_old_backups(backup_dir: str) -> int:
    """Deletes backup files older than RETENTION_DAYS. Returns count deleted."""
    cutoff = datetime.now(timezone.utc).timestamp() - RETENTION_DAYS * 86400
    deleted = 0
    for name in os.listdir(backup_dir):
        path = os.path.join(backup_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                deleted += 1
        except OSError as e:
            logger.warning("No se pudo evaluar/borrar backup viejo %s: %s", path, e)
    return deleted


def create_local_backup() -> str | None:
    """Copies gastos.db into a local, timestamped backup file (kept 7 days).

    Replaces the old Telegram broadcast — no process sends the database file
    to any Telegram chat. Returns the ISO UTC timestamp on success, None on failure.
    """
    if not os.path.exists(DB_PATH):
        logger.warning("Backup: DB no encontrada en %s", DB_PATH)
        return None

    backup_dir = _backup_dir()
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = os.path.join(backup_dir, f"gastos_{ts_compact}.db")
        shutil.copy2(DB_PATH, dest)
    except OSError as e:
        logger.error("Error creando backup local: %s", e)
        return None

    deleted = _prune_old_backups(backup_dir)

    ts = datetime.now(timezone.utc).isoformat()
    try:
        with open(LAST_BACKUP_PATH, "w") as f:
            f.write(ts)
    except OSError as e:
        logger.warning("No se pudo escribir last_backup.txt: %s", e)

    logger.info(
        "Backup local creado en %s (%d backup(s) viejo(s) eliminado(s)). Timestamp: %s",
        dest, deleted, ts,
    )
    return ts
