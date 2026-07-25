"""Small destructive smoke test intended for an empty scratch PostgreSQL DB."""
import db
import sqlro


def main():
    db.init_db({"999000111": "Smoke Test"})
    user = db.get_user_by_telegram_id("999000111")
    assert user
    expense_id = db.create_expense(
        user["id"], None, "Prueba PostgreSQL", 123.45, "Prueba PostgreSQL 123,45"
    )
    assert expense_id
    assert db.get_expense_by_id(expense_id)["concept"] == "Prueba PostgreSQL"
    assert db.get_recent_expenses()
    assert db.get_expense_years()
    assert db.get_expenses_today()
    assert db.get_months_with_data()
    assert sqlro.run_readonly(
        "SELECT concept FROM expenses WHERE id = ?", (expense_id,)
    )[0]["concept"] == "Prueba PostgreSQL"
    try:
        sqlro.run_readonly("UPDATE expenses SET amount = 0")
    except sqlro.ReadOnlySQLError:
        pass
    else:
        raise AssertionError("sqlro accepted a write")
    print("postgres_smoke_ok")


if __name__ == "__main__":
    main()
