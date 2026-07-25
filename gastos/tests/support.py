import db


def reset_database(users=None):
    db.init_db()
    with db.get_conn() as conn:
        conn.execute(
            "TRUNCATE TABLE expense_classifications, reports, ipc_series, "
            "cambios_dolar, expenses, fixed_expenses, keywords, subcategories, "
            "categories, users RESTART IDENTITY CASCADE"
        )
    db.init_db(users or {})
