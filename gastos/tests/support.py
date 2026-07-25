import db
import pgcompat


def reset_database(users=None):
    db.init_db()
    with pgcompat.current_pool().connection() as conn:
        conn.execute(
            "TRUNCATE TABLE llm_calls, expense_classifications, reports, ipc_series, "
            "cambios_dolar, expenses, fixed_expenses, keywords, subcategories, "
            "categories, memberships, families, users RESTART IDENTITY CASCADE"
        )
        conn.execute(
            "INSERT INTO families (id, name) OVERRIDING SYSTEM VALUE "
            "VALUES (1, 'Familia de prueba')"
        )
        conn.execute("SELECT setval(pg_get_serial_sequence('families', 'id'), 1)")
        conn.commit()
    pgcompat.set_family_id(1)
    db._sync_users(users or {})
    import seed
    with db.get_conn() as conn:
        seed.create_family_defaults(conn, 1)
