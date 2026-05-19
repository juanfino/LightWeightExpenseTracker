import sqlite3
import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/gastos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    color       TEXT    NOT NULL DEFAULT '#6366f1',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    UNIQUE NOT NULL,
    color      TEXT    NOT NULL DEFAULT '#6366f1',
    icon       TEXT    NOT NULL DEFAULT '💰',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT    UNIQUE NOT NULL,
    category_id INTEGER NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category_id INTEGER,
    concept     TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    raw_text    TEXT    NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS fixed_expenses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    concept          TEXT    NOT NULL,
    estimated_amount REAL,
    category_id      INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS fixed_expense_payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fixed_expense_id INTEGER NOT NULL REFERENCES fixed_expenses(id) ON DELETE CASCADE,
    expense_id       INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
    year             INTEGER NOT NULL,
    month            INTEGER NOT NULL,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
    UNIQUE(fixed_expense_id, year, month)
);

CREATE TABLE IF NOT EXISTS cambios_dolar (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha      TEXT    NOT NULL,
    monto_usd  REAL    NOT NULL,
    cotizacion REAL    NOT NULL,
    monto_ars  REAL    NOT NULL,
    usuario    TEXT    NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_users_color():
    """Adds color column to users table for existing databases."""
    with get_conn() as conn:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN color TEXT NOT NULL DEFAULT '#6366f1'")
        except sqlite3.OperationalError:
            pass  # column already exists


def _migrate_fixed_payment_nullable_expense():
    """Rebuilds fixed_expense_payments to make expense_id nullable (v1.6.0 had it NOT NULL)."""
    with get_conn() as conn:
        info = conn.execute("PRAGMA table_info(fixed_expense_payments)").fetchall()
        if not info:
            return  # table doesn't exist yet; SCHEMA CREATE will handle it
        for col in info:
            if col["name"] == "expense_id" and col["notnull"] == 1:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS _fep_new (
                        id               INTEGER PRIMARY KEY AUTOINCREMENT,
                        fixed_expense_id INTEGER NOT NULL REFERENCES fixed_expenses(id) ON DELETE CASCADE,
                        expense_id       INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
                        year             INTEGER NOT NULL,
                        month            INTEGER NOT NULL,
                        created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
                        UNIQUE(fixed_expense_id, year, month)
                    );
                    INSERT INTO _fep_new SELECT * FROM fixed_expense_payments;
                    DROP TABLE fixed_expense_payments;
                    ALTER TABLE _fep_new RENAME TO fixed_expense_payments;
                """)
                break


def init_db(users: dict | None = None):
    """Crea tablas. Si la DB es nueva, ejecuta seed y crea los usuarios configurados."""
    is_new = not os.path.exists(DB_PATH)
    try:
        with get_conn() as conn:
            conn.executescript(SCHEMA)
    except sqlite3.DatabaseError:
        logger.warning(
            "DB corrupta en %s — eliminando y creando base de datos nueva.", DB_PATH
        )
        os.remove(DB_PATH)
        is_new = True
        with get_conn() as conn:
            conn.executescript(SCHEMA)
    _migrate_users_color()
    _migrate_fixed_payment_nullable_expense()
    if is_new:
        import seed
        seed.run()
    if users:
        _sync_users(users)


def _sync_users(users: dict):
    """Inserta o actualiza los usuarios definidos en la config de HA."""
    with get_conn() as conn:
        for telegram_id, name in users.items():
            conn.execute(
                "INSERT INTO users (telegram_id, name) VALUES (?, ?)"
                " ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name",
                (str(telegram_id), name),
            )


# ── Usuarios ──────────────────────────────────────────────────────────────────

def get_user_by_telegram_id(tg_id: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (str(tg_id),)
        ).fetchone()


# ── Gastos ────────────────────────────────────────────────────────────────────

def create_expense(user_id: int, category_id: int | None, concept: str, amount: float, raw_text: str) -> int:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, category_id, concept, amount, raw_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, category_id, concept, amount, raw_text, now_utc),
        )
        return cur.lastrowid


def create_expense_full(user_id: int, category_id: int | None, concept: str, amount: float, date_str: str) -> int:
    """Like create_expense but accepts an explicit date (YYYY-MM-DD in ART/UTC-3).
    Stores as 03:00 UTC (= midnight ART) so date queries using '-3 hours' return the correct day."""
    created_at = f"{date_str} 03:00:00"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, category_id, concept, amount, raw_text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, category_id, concept.strip(), amount, concept.strip(), created_at),
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
            SELECT e.id, e.user_id, e.category_id, e.concept, e.amount, e.raw_text, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')       AS category_color,
                   COALESCE(c.icon,  '❓')             AS category_icon
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_expenses_by_month(year: int, month: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.user_id, e.category_id, e.concept, e.amount, e.raw_text, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')       AS category_color,
                   COALESCE(c.icon,  '❓')             AS category_icon
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            ORDER BY e.created_at DESC
            """,
            (str(year), f"{month:02d}"),
        ).fetchall()


def get_expenses_by_week(week_start: str, week_end: str):
    """Retorna gastos entre week_start y week_end (YYYY-MM-DD), comparando en hora Buenos Aires (UTC-3)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.created_at,
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
            SELECT e.id, e.concept, e.amount, e.created_at,
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


def get_expenses_summary_by_category(year: int, month: int):
    """Retorna [{category, total, color, icon, pct}] para el mes dado."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(c.name, 'Sin categoría') AS name,
                   COALESCE(c.color, '#6b7280')       AS color,
                   COALESCE(c.icon,  '❓')             AS icon,
                   SUM(e.amount)                      AS total
            FROM expenses e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            GROUP BY e.category_id
            ORDER BY total DESC
            """,
            (str(year), f"{month:02d}"),
        ).fetchall()

    grand_total = sum(r["total"] for r in rows)
    result = []
    for r in rows:
        pct = round(r["total"] / grand_total * 100) if grand_total else 0
        result.append({
            "name":  r["name"],
            "color": r["color"],
            "icon":  r["icon"],
            "total": r["total"],
            "pct":   pct,
        })
    return result


def get_expenses_by_week_of_month(year: int, month: int):
    """Agrupa los gastos del mes por semana del mes (1–5) para el gráfico de barras."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT CAST(strftime('%d', e.created_at) AS INTEGER) AS day,
                   SUM(e.amount) AS total
            FROM expenses e
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            GROUP BY day
            """,
            (str(year), f"{month:02d}"),
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


def get_expenses_by_user(year: int, month: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.name, SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            GROUP BY e.user_id
            ORDER BY total DESC
            """,
            (str(year), f"{month:02d}"),
        ).fetchall()


# ── Edición de gastos ─────────────────────────────────────────────────────────

def update_expense(expense_id: int, concept: str, amount: float, category_id: int | None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET concept=?, amount=?, category_id=? WHERE id=?",
            (concept.strip(), amount, category_id, expense_id),
        )
        return cur.rowcount > 0


def get_expense_by_id(expense_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()


def get_expenses_uncategorized():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.created_at,
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
            (amount, expense_id, user_id),
        )
        return cur.rowcount > 0


def update_expense_category(expense_id: int, user_id: int, category_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE expenses SET category_id = ? WHERE id = ? AND user_id = ?",
            (category_id, expense_id, user_id),
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


def get_expense_count_by_category() -> dict:
    """Retorna {category_id: count} para categorías con al menos 1 gasto."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category_id, COUNT(*) AS cnt FROM expenses GROUP BY category_id"
        ).fetchall()
    return {r["category_id"]: r["cnt"] for r in rows if r["category_id"] is not None}


def create_category(name: str, icon: str, color: str) -> int | None:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO categories (name, icon, color) VALUES (?, ?, ?)",
                (name.strip(), icon.strip(), color.strip()),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
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
    except sqlite3.IntegrityError:
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
        return conn.execute("SELECT id, name, color FROM users ORDER BY name").fetchall()


def get_expenses_by_week_of_month_by_user(year: int, month: int):
    """Groups expenses by week-of-month and user for the stacked weekly bar chart."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT CAST(strftime('%d', e.created_at) AS INTEGER) AS day,
                   u.id   AS user_id,
                   u.name AS user_name,
                   SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
              AND strftime('%m', e.created_at) = ?
            GROUP BY day, e.user_id
            """,
            (str(year), f"{month:02d}"),
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


def get_annual_data(year: int) -> dict:
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
            GROUP BY month_num, e.category_id
            ORDER BY cat_name, month_num
            """,
            (str(year),),
        ).fetchall()

    cats: dict[str, list] = {}
    cat_colors: dict[str, str] = {}
    for r in rows:
        n = r["cat_name"]
        if n not in cats:
            cats[n] = [0.0] * 12
            cat_colors[n] = r["cat_color"]
        cats[n][r["month_num"] - 1] += r["total"]

    monthly_totals = [0.0] * 12
    for values in cats.values():
        for i, v in enumerate(values):
            monthly_totals[i] += v

    annual_total = sum(monthly_totals[:active_months])
    monthly_avg  = annual_total / active_months if active_months > 0 else 0

    peak_idx   = max(range(active_months), key=lambda i: monthly_totals[i]) if active_months > 0 else 0
    peak_month = {"name": MONTHS_SHORT[peak_idx], "amount": monthly_totals[peak_idx]}

    cat_totals   = {n: sum(v[:active_months]) for n, v in cats.items()}
    top_cat_name = max(cat_totals, key=cat_totals.get) if cat_totals else ""
    top_cat_amt  = cat_totals.get(top_cat_name, 0)
    top_cat_pct  = round(top_cat_amt / annual_total * 100) if annual_total else 0

    with get_conn() as conn:
        user_rows = conn.execute(
            """
            SELECT CAST(strftime('%m', e.created_at) AS INTEGER) AS month_num,
                   u.name AS user_name,
                   SUM(e.amount) AS total
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            WHERE strftime('%Y', e.created_at) = ?
            GROUP BY month_num, e.user_id
            ORDER BY month_num
            """,
            (str(year),),
        ).fetchall()

    by_user: dict[str, list] = {}
    for r in user_rows:
        nm = r["user_name"]
        if nm not in by_user:
            by_user[nm] = [0.0] * 12
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


def get_monthly_totals(months: int = 6) -> list[dict]:
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
            WHERE created_at >= ?
            GROUP BY ym
            ORDER BY ym
            """,
            (start_date,),
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
        total = row["total"] if row else 0.0
        cnt   = row["cnt"]   if row else 0
        result.append({
            "year":      y,
            "month":     m,
            "total":     total,
            "count":     cnt,
            "avg_daily": round(total / days_elapsed, 2) if days_elapsed > 0 else 0,
        })
    return result


# ── Keywords ──────────────────────────────────────────────────────────────────

def get_all_keywords():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT k.id, k.keyword, k.category_id, c.name AS category_name
            FROM keywords k
            JOIN categories c ON c.id = k.category_id
            ORDER BY c.name, k.keyword
            """
        ).fetchall()


def add_keyword(keyword: str, category_id: int) -> str:
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
            "INSERT INTO keywords (keyword, category_id) VALUES (?, ?)"
            " ON CONFLICT(keyword) DO UPDATE SET category_id = excluded.category_id",
            (kw, category_id),
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


# ── Gastos Fijos ──────────────────────────────────────────────────────────────

def get_all_fixed_expenses():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.category_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            WHERE fe.active = 1
            ORDER BY fe.concept
            """
        ).fetchall()


def get_fixed_expense_by_id(fe_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.category_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            WHERE fe.id = ?
            """,
            (fe_id,),
        ).fetchone()


def create_fixed_expense(concept: str, estimated_amount: float | None, category_id: int | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fixed_expenses (concept, estimated_amount, category_id) VALUES (?, ?, ?)",
            (concept.strip(), estimated_amount, category_id),
        )
        return cur.lastrowid


def update_fixed_expense(fe_id: int, concept: str, estimated_amount: float | None, category_id: int | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE fixed_expenses SET concept=?, estimated_amount=?, category_id=? WHERE id=?",
            (concept.strip(), estimated_amount, category_id, fe_id),
        )


def deactivate_fixed_expense(fe_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE fixed_expenses SET active=0 WHERE id=?", (fe_id,))


def get_fixed_payments_for_month(year: int, month: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT fe.id, fe.concept, fe.estimated_amount, fe.category_id, fe.active, fe.created_at,
                   COALESCE(c.name,  'Sin categoría') AS category_name,
                   COALESCE(c.color, '#6b7280')        AS category_color,
                   COALESCE(c.icon,  '❓')              AS category_icon,
                   fep.id         AS payment_id,
                   fep.expense_id AS payment_expense_id,
                   e.amount       AS actual_amount
            FROM fixed_expenses fe
            LEFT JOIN categories c ON c.id = fe.category_id
            LEFT JOIN fixed_expense_payments fep
                ON fep.fixed_expense_id = fe.id AND fep.year = ? AND fep.month = ?
            LEFT JOIN expenses e ON e.id = fep.expense_id
            WHERE fe.active = 1
            ORDER BY fe.concept
            """,
            (year, month),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["paid"] = d["payment_id"] is not None
        result.append(d)
    return result


def create_fixed_payment(fixed_expense_id: int, expense_id: int, year: int, month: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO fixed_expense_payments (fixed_expense_id, expense_id, year, month)"
            " VALUES (?, ?, ?, ?)",
            (fixed_expense_id, expense_id, year, month),
        )


def create_fixed_payment_without_expense(fixed_expense_id: int, year: int, month: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO fixed_expense_payments (fixed_expense_id, expense_id, year, month)"
            " VALUES (?, NULL, ?, ?)",
            (fixed_expense_id, year, month),
        )


def get_fixed_expense_monthly_summary(year: int, month: int) -> dict:
    rows = get_fixed_payments_for_month(year, month)
    count_total     = len(rows)
    count_paid      = sum(1 for r in rows if r["paid"])
    total_estimated = sum(r["estimated_amount"] or 0 for r in rows)
    total_paid      = sum(r["actual_amount"] or 0 for r in rows if r["paid"])
    return {
        "count_total":     count_total,
        "count_paid":      count_paid,
        "total_estimated": total_estimated,
        "total_paid":      total_paid,
    }


# ── Cambios de Dólar ──────────────────────────────────────────────────────────

def registrar_cambio(fecha: str, monto_usd: float, cotizacion: float, usuario: str) -> int:
    monto_ars = monto_usd * cotizacion
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cambios_dolar (fecha, monto_usd, cotizacion, monto_ars, usuario)"
            " VALUES (?, ?, ?, ?, ?)",
            (fecha, monto_usd, cotizacion, monto_ars, usuario),
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
            "SELECT id, fecha, monto_usd, cotizacion, monto_ars, usuario"
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
