"""Small destructive smoke test intended for an empty scratch PostgreSQL DB."""
from decimal import Decimal

import db
import pgcompat
import sqlro


def main():
    db.init_db({"999000111": "Smoke Test"})
    user = db.get_user_by_telegram_id("999000111")
    assert user
    expense_id = db.create_expense(
        user["id"], None, "Prueba PostgreSQL", 123.45, "Prueba PostgreSQL 123,45"
    )
    assert expense_id
    stored = db.get_expense_by_id(expense_id)
    assert stored["concept"] == "Prueba PostgreSQL"
    assert stored["amount"] == Decimal("123.45")
    assert isinstance(stored["amount"], Decimal)
    assert set(db.SUPPORTED_CURRENCIES) == {"ARS", "USD", "BRL", "EUR"}
    with pgcompat.current_pool().connection() as conn:
        fk_names = {
            row[0]
            for row in conn.execute(
                "SELECT conname FROM pg_constraint WHERE contype = 'f' "
                "AND conname = ANY(%s)",
                ([
                    "fk_expenses_currency",
                    "fk_fixed_expenses_currency",
                    "fk_incomes_currency",
                    "fk_expense_classifications_currency",
                    "fk_families_default_currency",
                    "fk_cambios_currency_given",
                    "fk_cambios_currency_received",
                    "fk_report_forecasts_currency",
                ],),
            ).fetchall()
        }
        assert len(fk_names) == 8, fk_names
        try:
            conn.execute("UPDATE families SET default_currency = 'ZZZ' WHERE id = 1")
            conn.commit()
        except Exception:
            conn.rollback()
        else:
            raise AssertionError("currency FK accepted an unknown code")
    assert db.get_recent_expenses()
    cambio_id = db.registrar_cambio(
        "2026-08-08", "100.00", "BRL", "18.25", "EUR", user["name"]
    )
    cambio = dict(db.get_cambios_historial()[0])
    assert cambio["id"] == cambio_id
    assert cambio["rate_received_per_given"] == Decimal("0.182500000000000000")
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
