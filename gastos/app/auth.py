"""Application-owned web authentication.

Authenticated browser sessions are opaque random tokens. Only their SHA-256
hashes are persisted, so a database read cannot be turned directly into an
authenticated browser session.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import requests

import pgcompat

SESSION_COOKIE = "gastos_session"
SESSION_DAYS = 30
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

_rate_lock = threading.Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _platform_connection():
    raw = pgcompat.current_pool().getconn()
    try:
        # Transaction-local role selection is critical with pooled
        # connections: no privileged role may survive into the next borrower.
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        return raw
    except Exception:
        pgcompat.current_pool().putconn(raw)
        raise


def _put_platform_connection(raw) -> None:
    pgcompat.current_pool().putconn(raw)


def platform_transaction():
    class _Context:
        def __enter__(self):
            self.raw = _platform_connection()
            return self.raw

        def __exit__(self, exc_type, _exc, _tb):
            try:
                self.raw.rollback() if exc_type else self.raw.commit()
            finally:
                _put_platform_connection(self.raw)
    return _Context()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def consume_rate_limit(key: str, *, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _rate_lock:
        events = _rate_events[key]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


def verify_turnstile(token: str | None, remote_ip: str | None) -> bool:
    secret = os.environ.get("TURNSTILE_SECRET", "")
    if not secret:
        return os.environ.get("FLASK_ENV") == "development" or os.environ.get("TESTING") == "1"
    if not token:
        return False
    response = requests.post(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data={"secret": secret, "response": token, "remoteip": remote_ip or ""},
        timeout=8,
    )
    response.raise_for_status()
    return bool(response.json().get("success"))


def create_session(user_id: int, user_agent: str | None, ip: str | None) -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with platform_transaction() as raw:
        raw.execute("DELETE FROM sessions WHERE expires_at <= now()")
        raw.execute(
            """
            INSERT INTO sessions (user_id, token_hash, csrf_token, expires_at, user_agent, ip)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, _hash(token), csrf_token, expires_at, (user_agent or "")[:500], ip),
        )
        raw.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))
    return token, csrf_token


def resolve_session(token: str | None):
    if not token:
        return None
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT u.id, u.email, u.name, u.color, s.csrf_token, s.expires_at,
                   m.family_id, m.role, f.name AS family_name, f.timezone
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            JOIN memberships m ON m.user_id = u.id
            JOIN families f ON f.id = m.family_id
            WHERE s.token_hash = %s AND s.expires_at > now()
            """,
            (_hash(token),),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "name": row[2], "color": row[3],
            "csrf_token": row[4], "expires_at": row[5], "family_id": row[6],
            "role": row[7], "family_name": row[8], "timezone": row[9],
        }


def revoke_session(token: str | None) -> None:
    if not token:
        return
    with platform_transaction() as raw:
        raw.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash(token),))


def find_user_by_email(email: str):
    with platform_transaction() as raw:
        row = raw.execute("SELECT id, email, name FROM users WHERE email = %s", (normalize_email(email),)).fetchone()
        return {"id": row[0], "email": row[1], "name": row[2]} if row else None


def issue_otp(email: str, *, flow: str, name: str | None = None, family_name: str | None = None) -> str:
    email = normalize_email(email)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_MINUTES)
    with platform_transaction() as raw:
        raw.execute(
            "UPDATE otp_codes SET consumed_at = now() WHERE email = %s AND consumed_at IS NULL",
            (email,),
        )
        raw.execute(
            """
            INSERT INTO otp_codes (email, code_hash, expires_at, flow, name, family_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (email, _hash(code), expires_at, flow, name, family_name),
        )
    return code


def consume_otp(email: str, code: str):
    email = normalize_email(email)
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT id, code_hash, attempts, flow, name, family_name
            FROM otp_codes
            WHERE email = %s AND consumed_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC LIMIT 1
            FOR UPDATE
            """,
            (email,),
        ).fetchone()
        if not row:
            return None
        otp_id, code_hash, attempts, flow, name, family_name = row
        if attempts >= OTP_MAX_ATTEMPTS:
            return None
        if not hmac.compare_digest(code_hash, _hash(code.strip())):
            raw.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = %s", (otp_id,))
            return None
        raw.execute("UPDATE otp_codes SET consumed_at = now() WHERE id = %s", (otp_id,))
        return {"email": email, "flow": flow, "name": name, "family_name": family_name}


def send_otp(email: str, code: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    sender = os.environ.get("RESEND_FROM_EMAIL", "Gastos Familiares <acceso@juampifinochietto.com>")
    if not api_key:
        if os.environ.get("TESTING") == "1":
            return
        raise RuntimeError("RESEND_API_KEY no está configurada")
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": sender,
            "to": [email],
            "subject": "Tu código de acceso",
            "html": (
                "<p>Tu código para Gastos Familiares es:</p>"
                f"<p style='font-size:32px;font-weight:700;letter-spacing:6px'>{code}</p>"
                "<p>Vence en 10 minutos y sólo puede usarse una vez.</p>"
            ),
        },
        timeout=10,
    )
    response.raise_for_status()


def create_account(email: str, name: str, family_name: str) -> int:
    email = normalize_email(email)
    name = name.strip()
    family_name = family_name.strip()
    if not name or not family_name:
        raise ValueError("Nombre y familia son obligatorios")
    with platform_transaction() as raw:
        user_id = raw.execute(
            "INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id",
            (email, name),
        ).fetchone()[0]
        family_id = raw.execute(
            "INSERT INTO families (name, created_by_user_id) VALUES (%s, %s) RETURNING id",
            (family_name, user_id),
        ).fetchone()[0]
        raw.execute(
            "INSERT INTO memberships (user_id, family_id, role) VALUES (%s, %s, 'owner')",
            (user_id, family_id),
        )
        # Seed inside the same transaction so registration cannot leave an
        # owner/family without its initial taxonomy after a partial failure.
        raw.execute("SET LOCAL ROLE gastos_app")
        raw.execute("SELECT set_config('app.family_id', %s, true)", (str(family_id),))
        import seed
        previous_family_id = pgcompat.current_family_id()
        pgcompat.set_family_id(family_id)
        try:
            seed.create_family_defaults(pgcompat.Connection(raw), family_id)
        finally:
            pgcompat.set_family_id(previous_family_id)
    return user_id


def link_google_identity(provider_user_id: str, email: str, name: str, family_name: str | None) -> int:
    email = normalize_email(email)
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT u.id FROM oauth_identities oi
            JOIN users u ON u.id = oi.user_id
            WHERE oi.provider = 'google' AND oi.provider_user_id = %s
            """,
            (provider_user_id,),
        ).fetchone()
        if row:
            return row[0]
        row = raw.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
    user_id = row[0] if row else create_account(email, name, family_name or f"Familia de {name}")
    with platform_transaction() as raw:
        raw.execute(
            """
            INSERT INTO oauth_identities (user_id, provider, provider_user_id)
            VALUES (%s, 'google', %s)
            ON CONFLICT (provider, provider_user_id) DO NOTHING
            """,
            (user_id, provider_user_id),
        )
    return user_id
