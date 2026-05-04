import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify

import db

app = Flask(__name__)

MONTHS_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def _month_label(year: int, month: int) -> str:
    return f"{MONTHS_ES[month]} {year}"


def _row_to_dict(row) -> dict:
    return dict(row)


# ── Páginas ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    now = datetime.now()
    return render_template("index.html", year=now.year, month=now.month,
                           month_label=_month_label(now.year, now.month))


@app.route("/history")
def history():
    categories = db.get_all_categories()
    return render_template("history.html", categories=[_row_to_dict(c) for c in categories])


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

    by_category = db.get_expenses_summary_by_category(year, month)
    by_week     = db.get_expenses_by_week_of_month(year, month)
    by_user_rows = db.get_expenses_by_user(year, month)

    total = sum(r["total"] for r in by_category)

    return jsonify({
        "month":       _month_label(year, month),
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

    by_category = db.get_expenses_summary_by_category(year, month)
    by_week     = db.get_expenses_by_week_of_month(year, month)
    by_user_rows = db.get_expenses_by_user(year, month)
    total = sum(r["total"] for r in by_category)

    return jsonify({
        "month":       _month_label(year, month),
        "total":       total,
        "by_category": by_category,
        "by_week":     by_week,
        "by_user":     [{"name": r["name"], "total": r["total"]} for r in by_user_rows],
    })


@app.route("/api/weekly")
def api_weekly():
    try:
        year = int(request.args.get("year", datetime.now().year))
        week = int(request.args.get("week", datetime.now().isocalendar().week))
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    rows = db.get_expenses_by_week(year, week)
    return jsonify([_row_to_dict(r) for r in rows])


@app.route("/api/expenses")
def api_expenses():
    try:
        year        = request.args.get("year")
        month       = request.args.get("month")
        category_id = request.args.get("category_id")
        user_id     = request.args.get("user_id")
    except Exception:
        return jsonify({"error": "Parámetros inválidos"}), 400

    if year and month:
        rows = db.get_expenses_by_month(int(year), int(month))
    else:
        rows = db.get_recent_expenses(limit=200)

    result = [_row_to_dict(r) for r in rows]

    if category_id:
        result = [r for r in result if str(r.get("category_id")) == str(category_id)]
    if user_id:
        result = [r for r in result if str(r.get("user_id")) == str(user_id)]

    return jsonify(result)


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
    added = db.add_keyword(keyword, int(category_id))
    if added:
        return jsonify({"ok": True})
    return jsonify({"error": "La keyword ya existe"}), 409


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


@app.route("/api/categories/add", methods=["POST"])
def api_categories_add():
    data  = request.get_json(silent=True) or {}
    name  = (data.get("name")  or "").strip()
    icon  = (data.get("icon")  or "💰").strip()
    color = (data.get("color") or "#6366f1").strip()
    if not name:
        return jsonify({"ok": False, "error": "El nombre es obligatorio"}), 400
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


def run_dashboard():
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
