import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify

import requests as http_requests

import db
import backup as backup_module

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

    by_category      = db.get_expenses_summary_by_category(year, month)
    by_week          = db.get_expenses_by_week_of_month(year, month)
    by_week_by_user  = db.get_expenses_by_week_of_month_by_user(year, month)
    by_user_rows     = db.get_expenses_by_user(year, month)
    total = sum(r["total"] for r in by_category)

    return jsonify({
        "month":           _month_label(year, month),
        "total":           total,
        "by_category":     by_category,
        "by_week":         by_week,
        "by_week_by_user": by_week_by_user,
        "by_user":         [{"name": r["name"], "total": r["total"]} for r in by_user_rows],
    })


@app.route("/api/users")
def api_users():
    return jsonify([{"id": u["id"], "name": u["name"], "color": u["color"]} for u in db.get_all_users()])


@app.route("/api/annual/<int:year>")
def api_annual(year: int):
    if year < 2020 or year > 2099:
        return jsonify({"error": "Año inválido"}), 400
    return jsonify(db.get_annual_data(year))


@app.route("/api/sparklines")
def api_sparklines():
    return jsonify(db.get_monthly_totals(6))


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
        if category_id == "null":
            result = [r for r in result if r.get("category_id") is None]
        else:
            result = [r for r in result if str(r.get("category_id")) == str(category_id)]
    if user_id:
        result = [r for r in result if str(r.get("user_id")) == str(user_id)]

    return jsonify(result)


@app.route("/api/categories")
def api_categories():
    return jsonify([dict(c) for c in db.get_all_categories()])


@app.route("/api/expenses/add", methods=["POST"])
def api_expenses_add():
    data        = request.get_json(silent=True) or {}
    concept     = (data.get("concept") or "").strip()
    amount      = data.get("amount")
    category_id = data.get("category_id")
    user_id     = data.get("user_id")
    date_str    = (data.get("date") or "").strip()

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

    cat_id = int(category_id) if category_id else None
    expense_id = db.create_expense_full(int(user_id), cat_id, concept, amount, date_str)
    return jsonify({"ok": True, "id": expense_id})


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
    data        = request.get_json(silent=True) or {}
    expense_id  = data.get("id")
    concept     = (data.get("concept") or "").strip()
    amount      = data.get("amount")
    category_id = data.get("category_id")   # may be None / null

    if not expense_id or not concept or amount is None:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400

    cat_id = int(category_id) if category_id else None
    updated = db.update_expense(int(expense_id), concept, amount, cat_id)
    if updated:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Gasto no encontrado"}), 404


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
    return jsonify(db.get_fixed_payments_for_month(year, month))


@app.route("/api/fixed-expenses/add", methods=["POST"])
def api_fixed_expenses_add():
    data             = request.get_json(silent=True) or {}
    concept          = (data.get("concept") or "").strip()
    estimated_amount = data.get("estimated_amount")
    category_id      = data.get("category_id")
    if not concept:
        return jsonify({"ok": False, "error": "El concepto es obligatorio"}), 400
    try:
        estimated_amount = float(estimated_amount) if estimated_amount not in (None, "") else None
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400
    cat_id = int(category_id) if category_id else None
    fe_id  = db.create_fixed_expense(concept, estimated_amount, cat_id)
    return jsonify({"ok": True, "id": fe_id})


@app.route("/api/fixed-expenses/update", methods=["POST"])
def api_fixed_expenses_update():
    data             = request.get_json(silent=True) or {}
    fe_id            = data.get("id")
    concept          = (data.get("concept") or "").strip()
    estimated_amount = data.get("estimated_amount")
    category_id      = data.get("category_id")
    if not fe_id or not concept:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    try:
        estimated_amount = float(estimated_amount) if estimated_amount not in (None, "") else None
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Monto inválido"}), 400
    cat_id = int(category_id) if category_id else None
    db.update_fixed_expense(int(fe_id), concept, estimated_amount, cat_id)
    return jsonify({"ok": True})


@app.route("/api/fixed-expenses/deactivate", methods=["POST"])
def api_fixed_expenses_deactivate():
    data  = request.get_json(silent=True) or {}
    fe_id = data.get("id")
    if not fe_id:
        return jsonify({"ok": False, "error": "Falta el campo 'id'"}), 400
    db.deactivate_fixed_expense(int(fe_id))
    return jsonify({"ok": True})


@app.route("/api/fixed-expenses/mark-paid", methods=["POST"])
def api_fixed_expenses_mark_paid():
    data             = request.get_json(silent=True) or {}
    fixed_expense_id = data.get("fixed_expense_id")
    year             = data.get("year")
    month            = data.get("month")
    if not fixed_expense_id or not year or not month:
        return jsonify({"ok": False, "error": "Faltan campos requeridos"}), 400
    db.create_fixed_payment_without_expense(int(fixed_expense_id), int(year), int(month))
    return jsonify({"ok": True})


@app.route("/api/fixed-expenses/pay", methods=["POST"])
def api_fixed_expenses_pay():
    data             = request.get_json(silent=True) or {}
    fixed_expense_id = data.get("fixed_expense_id")
    amount           = data.get("amount")
    year             = data.get("year")
    month            = data.get("month")
    user_id          = data.get("user_id")

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

    y, m   = int(year), int(month)
    date_str = f"{y}-{m:02d}-15"
    expense_id = db.create_expense_full(int(user_id), fe["category_id"], fe["concept"], amount, date_str)
    db.create_fixed_payment(int(fixed_expense_id), expense_id, y, m)
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
    ts = backup_module.send_db_backup()
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


def run_dashboard():
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
