import logging
import db

logger = logging.getLogger(__name__)

NEW_CATEGORIES = [
    {"name": "Hogar",            "color": "#22c55e", "icon": "🏠"},
    {"name": "Vehículos",        "color": "#f59e0b", "icon": "🚗"},
    {"name": "Salud",            "color": "#ef4444", "icon": "💊"},
    {"name": "Servicios",        "color": "#3b82f6", "icon": "🔌"},
    {"name": "Entretenimiento",  "color": "#a855f7", "icon": "🎬"},
    {"name": "Transporte",       "color": "#06b6d4", "icon": "🚌"},
    {"name": "Hijos",            "color": "#f97316", "icon": "👶"},
    {"name": "Gastos Generales", "color": "#ec4899", "icon": "🛍️"},
    {"name": "Trabajo",          "color": "#8b5cf6", "icon": "💼"},
    {"name": "Sin categoría",    "color": "#6b7280", "icon": "❓"},
]

SUBCATEGORIES = {
    "Hogar":            ["Supermercado", "Alimentación", "Empleada Doméstica", "Mantenimiento Casa", "Limpieza"],
    "Vehículos":        ["Nafta", "Seguro", "Patente", "Service", "Peaje"],
    "Salud":            ["Farmacia", "Médico", "Obra social"],
    "Servicios":        ["Internet", "Teléfono", "Electricidad", "Gas", "Agua"],
    "Entretenimiento":  ["Gustos", "Salidas", "Streaming", "Deportes"],
    "Hijos":            ["Educación", "Salud", "Ropa", "Actividades"],
    "Gastos Generales": ["Ropa", "Compras varias"],
    "Trabajo":          ["Impuestos", "Gastos laborales"],
}

DEFAULT_KEYWORDS = {
    "Hogar":            ["supermercado", "super", "almacen", "verduleria", "carniceria",
                         "panaderia", "feria", "mercado", "kiosco", "fiambreria", "despensa"],
    "Vehículos":        ["nafta", "combustible", "ypf", "shell", "axion", "puma",
                         "taller", "mecanico", "gomeria", "repuesto", "aceite", "patente"],
    "Salud":            ["farmacia", "medico", "doctor", "clinica", "hospital",
                         "remedios", "medicamento", "turno", "dentista", "oculista"],
    "Servicios":        ["luz", "gas", "agua", "internet", "telefono", "celular",
                         "claro", "personal", "movistar", "directv", "netflix", "spotify"],
    "Entretenimiento":  ["cine", "teatro", "bar", "restaurant", "restaurante",
                         "pizza", "sushi", "delivery", "pedidosya", "rappi"],
    "Transporte":       ["uber", "taxi", "remis", "subte", "colectivo", "tren", "peaje"],
    "Hijos":            ["colegio", "universidad", "curso", "libro", "cuota", "matricula"],
    "Gastos Generales": ["ropa", "zapatillas", "zapatos", "indumentaria", "calzado"],
}

# (category_name, subcategory_name, [keywords])
KEYWORD_SUBCATEGORY_MAP = [
    ("Vehículos",        "Nafta",              ["nafta", "combustible", "ypf", "shell", "axion", "puma"]),
    ("Vehículos",        "Service",            ["taller", "mecanico", "gomeria", "repuesto", "aceite"]),
    ("Vehículos",        "Patente",            ["patente"]),
    ("Salud",            "Farmacia",           ["farmacia", "remedios", "medicamento"]),
    ("Salud",            "Médico",             ["medico", "doctor", "clinica", "hospital", "turno", "dentista", "oculista"]),
    ("Servicios",        "Electricidad",       ["luz", "electricidad"]),
    ("Servicios",        "Gas",                ["gas"]),
    ("Servicios",        "Agua",               ["agua"]),
    ("Servicios",        "Internet",           ["internet", "directv"]),
    ("Servicios",        "Teléfono",           ["telefono", "celular", "claro", "personal", "movistar"]),
    ("Servicios",        "Streaming",          ["netflix", "spotify"]),
    ("Hijos",            "Educación",          ["colegio", "universidad", "curso", "libro", "cuota", "matricula", "ondina"]),
    ("Hogar",            "Alimentación",       ["verduleria", "carniceria", "panaderia", "feria", "almacen", "kiosco",
                                                "fiambreria", "despensa", "pastas", "dietetica", "verdu", "mercado",
                                                "alimentos", "carnes", "santa elena"]),
    ("Hogar",            "Supermercado",       ["supermercado", "super", "jumbo", "dia", "coto", "walmart", "carrefour"]),
    ("Hogar",            "Empleada Doméstica", ["empleada", "limpieza doméstica"]),
    ("Hogar",            "Limpieza",           ["limpieza"]),
    ("Entretenimiento",  "Gustos",             ["gustito", "gusto", "regalo", "regalos"]),
    ("Entretenimiento",  "Salidas",            ["cine", "teatro", "bar", "restaurant", "restaurante", "pizza", "sushi"]),
    ("Entretenimiento",  "Streaming",          ["delivery", "pedidosya", "rappi"]),
    ("Gastos Generales", "Ropa",               ["ropa", "zapatillas", "zapatos", "indumentaria", "calzado"]),
    ("Trabajo",          "Impuestos",          ["impuesto", "afip", "arba", "monotributo", "ingresos brutos"]),
]

# (old_name, new_category, new_subcategory)
CATEGORY_MIGRATIONS = [
    ("Alimentación",       "Hogar",            "Alimentación"),
    ("Supermercado",       "Hogar",            "Supermercado"),
    ("Educación",          "Hijos",            "Educación"),
    ("Empleada Domestica", "Hogar",            "Empleada Doméstica"),
    ("Mantenimiento Casa", "Hogar",            "Mantenimiento Casa"),
    ("Gustos",             "Entretenimiento",  "Gustos"),
    ("Impuestos",          "Trabajo",          "Impuestos"),
    ("Ropa",               "Gastos Generales", "Ropa"),
]


def seed(conn):
    for cat in NEW_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, color, icon) VALUES (?, ?, ?)",
            (cat["name"], cat["color"], cat["icon"]),
        )

    for cat_name, subcats in SUBCATEGORIES.items():
        row = conn.execute("SELECT id FROM categories WHERE name = ?", (cat_name,)).fetchone()
        if row is None:
            continue
        cat_id = row[0]
        for subcat_name in subcats:
            exists = conn.execute(
                "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
                (cat_id, subcat_name),
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
                    (cat_id, subcat_name),
                )

    for cat_name, keywords in DEFAULT_KEYWORDS.items():
        row = conn.execute("SELECT id FROM categories WHERE name = ?", (cat_name,)).fetchone()
        if row is None:
            continue
        cat_id = row[0]
        for kw in keywords:
            conn.execute(
                "INSERT OR IGNORE INTO keywords (keyword, category_id) VALUES (?, ?)",
                (kw, cat_id),
            )

    seed_keyword_subcategories(conn)

    try:
        for old_name, new_cat_name, subcat_name in CATEGORY_MIGRATIONS:
            old_row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (old_name,)
            ).fetchone()
            if old_row is None:
                continue
            old_cat_id = old_row[0]

            new_row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (new_cat_name,)
            ).fetchone()
            if new_row is None:
                logger.warning("seed migration: new category '%s' not found, skipping", new_cat_name)
                continue
            new_cat_id = new_row[0]

            subcat_row = conn.execute(
                "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
                (new_cat_id, subcat_name),
            ).fetchone()
            if subcat_row is None:
                logger.warning(
                    "seed migration: subcategory '%s' under '%s' not found, skipping",
                    subcat_name, new_cat_name,
                )
                continue
            new_subcat_id = subcat_row[0]

            conn.execute(
                "UPDATE expenses SET category_id = ?, subcategory_id = ? WHERE category_id = ?",
                (new_cat_id, new_subcat_id, old_cat_id),
            )
            conn.execute(
                "UPDATE keywords SET category_id = ?, subcategory_id = ? WHERE category_id = ?",
                (new_cat_id, new_subcat_id, old_cat_id),
            )

            remaining = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE category_id = ?", (old_cat_id,)
            ).fetchone()[0]
            if remaining == 0:
                conn.execute("DELETE FROM categories WHERE id = ?", (old_cat_id,))
    except Exception as e:
        logger.error("seed migration error: %s", e)


def seed_keyword_subcategories(conn):
    try:
        for cat_name, subcat_name, keywords in KEYWORD_SUBCATEGORY_MAP:
            cat_row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (cat_name,)
            ).fetchone()
            if cat_row is None:
                logger.warning("seed_keyword_subcategories: category '%s' not found, skipping", cat_name)
                continue
            cat_id = cat_row[0]

            subcat_row = conn.execute(
                "SELECT id FROM subcategories WHERE category_id = ? AND name = ?",
                (cat_id, subcat_name),
            ).fetchone()
            if subcat_row is None:
                logger.warning(
                    "seed_keyword_subcategories: subcategory '%s' under '%s' not found, skipping",
                    subcat_name, cat_name,
                )
                continue
            subcat_id = subcat_row[0]

            for kw in keywords:
                conn.execute(
                    "UPDATE keywords SET subcategory_id = ? WHERE keyword = ? AND subcategory_id IS NULL",
                    (subcat_id, kw),
                )
    except Exception as e:
        logger.error("seed_keyword_subcategories error: %s", e)
