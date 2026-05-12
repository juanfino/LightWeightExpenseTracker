import db

DEFAULT_CATEGORIES = [
    {"name": "Alimentación",    "color": "#22c55e", "icon": "🛒"},
    {"name": "Vehículos",       "color": "#f59e0b", "icon": "🚗"},
    {"name": "Salud",           "color": "#ef4444", "icon": "💊"},
    {"name": "Servicios",       "color": "#3b82f6", "icon": "🔌"},
    {"name": "Entretenimiento", "color": "#a855f7", "icon": "🎬"},
    {"name": "Transporte",      "color": "#06b6d4", "icon": "🚌"},
    {"name": "Educación",       "color": "#f97316", "icon": "📚"},
    {"name": "Ropa",            "color": "#ec4899", "icon": "👕"},
    {"name": "Sin categoría",   "color": "#6b7280", "icon": "❓"},
]

DEFAULT_KEYWORDS = {
    "Alimentación":    ["supermercado", "super", "almacen", "verduleria", "carniceria",
                        "panaderia", "feria", "mercado", "kiosco", "fiambreria", "despensa"],
    "Vehículos":       ["nafta", "combustible", "ypf", "shell", "axion", "puma",
                        "taller", "mecanico", "gomeria", "repuesto", "aceite", "patente"],
    "Salud":           ["farmacia", "medico", "doctor", "clinica", "hospital",
                        "remedios", "medicamento", "turno", "dentista", "oculista"],
    "Servicios":       ["luz", "gas", "agua", "internet", "telefono", "celular",
                        "claro", "personal", "movistar", "directv", "netflix", "spotify"],
    "Entretenimiento": ["cine", "teatro", "bar", "restaurant", "restaurante",
                        "pizza", "sushi", "delivery", "pedidosya", "rappi"],
    "Transporte":      ["uber", "taxi", "remis", "subte", "colectivo", "tren", "peaje"],
    "Educación":       ["colegio", "universidad", "curso", "libro", "cuota", "matricula"],
    "Ropa":            ["ropa", "zapatillas", "zapatos", "indumentaria", "calzado"],
}


def run():
    with db.get_conn() as conn:
        for cat in DEFAULT_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, color, icon) VALUES (?, ?, ?)",
                (cat["name"], cat["color"], cat["icon"]),
            )

    with db.get_conn() as conn:
        for cat_name, keywords in DEFAULT_KEYWORDS.items():
            row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (cat_name,)
            ).fetchone()
            if row is None:
                continue
            cat_id = row[0]
            for kw in keywords:
                conn.execute(
                    "INSERT OR IGNORE INTO keywords (keyword, category_id) VALUES (?, ?)",
                    (kw, cat_id),
                )
