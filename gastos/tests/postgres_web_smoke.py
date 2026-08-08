"""Exercise every non-parameterized GET route against a migrated scratch DB."""
from datetime import date

import dashboard
import db
import pgcompat
from support import authenticated_client


def main():
    client, _csrf_headers = authenticated_client(dashboard)
    user = db.get_user_by_telegram_id("100")
    pgcompat.set_family_id(user["family_id"])
    pgcompat.set_user_id(user["id"])
    today = date.today().isoformat()
    db.create_expense_full(user["id"], None, "Decimal API smoke", "123.45", today)
    db.create_fixed_expense("Decimal fixed smoke", "67.89", None)
    db.create_income(user["id"], "Decimal income smoke", "456.78", "ARS", today)
    db.registrar_cambio(today, "10.25", "1234.56", user["name"])
    failures = []
    skipped = {"/static/<path:filename>", "/resumenes/<period>"}
    routes = sorted(
        {
            rule.rule
            for rule in dashboard.app.url_map.iter_rules()
            if "GET" in rule.methods and rule.rule not in skipped and "<" not in rule.rule
        }
    )
    for route in routes:
        response = client.get(route)
        print(route, response.status_code)
        if response.status_code >= 500:
            failures.append((route, response.status_code))
        if route.startswith("/api/") and response.is_json:
            response.get_json()
    numeric_samples = [
        next(row["amount"] for row in client.get("/api/expenses").get_json()
             if row["concept"] == "Decimal API smoke"),
        next(row["estimated_amount"] for row in client.get("/api/fixed-expenses").get_json()
             if row["concept"] == "Decimal fixed smoke"),
        next(row["amount"] for row in client.get("/api/incomes").get_json()
             if row["concept"] == "Decimal income smoke"),
        client.get("/api/cambios/resumen").get_json()["total_usd_mes"],
    ]
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in numeric_samples), numeric_samples
    if failures:
        raise AssertionError(f"GET route failures: {failures}")
    print(f"postgres_web_smoke_ok routes={len(routes)}")


if __name__ == "__main__":
    main()
