import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/gastos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
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


def init_db(users: dict | None = None):
    """Crea tablas. Si la DB es nueva, ejecuta seed y crea los usuarios configurados."""
    is_new = not os.path.exists(DB_PATH)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
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
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, category_id, concept, amount, raw_text)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, category_id, concept, amount, raw_text),
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
            SELECT e.id, e.concept, e.amount, e.raw_text, e.created_at,
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
            SELECT e.id, e.concept, e.amount, e.raw_text, e.created_at,
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


def get_expenses_by_week(year: int, week: int):
    """Retorna gastos de la isoweek dada (lun–dom)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.id, e.concept, e.amount, e.created_at,
                   u.name AS user_name,
                   COALESCE(c.name, 'Sin categoría') AS category_name
            FROM expenses e
            JOIN users u ON u.id = e.user_id
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE strftime('%Y', e.created_at) = ?
              AND CAST(strftime('%W', e.created_at) AS INTEGER) = ?
            ORDER BY e.created_at DESC
            """,
            (str(year), week),
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
            WHERE date(e.created_at) = date('now')
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
