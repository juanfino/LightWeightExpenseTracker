"""Generic per-family taxonomy.

Schema changes belong to Alembic. This module is called only when a family is
created; it deliberately contains no household-specific learned keywords.
"""
import pgcompat


CATEGORIES = [
    ("Hogar", "#22c55e", "🏠", ["Supermercado", "Verdulería", "Carnicería", "Limpieza", "Servicios"]),
    ("Transporte", "#06b6d4", "🚌", ["Nafta", "Transporte público", "Estacionamiento", "Mantenimiento"]),
    ("Salud", "#ef4444", "💊", ["Farmacia", "Consultas", "Obra social"]),
    ("Educación", "#f97316", "📚", ["Cuotas", "Materiales"]),
    ("Ocio", "#a855f7", "🎬", ["Salidas", "Streaming", "Viajes"]),
    ("Cuidado personal", "#ec4899", "💇", ["Peluquería", "Cosmética"]),
    ("Ropa", "#8b5cf6", "👕", []),
    ("Mascotas", "#84cc16", "🐾", []),
    ("Impuestos y servicios", "#3b82f6", "🧾", []),
    ("Gastos generales", "#f59e0b", "🛍️", []),
    ("Sin categoría", "#6b7280", "❓", []),
]

KEYWORDS = {
    "supermercado": ("Hogar", "Supermercado"),
    "verduleria": ("Hogar", "Verdulería"),
    "carniceria": ("Hogar", "Carnicería"),
    "limpieza": ("Hogar", "Limpieza"),
    "nafta": ("Transporte", "Nafta"),
    "colectivo": ("Transporte", "Transporte público"),
    "subte": ("Transporte", "Transporte público"),
    "estacionamiento": ("Transporte", "Estacionamiento"),
    "farmacia": ("Salud", "Farmacia"),
    "medico": ("Salud", "Consultas"),
    "obra social": ("Salud", "Obra social"),
    "colegio": ("Educación", "Cuotas"),
    "streaming": ("Ocio", "Streaming"),
    "peluqueria": ("Cuidado personal", "Peluquería"),
    "veterinaria": ("Mascotas", None),
}

INCOME_CATEGORIES = [
    ("Sueldo", "💼", "#22c55e"),
    ("Freelance / Honorarios", "🧑‍💻", "#06b6d4"),
    ("Alquiler", "🏠", "#f59e0b"),
    ("Venta", "🏷️", "#8b5cf6"),
    ("Reintegro", "↩️", "#3b82f6"),
    ("Intereses / Inversiones", "📈", "#14b8a6"),
    ("Regalo", "🎁", "#ec4899"),
    ("Otros", "💵", "#6b7280"),
]


def create_family_defaults(conn, family_id: int) -> None:
    """Seed an empty family idempotently using the active tenant transaction."""
    previous = pgcompat.current_family_id()
    if previous != family_id:
        raise ValueError("El tenant activo no coincide con la familia a inicializar")

    category_ids = {}
    subcategory_ids = {}
    for name, color, icon, subcategories in CATEGORIES:
        conn.execute(
            """
            INSERT INTO categories (family_id, name, color, icon)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (family_id, name) DO NOTHING
            """,
            (family_id, name, color, icon),
        )
        category = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (name,)
        ).fetchone()
        category_ids[name] = category["id"]
        for subcategory_name in subcategories:
            conn.execute(
                """
                INSERT INTO subcategories (family_id, category_id, name)
                VALUES (?, ?, ?)
                ON CONFLICT (family_id, category_id, name) DO NOTHING
                """,
                (family_id, category["id"], subcategory_name),
            )
            subcategory = conn.execute(
                "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
                (category["id"], subcategory_name),
            ).fetchone()
            subcategory_ids[(name, subcategory_name)] = subcategory["id"]

    for keyword, (category_name, subcategory_name) in KEYWORDS.items():
        conn.execute(
            """
            INSERT INTO keywords (family_id, keyword, category_id, subcategory_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (family_id, keyword) DO NOTHING
            """,
            (
                family_id,
                keyword,
                category_ids[category_name],
                subcategory_ids.get((category_name, subcategory_name)),
            ),
        )

    for name, icon, color in INCOME_CATEGORIES:
        conn.execute(
            """
            INSERT INTO income_categories (family_id, name, icon, color)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (family_id, name) DO NOTHING
            """,
            (family_id, name, icon, color),
        )
