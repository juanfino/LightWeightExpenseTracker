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
INVITATION_DAYS = 7

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
    """Resolve a session to its identity.

    A verified identity with no active family membership is a valid, durable
    state (mid-onboarding) — the joins are LEFT so such a session still
    resolves, just with family_id/role/family_name/timezone as None.
    """
    if not token:
        return None
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT u.id, u.email, u.name, u.color, u.is_superadmin, u.telegram_id,
                   s.csrf_token, s.expires_at,
                   m.family_id, m.role, f.name AS family_name, f.timezone,
                   m.onboarding_dismissed_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN memberships m ON m.user_id = u.id AND m.active
            LEFT JOIN families f ON f.id = m.family_id
            WHERE s.token_hash = %s AND s.expires_at > now()
            """,
            (_hash(token),),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "name": row[2], "color": row[3],
            "is_superadmin": row[4], "telegram_id": row[5],
            "csrf_token": row[6], "expires_at": row[7],
            "family_id": row[8], "role": row[9], "family_name": row[10],
            "timezone": row[11], "onboarding_dismissed": row[12] is not None,
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


def user_has_active_membership(user_id: int) -> bool:
    with platform_transaction() as raw:
        return bool(raw.execute(
            "SELECT 1 FROM memberships WHERE user_id = %s AND active",
            (user_id,),
        ).fetchone())


def dismiss_onboarding(user_id: int) -> None:
    """Manually hide the dashboard's onboarding checklist for this membership.

    Scoped to the active membership (not the user) so leaving and joining or
    creating a different family starts the checklist fresh.
    """
    with platform_transaction() as raw:
        raw.execute(
            "UPDATE memberships SET onboarding_dismissed_at = now() WHERE user_id = %s AND active",
            (user_id,),
        )


def issue_otp(
    email: str,
    *,
    flow: str,
    name: str | None = None,
    family_name: str | None = None,
    invitation_id: int | None = None,
) -> str:
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
            INSERT INTO otp_codes
                (email, code_hash, expires_at, flow, name, family_name, invitation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (email, _hash(code), expires_at, flow, name, family_name, invitation_id),
        )
    return code


def consume_otp(email: str, code: str):
    email = normalize_email(email)
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT id, code_hash, attempts, flow, name, family_name, invitation_id
            FROM otp_codes
            WHERE email = %s AND consumed_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC LIMIT 1
            FOR UPDATE
            """,
            (email,),
        ).fetchone()
        if not row:
            return None
        otp_id, code_hash, attempts, flow, name, family_name, invitation_id = row
        if attempts >= OTP_MAX_ATTEMPTS:
            return None
        if not hmac.compare_digest(code_hash, _hash(code.strip())):
            raw.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = %s", (otp_id,))
            return None
        raw.execute("UPDATE otp_codes SET consumed_at = now() WHERE id = %s", (otp_id,))
        return {
            "email": email, "flow": flow, "name": name,
            "family_name": family_name, "invitation_id": invitation_id,
        }


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


def update_user_name(user_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("El nombre es obligatorio")
    with platform_transaction() as raw:
        raw.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))


def create_family_for_existing_user(user_id: int, family_name: str) -> int:
    family_name = family_name.strip()
    if not family_name:
        raise ValueError("Nombre de familia obligatorio")
    with platform_transaction() as raw:
        if raw.execute(
            "SELECT 1 FROM memberships WHERE user_id = %s AND active", (user_id,)
        ).fetchone():
            raise ValueError("El usuario ya pertenece a una familia")
        family_id = raw.execute(
            "INSERT INTO families (name, created_by_user_id) VALUES (%s, %s) RETURNING id",
            (family_name, user_id),
        ).fetchone()[0]
        raw.execute(
            """
            INSERT INTO memberships (user_id, family_id, role, active)
            VALUES (%s, %s, 'owner', true)
            """,
            (user_id, family_id),
        )
        raw.execute("SET LOCAL ROLE gastos_app")
        raw.execute("SELECT set_config('app.family_id', %s, true)", (str(family_id),))
        import seed
        previous_family_id = pgcompat.current_family_id()
        pgcompat.set_family_id(family_id)
        try:
            seed.create_family_defaults(pgcompat.Connection(raw), family_id)
        finally:
            pgcompat.set_family_id(previous_family_id)
    return family_id


def link_google_identity(provider_user_id: str, email: str, name: str) -> int:
    """Find-or-create the bare identity behind a Google login.

    Never creates a family — a Google-authenticated identity with no
    membership lands on onboarding just like the email/OTP path.
    """
    email = normalize_email(email)
    with platform_transaction() as raw:
        identity = raw.execute(
            """
            SELECT u.id FROM oauth_identities oi
            JOIN users u ON u.id = oi.user_id
            WHERE oi.provider = 'google' AND oi.provider_user_id = %s
            """,
            (provider_user_id,),
        ).fetchone()
        by_email = (
            None if identity
            else raw.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        )
        if identity or by_email:
            user_id = (identity or by_email)[0]
        else:
            user_id = raw.execute(
                "INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id",
                (email, name.strip()),
            ).fetchone()[0]
        raw.execute(
            """
            INSERT INTO oauth_identities (user_id, provider, provider_user_id)
            VALUES (%s, 'google', %s)
            ON CONFLICT (provider, provider_user_id) DO NOTHING
            """,
            (user_id, provider_user_id),
        )
    return user_id


class InvitationError(ValueError):
    pass


class MembershipConflict(InvitationError):
    def __init__(self, family_name: str):
        super().__init__(f"Ya pertenecés a {family_name}. Tenés que salir de ahí primero.")
        self.family_name = family_name


def get_invitation(token: str | None = None, *, invitation_id: int | None = None):
    if not token and invitation_id is None:
        return None
    with platform_transaction() as raw:
        if token:
            where, value = "i.token_hash = %s", _hash(token)
        else:
            where, value = "i.id = %s", invitation_id
        row = raw.execute(
            f"""
            SELECT i.id, i.family_id, f.name, i.expires_at,
                   i.consumed_at, i.revoked_at
            FROM invitations i
            JOIN families f ON f.id = i.family_id
            WHERE {where}
            """,
            (value,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "family_id": row[1], "family_name": row[2],
            "expires_at": row[3], "consumed_at": row[4], "revoked_at": row[5],
            "valid": not row[4] and not row[5] and row[3] > datetime.now(timezone.utc),
        }


def create_invitation(family_id: int, created_by_user_id: int) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITATION_DAYS)
    with platform_transaction() as raw:
        row = raw.execute(
            """
            INSERT INTO invitations
                (family_id, token_hash, role, created_by_user_id, expires_at)
            VALUES (%s, %s, 'member', %s, %s)
            RETURNING id, created_at
            """,
            (family_id, _hash(token), created_by_user_id, expires_at),
        ).fetchone()
    return token, {"id": row[0], "created_at": row[1], "expires_at": expires_at}


def list_family_invitations(family_id: int):
    with platform_transaction() as raw:
        rows = raw.execute(
            """
            SELECT id, expires_at, consumed_at, revoked_at, created_at
            FROM invitations
            WHERE family_id = %s
            ORDER BY created_at DESC
            """,
            (family_id,),
        ).fetchall()
    now = datetime.now(timezone.utc)
    return [
        {
            "id": r[0], "expires_at": r[1], "consumed_at": r[2],
            "revoked_at": r[3], "created_at": r[4],
            "status": (
                "usada" if r[2] else "revocada" if r[3]
                else "vencida" if r[1] <= now else "activa"
            ),
        }
        for r in rows
    ]


def revoke_invitation(invitation_id: int, family_id: int) -> bool:
    with platform_transaction() as raw:
        result = raw.execute(
            """
            UPDATE invitations SET revoked_at = now()
            WHERE id = %s AND family_id = %s
              AND consumed_at IS NULL AND revoked_at IS NULL
            """,
            (invitation_id, family_id),
        )
        return result.rowcount == 1


def create_user_without_family(email: str, name: str) -> int:
    with platform_transaction() as raw:
        return raw.execute(
            "INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id",
            (normalize_email(email), name.strip()),
        ).fetchone()[0]


def accept_invitation(invitation_id: int, user_id: int) -> int:
    with platform_transaction() as raw:
        invitation = raw.execute(
            """
            SELECT family_id, expires_at, consumed_at, revoked_at
            FROM invitations WHERE id = %s FOR UPDATE
            """,
            (invitation_id,),
        ).fetchone()
        if (
            not invitation or invitation[2] or invitation[3]
            or invitation[1] <= datetime.now(timezone.utc)
        ):
            raise InvitationError("La invitación no es válida o ya venció.")
        active = raw.execute(
            """
            SELECT f.name FROM memberships m
            JOIN families f ON f.id = m.family_id
            WHERE m.user_id = %s AND m.active
            """,
            (user_id,),
        ).fetchone()
        if active:
            raise MembershipConflict(active[0])
        family_id = invitation[0]
        historical = raw.execute(
            "SELECT id FROM memberships WHERE user_id = %s AND family_id = %s",
            (user_id, family_id),
        ).fetchone()
        if historical:
            raw.execute(
                """
                UPDATE memberships
                SET active = true, role = 'member', deactivated_at = NULL
                WHERE id = %s
                """,
                (historical[0],),
            )
        else:
            raw.execute(
                """
                INSERT INTO memberships (user_id, family_id, role, active)
                VALUES (%s, %s, 'member', true)
                """,
                (user_id, family_id),
            )
        updated = raw.execute(
            """
            UPDATE invitations
            SET consumed_at = now(), consumed_by_user_id = %s
            WHERE id = %s AND consumed_at IS NULL AND revoked_at IS NULL
            """,
            (user_id, invitation_id),
        )
        if updated.rowcount != 1:
            raise InvitationError("La invitación ya fue utilizada.")
        return family_id


def get_family_management(family_id: int):
    with platform_transaction() as raw:
        family = raw.execute(
            "SELECT id, name FROM families WHERE id = %s", (family_id,)
        ).fetchone()
        members = raw.execute(
            """
            SELECT m.id, u.id, u.name, u.email, m.role, m.active, m.created_at
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            WHERE m.family_id = %s
            ORDER BY m.active DESC, CASE m.role WHEN 'owner' THEN 0 ELSE 1 END,
                     u.name
            """,
            (family_id,),
        ).fetchall()
    return {
        "id": family[0], "name": family[1],
        "members": [
            {
                "membership_id": r[0], "user_id": r[1], "name": r[2],
                "email": r[3], "role": r[4], "active": r[5],
                "created_at": r[6],
            }
            for r in members
        ],
    }


def create_telegram_link_token(user_id: int) -> str:
    """Create a 15-minute, single-use token and invalidate older pending tokens."""
    raw_token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with platform_transaction() as raw:
        raw.execute(
            "UPDATE telegram_link_tokens SET consumed_at = now() "
            "WHERE user_id = %s AND consumed_at IS NULL",
            (user_id,),
        )
        raw.execute(
            """
            INSERT INTO telegram_link_tokens (user_id, token_hash, expires_at)
            VALUES (%s, %s, now() + interval '15 minutes')
            """,
            (user_id, token_hash),
        )
    return raw_token


def telegram_link_status(user_id: int) -> bool:
    with platform_transaction() as raw:
        row = raw.execute(
            "SELECT telegram_id FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return bool(row and row[0])


def consume_telegram_link_token(raw_token: str, telegram_id: str) -> dict:
    """Atomically bind one private Telegram chat to the logged-in web identity."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with platform_transaction() as raw:
        token = raw.execute(
            """
            SELECT t.id, t.user_id, u.name, m.family_id
            FROM telegram_link_tokens t
            JOIN users u ON u.id = t.user_id
            JOIN memberships m ON m.user_id = u.id AND m.active
            WHERE t.token_hash = %s AND t.consumed_at IS NULL
              AND t.expires_at > now()
            FOR UPDATE OF t
            """,
            (token_hash,),
        ).fetchone()
        if not token:
            raise ValueError("Este enlace venció o ya fue utilizado.")
        conflict = raw.execute(
            "SELECT id FROM users WHERE telegram_id = %s AND id <> %s",
            (str(telegram_id), token[1]),
        ).fetchone()
        if conflict:
            raise ValueError("Este Telegram ya está conectado a otra cuenta.")
        raw.execute(
            "UPDATE users SET telegram_id = %s WHERE id = %s",
            (str(telegram_id), token[1]),
        )
        raw.execute(
            "UPDATE telegram_link_tokens SET consumed_at = now() WHERE id = %s",
            (token[0],),
        )
    return {"id": token[1], "name": token[2], "family_id": token[3]}


def unlink_telegram(user_id: int) -> None:
    with platform_transaction() as raw:
        raw.execute("UPDATE users SET telegram_id = NULL WHERE id = %s", (user_id,))
        raw.execute(
            "UPDATE telegram_link_tokens SET consumed_at = now() "
            "WHERE user_id = %s AND consumed_at IS NULL",
            (user_id,),
        )


def rename_family(family_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("El nombre de la familia es obligatorio.")
    with platform_transaction() as raw:
        raw.execute("UPDATE families SET name = %s WHERE id = %s", (name, family_id))


def remove_family_member(family_id: int, user_id: int) -> None:
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT role FROM memberships
            WHERE family_id = %s AND user_id = %s AND active
            FOR UPDATE
            """,
            (family_id, user_id),
        ).fetchone()
        if not row:
            raise ValueError("El miembro ya no está activo.")
        if row[0] == "owner":
            raise ValueError("Transferí la propiedad antes de remover al owner.")
        raw.execute(
            """
            UPDATE memberships SET active = false, deactivated_at = now()
            WHERE family_id = %s AND user_id = %s AND active
            """,
            (family_id, user_id),
        )
        raw.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def transfer_family_ownership(
    family_id: int, current_owner_id: int, new_owner_id: int
) -> None:
    if current_owner_id == new_owner_id:
        raise ValueError("Elegí otro miembro.")
    with platform_transaction() as raw:
        rows = raw.execute(
            """
            SELECT user_id, role FROM memberships
            WHERE family_id = %s AND user_id IN (%s, %s) AND active
            FOR UPDATE
            """,
            (family_id, current_owner_id, new_owner_id),
        ).fetchall()
        roles = {r[0]: r[1] for r in rows}
        if roles.get(current_owner_id) != "owner" or roles.get(new_owner_id) != "member":
            raise ValueError("La transferencia ya no es válida.")
        raw.execute(
            "UPDATE memberships SET role = 'member' WHERE family_id = %s AND user_id = %s",
            (family_id, current_owner_id),
        )
        raw.execute(
            "UPDATE memberships SET role = 'owner' WHERE family_id = %s AND user_id = %s",
            (family_id, new_owner_id),
        )


def leave_family(family_id: int, user_id: int) -> None:
    remove_family_member(family_id, user_id)


def delete_family(family_id: int, owner_id: int, confirmation: str) -> None:
    with platform_transaction() as raw:
        row = raw.execute(
            """
            SELECT f.name
            FROM families f
            JOIN memberships m ON m.family_id = f.id
            WHERE f.id = %s AND m.user_id = %s AND m.role = 'owner' AND m.active
            FOR UPDATE
            """,
            (family_id, owner_id),
        ).fetchone()
        if not row:
            raise ValueError("Solo el owner puede eliminar la familia.")
        if not hmac.compare_digest(row[0], confirmation.strip()):
            raise ValueError("El nombre ingresado no coincide exactamente.")
        user_ids = [
            r[0] for r in raw.execute(
                "SELECT user_id FROM memberships WHERE family_id = %s", (family_id,)
            ).fetchall()
        ]
        raw.execute("DELETE FROM families WHERE id = %s", (family_id,))
        if user_ids:
            raw.execute("DELETE FROM sessions WHERE user_id = ANY(%s)", (user_ids,))
