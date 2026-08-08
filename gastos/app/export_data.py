"""Portable, tenant-scoped CSV exports.

All reads go through db.py under the request's RLS-bound family context.
"""
import csv
import io
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

import db

UTF8_BOM = b"\xef\xbb\xbf"


def _safe(value, timezone_name: str):
    if value is None:
        return ""
    if isinstance(value, datetime):
        zone = ZoneInfo(timezone_name)
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(zone).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return format(value, ".15g")
    text = str(value)
    # Prevent spreadsheet formula execution while preserving the visible value.
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def csv_bytes(headers: list[str], rows: list[list], timezone_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=",", quotechar='"', lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe(value, timezone_name) for value in row])
    return UTF8_BOM + output.getvalue().encode("utf-8")


def datasets(timezone_name: str) -> dict[str, bytes]:
    expenses = db.get_recent_expenses(limit=1_000_000)
    fixed = db.get_all_fixed_expenses()
    changes = db.get_cambios_historial(limit=1_000_000)
    incomes = db.get_incomes()
    shopping = db.get_shopping_items()
    categories = db.get_all_categories()
    subcategories = db.get_all_subcategories()
    keywords = db.get_all_keywords()

    result = {
        "movimientos": csv_bytes(
            ["id", "fecha_hora", "concepto", "monto", "moneda", "categoria",
             "subcategoria", "gasto_fijo_id", "persona"],
            [[r["id"], r["created_at"], r["concept"], r["amount"], r["currency"],
              r["category_name"], r["subcategory_name"], r["fixed_expense_id"], r["user_name"]]
             for r in expenses],
            timezone_name,
        ),
        "gastos_fijos": csv_bytes(
            ["id", "concepto", "monto_estimado", "moneda", "categoria_id",
             "subcategoria_id", "activo", "creado"],
            [[r["id"], r["concept"], r["estimated_amount"], r["currency"], r["category_id"],
              r["subcategory_id"], r["active"], r["created_at"]] for r in fixed],
            timezone_name,
        ),
        "dolares": csv_bytes(
            ["id", "fecha", "monto_entregado", "moneda_entregada", "monto_recibido",
             "moneda_recibida", "tasa_recibida_por_entregada", "persona"],
            [[r["id"], r["fecha"], r["amount_given"], r["currency_given"],
              r["amount_received"], r["currency_received"], r["rate_received_per_given"],
              r["usuario"]] for r in changes],
            timezone_name,
        ),
        "ingresos": csv_bytes(
            ["id", "fecha", "concepto", "monto", "moneda", "categoria", "persona", "creado"],
            [[r["id"], r["date"], r["concept"], r["amount"], r["currency"],
              r["category_name"], r["user_name"], r["created_at"]] for r in incomes],
            timezone_name,
        ),
        "lista_compras": csv_bytes(
            ["id", "producto", "cantidad", "categoria", "estado", "creado_por", "creado",
             "comprado_por", "comprado"],
            [[r["id"], r["name"], r["quantity"], r["category_name"], r["status"],
              r["created_by_name"], r["created_at"], r["bought_by_name"], r["bought_at"]]
             for r in shopping],
            timezone_name,
        ),
    }
    taxonomy_rows = (
        [["categoria", r["id"], "", r["name"], r["icon"], r["color"], ""] for r in categories]
        + [["subcategoria", r["id"], r["category_id"], r["name"], "", "", ""] for r in subcategories]
        + [["keyword", r["id"], r["category_id"], r["keyword"], "", "", r["subcategory_id"]]
           for r in keywords]
    )
    result["taxonomia"] = csv_bytes(
        ["tipo", "id", "categoria_id", "nombre_o_palabra", "icono", "color", "subcategoria_id"],
        taxonomy_rows, timezone_name,
    )
    return result


def zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"{name}.csv", content)
    return output.getvalue()
