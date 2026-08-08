import hashlib
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from psycopg import IntegrityError
from psycopg.rows import dict_row

import pgcompat
import money

logger = logging.getLogger(__name__)

# Las monedas son deliberadamente pocas: un gasto conserva siempre su valor
# original y la aplicación nunca convierte ni suma importes de distinta moneda.
SUPPORTED_CURRENCIES = ("ARS", "USD")
DEFAULT_CURRENCY = "ARS"


def normalize_currency(currency: str | None) -> str:
    """Return a supported ISO currency, defaulting to ARS for legacy callers."""
    value = (currency or DEFAULT_CURRENCY).upper().strip()
    if value not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Moneda inválida: {currency}")
    return value

# Color por defecto de un usuario recién creado (indigo). Se usa como sentinela:
# un usuario que todavía lo tiene se considera "sin color asignado".
DEFAULT_USER_COLOR = "#6366f1"

# Paleta de colores bien separados para distinguir usuarios en el dashboard.
USER_COLOR_PALETTE = [
    "#6366f1",  # indigo
    "#f59e0b",  # ámbar
    "#10b981",  # esmeralda
    "#ec4899",  # rosa
    "#06b6d4",  # cyan
    "#8b5cf6",  # violeta
    "#ef4444",  # rojo
    "#84cc16",  # lima
]

@contextmanager
def get_conn():
    with pgcompat.current_pool().connection() as raw:
        conn = pgcompat.Connection(raw)
        try:
            family_id = pgcompat.current_family_id()
            if family_id is not None:
                raw.execute("SET LOCAL ROLE gastos_app")
                raw.execute(
                    "SELECT set_config('app.family_id', %s, true)",
                    (str(family_id),),
                )
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise


def _migrate_users_color():
    """Adds color column to users table for existing databases."""
    with get_conn() as conn:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN color TEXT NOT NULL DEFAULT '#6366f1'")
        except sqlite3.OperationalError:
            pass  # column already exists


def _migrate_cambios_tipo():
    """Adds tipo column (venta/compra) to cambios_dolar for existing databases."""
    with get_conn() as conn:
        try:
            conn.execute("ALTER TABLE cambios_dolar ADD COLUMN tipo TEXT NOT NULL DEFAULT 'venta'")
        except sqlite3.OperationalError:
            pass  # column already exists


def _migrate_fixed_expenses_to_expense_link():
    """One-time, idempotent conversion of the old fixed_expense_payments join table into
    fixed_expense_id/fixed_expense_year/fixed_expense_month columns directly on expenses
    (v2.0.0). If fixed_expense_payments doesn't exist, this is a no-op — either a fresh DB
    (a fresh PostgreSQL DB already has the columns via Alembic) or a DB that already
    went through this legacy migration.

    Payments with a NULL expense_id have no amount to migrate — there's nothing to fabricate
    an expense from — so they're counted and dropped rather than converted. This is a
    deliberate, logged data loss; see CHANGELOG 2.0.0."""
    with get_conn() as conn:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fixed_expense_payments'"
        ).fetchone()
        if not table_exists:
            return

        cols = [r[1] for r in conn.execute("PRAGMA table_info(expenses)").fetchall()]
        if "fixed_expense_id" not in cols:
            conn.execute(
                "ALTER TABLE expenses ADD COLUMN fixed_expense_id INTEGER"
                " REFERENCES fixed_expenses(id) ON DELETE SET NULL"
            )
        if "fixed_expense_year" not in cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN fixed_expense_year INTEGER")
        if "fixed_expense_month" not in cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN fixed_expense_month INTEGER")

        payments = conn.execute(
            "SELECT fixed_expense_id, expense_id, year, month FROM fixed_expense_payments"
        ).fetchall()

        converted = 0
        dropped = 0
        for p in payments:
            if p["expense_id"] is not None:
                conn.execute(
                    "UPDATE expenses SET fixed_expense_id=?, fixed_expense_year=?, fixed_expense_month=?"
                    " WHERE id=?",
                    (p["fixed_expense_id"], p["year"], p["month"], p["expense_id"]),
                )
                converted += 1
            else:
                dropped += 1

        conn.execute("DROP TABLE fixed_expense_payments")

        logger.info(
            "Migración fixed_expense_payments -> expenses: %d convertidos, %d descartados "
            "(pago sin gasto vinculado, sin monto para migrar — re-vincular manualmente vía "
            "'ya lo pagué' si corresponde).",
            converted, dropped,
        )


def _migrate_expenses_subcategory():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(expenses)").fetchall()]
        if "subcategory_id" not in cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)")


def _migrate_keywords_subcategory():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(keywords)").fetchall()]
        if "subcategory_id" not in cols:
            conn.execute("ALTER TABLE keywords ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)")


def _migrate_fixed_expenses_subcategory():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(fixed_expenses)").fetchall()]
        if "subcategory_id" not in cols:
            conn.execute("ALTER TABLE fixed_expenses ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id)")


def _migrate_currencies():
    """Adds native currency columns, marking every historic record as ARS.

    SQLite cannot add a CHECK constraint with older versions reliably, therefore
    the application validates all writes and fresh databases get the constraint
    through the historical SQLite bootstrap.
    """
    with get_conn() as conn:
        expense_cols = [r[1] for r in conn.execute("PRAGMA table_info(expenses)").fetchall()]
        if "currency" not in expense_cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN currency TEXT NOT NULL DEFAULT 'ARS'")
        fixed_cols = [r[1] for r in conn.execute("PRAGMA table_info(fixed_expenses)").fetchall()]
        if "currency" not in fixed_cols:
            conn.execute("ALTER TABLE fixed_expenses ADD COLUMN currency TEXT NOT NULL DEFAULT 'ARS'")
        conn.execute("UPDATE expenses SET currency='ARS' WHERE currency IS NULL OR currency NOT IN ('ARS','USD')")
        conn.execute("UPDATE fixed_expenses SET currency='ARS' WHERE currency IS NULL OR currency NOT IN ('ARS','USD')")
        classification_cols = [r[1] for r in conn.execute("PRAGMA table_info(expense_classifications)").fetchall()]
        if "currency" not in classification_cols:
            conn.execute("ALTER TABLE expense_classifications ADD COLUMN currency TEXT NOT NULL DEFAULT 'ARS'")


def init_db(users: dict | None = None, *, user_emails: dict | None = None):
    """Apply versioned migrations, seed defaults and synchronize configured users."""
    project_dir = os.path.dirname(os.path.dirname(__file__))
    alembic_cfg = Config(os.path.join(project_dir, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(project_dir, "migrations"))
    command.upgrade(alembic_cfg, "head")
    pgcompat.set_family_id(1)
    import seed
    with get_conn() as conn:
        family_one_exists = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM families WHERE id = 1)"
        ).fetchone()[0]
        if family_one_exists and conn.execute(
            "SELECT COUNT(*) FROM categories WHERE family_id = 1"
        ).fetchone()[0] == 0:
            seed.create_family_defaults(conn, 1)
    if users:
        _sync_users(users)
    if user_emails:
        _sync_user_emails(user_emails)
    bootstrap_email = os.environ.get("AUTH_BOOTSTRAP_EMAIL", "").strip().casefold()
    if bootstrap_email:
        _bootstrap_web_identity(bootstrap_email)
    superadmin_email = os.environ.get("SUPERADMIN_EMAIL", "").strip().casefold()
    if superadmin_email:
        _bootstrap_superadmin(superadmin_email)


def get_daily_quote(family_id: int, local_date, language: str = "es-AR"):
    """Return a stable active quote for a family-local day.

    Exact and base-language rows form the preferred pool. If neither exists,
    selection degrades to every active quote. Decorative content must never make
    the dashboard unavailable, so database errors intentionally return ``None``.
    """
    requested = (language or "es").strip() or "es"
    base_language = requested.split("-", 1)[0]
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text, author, language, tag, verified "
                "FROM quotes WHERE active=true ORDER BY id"
            ).fetchall()
        if not rows:
            return None
        preferred = [
            row for row in rows
            if row["language"] == requested or row["language"] == base_language
        ]
        eligible = preferred or rows
        digest = hashlib.sha256(
            f"{local_date.isoformat()}:{family_id}".encode("utf-8")
        ).digest()
        return eligible[int.from_bytes(digest[:8], "big") % len(eligible)]
    except Exception:
        logger.warning("No se pudo seleccionar la frase diaria", exc_info=True)
        return None


def _bootstrap_web_identity(email: str) -> None:
    """Attach the existing family-1 owner to the first web-login email.

    This is intentionally a one-way NULL-only bootstrap: changing the env var
    later cannot take over or rewrite an established identity.
    """
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        existing = raw.execute(
            "SELECT id FROM users WHERE email = %s", (email,)
        ).fetchone()
        if existing:
            raw.commit()
            return
        owner = raw.execute(
            """
            SELECT u.id, u.email
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            WHERE m.family_id = 1 AND m.role = 'owner'
            ORDER BY m.created_at, u.id
            LIMIT 1
            """
        ).fetchone()
        if not owner:
            raise RuntimeError("No existe un owner para bootstrap de autenticación")
        if owner[1] and owner[1].casefold() != email:
            raise RuntimeError("AUTH_BOOTSTRAP_EMAIL no coincide con el email ya configurado")
        raw.execute(
            "UPDATE users SET email = %s WHERE id = %s AND email IS NULL",
            (email, owner[0]),
        )
        raw.commit()


def _bootstrap_superadmin(email: str) -> None:
    """Grant superadmin to the configured identity, never through HTTP."""
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        row = raw.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not row:
            raise RuntimeError("SUPERADMIN_EMAIL no corresponde a un usuario existente")
        raw.execute("UPDATE users SET is_superadmin = (id = %s)", (row[0],))
        raw.commit()


def _sync_user_emails(user_emails: dict) -> None:
    """Attach web identity to legacy Telegram users without overwriting it."""
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        for telegram_id, email in user_emails.items():
            normalized = str(email).strip().casefold()
            conflict = raw.execute(
                "SELECT id FROM users WHERE email = %s AND telegram_id <> %s",
                (normalized, str(telegram_id)),
            ).fetchone()
            if conflict:
                raise RuntimeError(f"El email {normalized} ya pertenece a otro usuario")
            row = raw.execute(
                "SELECT id, email FROM users WHERE telegram_id = %s",
                (str(telegram_id),),
            ).fetchone()
            if not row:
                raise RuntimeError(f"No existe el usuario Telegram {telegram_id}")
            if row[1] and row[1].casefold() != normalized:
                raise RuntimeError(
                    f"El usuario Telegram {telegram_id} ya tiene otro email configurado"
                )
            raw.execute(
                "UPDATE users SET email = %s WHERE id = %s AND email IS NULL",
                (normalized, row[0]),
            )
        raw.commit()


def _sync_users(users: dict):
    """Inserta o actualiza los usuarios definidos en la config de HA."""
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        for telegram_id, name in users.items():
            user_id = raw.execute(
                "INSERT INTO users (telegram_id, name) VALUES (%s, %s)"
                " ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name"
                " RETURNING id",
                (str(telegram_id), name),
            ).fetchone()[0]
            raw.execute(
                """
                INSERT INTO memberships (user_id, family_id, role)
                SELECT
                    %s, 1,
                    CASE WHEN EXISTS (SELECT 1 FROM memberships WHERE family_id = 1)
                         THEN 'member' ELSE 'owner' END
                WHERE EXISTS (SELECT 1 FROM families WHERE id = 1)
                  AND NOT EXISTS (
                    SELECT 1 FROM memberships WHERE user_id = %s
                )
                ON CONFLICT DO NOTHING
                """,
                (user_id, user_id),
            )
            raw.execute(
                """
                UPDATE families
                SET created_by_user_id = COALESCE(created_by_user_id, %s)
                WHERE id = 1
                """,
                (user_id,),
            )
        raw.commit()
    pgcompat.set_family_id(1)
    _assign_default_user_colors()


def create_family(name: str, *, timezone_name: str = "America/Argentina/Buenos_Aires") -> int:
    """Create a family and its generic taxonomy. Authentication will call this in Phase 3."""
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        family_id = raw.execute(
            "INSERT INTO families (name, timezone) VALUES (%s, %s) RETURNING id",
            (name, timezone_name),
        ).fetchone()[0]
        raw.commit()
    pgcompat.set_family_id(family_id)
    import seed
    with get_conn() as conn:
        seed.create_family_defaults(conn, family_id)
    return family_id


def record_llm_call(
    module: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd_estimate: float,
    latency_ms: int,
    success: bool,
    error_text: str | None = None,
) -> None:
    """Persist LLM telemetry without ever making the user-facing call fail."""
    family_id = pgcompat.current_family_id()
    if family_id is None:
        logger.warning("No se registró llamada LLM de %s: falta family_id", module)
        return
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls
                    (family_id, user_id, module, model, tokens_in, tokens_out,
                     cost_usd_estimate, latency_ms, success, error_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    family_id,
                    pgcompat.current_user_id(),
                    module,
                    model,
                    int(tokens_in or 0),
                    int(tokens_out or 0),
                    cost_usd_estimate,
                    max(0, int(latency_ms)),
                    success,
                    (error_text or "")[:2000] or None,
                ),
            )
    except Exception:
        logger.exception("No se pudo registrar telemetría LLM de %s", module)


def get_current_family_timezone() -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT timezone FROM families WHERE id = NULLIF(current_setting('app.family_id', true), '')::integer"
        ).fetchone()
    return row["timezone"] if row else "America/Argentina/Buenos_Aires"


def count_routine_llm_calls_today() -> int:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM llm_calls l
            JOIN families f ON f.id = l.family_id
            WHERE l.module <> 'resumen'
              AND l.created_at >= date_trunc(
                    'day', now() AT TIME ZONE f.timezone
                  ) AT TIME ZONE f.timezone
            """
        ).fetchone()["count"]


def count_reports_this_month() -> int:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM reports r
            JOIN families f ON f.id = r.family_id
            WHERE r.generated_at >= date_trunc(
                    'month', now() AT TIME ZONE f.timezone
                  ) AT TIME ZONE f.timezone
            """
        ).fetchone()["count"]


def get_family_quota_limits() -> dict:
    """Return tenant-visible overrides; missing values keep application defaults."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT routine_daily_limit, summary_monthly_limit
            FROM family_quota_overrides
            """
        ).fetchone()
    return dict(row) if row else {
        "routine_daily_limit": None,
        "summary_monthly_limit": None,
    }


def record_system_error(
    source: str,
    error: BaseException,
    details: str | None = None,
) -> None:
    """Persist tenant-attributed unhandled failures without masking the original."""
    family_id = pgcompat.current_family_id()
    if family_id is None:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO system_errors
                    (family_id, user_id, source, error_type, message, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    family_id,
                    pgcompat.current_user_id(),
                    source[:50],
                    type(error).__name__[:120],
                    str(error)[:1000] or type(error).__name__,
                    (details or "")[-8000:] or None,
                ),
            )
    except Exception:
        logger.exception("No se pudo registrar el error no manejado")


def get_superadmin_dashboard() -> dict:
    """Cross-family operational view. This is the only dashboard BYPASSRLS read."""
    with pgcompat.current_pool().connection() as raw:
        original_row_factory = raw.row_factory
        raw.row_factory = dict_row
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        families = raw.execute(
            """
            SELECT
                f.id, f.name, f.created_at,
                COUNT(DISTINCT m.user_id) FILTER (WHERE m.active) AS users,
                COUNT(DISTINCT m.user_id) FILTER (
                    WHERE m.active AND u.last_login_at >= now() - interval '7 days'
                ) AS active_7d,
                COUNT(DISTINCT m.user_id) FILTER (
                    WHERE m.active AND u.last_login_at >= now() - interval '30 days'
                ) AS active_30d,
                COUNT(DISTINCT e.id) AS expenses,
                COUNT(DISTINCT e.id) FILTER (
                    WHERE e.created_at >= date_trunc('month', now())
                ) AS expenses_month,
                COALESCE(q.routine_daily_limit, %s) AS routine_limit,
                COALESCE(q.summary_monthly_limit, %s) AS summary_limit,
                q.routine_daily_limit IS NOT NULL AS routine_overridden,
                q.summary_monthly_limit IS NOT NULL AS summary_overridden
            FROM families f
            LEFT JOIN memberships m ON m.family_id = f.id
            LEFT JOIN users u ON u.id = m.user_id
            LEFT JOIN expenses e ON e.family_id = f.id
            LEFT JOIN family_quota_overrides q ON q.family_id = f.id
            GROUP BY f.id, q.routine_daily_limit, q.summary_monthly_limit
            ORDER BY f.name
            """,
            (100, 15),
        ).fetchall()
        llm_by_family = raw.execute(
            """
            SELECT f.id AS family_id, f.name AS family_name,
                   COUNT(l.id) AS calls,
                   COALESCE(SUM(l.cost_usd_estimate), 0) AS cost,
                   COUNT(l.id) FILTER (WHERE NOT l.success) AS failures
            FROM families f
            LEFT JOIN llm_calls l
              ON l.family_id = f.id
             AND l.created_at >= now() - interval '30 days'
            GROUP BY f.id, f.name
            ORDER BY cost DESC, f.name
            """
        ).fetchall()
        llm_breakdown = raw.execute(
            """
            SELECT module, model, COUNT(*) AS calls,
                   COALESCE(SUM(tokens_in), 0) AS tokens_in,
                   COALESCE(SUM(tokens_out), 0) AS tokens_out,
                   COALESCE(SUM(cost_usd_estimate), 0) AS cost
            FROM llm_calls
            WHERE created_at >= now() - interval '30 days'
            GROUP BY module, model
            ORDER BY cost DESC, calls DESC
            """
        ).fetchall()
        llm_daily = raw.execute(
            """
            SELECT created_at::date AS day, COUNT(*) AS calls,
                   COALESCE(SUM(cost_usd_estimate), 0) AS cost
            FROM llm_calls
            WHERE created_at >= current_date - 29
            GROUP BY created_at::date
            ORDER BY day
            """
        ).fetchall()
        errors = raw.execute(
            """
            SELECT se.created_at, se.source, se.error_type, se.message,
                   f.name AS family_name, u.name AS user_name
            FROM system_errors se
            JOIN families f ON f.id = se.family_id
            LEFT JOIN users u ON u.id = se.user_id
            ORDER BY se.created_at DESC
            LIMIT 30
            """
        ).fetchall()
        llm_errors = raw.execute(
            """
            SELECT l.created_at, l.module AS source, 'LLM' AS error_type,
                   COALESCE(l.error_text, 'Llamada fallida') AS message,
                   f.name AS family_name, u.name AS user_name
            FROM llm_calls l
            JOIN families f ON f.id = l.family_id
            LEFT JOIN users u ON u.id = l.user_id
            WHERE NOT l.success
            ORDER BY l.created_at DESC
            LIMIT 30
            """
        ).fetchall()
        costs = raw.execute(
            """
            SELECT provider, unit_label, unit_rate_usd, monthly_volume, notes,
                   unit_rate_usd * monthly_volume AS estimated_monthly_cost
            FROM infrastructure_cost_settings
            ORDER BY CASE provider
                WHEN 'Anthropic' THEN 1 WHEN 'OpenAI' THEN 2
                WHEN 'Resend' THEN 3 ELSE 4 END
            """
        ).fetchall()
        raw.row_factory = original_row_factory
        raw.commit()
    recent_errors = sorted(
        [dict(row) for row in errors] + [dict(row) for row in llm_errors],
        key=lambda row: row["created_at"],
        reverse=True,
    )[:30]
    return {
        "families": [dict(row) for row in families],
        "llm_by_family": [dict(row) for row in llm_by_family],
        "llm_breakdown": [dict(row) for row in llm_breakdown],
        "llm_daily": [dict(row) for row in llm_daily],
        "recent_errors": recent_errors,
        "costs": [dict(row) for row in costs],
    }


def set_family_quota_override(
    family_id: int,
    routine_daily_limit: int | None,
    summary_monthly_limit: int | None,
) -> bool:
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        if not raw.execute(
            "SELECT 1 FROM families WHERE id = %s", (family_id,)
        ).fetchone():
            raw.commit()
            return False
        if routine_daily_limit is None and summary_monthly_limit is None:
            raw.execute(
                "DELETE FROM family_quota_overrides WHERE family_id = %s",
                (family_id,),
            )
        else:
            raw.execute(
                """
                INSERT INTO family_quota_overrides
                    (family_id, routine_daily_limit, summary_monthly_limit, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (family_id) DO UPDATE SET
                    routine_daily_limit = excluded.routine_daily_limit,
                    summary_monthly_limit = excluded.summary_monthly_limit,
                    updated_at = now()
                """,
                (family_id, routine_daily_limit, summary_monthly_limit),
            )
        raw.commit()
        return True


def update_infrastructure_cost(
    provider: str,
    unit_label: str,
    unit_rate_usd,
    monthly_volume,
    notes: str | None,
) -> bool:
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        result = raw.execute(
            """
            UPDATE infrastructure_cost_settings
            SET unit_label = %s, unit_rate_usd = %s, monthly_volume = %s,
                notes = %s, updated_at = now()
            WHERE provider = %s
            """,
            (unit_label, unit_rate_usd, monthly_volume, notes, provider),
        )
        raw.commit()
        return result.rowcount == 1


def _assign_default_user_colors():
    """Asigna un color distinto de la paleta a cada usuario que todavía tenga el
    color por defecto, para que se distingan bien en el dashboard. No pisa colores
    elegidos a mano (los que difieren del default)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.color
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            WHERE m.family_id = NULLIF(current_setting('app.family_id', true), '')::integer
            ORDER BY u.id
            """
        ).fetchall()
        for index, row in enumerate(rows):
            if row["color"] == DEFAULT_USER_COLOR:
                new_color = USER_COLOR_PALETTE[index % len(USER_COLOR_PALETTE)]
                conn.execute(
                    "UPDATE users SET color = ? WHERE id = ?",
                    (new_color, row["id"]),
                )


# ── Usuarios ──────────────────────────────────────────────────────────────────

def get_user_by_telegram_id(tg_id: str):
    """Resolve the configured Telegram identity and its one family.

    This platform lookup intentionally happens before tenant RLS is selected.
    """
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        conn = pgcompat.Connection(raw)
        return conn.execute(
            """
            SELECT u.*, m.family_id, m.role
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            WHERE u.telegram_id = ? AND m.active
            """,
            (str(tg_id),),
        ).fetchone()


def get_superadmin_telegram_id() -> str | None:
    with pgcompat.current_pool().connection() as raw:
        raw.execute("SET LOCAL ROLE gastos_superadmin")
        row = raw.execute(
            "SELECT telegram_id FROM users "
            "WHERE is_superadmin AND telegram_id IS NOT NULL LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None


# ── Gastos ────────────────────────────────────────────────────────────────────

def _normalize_concept(concept: str) -> str:
    """Collapse internal whitespace/newlines so a malformed concept (e.g. from a
    voice extraction) can't break the dashboard's inline JS delete/edit handlers."""
    return " ".join(concept.split())


def create_expense(user_id: int, category_id: int | None, concept: str, amount: float, raw_text: str,
                   subcategory_id: int | None = None, currency: str = DEFAULT_CURRENCY) -> int:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, category_id, subcategory_id, concept, amount, currency, raw_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, category_id, subcategory_id, _normalize_concept(concept), money.amount(amount), normalize_currency(currency), raw_text, now_utc),
        )
        return cur.lastrowid


def create_expense_full(user_id: int, category_id: int | None, concept: str, amount: float, date_str: str,
                        subcategory_id: int | None = None, currency: str = DEFAULT_CURRENCY) -> int:
    """Like create_expense but accepts an explicit date (YYYY-MM-DD in ART/UTC-3).
    Stores as 03:00 UTC (= midnight ART) so date queries using '-3 hours' return the correct day."""
    created_at = f"{date_str} 03:00:00"
    concept = _normalize_concept(concept)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, category_id, subcategory_id, concept, amount, currency, raw_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, category_id, subcategory_id, concept, money.amount(amount), normalize_currency(currency), concept, created_at),
        )
        return cur.lastrowid


def delete_expense(expense_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        return cur.rowcount > 0


def get_recent_expenses(limit: int = 50):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.user_id, e.category_id, e.concept, e.amount, e.currency, e.raw_text, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')       AS category_color,
                   COALESCE(c.icon,  '❓')             AS category_icon,
                   e.subcategory_id,
                   s.name AS subcategory_name,
                   e.fixed_expense_id
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_recent_expenses_for_user(user_id: int, limit: int = 30):
    """Like get_recent_expenses but scoped to a single user. Used to give the
    natural-language intent layer the requesting user's recent context so it can
    resolve references like "el último gasto"."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.user_id, e.category_id, e.concept, e.amount, e.currency, e.raw_text, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   e.subcategory_id,
                   s.name AS subcategory_name,
                   e.fixed_expense_id
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            WHERE e.user_id = ?
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()


def get_expenses_filtered(year: int | None = None, month: int | None = None):
    """Expenses filtered by year and/or month, each optional — omit either (or both)
    to remove that constraint, used by the history screen's "Todos" filter."""
    conditions = []
    params = []
    if year is not None:
        conditions.append("strftime('%Y', e.created_at) = ?")
        params.append(str(year))
    if month is not None:
        conditions.append("strftime('%m', e.created_at) = ?")
        params.append(f"{month:02d}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT e.id, e.user_id, e.category_id, e.concept, e.amount, e.currency, e.raw_text, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')       AS category_color,
                   COALESCE(c.icon,  '❓')             AS category_icon,
                   e.subcategory_id,
                   s.name AS subcategory_name,
                   e.fixed_expense_id
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            {where}
            ORDER BY e.created_at DESC
            """,
            params,
        ).fetchall()


def get_expense_years():
    """Distinct years with at least one expense, newest first — populates the
    history screen's year filter so it only offers years that actually have data."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT strftime('%Y', created_at) AS year FROM expenses ORDER BY year DESC"
        ).fetchall()
    return [int(r["year"]) for r in rows]


def get_expenses_by_week(week_start: str, week_end: str):
    """Retorna gastos entre week_start y week_end (YYYY-MM-DD), comparando en hora Buenos Aires (UTC-3)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.currency, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.icon, '❓')             AS category_icon
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE date(datetime(e.created_at, '-3 hours')) BETWEEN ? AND ?
            ORDER BY e.created_at DESC
            """,
            (week_start, week_end),
        ).fetchall()


def get_expenses_today():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.currency, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.icon,  '❓')             AS category_icon
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE date(datetime(e.created_at, '-3 hours')) = date(datetime('now', '-3 hours'))
            ORDER BY e.created_at DESC
            """
        ).fetchall()


def get_expenses_summary_by_category(year: int, month: int, user_name: str | None = None,
                                     currency: str = DEFAULT_CURRENCY):
    """Retorna [{category, total, color, icon, pct}] para el mes dado, opcionalmente filtrado por usuario."""
    params = [str(year), f"{month:02d}", normalize_currency(currency)]
    user_join   = "JOIN users u ON u.id = e.user_id" if user_name else ""
    user_filter = "AND u.name = ?"                   if user_name else ""
    if user_name:
        params.append(user_name)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(c.name, 'Sin categoría') AS name,
                   COALESCE(c.color, '#6b7280')       AS color,
                   COALESCE(c.icon,  '❓')             AS icon,
                   SUM(e.amount)                      AS total
            FROM expenses e
            {user_join}
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
              AND e.currency = ?
              {user_filter}
            GROUP BY e.category_id, c.name, c.color, c.icon
            ORDER BY total DESC
            """,
            params,
        ).fetchall()

    grand_total = sum(r["total"] for r in rows)
    result = []
    for r in rows:
        pct = money.statistic(r["total"] / grand_total * 100, 0) if grand_total else 0
        result.append({
            "name":  r["name"],
            "color": r["color"],
            "icon":  r["icon"],
            "total": r["total"],
            "pct":   pct,
        })
    return result


def get_expenses_by_week_of_month(year: int, month: int, user_name: str | None = None,
                                  currency: str = DEFAULT_CURRENCY):
    """Agrupa los gastos del mes por semana del mes (1–5) para el gráfico de barras.

    Con `user_name` filtra al usuario indicado (mismo filtro que la variante
    por-usuario), para que la línea comparativa del mes anterior respete el
    filtro "Ver gastos de" del dashboard.
    """
    params: list = [str(year), f"{month:02d}", normalize_currency(currency)]
    user_filter = ""
    if user_name:
        user_filter = "AND u.name = ?"
        params.append(user_name)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(strftime('%d', e.created_at) AS INTEGER) AS day,
                   SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
              AND e.currency = ?
              {user_filter}
            GROUP BY day
            """,
            params,
        ).fetchall()

    weekly: dict[int, float] = {}
    for r in rows:
        week_num = (r["day"] - 1) // 7 + 1
        weekly[week_num] = weekly.get(week_num, 0) + r["total"]

    return [
        {"week": w, "label": f"Sem {w}", "total": weekly.get(w, 0)}
        for w in range(1, 6)
        if w in weekly
    ]


def get_expenses_by_user(year: int, month: int, currency: str = DEFAULT_CURRENCY):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.name, SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
              AND e.currency = ?
            GROUP BY e.user_id, u.name
            ORDER BY total DESC
            """,
            (str(year), f"{month:02d}", normalize_currency(currency)),
        ).fetchall()


# ── Edición de gastos ─────────────────────────────────────────────────────────

def update_expense(expense_id: int, concept: str, amount: float, category_id: int | None,
                   subcategory_id: int | None = None, date_str: str | None = None,
                   currency: str | None = None) -> bool:
    """`date_str` (YYYY-MM-DD, ART) is stored as 03:00 UTC — same convention as
    create_expense_full/update_expense_fields — so ART date queries land on the right day."""
    concept = _normalize_concept(concept)
    if currency is not None:
        requested_currency = normalize_currency(currency)
        with get_conn() as conn:
            current = conn.execute(
                "SELECT fixed_expense_id, currency FROM expenses WHERE id=?", (expense_id,)
            ).fetchone()
        if (current and current["fixed_expense_id"] is not None
                and requested_currency != current["currency"]):
            return False
    sets = ["concept=?", "amount=?", "category_id=?"]
    params: list = [concept, money.amount(amount), category_id]
    if subcategory_id is not None:
        sets.append("subcategory_id=?")
        params.append(subcategory_id)
    if date_str is not None:
        sets.append("created_at=?")
        params.append(f"{date_str} 03:00:00")
    if currency is not None:
        sets.append("currency=?")
        params.append(requested_currency)
    params.append(expense_id)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE expenses SET {', '.join(sets)} WHERE id=?",
            params,
        )
        return cur.rowcount > 0


def get_expense_by_id(expense_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.*, s.name AS subcategory_name
            FROM expenses e
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            WHERE e.id = ?
            """,
            (expense_id,),
        ).fetchone()


def get_expenses_uncategorized():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.currency, e.created_at,
                   u.name AS user_name
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE e.category_id IS NULL
            ORDER BY e.created_at DESC
            """
        ).fetchall()


def update_expense_amount(expense_id: int, user_id: int, amount: float) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET amount = ? WHERE id = ? AND user_id = ?",
            (money.amount(amount), expense_id, user_id),
        )
        return cur.rowcount > 0


def update_expense_category(expense_id: int, user_id: int, category_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET category_id = ? WHERE id = ? AND user_id = ?",
            (category_id, expense_id, user_id),
        )
        return cur.rowcount > 0


def update_expense_fields(expense_id: int, user_id: int, *, amount: float | None = None,
                          concept: str | None = None, category_id: int | None = None,
                          subcategory_id: int | None = None, date_str: str | None = None,
                          currency: str | None = None) -> bool:
    """Parameterized, user-scoped UPDATE that only touches the fields provided.

    Used by the natural-language edit flow: the model supplies which fields to
    change, application code builds the write. ``WHERE ... AND user_id = ?``
    enforces ownership at the SQL level (a user can only edit their own expenses
    via the bot; the web dashboard uses the unscoped helpers). ``None`` means
    "leave unchanged". ``date_str`` (YYYY-MM-DD, ART) is stored as 03:00 UTC to
    match create_expense_full so ART date queries land on the right day."""
    sets: list[str] = []
    params: list = []
    if amount is not None:
        sets.append("amount = ?")
        params.append(money.amount(amount))
    if concept is not None:
        sets.append("concept = ?")
        params.append(_normalize_concept(concept))
    if category_id is not None:
        sets.append("category_id = ?")
        params.append(category_id)
    if subcategory_id is not None:
        sets.append("subcategory_id = ?")
        params.append(subcategory_id)
    if date_str is not None:
        sets.append("created_at = ?")
        params.append(f"{date_str} 03:00:00")
    if currency is not None:
        # A linked payment inherits its fixed expense currency and cannot drift.
        requested_currency = normalize_currency(currency)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT fixed_expense_id, currency FROM expenses WHERE id=? AND user_id=?",
                (expense_id, user_id),
            ).fetchone()
        if (row and row["fixed_expense_id"] is not None
                and requested_currency != row["currency"]):
            return False
        sets.append("currency = ?")
        params.append(requested_currency)
    if not sets:
        return False
    params.extend([expense_id, user_id])
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE expenses SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            params,
        )
        return cur.rowcount > 0


def recategorize_by_concept(concept: str, category_id: int) -> int:
    """Actualiza category_id de todos los gastos cuyo concept contenga 'concept' (case-insensitive)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET category_id = ? WHERE LOWER(concept) LIKE LOWER(?)",
            (category_id, f"%{concept}%"),
        )
        return cur.rowcount


# ── Categorías ────────────────────────────────────────────────────────────────

PROTECTED_CATEGORY = "Sin categoría"


def get_all_categories():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories ORDER BY name"
        ).fetchall()


def get_category_by_id(category_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()


def get_category_by_name(name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories WHERE name = ?", (name,)
        ).fetchone()


def find_category_normalized(name: str):
    """Return an existing category row whose name matches `name` ignoring accents
    and case (consistent with categorizer matching), or None. Used to dup-guard
    taxonomy creation from the natural-language layer."""
    from categorizer import normalize
    target = normalize((name or "").strip())
    if not target:
        return None
    for c in get_all_categories():
        if normalize(c["name"]) == target:
            return c
    return None


def get_expense_count_by_category() -> dict:
    """Retorna {category_id: count} para categorías con al menos 1 gasto."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category_id, COUNT(*) AS cnt FROM expenses GROUP BY category_id"
        ).fetchall()
    return {r["category_id"]: r["cnt"] for r in rows if r["category_id"] is not None}


def has_expenses() -> bool:
    """Return whether the current tenant has logged at least one expense."""
    with get_conn() as conn:
        return conn.execute("SELECT EXISTS (SELECT 1 FROM expenses)").fetchone()[0]


def create_category(name: str, icon: str, color: str) -> int | None:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO categories (name, icon, color) VALUES (?, ?, ?)",
                (name.strip(), icon.strip(), color.strip()),
            )
            return cur.lastrowid
    except IntegrityError:
        return None


def update_category(category_id: int, name: str, icon: str, color: str) -> tuple[bool, str | None]:
    existing = get_category_by_id(category_id)
    if existing is None:
        return False, "Categoría no encontrada"
    if existing["name"] == PROTECTED_CATEGORY:
        return False, "Esta categoría no se puede modificar"
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE categories SET name=?, icon=?, color=? WHERE id=?",
                (name.strip(), icon.strip(), color.strip(), category_id),
            )
            return (cur.rowcount > 0, None)
    except IntegrityError:
        return False, f"Ya existe una categoría llamada '{name}'"


def delete_category(category_id: int) -> tuple[bool, str | None]:
    existing = get_category_by_id(category_id)
    if existing is None:
        return False, "Categoría no encontrada"
    if existing["name"] == PROTECTED_CATEGORY:
        return False, "Esta categoría no se puede eliminar"
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE category_id = ?", (category_id,)
        ).fetchone()[0]
    if count > 0:
        return False, f"Tiene {count} gasto{'s' if count != 1 else ''} asociado{'s' if count != 1 else ''}"
    with get_conn() as conn:
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    return True, None


# ── Usuarios ─────────────────────────────────────────────────────────────────

def get_all_users():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.id, u.name, u.color
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            WHERE m.family_id = NULLIF(current_setting('app.family_id', true), '')::integer
            ORDER BY u.name
            """
        ).fetchall()


def get_expenses_by_week_of_month_by_user(year: int, month: int, user_name: str | None = None,
                                          currency: str = DEFAULT_CURRENCY):
    """Groups expenses by week-of-month and user for the stacked weekly bar chart."""
    params = [str(year), f"{month:02d}", normalize_currency(currency)]
    user_filter = ""
    if user_name:
        user_filter = "AND u.name = ?"
        params.append(user_name)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(strftime('%d', e.created_at) AS INTEGER) AS day,
                   u.id   AS user_id,
                   u.name AS user_name,
                   SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
              AND e.currency = ?
              {user_filter}
            GROUP BY day, e.user_id, u.id, u.name
            """,
            params,
        ).fetchall()

    users_seen: dict[int, str] = {}
    for r in rows:
        users_seen[r["user_id"]] = r["user_name"]

    weekly: dict[int, dict[int, float]] = {}
    for r in rows:
        week_num = (r["day"] - 1) // 7 + 1
        if week_num not in weekly:
            weekly[week_num] = {}
        uid = r["user_id"]
        weekly[week_num][uid] = weekly[week_num].get(uid, 0) + r["total"]

    return [
        {
            "week":    w,
            "label":   f"Sem {w}",
            "total":   sum(weekly[w].values()),
            "by_user": {users_seen[uid]: weekly[w].get(uid, 0) for uid in users_seen},
        }
        for w in range(1, 6)
        if w in weekly
    ]


def get_gastos_por_categoria(year: int, month: int, user_name: str | None = None,
                             currency: str = DEFAULT_CURRENCY) -> list[dict]:
    """Retorna gastos del mes agrupados por categoría y subcategoría, ordenados por total DESC."""
    params = [str(year), f"{month:02d}", normalize_currency(currency)]
    user_filter = ""
    if user_name:
        user_filter = "AND u.name = ?"
        params.append(user_name)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(c.name, 'Sin categoría') AS categoria,
                   COALESCE(c.color, '#6b7280')       AS color,
                   COALESCE(c.icon,  '❓')             AS icon,
                   s.name                             AS subcategoria,
                   SUM(e.amount)                      AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
              AND e.currency = ?
              {user_filter}
            GROUP BY e.category_id, e.subcategory_id,
                     c.name, c.color, c.icon, s.name
            ORDER BY categoria
            """,
            params,
        ).fetchall()

    cats: dict[str, dict] = {}
    cat_meta: dict[str, dict] = {}
    for r in rows:
        cat = r["categoria"]
        if cat not in cats:
            cats[cat] = {"total": money.MONEY_ZERO, "subcategorias": []}
            cat_meta[cat] = {"color": r["color"], "icon": r["icon"]}
        cats[cat]["total"] += r["total"]
        if r["subcategoria"]:
            cats[cat]["subcategorias"].append({"nombre": r["subcategoria"], "total": r["total"]})

    result = []
    for cat, data in sorted(cats.items(), key=lambda x: x[1]["total"], reverse=True):
        result.append({
            "categoria":    cat,
            "color":        cat_meta[cat]["color"],
            "icon":         cat_meta[cat]["icon"],
            "total":        data["total"],
            "subcategorias": sorted(data["subcategorias"], key=lambda x: x["total"], reverse=True),
        })
    return result


def get_annual_data(year: int, currency: str = DEFAULT_CURRENCY) -> dict:
    """Returns full-year expense data broken down by month and category."""
    import calendar
    from datetime import date
    today = date.today()
    active_months = 12 if year < today.year else today.month

    MONTHS_SHORT = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT CAST(strftime('%m', e.created_at) AS INTEGER) AS month_num,
                   COALESCE(c.name,  'Sin categoría') AS cat_name,
                   COALESCE(c.color, '#707080')        AS cat_color,
                   SUM(e.amount) AS total
            FROM expenses e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y', e.created_at) = ?
              AND e.currency = ?
            GROUP BY month_num, e.category_id, c.name, c.color
            ORDER BY cat_name, month_num
            """,
            (str(year), normalize_currency(currency)),
        ).fetchall()

    cats: dict[str, list] = {}
    cat_colors: dict[str, str] = {}
    for r in rows:
        n = r["cat_name"]
        if n not in cats:
            cats[n] = [money.MONEY_ZERO] * 12
            cat_colors[n] = r["cat_color"]
        cats[n][r["month_num"] - 1] += r["total"]

    monthly_totals = [money.MONEY_ZERO] * 12
    for values in cats.values():
        for i, v in enumerate(values):
            monthly_totals[i] += v

    annual_total = sum(monthly_totals[:active_months])
    monthly_avg = money.rounded(annual_total / active_months) if active_months > 0 else money.MONEY_ZERO

    peak_idx   = max(range(active_months), key=lambda i: monthly_totals[i]) if active_months > 0 else 0
    peak_month = {"name": MONTHS_SHORT[peak_idx], "amount": monthly_totals[peak_idx]}

    cat_totals   = {n: sum(v[:active_months]) for n, v in cats.items()}
    top_cat_name = max(cat_totals, key=cat_totals.get) if cat_totals else ""
    top_cat_amt  = cat_totals.get(top_cat_name, 0)
    top_cat_pct = money.statistic(top_cat_amt / annual_total * 100, 0) if annual_total else 0

    with get_conn() as conn:
        user_rows = conn.execute(
            """
            SELECT CAST(strftime('%m', e.created_at) AS INTEGER) AS month_num,
                   u.name AS user_name,
                   SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND e.currency = ?
            GROUP BY month_num, e.user_id, u.name
            ORDER BY month_num
            """,
            (str(year), normalize_currency(currency)),
        ).fetchall()

    by_user: dict[str, list] = {}
    for r in user_rows:
        nm = r["user_name"]
        if nm not in by_user:
            by_user[nm] = [money.MONEY_ZERO] * 12
        by_user[nm][r["month_num"] - 1] += r["total"]

    by_category_sorted = sorted(cats.keys(), key=lambda x: cat_totals.get(x, 0), reverse=True)

    return {
        "months":        MONTHS_SHORT,
        "active_months": active_months,
        "by_category": [
            {"name": n, "color": cat_colors[n], "values": cats[n], "total": cat_totals.get(n, 0)}
            for n in by_category_sorted
        ],
        "monthly_totals": monthly_totals,
        "annual_total":   annual_total,
        "monthly_avg":    monthly_avg,
        "peak_month":     peak_month,
        "top_category":   {"name": top_cat_name, "amount": top_cat_amt, "pct": top_cat_pct},
        "by_user":        by_user,
    }


def get_monthly_totals(months: int = 6, currency: str = DEFAULT_CURRENCY) -> list[dict]:
    """Returns aggregated totals for the last N months, used for sparklines."""
    import calendar
    from datetime import date
    today = date.today()

    # Compute the first day of the oldest month in the window
    start_m = today.month - months + 1
    start_y = today.year
    while start_m <= 0:
        start_m += 12
        start_y -= 1
    start_date = f"{start_y}-{start_m:02d}-01"

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT strftime('%Y-%m', created_at) AS ym,
                   COUNT(*)                      AS cnt,
                   COALESCE(SUM(amount), 0)      AS total
            FROM expenses
            WHERE created_at >= ? AND currency = ?
            GROUP BY ym
            ORDER BY ym
            """,
            (start_date, normalize_currency(currency)),
        ).fetchall()

    row_map = {r["ym"]: r for r in rows}
    result = []
    for i in range(months - 1, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        ym = f"{y}-{m:02d}"
        days_in_month = calendar.monthrange(y, m)[1]
        days_elapsed  = today.day if (y == today.year and m == today.month) else days_in_month
        row   = row_map.get(ym)
        total = row["total"] if row else money.MONEY_ZERO
        cnt   = row["cnt"]   if row else 0
        result.append({
            "year":      y,
            "month":     m,
            "total":     total,
            "count":     cnt,
            "avg_daily": money.rounded(total / days_elapsed) if days_elapsed > 0 else money.MONEY_ZERO,
        })
    return result


# ── Keywords ──────────────────────────────────────────────────────────────────

def get_all_keywords():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT k.id, k.keyword, k.category_id, c.name AS category_name,
                   k.subcategory_id, s.name AS subcategory_name
            FROM keywords k
            JOIN categories c ON c.id = k.category_id
            LEFT JOIN subcategories s ON s.id = k.subcategory_id
            ORDER BY c.name, k.keyword
            """
        ).fetchall()


def add_keyword(keyword: str, category_id: int, subcategory_id: int | None = None) -> str:
    """
    Inserta o actualiza el keyword.
    Retorna: 'new' si se insertó, 'remapped' si existía con otra categoría,
             'unchanged' si ya apuntaba a la misma categoría.
    """
    kw = keyword.lower().strip()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT category_id FROM keywords WHERE keyword = ?", (kw,)
        ).fetchone()
        conn.execute(
            "INSERT INTO keywords (keyword, category_id, subcategory_id) VALUES (?, ?, ?)"
            " ON CONFLICT(family_id, keyword) DO UPDATE SET category_id = excluded.category_id,"
            " subcategory_id = excluded.subcategory_id",
            (kw, category_id, subcategory_id),
        )
    if existing is None:
        return "new"
    if existing["category_id"] != category_id:
        return "remapped"
    return "unchanged"


def delete_keyword(keyword_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        return cur.rowcount > 0


def get_expense_count_by_subcategory(subcategory_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE subcategory_id = ?", (subcategory_id,)
        ).fetchone()[0]


def update_keyword(keyword_id: int, keyword: str, category_id: int, subcategory_id: int | None) -> bool:
    kw = keyword.lower().strip()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE keywords SET keyword=?, category_id=?, subcategory_id=? WHERE id=?",
            (kw, category_id, subcategory_id, keyword_id),
        )
        return cur.rowcount > 0


# ── Subcategorías ─────────────────────────────────────────────────────────────

def get_subcategories(category_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM subcategories WHERE category_id = ? ORDER BY name",
            (category_id,),
        ).fetchall()


def get_all_subcategories():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT s.id, s.category_id, s.name, c.name AS category_name
            FROM subcategories s
            JOIN categories c ON c.id = s.category_id
            ORDER BY c.name, s.name
            """
        ).fetchall()


def get_subcategory_by_id(subcategory_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM subcategories WHERE id = ?", (subcategory_id,)
        ).fetchone()


def find_subcategory_normalized(category_id: int, name: str):
    """Return an existing subcategory row under `category_id` whose name matches
    `name` ignoring accents and case, or None. Dup-guard for taxonomy creation."""
    from categorizer import normalize
    target = normalize((name or "").strip())
    if not target:
        return None
    for s in get_subcategories(category_id):
        if normalize(s["name"]) == target:
            return s
    return None


def add_subcategory(category_id: int, name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
            (category_id, name.strip()),
        )
        return cur.lastrowid


def delete_subcategory(subcategory_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM subcategories WHERE id = ?", (subcategory_id,))
        return cur.rowcount > 0


def update_expense_subcategory(expense_id: int, subcategory_id: int | None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET subcategory_id = ? WHERE id = ?",
            (subcategory_id, expense_id),
        )
        return cur.rowcount > 0


def update_keyword_subcategory(keyword_id: int, subcategory_id: int | None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE keywords SET subcategory_id = ? WHERE id = ?",
            (subcategory_id, keyword_id),
        )
        return cur.rowcount > 0


# ── Gastos Fijos ──────────────────────────────────────────────────────────────

def get_all_fixed_expenses():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.currency, fe.category_id, fe.subcategory_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon,
                   s.name AS subcategory_name
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            LEFT JOIN subcategories s ON s.id = fe.subcategory_id
            WHERE fe.active = 1
            ORDER BY fe.concept
            """
        ).fetchall()


def get_fixed_expense_by_id(fe_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.currency, fe.category_id, fe.subcategory_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon,
                   s.name AS subcategory_name
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            LEFT JOIN subcategories s ON s.id = fe.subcategory_id
            WHERE fe.id = ?
            """,
            (fe_id,),
        ).fetchone()


def create_fixed_expense(concept: str, estimated_amount: float | None, category_id: int | None,
                         subcategory_id: int | None = None, currency: str = DEFAULT_CURRENCY) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fixed_expenses (concept, estimated_amount, currency, category_id, subcategory_id) VALUES (?, ?, ?, ?, ?)",
            (concept.strip(), money.amount(estimated_amount) if estimated_amount is not None else None, normalize_currency(currency), category_id, subcategory_id),
        )
        return cur.lastrowid


def update_fixed_expense(fe_id: int, concept: str, estimated_amount: float | None, category_id: int | None,
                         subcategory_id: int | None = None, currency: str | None = None):
    current = get_fixed_expense_by_id(fe_id)
    if current is None:
        return False
    if currency is not None and normalize_currency(currency) != current["currency"]:
        with get_conn() as conn:
            linked = conn.execute("SELECT 1 FROM expenses WHERE fixed_expense_id=? LIMIT 1", (fe_id,)).fetchone()
        if linked:
            return False
    with get_conn() as conn:
        conn.execute(
            "UPDATE fixed_expenses SET concept=?, estimated_amount=?, currency=?, category_id=?, subcategory_id=? WHERE id=?",
            (concept.strip(), money.amount(estimated_amount) if estimated_amount is not None else None, normalize_currency(currency or current["currency"]), category_id, subcategory_id, fe_id),
        )
    return True


def deactivate_fixed_expense(fe_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE fixed_expenses SET active=0 WHERE id=?", (fe_id,))


def get_fixed_payments_for_period(year: int, month: int) -> list[dict]:
    """Returns every active fixed expense together with ALL expenses linked to it for the
    given period. Any number of expenses may share a period (e.g. a legitimate double
    payment from a utility billing error), so this aggregates rather than assuming at most
    one payment per fixed expense per month like the old fixed_expense_payments table did."""
    with get_conn() as conn:
        fixed_rows = conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.currency, fe.category_id, fe.subcategory_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon,
                   s.name AS subcategory_name
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            LEFT JOIN subcategories s ON s.id = fe.subcategory_id
            WHERE fe.active = 1
            ORDER BY fe.concept
            """
        ).fetchall()

        payment_rows = conn.execute(
            """
            SELECT id, fixed_expense_id, amount, currency, concept, created_at
            FROM expenses
            WHERE fixed_expense_id IS NOT NULL
              AND fixed_expense_year = ? AND fixed_expense_month = ?
            ORDER BY created_at
            """,
            (year, month),
        ).fetchall()

    payments_by_fe: dict[int, list[dict]] = {}
    for p in payment_rows:
        payments_by_fe.setdefault(p["fixed_expense_id"], []).append(dict(p))

    result = []
    for r in fixed_rows:
        d = dict(r)
        payments = payments_by_fe.get(r["id"], [])
        d["payments"] = payments
        d["paid"] = len(payments) > 0
        d["total_paid"] = sum(p["amount"] for p in payments)
        # Single-payment convenience field for callers/templates that only show one amount.
        d["actual_amount"] = d["total_paid"] if payments else None
        result.append(d)
    return result


def get_unlinked_expenses_for_period(year: int, month: int) -> list[dict]:
    """Expenses in the given period not yet linked to any fixed expense — the candidate
    pool for the "ya lo pagué" search (fixed_matcher.find_candidate_expenses)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.category_id, e.subcategory_id, e.concept, e.amount, e.currency, e.created_at,
                   u.name AS user_name
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE e.fixed_expense_id IS NULL
              AND strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            ORDER BY e.created_at DESC
            """,
            (str(year), f"{month:02d}"),
        ).fetchall()


def link_expense_to_fixed(expense_id: int, fixed_expense_id: int, year: int, month: int) -> bool:
    """Links an expense to a fixed expense for the given period, forcing the expense's
    category/subcategory to the fixed expense's own. This is the single choke point every
    linking flow (creation-time offer, "ya lo pagué" candidate pick, dashboard/bot manual
    edit, NL edit) goes through, so a recurring bill can't drift category depending on which
    path registered it."""
    fe = get_fixed_expense_by_id(fixed_expense_id)
    if fe is None:
        return False
    with get_conn() as conn:
        expense = conn.execute("SELECT currency FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if expense is None or expense["currency"] != fe["currency"]:
            return False
        cur = conn.execute(
            "UPDATE expenses SET fixed_expense_id=?, fixed_expense_year=?, fixed_expense_month=?,"
            " category_id=?, subcategory_id=? WHERE id=?",
            (fixed_expense_id, year, month, fe["category_id"], fe["subcategory_id"], expense_id),
        )
        return cur.rowcount > 0


def unlink_expense_from_fixed(expense_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET fixed_expense_id=NULL, fixed_expense_year=NULL, fixed_expense_month=NULL"
            " WHERE id=?",
            (expense_id,),
        )
        return cur.rowcount > 0


def get_fixed_expense_monthly_summary(year: int, month: int,
                                      currency: str = DEFAULT_CURRENCY) -> dict:
    selected_currency = normalize_currency(currency)
    rows = [
        r for r in get_fixed_payments_for_period(year, month)
        if r["currency"] == selected_currency
    ]
    count_total     = len(rows)
    count_paid      = sum(1 for r in rows if r["paid"])
    total_estimated = sum(r["estimated_amount"] or 0 for r in rows)
    total_paid      = sum(r["total_paid"] for r in rows)
    return {
        "count_total":     count_total,
        "count_paid":      count_paid,
        "total_estimated": total_estimated,
        "total_paid":      total_paid,
    }


# ── Cambios de Dólar ──────────────────────────────────────────────────────────

def registrar_cambio(fecha: str, monto_usd: float, cotizacion: float, usuario: str, tipo: str = "venta") -> int:
    monto_usd = money.amount(monto_usd)
    cotizacion = money.amount(cotizacion)
    monto_ars = money.amount(monto_usd * cotizacion)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cambios_dolar (fecha, monto_usd, cotizacion, monto_ars, usuario, tipo)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, monto_usd, cotizacion, monto_ars, usuario, tipo),
        )
        return cur.lastrowid


def get_cambios_resumen_mes(year: int, month: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(monto_usd), 0)  AS total_usd,
                   COALESCE(AVG(cotizacion), 0)  AS cotizacion_promedio,
                   COALESCE(SUM(monto_ars), 0)  AS total_ars
            FROM cambios_dolar
            WHERE strftime('%Y', fecha) = ?
              AND strftime('%m', fecha) = ?
            """,
            (str(year), f"{month:02d}"),
        ).fetchone()
    return {
        "total_usd_mes":          row["total_usd"],
        "cotizacion_promedio_mes": row["cotizacion_promedio"],
        "total_ars_mes":          row["total_ars"],
    }


def get_cambios_historial(limit: int = 50) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, fecha, monto_usd, cotizacion, monto_ars, usuario, tipo"
            " FROM cambios_dolar ORDER BY fecha DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_cambios_por_mes(months: int = 12) -> list:
    from datetime import date
    today = date.today()
    start_m = today.month - months + 1
    start_y = today.year
    while start_m <= 0:
        start_m += 12
        start_y -= 1
    start_date = f"{start_y}-{start_m:02d}-01"
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT strftime('%Y-%m', fecha)    AS mes,
                   COALESCE(SUM(monto_usd), 0) AS total_usd,
                   COALESCE(SUM(monto_ars), 0) AS total_ars,
                   COALESCE(AVG(cotizacion), 0) AS cotizacion_promedio
            FROM cambios_dolar
            WHERE fecha >= ?
            GROUP BY mes
            ORDER BY mes ASC
            """,
            (start_date,),
        ).fetchall()


def get_cambios_cotizacion_historica() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT fecha, cotizacion FROM cambios_dolar ORDER BY fecha ASC"
        ).fetchall()


def delete_cambio(cambio_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM cambios_dolar WHERE id = ?", (cambio_id,))
        return cur.rowcount > 0


def get_latest_cotizacion_upto(year: int, month: int, lookback_months: int = 12) -> dict | None:
    """Most recent dollar-operation cotización (either tipo) dated strictly before the
    given period, looked up to ``lookback_months`` back — the report's equivalence
    fallback for a period with no dollar operations of its own to derive a rate from."""
    period_start = f"{year:04d}-{month:02d}-01"
    lookback_total = year * 12 + (month - 1) - lookback_months
    lookback_year, lookback_month = lookback_total // 12, lookback_total % 12 + 1
    lookback_start = f"{lookback_year:04d}-{lookback_month:02d}-01"
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT fecha, cotizacion, tipo
            FROM cambios_dolar
            WHERE fecha < ? AND fecha >= ?
            ORDER BY fecha DESC
            LIMIT 1
            """,
            (period_start, lookback_start),
        ).fetchone()
    return dict(row) if row else None


# ── Resúmenes mensuales (IA) ────────────────────────────────────────────────

def get_expenses_for_period_art(year: int, month: int, currency: str | None = None) -> list[dict]:
    """All expenses whose ART-adjusted date falls in (year, month) — the cash-basis
    period the monthly report is built on. strftime() (see the Postgres compat shim in
    migrations/0001) already converts its timestamptz argument via
    `AT TIME ZONE 'America/Argentina/Buenos_Aires'` before formatting, so it must be
    called on created_at directly — wrapping it in an extra datetime(created_at,
    '-3 hours') double-applies the ART shift and misfiles any expense from the first
    three hours of ART on the 1st of a month into the previous month."""
    period = f"{year:04d}-{month:02d}"
    currency_filter = " AND e.currency = ?" if currency else ""
    params = [period] + ([normalize_currency(currency)] if currency else [])
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT e.id, e.user_id, e.category_id, e.subcategory_id, e.concept, e.amount, e.currency,
                   e.created_at, e.fixed_expense_id,
                   u.name AS user_name,
                   c.name AS category_name,
                   s.name AS subcategory_name
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            LEFT JOIN subcategories s ON s.id = e.subcategory_id
            WHERE strftime('%Y-%m', e.created_at) = ?
            {currency_filter}
            ORDER BY e.created_at
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_expenses_excluding_period(year: int, month: int, currency: str | None = None) -> list[dict]:
    """All expenses outside the given ART period — the historical population used to
    compute per-category outlier stats and recurrence evidence in dossier.py."""
    period = f"{year:04d}-{month:02d}"
    currency_filter = " AND e.currency = ?" if currency else ""
    params = [period] + ([normalize_currency(currency)] if currency else [])
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT e.id, e.category_id, e.subcategory_id, e.concept, e.amount, e.currency,
                   e.created_at, e.fixed_expense_id,
                   c.name AS category_name
            FROM expenses e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y-%m', e.created_at) != ?
            {currency_filter}
            ORDER BY e.created_at
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_first_expense_date() -> str | None:
    """Date (ART) of the earliest expense ever recorded — a hard fact the report uses
    to calibrate how much confidence a short history should carry. date(timestamptz)
    is native Postgres (no compat shim, no built-in TZ conversion), so it correctly
    needs the explicit -3 hours ART shift here — unlike strftime() above, this one
    isn't double-applying anything."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(date(datetime(created_at, '-3 hours'))) AS d FROM expenses"
        ).fetchone()
    return row["d"] if row else None


def get_months_with_data() -> list[str]:
    """Distinct 'YYYY-MM' periods (ART) with at least one expense, ascending. Backs
    both the report's "months available" hard fact and the /resumenes period selector."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT strftime('%Y-%m', created_at) AS ym
            FROM expenses
            ORDER BY ym
            """
        ).fetchall()
    return [r["ym"] for r in rows]


def get_cambios_resumen_mes_by_tipo(year: int, month: int) -> dict:
    """Dollar operations for the month split by tipo (venta/compra) — always both keys
    present (zeroed if that side had no operations), per the report's "both sides,
    always" requirement."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tipo,
                   COUNT(*)                     AS cnt,
                   COALESCE(SUM(monto_usd), 0)  AS total_usd,
                   COALESCE(SUM(monto_ars), 0)  AS total_ars,
                   COALESCE(AVG(cotizacion), 0) AS cotizacion_promedio,
                   COALESCE(MIN(cotizacion), 0) AS cotizacion_min,
                   COALESCE(MAX(cotizacion), 0) AS cotizacion_max
            FROM cambios_dolar
            WHERE strftime('%Y', fecha) = ? AND strftime('%m', fecha) = ?
            GROUP BY tipo
            """,
            (str(year), f"{month:02d}"),
        ).fetchall()
    by_tipo = {r["tipo"]: dict(r) for r in rows}
    empty = {"cnt": 0, "total_usd": money.MONEY_ZERO, "total_ars": money.MONEY_ZERO,
             "cotizacion_promedio": money.MONEY_ZERO, "cotizacion_min": money.MONEY_ZERO,
             "cotizacion_max": money.MONEY_ZERO}
    return {tipo: by_tipo.get(tipo, dict(empty)) for tipo in ("venta", "compra")}


def get_cambios_for_period(year: int, month: int) -> list[dict]:
    """Raw dollar-operation rows for the month — the report fingerprint's dollar-ops
    input (id, fecha, monto_usd, cotizacion, tipo only; no derived totals)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, fecha, monto_usd, cotizacion, tipo FROM cambios_dolar"
            " WHERE strftime('%Y', fecha) = ? AND strftime('%m', fecha) = ?"
            " ORDER BY id",
            (str(year), f"{month:02d}"),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Índice de precios (IPC) ──────────────────────────────────────────────────

def get_ipc_series() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT year, month, value, is_estimated, updated_at FROM ipc_series"
            " ORDER BY year, month"
        ).fetchall()
    return [dict(r) for r in rows]


def get_ipc_value(year: int, month: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT year, month, value, is_estimated, updated_at FROM ipc_series"
            " WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
    return dict(row) if row else None


def upsert_ipc_value(year: int, month: int, value: float, is_estimated: bool) -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ipc_series (year, month, value, is_estimated, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(family_id, year, month) DO UPDATE SET value=excluded.value,"
            " is_estimated=excluded.is_estimated, updated_at=excluded.updated_at",
            (year, month, value, is_estimated, now_utc),
        )


# ── Reportes mensuales ───────────────────────────────────────────────────────

def create_report(year: int, month: int, model: str | None, prompt_version: str | None,
                   dossier_json: str, output_json: str | None, fingerprint: str,
                   llm_ok: bool) -> int:
    """Append-only insert — a regeneration is always a new row, never an overwrite."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports (year, month, generated_at, model, prompt_version,"
            " dossier_json, output_json, fingerprint, llm_ok)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (year, month, now_utc, model, prompt_version, dossier_json, output_json,
             fingerprint, llm_ok),
        )
        return cur.lastrowid


def get_latest_report(year: int, month: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE year = ? AND month = ?"
            " ORDER BY generated_at DESC, id DESC LIMIT 1",
            (year, month),
        ).fetchone()
    return dict(row) if row else None


def get_latest_report_overall() -> dict | None:
    """Most recent report across every period — backs /resumenes with no month given."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reports ORDER BY year DESC, month DESC, generated_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_report_history(year: int, month: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, generated_at, model, llm_ok FROM reports"
            " WHERE year = ? AND month = ? ORDER BY generated_at DESC",
            (year, month),
        ).fetchall()
    return [dict(r) for r in rows]


def save_classifications(report_id: int, rows: list[dict]) -> None:
    """rows: [{expense_id, concept, amount, currency, label, confidence}]."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO expense_classifications"
            " (report_id, expense_id, concept, amount, currency, label, confidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (report_id, r["expense_id"], r["concept"], r["amount"], normalize_currency(r.get("currency")), r["label"],
                 r.get("confidence"), now_utc)
                for r in rows
            ],
        )


def get_classifications_for_report(report_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT expense_id, concept, amount, currency, label, confidence"
            " FROM expense_classifications WHERE report_id = ?",
            (report_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_classifications_before(year: int, month: int, lookback_months: int = 6) -> list[dict]:
    """Latest classification per prior period within lookback_months, for the
    cross-month-consistency context injected into the next classification call.
    Only the latest report per period is used (reports are append-only; latest wins)."""
    periods: list[tuple[int, int]] = []
    y, m = year, month
    for _ in range(lookback_months):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        periods.append((y, m))

    result: list[dict] = []
    with get_conn() as conn:
        for py, pm in periods:
            report_row = conn.execute(
                "SELECT id FROM reports WHERE year = ? AND month = ?"
                " ORDER BY generated_at DESC, id DESC LIMIT 1",
                (py, pm),
            ).fetchone()
            if not report_row:
                continue
            rows = conn.execute(
                "SELECT expense_id, concept, amount, currency, label, confidence"
                " FROM expense_classifications WHERE report_id = ?",
                (report_row["id"],),
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["year"] = py
                d["month"] = pm
                result.append(d)
    return result


def update_cambio(cambio_id: int, fecha: str, monto_usd: float, cotizacion: float, tipo: str | None = None) -> bool:
    monto_usd = money.amount(monto_usd)
    cotizacion = money.amount(cotizacion)
    monto_ars = money.amount(monto_usd * cotizacion)
    with get_conn() as conn:
        if tipo is None:
            cur = conn.execute(
                "UPDATE cambios_dolar SET fecha=?, monto_usd=?, cotizacion=?, monto_ars=? WHERE id=?",
                (fecha, monto_usd, cotizacion, monto_ars, cambio_id),
            )
        else:
            cur = conn.execute(
                "UPDATE cambios_dolar SET fecha=?, monto_usd=?, cotizacion=?, monto_ars=?, tipo=? WHERE id=?",
                (fecha, monto_usd, cotizacion, monto_ars, tipo, cambio_id),
            )
        return cur.rowcount > 0


# ── Ingresos ─────────────────────────────────────────────────────────────────

def get_income_categories():
    with get_conn() as conn:
        return conn.execute(
            "SELECT c.*, COUNT(i.id) AS income_count FROM income_categories c "
            "LEFT JOIN incomes i ON i.income_category_id=c.id "
            "GROUP BY c.id ORDER BY c.name"
        ).fetchall()


def get_income_category(category_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM income_categories WHERE id=?", (category_id,)).fetchone()


def find_income_category_normalized(name: str):
    from categorizer import normalize
    target = normalize((name or "").strip())
    return next((r for r in get_income_categories() if normalize(r["name"]) == target), None)


def create_income_category(name: str, icon: str = "💵", color: str = "#22c55e") -> int | None:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO income_categories (name, icon, color) VALUES (?, ?, ?)",
                (name.strip(), icon.strip() or "💵", color.strip() or "#22c55e"),
            )
            return cur.lastrowid
    except IntegrityError:
        return None


def update_income_category(category_id: int, name: str, icon: str, color: str) -> bool:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE income_categories SET name=?, icon=?, color=? WHERE id=?",
                (name.strip(), icon.strip() or "💵", color.strip() or "#22c55e", category_id),
            )
            return cur.rowcount > 0
    except IntegrityError:
        return False


def delete_income_category(category_id: int) -> tuple[bool, str | None]:
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM incomes WHERE income_category_id=?", (category_id,)
        ).fetchone()[0]
        if count:
            return False, f"Tiene {count} ingreso{'s' if count != 1 else ''} asociado{'s' if count != 1 else ''}"
        cur = conn.execute("DELETE FROM income_categories WHERE id=?", (category_id,))
        return cur.rowcount > 0, None if cur.rowcount else "Categoría no encontrada"


def create_income(user_id: int, concept: str, amount: float, currency: str, date_str: str,
                  income_category_id: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO incomes (user_id, concept, amount, currency, income_category_id, date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, _normalize_concept(concept), money.amount(amount), normalize_currency(currency),
             income_category_id, date_str),
        )
        return cur.lastrowid


def get_incomes(year: int | None = None, month: int | None = None):
    conditions, params = [], []
    if year is not None:
        conditions.append("EXTRACT(YEAR FROM i.date)=?")
        params.append(year)
    if month is not None:
        conditions.append("EXTRACT(MONTH FROM i.date)=?")
        params.append(month)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with get_conn() as conn:
        return conn.execute(
            f"SELECT i.*, u.name AS user_name, c.name AS category_name, "
            f"c.icon AS category_icon, c.color AS category_color FROM incomes i "
            f"JOIN users u ON u.id=i.user_id "
            f"LEFT JOIN income_categories c ON c.id=i.income_category_id "
            f"{where} ORDER BY i.date DESC, i.created_at DESC",
            params,
        ).fetchall()


def get_income(income_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM incomes WHERE id=?", (income_id,)).fetchone()


def update_income(income_id: int, user_id: int, concept: str, amount: float, currency: str,
                  date_str: str, income_category_id: int | None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE incomes SET concept=?, amount=?, currency=?, date=?, income_category_id=? "
            "WHERE id=? AND user_id=?",
            (_normalize_concept(concept), money.amount(amount), normalize_currency(currency), date_str,
             income_category_id, income_id, user_id),
        )
        return cur.rowcount > 0


def delete_income(income_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM incomes WHERE id=? AND user_id=?", (income_id, user_id))
        return cur.rowcount > 0


def get_income_month_totals(year: int, month: int) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT currency, COALESCE(SUM(amount), 0) AS total FROM incomes "
            "WHERE EXTRACT(YEAR FROM date)=? AND EXTRACT(MONTH FROM date)=? GROUP BY currency",
            (year, month),
        ).fetchall()
    return {row["currency"]: row["total"] for row in rows}


# ── Lista de compras ─────────────────────────────────────────────────────────

def purge_old_shopping_items() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM shopping_items WHERE status='bought' "
            "AND bought_at < CURRENT_TIMESTAMP - INTERVAL '30 days'"
        )
        return cur.rowcount


def get_shopping_items(include_recent: bool = True):
    purge_old_shopping_items()
    status_filter = "" if include_recent else "WHERE si.status='pending'"
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT si.*, c.name AS category_name, c.icon AS category_icon, c.color AS category_color,
                   creator.name AS created_by_name, buyer.name AS bought_by_name
            FROM shopping_items si
            LEFT JOIN categories c ON c.id=si.category_id
            JOIN users creator ON creator.id=si.created_by_user_id
            LEFT JOIN users buyer ON buyer.id=si.bought_by_user_id
            {status_filter}
            ORDER BY CASE WHEN si.status='pending' THEN 0 ELSE 1 END,
                     COALESCE(c.name, 'ZZZ'), si.created_at DESC
            """
        ).fetchall()


def get_shopping_item(item_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM shopping_items WHERE id=?", (item_id,)).fetchone()


def find_pending_shopping_item(name: str):
    from categorizer import normalize
    target = normalize(name)
    return next(
        (r for r in get_shopping_items(False) if normalize(r["name"]) == target),
        None,
    )


def add_shopping_item(user_id: int, name: str, quantity: str | None = None,
                      category_id: int | None = None) -> tuple[int, bool]:
    existing = find_pending_shopping_item(name)
    if existing:
        if quantity:
            with get_conn() as conn:
                conn.execute("UPDATE shopping_items SET quantity=? WHERE id=?", (quantity.strip(), existing["id"]))
        return existing["id"], False
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO shopping_items (name, quantity, category_id, created_by_user_id) "
            "VALUES (?, ?, ?, ?)",
            (_normalize_concept(name), (quantity or "").strip() or None, category_id, user_id),
        )
        return cur.lastrowid, True


def update_shopping_item(item_id: int, name: str, quantity: str | None,
                         category_id: int | None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE shopping_items SET name=?, quantity=?, category_id=? WHERE id=? AND status='pending'",
            (_normalize_concept(name), (quantity or "").strip() or None, category_id, item_id),
        )
        return cur.rowcount > 0


def mark_shopping_item_bought(item_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE shopping_items SET status='bought', bought_by_user_id=?, bought_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='pending'",
            (user_id, item_id),
        )
        return cur.rowcount > 0


def mark_shopping_item_bought_by_name(name: str, user_id: int):
    item = find_pending_shopping_item(name)
    return item if item and mark_shopping_item_bought(item["id"], user_id) else None


def readd_shopping_item(item_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE shopping_items SET status='pending', created_by_user_id=?, created_at=CURRENT_TIMESTAMP, "
            "bought_by_user_id=NULL, bought_at=NULL WHERE id=? AND status='bought'",
            (user_id, item_id),
        )
        return cur.rowcount > 0


def clear_bought_shopping_items() -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM shopping_items WHERE status='bought'").rowcount


def shopping_pending_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM shopping_items WHERE status='pending'").fetchone()[0]
