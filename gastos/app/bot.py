import logging
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

import db
import parser as msg_parser
import categorizer

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")


# ── Formateo ──────────────────────────────────────────────────────────────────

def fmt_amount(amount: float) -> str:
    """2500.5 → '$2.500,50'  |  150000.0 → '$150.000'"""
    if amount == int(amount):
        # whole number: use dot as thousands separator
        return "$" + f"{int(amount):,}".replace(",", ".")
    # has decimals: format with 2 decimal places, swap separators
    formatted = f"{amount:,.2f}"          # "2,500.50"
    int_part, dec_part = formatted.split(".")
    int_part = int_part.replace(",", ".")  # "2.500"
    return f"${int_part},{dec_part}"       # "$2.500,50"


def fmt_date(dt_str: str) -> str:
    """'2026-05-03 14:32:00' → '03/05 14:32'"""
    try:
        date_part, time_part = dt_str.split(" ")
        y, m, d = date_part.split("-")
        h, mi, _ = time_part.split(":")
        return f"{d}/{m} {h}:{mi}"
    except Exception:
        return dt_str


# ── Guard de usuario ──────────────────────────────────────────────────────────

async def _get_authorized_user(update: Update):
    """Retorna el row del usuario o None. Responde con rechazo si no autorizado."""
    telegram_id = str(update.message.chat_id)
    user = db.get_user_by_telegram_id(telegram_id)
    if user is None:
        await update.message.reply_text("⛔ No estás autorizado para usar este bot.")
    return user


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    text = update.message.text.strip()
    parsed = msg_parser.parse_message(text)

    if parsed is None:
        await update.message.reply_text(
            "❓ No pude entender el gasto.\n"
            "Formatos válidos:\n"
            "  <code>Supermercado 15000</code>\n"
            "  <code>15000 nafta</code>\n"
            "  <code>Cena con amigos 8500,50</code>",
            parse_mode="HTML",
        )
        return

    keywords = db.get_all_keywords()
    category_id = categorizer.categorize(parsed["concept"], keywords)

    # Resolve category name for reply
    categories = {r["id"]: r for r in db.get_all_categories()}
    cat = categories.get(category_id)
    cat_name = cat["name"] if cat else "Sin categoría"
    cat_icon = cat["icon"] if cat else "❓"

    try:
        expense_id = db.create_expense(
            user_id=user["id"],
            category_id=category_id,
            concept=parsed["concept"],
            amount=parsed["amount"],
            raw_text=text,
        )
    except Exception as e:
        logger.error("Error guardando gasto: %s", e)
        await update.message.reply_text("⚠️ Hubo un error al guardar el gasto. Intentá de nuevo.")
        return

    await update.message.reply_text(
        f"✅ <b>Gasto registrado</b>\n"
        f"📋 {parsed['concept']}\n"
        f"💰 {fmt_amount(parsed['amount'])}\n"
        f"{cat_icon} {cat_name}\n"
        f"👤 {user['name']}\n"
        f"<code>#ID{expense_id}</code>",
        parse_mode="HTML",
    )


async def cmd_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    from datetime import datetime
    now = datetime.now()
    summary = db.get_expenses_summary_by_category(now.year, now.month)

    if not summary:
        await update.message.reply_text("📭 No hay gastos registrados este mes.")
        return

    total = sum(r["total"] for r in summary)
    month_name = now.strftime("%B %Y").capitalize()

    lines = [f"📊 <b>Gastos de {month_name}</b>\n", f"💰 Total: <b>{fmt_amount(total)}</b>\n"]
    for r in summary:
        lines.append(f"{r['icon']} {r['name']}: {fmt_amount(r['total'])} ({r['pct']}%)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    from datetime import datetime
    now = datetime.now()
    iso = now.isocalendar()
    expenses = db.get_expenses_by_week(iso.year, iso.week)

    if not expenses:
        await update.message.reply_text("📭 No hay gastos esta semana.")
        return

    total = sum(r["amount"] for r in expenses)
    lines = [f"📅 <b>Gastos de la semana</b> — {fmt_amount(total)}\n"]
    for r in expenses:
        lines.append(
            f"• {fmt_date(r['created_at'])}  {r['concept']}  "
            f"<b>{fmt_amount(r['amount'])}</b>  [{r['category_name']}]  {r['user_name']}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    expenses = db.get_expenses_today()

    if not expenses:
        await update.message.reply_text("📭 No hay gastos hoy.")
        return

    total = sum(r["amount"] for r in expenses)
    lines = [f"🗓 <b>Gastos de hoy</b> — {fmt_amount(total)}\n"]
    for r in expenses:
        lines.append(
            f"{r['category_icon']} {r['concept']}  "
            f"<b>{fmt_amount(r['amount'])}</b>  {r['user_name']}"
            f"  <code>#ID{r['id']}</code>"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    if not context.args:
        await update.message.reply_text(
            "Uso: <code>/borrar ID</code>\n"
            "El ID se muestra al registrar un gasto (<code>#ID42</code>).",
            parse_mode="HTML",
        )
        return

    try:
        expense_id = int(context.args[0].lstrip("#").upper().replace("ID", ""))
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Usá el número que aparece en <code>#ID42</code>.", parse_mode="HTML")
        return

    deleted = db.delete_expense(expense_id)
    if deleted:
        await update.message.reply_text(f"🗑 Gasto <code>#ID{expense_id}</code> eliminado.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ No encontré el gasto <code>#ID{expense_id}</code>.", parse_mode="HTML")


async def cmd_add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Uso: <code>/add_keyword PALABRA CATEGORÍA</code>\n"
            "Ejemplo: <code>/add_keyword churrasco Alimentación</code>",
            parse_mode="HTML",
        )
        return

    keyword = context.args[0].lower()
    category_name = " ".join(context.args[1:])

    cat = db.get_category_by_name(category_name)
    if cat is None:
        cats = db.get_all_categories()
        names = "\n".join(f"  • {c['name']}" for c in cats)
        await update.message.reply_text(
            f"❌ Categoría <b>{category_name}</b> no existe.\n\nCategorías disponibles:\n{names}",
            parse_mode="HTML",
        )
        return

    added = db.add_keyword(keyword, cat["id"])
    if added:
        await update.message.reply_text(
            f"✅ Keyword <b>{keyword}</b> agregada a <b>{cat['name']}</b>.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(f"⚠️ La keyword <b>{keyword}</b> ya existe.", parse_mode="HTML")


async def cmd_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    cats = db.get_all_categories()
    lines = ["🏷️ <b>Categorías disponibles</b>\n"]
    for c in cats:
        lines.append(f"{c['icon']} {c['name']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    await update.message.reply_text(
        "📖 <b>Cómo usar el bot</b>\n\n"
        "<b>Registrar un gasto:</b>\n"
        "  <code>Supermercado 15000</code>\n"
        "  <code>15000 nafta</code>\n"
        "  <code>Cena cumpleaños 8500,50</code>\n\n"
        "<b>Comandos:</b>\n"
        "  /gastos — resumen del mes actual\n"
        "  /semana — gastos de la semana\n"
        "  /hoy — gastos de hoy\n"
        "  /borrar ID — elimina un gasto por ID\n"
        "  /add_keyword PALABRA CATEGORÍA — agrega keyword\n"
        "  /categorias — lista de categorías\n"
        "  /ayuda — este mensaje",
        parse_mode="HTML",
    )


# ── Arranque ──────────────────────────────────────────────────────────────────

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("gastos",      cmd_gastos))
    app.add_handler(CommandHandler("semana",      cmd_semana))
    app.add_handler(CommandHandler("hoy",         cmd_hoy))
    app.add_handler(CommandHandler("borrar",      cmd_borrar))
    app.add_handler(CommandHandler("add_keyword", cmd_add_keyword))
    app.add_handler(CommandHandler("categorias",  cmd_categorias))
    app.add_handler(CommandHandler("ayuda",       cmd_ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
