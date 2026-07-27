import db
import auth
import pgcompat


def reset_database(users=None):
    db.init_db()
    with pgcompat.current_pool().connection() as conn:
        conn.execute(
            "TRUNCATE TABLE oauth_identities, otp_codes, sessions, llm_calls, "
            "expense_classifications, reports, ipc_series, "
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


def authenticated_client(dashboard, telegram_id="100"):
    user = db.get_user_by_telegram_id(telegram_id)
    token, csrf_token = auth.create_session(user["id"], "test-client", "127.0.0.1")
    client = dashboard.app.test_client()
    client.set_cookie(auth.SESSION_COOKIE, token)
    return client, {"X-CSRF-Token": csrf_token}
