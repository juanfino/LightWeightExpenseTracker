import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)

LAST_BACKUP_PATH = "/data/last_backup.txt"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable requerida {name}")
    return value


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=_required_env("R2_ENDPOINT"),
        aws_access_key_id=_required_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_required_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def create_backup() -> str | None:
    """Create a compressed PostgreSQL dump and upload it to the private R2 bucket."""
    now = datetime.now(timezone.utc)
    object_key = f"daily/gastos_{now.strftime('%Y%m%dT%H%M%SZ')}.dump"
    temp_path = None
    try:
        database_url = _required_env("DATABASE_URL")
        bucket = _required_env("R2_BUCKET")
        with tempfile.NamedTemporaryFile(prefix="gastos_", suffix=".dump", delete=False) as tmp:
            temp_path = tmp.name
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", temp_path, database_url],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if os.path.getsize(temp_path) == 0:
            raise RuntimeError("pg_dump generó un archivo vacío")
        client = _r2_client()
        client.upload_file(temp_path, bucket, object_key)
        size = os.path.getsize(temp_path)
        remote_size = client.head_object(Bucket=bucket, Key=object_key)["ContentLength"]
        if remote_size != size:
            raise RuntimeError(f"tamaño remoto inesperado: local={size}, remoto={remote_size}")
    except Exception as e:
        logger.exception("Error creando/subiendo backup PostgreSQL: %s", e)
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    ts = now.isoformat()
    try:
        with open(LAST_BACKUP_PATH, "w") as f:
            f.write(ts)
    except OSError as e:
        logger.warning("No se pudo escribir last_backup.txt: %s", e)

    logger.info(
        "Backup PostgreSQL verificado en R2: s3://%s/%s. Timestamp: %s",
        bucket, object_key, ts,
    )
    return ts


# Kept temporarily for callers deployed during the 2.6.0 transition.
create_local_backup = create_backup
