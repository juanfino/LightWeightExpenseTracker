import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, redirect

import requests as http_requests

import db
import backup as backup_module
import fixed_matcher
import categorizer
import report

app = Flask(__name__)

BAIRES = timezone(timedelta(hours=-3))

MONTHS_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def _month_label(year: int, month: int) -> str:
    return f"{MONTHS_ES[month]} {year}"


def _to_baires_str(dt_str: str) -> str:
    """Converts a UTC SQLite timestamp string to Buenos Aires (UTC-3) time."""
    if not dt_str:
        return dt_str
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        dt_ba = dt.replace(tzinfo=timezone.utc).astimezone(BAIRES)
        return dt_ba.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return dt_str


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("created_at"):
        d["created_at"] = _to_baires_str(d["created_at"])
    return d


def _currency_arg() -> str:
    """Validate dashboard currency input; ARS remains the default surface."""
    body = request.get_json(silent=True) or {}
    return db.normalize_currency(request.args.get("currency") or body.get("currency") or "ARS")


# ── Páginas ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    now = datetime.now()
    return render_template("index.html", year=now.year, month=now.month,
                           month_label=_month_label(now.year, now.month))


@app.route("/history")
def history():
    categories = db.get_all_categories()
    users      = db.get_all_users()
    years      = db.get_expense_years()
    return render_template(
        "history.html",
        categories=[_row_to_dict(c) for c in categories],
        users=[_row_to_dict(u) for u in users],
        years=years,
    )


@app.route("/settings")
def settings():
    categories     = db.get_all_categories()
    keywords       = db.get_all_keywords()
    expense_counts = db.get_expense_count_by_category()
    cats_with_counts = []
    for c in categories:
        d = _row_to_dict(c)
        d["expense_count"] = expense_counts.get(c["id"], 0)
        cats_with_counts.append(d)
    return render_template(
        "settings.html",
        categories=cats_with_counts,
        keywords=[_row_to_dict(k) for k in keywords],
    )


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/summary")
def api_summary():
    now = datetime.now()
    year, month = now.year, now.month

    try:
        currency = _currency_arg()
    except ValueError:
        return jsonify({"error": "Moneda inválida"}), 400
    by_category = db.get_expenses_summary_by_category(year, month, currency=currency)
    by_week     = db.get_expenses_by_week_of_month(year, month, currency=currency)
    by_user_rows = db.get_expenses_by_user(year, month, currency=currency)

    total = sum(r["total"] for r in by_category)
    other_currency = "USD" if currency == "ARS" else "ARS"
    other_total = sum(r["total"] for r in db.get_expenses_summary_by_category(year, month, currency=other_currency))

    return jsonify({
        "month":       _month_label(year, month),
        "currency":    currency,
        "other_currency": other_currency,
        "other_total": other_total,
        "total":       total,
        "by_category": by_category,
        "by_week":     by_week,
        "by_user":     [{"name": r["name"], "total": r["total"]} for r in by_user_rows],
    })


@app.route("/api/monthly")
def api_monthly():
    try:
        year  = int(request.args.get("year",  datetime.now().year))
        month = int(request.args.get("month", datetime.now().month))
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    usuario   = request.args.get("usuario", "").strip()
    user_name = usuario if usuario and usuario != "Todos" else None
    try:
        currency = _currency_arg()
    except ValueError:
        return jsonify({"error": "Moneda inválida"}), 400

    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)

    by_category      = db.get_expenses_summary_by_category(year, month, user_name, currency)
    by_week          = db.get_expenses_by_week_of_month(year, month, user_name, currency)
    by_week_by_user  = db.get_expenses_by_week_of_month_by_user(year, month, user_name, currency)
    by_week_prev     = db.get_expenses_by_week_of_month(prev_y, prev_m, user_name, currency)
    by_user_rows     = db.get_expenses_by_user(year, month, currency)
    total = sum(r["total"] for r in by_category)
    other_currency = "USD" if currency == "ARS" else "ARS"
    other_total = sum(r["total"] for r in db.get_expenses_summary_by_category(year, month, user_name, other_currency))

    return jsonify({
        "month":           _month_label(year, month),
        "currency":        currency,
        "other_currency":  other_currency,
        "other_total":     other_total,
        "total":           total,
        "by_category":     by_category,
        "by_week":         by_week,
        "by_week_by_user": by_week_by_user,
        "by_week_prev":    by_week_prev,
        "by_user":         [{"name": r["name"], "total": r["total"]} for r in by_user_rows],
    })


@app.route("/api/users")
def api_users():
    return jsonify([{"id": u["id"], "name": u["name"], "color": u["color"]} for u in db.get_all_users()])


@app.route("/api/annual/<int:year>")
def api_annual(year: int):
    if year < 2020 or year > 2099:
        return jsonify({"error": "Año inválido"}), 400
    try:
        return jsonify(db.get_annual_data(year, _currency_arg()))
    except ValueError:
        return jsonify({"error": "Moneda inválida"}), 400


@app.route("/api/sparklines")
def api_sparklines():
    try:
        return jsonify(db.get_monthly_totals(6, _currency_arg()))
    except ValueError:
        return jsonify({"error": "Moneda inválida"}), 400


@app.route("/api/weekly")
def api_weekly():
    # Standalone API endpoint — not surfaced in the dashboard UI.
    # Accepts ?year=YYYY&week=N (ISO week number) and returns that week's expenses.
    try:
        year = int(request.args.get("year", datetime.now().year))
        week = int(request.args.get("week", datetime.now().isocalendar().week))
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    rows = db.get_expenses_by_week(year, week)
    return jsonify([_row_to_dict(r) for r in rows])


@app.route("/api/expenses")
def api_expenses():
    year_raw       = request.args.get("year", "")
    month_raw      = request.args.get("month", "")
    category_id    = request.args.get("category_id")
    subcategory_id = request.args.get("subcategory_id")
    fixed          = request.args.get("fixed", "").strip()
    user_id        = request.args.get("user_id")
    usuario        = request.args.get("usuario", "").strip()
    q              = request.args.get("q", "").strip()
    currency       = request.args.get("currency", "").upper().strip()

    try:
        if not year_raw and not month_raw:
            rows = db.get_recent_expenses(limit=200)
        else:
            year  = int(year_raw)  if year_raw  and year_raw  != "all" else None
            month = int(month_raw) if month_raw and month_raw != "all" else None
            rows = db.get_expenses_filtered(year, month)
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    result = [_row_to_dict(r) for r in rows]

    if category_id:
        if category_id == "null":
            result = [r for r in result if r.get("category_id") is None]
        else:
            result = [r for r in result if str(r.get("category_id")) == str(category_id)]
    if subcategory_id:
        result = [r for r in result if str(r.get("subcategory_id")) == str(subcategory_id)]
    if fixed == "fixed":
        result = [r for r in result if r.get("fixed_expense_id") is not None]
    elif fixed == "variable":
        result = [r for r in result if r.get("fixed_expense_id") is None]
    if user_id:
        result = [r for r in result if str(r.get("user_id")) == str(user_id)]
    if usuario and usuario != "Todos":
        result = [r for r in result if r.get("user_name") == usuario]
    if q:
        needle = categorizer.normalize(q)
        result = [r for r in result if needle in categorizer.normalize(r.get("concept") or "")]
    if currency:
        if currency not in db.SUPPORTED_CURRENCIES:
            return jsonify({"error": "Moneda inválida"}), 400
        result = [r for r in result if r.get("currency") == currency]

    return jsonify(result)


@app.route("/api/gastos-por-categoria")
def api_gastos_por_categoria():
    mes     = request.args.get("mes", "")
    usuario = request.args.get("usuario", "").strip()
    try:
        year, month = int(mes[:4]), int(mes[5:7])
    except (ValueError, IndexError, TypeError):
        now = datetime.now()
        year, month = now.year, now.month
    user_name = usuario if usuario and usuario != "Todos" else None
    try:
        return jsonify(db.get_gastos_por_categoria(year, month, user_name, _currency_arg()))
    except ValueError:
        return jsonify({"error": "Moneda inválida"}), 400


@app.route("/api/categories")
def api_categories():
    return jsonify([dict(c) for c in db.get_all_categories()])


@app.route("/api/expenses/add", methods=["POST"])
def api_expenses_add():
    data        = request.get_json(silent=True) or {}
    concept     = (data.get("concept") or "").strip()
    amount      = data.get("amount")
    category_id = data.get("category_id")
    subcategory_id = data.get("subcategory_id")
    user_id     = data.get("user_id")
    date_str    = (data.get("date") or "").strip()
    currency    = (data.get("currency") or "ARS").upper()

    if not concept or amount is None or not user_id or not date_str:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Fecha inválida"}), 400

    try:
        cat_id = int(category_id) if category_id else None
        subcat_id = int(subcategory_id) if subcategory_id else None
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Categoría o subcategoría inválida"}), 400

    if cat_id and not db.get_category_by_id(cat_id):
        return jsonify({"ok": False, "error": "Categoría no encontrada"}), 404
    if subcat_id:
        subcat = db.get_subcategory_by_id(subcat_id)
        if not subcat or subcat["category_id"] != cat_id:
            return jsonify({"ok": False, "error": "La subcategoría no pertenece a la categoría seleccionada"}), 400
    try:
        expense_id = db.create_expense_full(
            int(user_id), cat_id, concept, amount, date_str,
            subcategory_id=subcat_id, currency=currency,
        )
    except ValueError:
        return jsonify({"ok": False, "error": "Moneda inválida"}), 400

    suggestion = None
    matches = fixed_matcher.find_fixed_expense_matches(concept, db.get_all_fixed_expenses(), currency)
    if len(matches) == 1:
        suggestion = {"id": matches[0]["id"], "concept": matches[0]["concept"]}

    return jsonify({"ok": True, "id": expense_id, "suggested_fixed_expense": suggestion})


@app.route("/api/expenses/delete", methods=["POST"])
def api_expenses_delete():
    data = request.get_json(silent=True) or {}
    expense_id = data.get("id")
    if not expense_id:
        return jsonify({"error": "Falta el campo 'id'"}), 400
    deleted = db.delete_expense(int(expense_id))
    if deleted:
        return jsonify({"ok": True})
    return jsonify({"error": "Gasto no encontrado"}), 404


@app.route("/api/keywords/add", methods=["POST"])
def api_keywords_add():
    data = request.get_json(silent=True) or {}
    keyword     = data.get("keyword", "").strip().lower()
    category_id = data.get("category_id")
    if not keyword or not category_id:
        return jsonify({"error": "Faltan campos 'keyword' o 'category_id'"}), 400
    status = db.add_keyword(keyword, int(category_id))
    return jsonify({"ok": True, "status": status})


@app.route("/api/keywords/delete", methods=["POST"])
def api_keywords_delete():
    data = request.get_json(silent=True) or {}
    keyword_id = data.get("id")
    if not keyword_id:
        return jsonify({"error": "Falta el campo 'id'"}), 400
    deleted = db.delete_keyword(int(keyword_id))
    if deleted:
        return jsonify({"ok": True})
    return jsonify({"error": "Keyword no encontrada"}), 404


@app.route("/api/expenses/update", methods=["POST"])
def api_expenses_update():
    data             = request.get_json(silent=True) or {}
    expense_id       = data.get("id")
    concept          = (data.get("concept") or "").strip()
    amount           = data.get("amount")
    category_id      = data.get("category_id")      # may be None / null
    subcategory_id   = data.get("subcategory_id")    # may be None / null
    fixed_expense_id = data.get("fixed_expense_id")  # may be None / null
    date_str         = (data.get("date") or "").strip() or None
    currency         = (data.get("currency") or "ARS").upper()

    if not expense_id or not concept or amount is None:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400

    if date_str is not None:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "Fecha inválida"}), 400

    cat_id    = int(category_id)    if category_id    else None
    subcat_id = int(subcategory_id) if subcategory_id else None
    existing = db.get_expense_by_id(int(expense_id))
    if not existing:
        return jsonify({"ok": False, "error": "Gasto no encontrado"}), 404
    if existing["fixed_expense_id"] is not None and currency != existing["currency"]:
        return jsonify({"ok": False, "error": "No se puede cambiar la moneda de un gasto vinculado a un fijo"}), 409
    target_fixed = None
    if fixed_expense_id:
        target_fixed = db.get_fixed_expense_by_id(int(fixed_expense_id))
        if not target_fixed:
            return jsonify({"ok": False, "error": "Gasto fijo no encontrado"}), 404
        if currency != target_fixed["currency"]:
            return jsonify({"ok": False, "error": "La moneda debe coincidir con el gasto fijo"}), 409
    try:
        updated = db.update_expense(int(expense_id), concept, amount, cat_id, subcat_id, date_str, currency)
    except ValueError:
        return jsonify({"ok": False, "error": "Moneda inválida"}), 400
    if not updated:
        return jsonify({"ok": False, "error": "Gasto no encontrado"}), 404

    resp = {"ok": True}
    if fixed_expense_id:
        # Linking forces category/subcategory to the fixed expense's own — the same choke
        # point (db.link_expense_to_fixed) every linking flow goes through, so a recurring
        # bill can't drift category depending on which surface registered it.
        expense = db.get_expense_by_id(int(expense_id))
        year, month = fixed_matcher.expense_period(expense["created_at"], BAIRES)
        db.link_expense_to_fixed(int(expense_id), int(fixed_expense_id), year, month)
        resp["category_id"]    = target_fixed["category_id"]
        resp["subcategory_id"] = target_fixed["subcategory_id"]
    else:
        db.unlink_expense_from_fixed(int(expense_id))

    return jsonify(resp)


@app.route("/api/expenses/<int:expense_id>/link-fixed", methods=["POST"])
def api_expenses_link_fixed(expense_id: int):
    """Links (or, with a null fixed_expense_id, unlinks) an existing expense to a fixed
    expense — used by the "suggested_fixed_expense" offer after adding an expense, and
    reusable anywhere else the dashboard wants to attach a link without resending the
    whole expense (concept/amount/etc)."""
    data             = request.get_json(silent=True) or {}
    fixed_expense_id = data.get("fixed_expense_id")

    expense = db.get_expense_by_id(expense_id)
    if not expense:
        return jsonify({"ok": False, "error": "Gasto no encontrado"}), 404

    if not fixed_expense_id:
        db.unlink_expense_from_fixed(expense_id)
        return jsonify({"ok": True})

    fe = db.get_fixed_expense_by_id(int(fixed_expense_id))
    if not fe:
        return jsonify({"ok": False, "error": "Gasto fijo no encontrado"}), 404
    if expense["currency"] != fe["currency"]:
        return jsonify({"ok": False, "error": "La moneda debe coincidir con el gasto fijo"}), 409

    year, month = fixed_matcher.expense_period(expense["created_at"], BAIRES)
    db.link_expense_to_fixed(expense_id, int(fixed_expense_id), year, month)
    return jsonify({"ok": True, "category_id": fe["category_id"], "subcategory_id": fe["subcategory_id"]})


@app.route("/api/subcategories")
def api_subcategories():
    category_id = request.args.get("category_id")
    if category_id:
        rows = db.get_subcategories(int(category_id))
        return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])
    rows = db.get_all_subcategories()
    return jsonify([{"id": r["id"], "category_id": r["category_id"], "name": r["name"],
                      "category_name": r["category_name"]} for r in rows])


@app.route("/api/expenses/<int:expense_id>/subcategory", methods=["POST"])
def api_expenses_set_subcategory(expense_id: int):
    data           = request.get_json(silent=True) or {}
    subcategory_id = data.get("subcategory_id")  # may be None / null
    subcat_id      = int(subcategory_id) if subcategory_id is not None else None
    db.update_expense_subcategory(expense_id, subcat_id)
    return jsonify({"ok": True})


@app.route("/api/subcategories/add", methods=["POST"])
def api_subcategories_add():
    data        = request.get_json(silent=True) or {}
    category_id = data.get("category_id")
    name        = (data.get("name") or "").strip()
    if not category_id or not name:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    try:
        category_id = int(category_id)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Categoría inválida"}), 400
    if not db.get_category_by_id(category_id):
        return jsonify({"ok": False, "error": "Categoría no encontrada"}), 404
    if db.find_subcategory_normalized(category_id, name):
        return jsonify({"ok": False, "error": f"Ya existe una subcategoría llamada '{name}'"}), 409
    new_id = db.add_subcategory(category_id, name)
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/subcategories/delete", methods=["POST"])
def api_subcategories_delete():
    data      = request.get_json(silent=True) or {}
    subcat_id = data.get("id")
    if not subcat_id:
        return jsonify({"ok": False, "error": "Falta el campo 'id'"}), 400
    count = db.get_expense_count_by_subcategory(int(subcat_id))
    if count > 0:
        return jsonify({"ok": False, "error": f"Hay {count} gasto{'s' if count != 1 else ''} con esta subcategoría"}), 400
    deleted = db.delete_subcategory(int(subcat_id))
    if deleted:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Subcategoría no encontrada"}), 404


@app.route("/api/keywords/<int:keyword_id>", methods=["PUT"])
def api_keywords_update(keyword_id: int):
    data           = request.get_json(silent=True) or {}
    keyword        = (data.get("keyword") or "").strip()
    category_id    = data.get("category_id")
    subcategory_id = data.get("subcategory_id")
    if not keyword or not category_id:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    subcat_id = int(subcategory_id) if subcategory_id else None
    updated = db.update_keyword(keyword_id, keyword, int(category_id), subcat_id)
    if updated:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Keyword no encontrada"}), 404


@app.route("/api/categories/add", methods=["POST"])
def api_categories_add():
    data  = request.get_json(silent=True) or {}
    name  = (data.get("name")  or "").strip()
    icon  = (data.get("icon")  or "💰").strip()
    color = (data.get("color") or "#6366f1").strip()
    if not name:
        return jsonify({"ok": False, "error": "El nombre es obligatorio"}), 400
    if db.find_category_normalized(name):
        return jsonify({"ok": False, "error": f"Ya existe una categoría llamada '{name}'"}), 409
    cat_id = db.create_category(name, icon, color)
    if cat_id is None:
        return jsonify({"ok": False, "error": f"Ya existe una categoría llamada '{name}'"}), 409
    return jsonify({"ok": True, "id": cat_id})


@app.route("/api/categories/update", methods=["POST"])
def api_categories_update():
    data        = request.get_json(silent=True) or {}
    category_id = data.get("id")
    name        = (data.get("name")  or "").strip()
    icon        = (data.get("icon")  or "💰").strip()
    color       = (data.get("color") or "#6366f1").strip()
    if not category_id or not name:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    ok, err = db.update_category(int(category_id), name, icon, color)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err}), 409


@app.route("/api/categories/delete", methods=["POST"])
def api_categories_delete():
    data        = request.get_json(silent=True) or {}
    category_id = data.get("id")
    if not category_id:
        return jsonify({"ok": False, "error": "Falta el campo 'id'"}), 400
    ok, err = db.delete_category(int(category_id))
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err}), 409


@app.route("/fijos")
def fijos_page():
    return render_template("fijos.html")


@app.route("/config")
def config_page():
    return render_template("config.html")


@app.route("/api/fixed-expenses")
def api_fixed_expenses():
    return jsonify([dict(fe) for fe in db.get_all_fixed_expenses()])


@app.route("/api/fixed-expenses/status")
def api_fixed_expenses_status():
    try:
        year  = int(request.args.get("year",  datetime.now().year))
        month = int(request.args.get("month", datetime.now().month))
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400
    return jsonify(db.get_fixed_payments_for_period(year, month))


@app.route("/api/fixed-expenses/<int:fe_id>/candidates")
def api_fixed_expenses_candidates(fe_id: int):
    """Candidate already-logged, unlinked expenses for the "ya lo pagué" search — the web
    counterpart of the bot's fix:already:/fixpick: flow, sharing the same scoring
    (fixed_matcher.find_candidate_expenses) so both surfaces agree on what counts as a match."""
    try:
        year  = int(request.args.get("year",  datetime.now().year))
        month = int(request.args.get("month", datetime.now().month))
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400
    fe = db.get_fixed_expense_by_id(fe_id)
    if not fe:
        return jsonify({"error": "Gasto fijo no encontrado"}), 404
    unlinked = db.get_unlinked_expenses_for_period(year, month)
    candidates = fixed_matcher.find_candidate_expenses(fe, unlinked)
    return jsonify([dict(c) for c in candidates])


@app.route("/api/fixed-expenses/add", methods=["POST"])
def api_fixed_expenses_add():
    data             = request.get_json(silent=True) or {}
    concept          = (data.get("concept") or "").strip()
    estimated_amount = data.get("estimated_amount")
    category_id      = data.get("category_id")
    currency         = data.get("currency") or "ARS"
    if not concept:
        return jsonify({"ok": False, "error": "El concepto es obligatorio"}), 400
    try:
        estimated_amount = float(estimated_amount) if estimated_amount not in (None, "") else None
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400
    cat_id    = int(category_id) if category_id else None
    subcat_id = int(data["subcategory_id"]) if data.get("subcategory_id") else None
    try:
        fe_id = db.create_fixed_expense(concept, estimated_amount, cat_id, subcat_id, currency)
    except ValueError:
        return jsonify({"ok": False, "error": "Moneda inválida"}), 400
    return jsonify({"ok": True, "id": fe_id})


@app.route("/api/fixed-expenses/update", methods=["POST"])
def api_fixed_expenses_update():
    data             = request.get_json(silent=True) or {}
    fe_id            = data.get("id")
    concept          = (data.get("concept") or "").strip()
    estimated_amount = data.get("estimated_amount")
    category_id      = data.get("category_id")
    currency         = data.get("currency")
    if not fe_id or not concept:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    try:
        estimated_amount = float(estimated_amount) if estimated_amount not in (None, "") else None
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400
    cat_id    = int(category_id) if category_id else None
    subcat_id = int(data["subcategory_id"]) if data.get("subcategory_id") else None
    try:
        updated = db.update_fixed_expense(int(fe_id), concept, estimated_amount, cat_id, subcat_id, currency)
    except ValueError:
        return jsonify({"ok": False, "error": "Moneda inválida"}), 400
    if not updated:
        return jsonify({"ok": False, "error": "No se puede cambiar la moneda de un fijo con pagos vinculados"}), 409
    return jsonify({"ok": True})


@app.route("/api/fixed-expenses/deactivate", methods=["POST"])
def api_fixed_expenses_deactivate():
    data  = request.get_json(silent=True) or {}
    fe_id = data.get("id")
    if not fe_id:
        return jsonify({"ok": False, "error": "Falta el campo 'id'"}), 400
    db.deactivate_fixed_expense(int(fe_id))
    return jsonify({"ok": True})


@app.route("/api/fixed-expenses/pay", methods=["POST"])
def api_fixed_expenses_pay():
    data             = request.get_json(silent=True) or {}
    fixed_expense_id = data.get("fixed_expense_id")
    amount           = data.get("amount")
    year             = data.get("year")
    month            = data.get("month")
    user_id          = data.get("user_id")
    date_str         = (data.get("date") or "").strip() or None

    if not fixed_expense_id or amount is None or not year or not month:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400

    fe = db.get_fixed_expense_by_id(int(fixed_expense_id))
    if not fe:
        return jsonify({"ok": False, "error": "Gasto fijo no encontrado"}), 404

    if not user_id:
        users = db.get_all_users()
        if not users:
            return jsonify({"ok": False, "error": "No hay usuarios configurados"}), 400
        user_id = users[0]["id"]

    y, m = int(year), int(month)

    if date_str is not None:
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "Fecha inválida"}), 400
        if (parsed.year, parsed.month) != (y, m):
            return jsonify({"ok": False, "error": "La fecha debe caer dentro del período seleccionado"}), 400
    else:
        # No date given (e.g. an older client) — default to today if the period being
        # viewed is the current month, otherwise the 1st of that month. We don't have
        # enough signal to guess a day within a past period, so we don't invent one.
        now = datetime.now(BAIRES)
        date_str = now.strftime("%Y-%m-%d") if (now.year, now.month) == (y, m) else f"{y:04d}-{m:02d}-01"

    expense_id = db.create_expense_full(
        int(user_id), None, fe["concept"], amount, date_str, currency=fe["currency"]
    )
    db.link_expense_to_fixed(expense_id, int(fixed_expense_id), y, m)
    return jsonify({"ok": True, "expense_id": expense_id})


@app.route("/admin/restore-db-url", methods=["POST"])
def admin_restore_db_url():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith("https://"):
        return jsonify({"success": False, "error": "La URL debe comenzar con https://"}), 400

    db_path = db.DB_PATH
    bak_path = db_path + ".bak"

    try:
        shutil.copy2(db_path, bak_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"Error al crear backup: {e}"}), 500

    try:
        resp = http_requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(db_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
    except Exception as e:
        try:
            shutil.copy2(bak_path, db_path)
        except Exception:
            pass
        return jsonify({"success": False, "error": f"Error al descargar la DB: {e}"}), 500

    # Validate SQLite magic header before restarting
    try:
        with open(db_path, "rb") as f:
            header = f.read(16)
    except Exception as e:
        shutil.copy2(bak_path, db_path)
        return jsonify({"success": False, "error": f"Error al leer el archivo descargado: {e}"}), 500

    if header != b"SQLite format 3\x00":
        shutil.copy2(bak_path, db_path)
        return jsonify({"success": False, "error": "El archivo descargado no es una base de datos SQLite válida"}), 400

    def _restart():
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"success": True})


@app.route("/admin/backup-now", methods=["POST"])
def admin_backup_now():
    ts = backup_module.create_local_backup()
    if ts is None:
        return jsonify({"success": False, "error": "Backup fallido — revisá los logs"}), 500
    return jsonify({"success": True, "timestamp": ts})


@app.route("/api/backup-status")
def api_backup_status():
    path = backup_module.LAST_BACKUP_PATH
    if not os.path.exists(path):
        return jsonify({"last_backup": None})
    try:
        with open(path) as f:
            ts = f.read().strip()
        return jsonify({"last_backup": ts})
    except Exception:
        return jsonify({"last_backup": None})


@app.route("/dolares")
def dolares_page():
    return render_template("dolares.html")


@app.route("/api/cambios/resumen")
def api_cambios_resumen():
    now = datetime.now(BAIRES)
    return jsonify(db.get_cambios_resumen_mes(now.year, now.month))


@app.route("/api/cambios/historial")
def api_cambios_historial():
    rows = db.get_cambios_historial(50)
    return jsonify([dict(r) for r in rows])


@app.route("/api/cambios/por_mes")
def api_cambios_por_mes():
    rows = db.get_cambios_por_mes(12)
    return jsonify([dict(r) for r in rows])


@app.route("/api/cambios/cotizacion_historica")
def api_cambios_cotizacion_historica():
    rows = db.get_cambios_cotizacion_historica()
    return jsonify([dict(r) for r in rows])


@app.route("/api/cambios/<int:cambio_id>", methods=["DELETE"])
def api_cambios_delete(cambio_id: int):
    deleted = db.delete_cambio(cambio_id)
    if deleted:
        return jsonify({"ok": True})
    return jsonify({"error": "Cambio no encontrado"}), 404


@app.route("/api/cambios/<int:cambio_id>", methods=["PUT"])
def api_cambios_update(cambio_id: int):
    data       = request.get_json(silent=True) or {}
    fecha      = (data.get("fecha") or "").strip()
    monto_usd  = data.get("monto_usd")
    cotizacion = data.get("cotizacion")
    tipo       = (data.get("tipo") or "").strip().lower() or None

    if not fecha or monto_usd is None or cotizacion is None:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400

    if tipo is not None and tipo not in ("venta", "compra"):
        return jsonify({"ok": False, "error": "Tipo inválido (venta/compra)"}), 400

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "Fecha inválida (YYYY-MM-DD)"}), 400

    try:
        monto_usd  = float(monto_usd)
        cotizacion = float(cotizacion)
        if monto_usd <= 0 or cotizacion <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Montos inválidos"}), 400

    updated = db.update_cambio(cambio_id, fecha, monto_usd, cotizacion, tipo=tipo)
    if updated:
        return jsonify({"ok": True, "monto_ars": monto_usd * cotizacion})
    return jsonify({"ok": False, "error": "Cambio no encontrado"}), 404


@app.route("/resumenes")
def resumenes_page():
    latest = report.get_latest_report_overall()
    if latest:
        year, month = latest["year"], latest["month"]
    else:
        now = datetime.now(BAIRES)
        year, month = now.year, now.month
    return render_template("resumenes.html", year=year, month=month)


@app.route("/resumenes/<period>")
def resumenes_page_period(period: str):
    try:
        year, month = _parse_period(period)
    except ValueError:
        return redirect("/resumenes")
    return render_template("resumenes.html", year=year, month=month)


def _parse_period(period: str) -> tuple[int, int]:
    year_str, month_str = period.split("-", 1)
    year, month = int(year_str), int(month_str)
    if not (1 <= month <= 12):
        raise ValueError("Mes inválido")
    return year, month


def _serialize_report(r: dict | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["generated_at"] = _to_baires_str(d["generated_at"])
    return d


@app.route("/api/resumenes/available-months")
def api_resumenes_available_months():
    return jsonify(db.get_months_with_data())


@app.route("/api/resumenes/<int:year>/<int:month>")
def api_resumen_get(year: int, month: int):
    return jsonify({"report": _serialize_report(report.get_report(year, month))})


@app.route("/api/resumenes/<int:year>/<int:month>/generate", methods=["POST"])
def api_resumen_generate(year: int, month: int):
    return jsonify({"report": _serialize_report(report.generate_report(year, month))})


def run_dashboard():
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    # threaded=True so a ~40-60s report generation request doesn't block other
    # dashboard tabs/users for the duration.
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
