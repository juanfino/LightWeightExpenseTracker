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
        "📖 <b>Gastos Familiares — Comandos disponibles</b>\n"
        "\n"
        "💰 <b>REGISTRAR UN GASTO</b>\n"
        "   <code>Supermercado 15000</code>\n"
        "   <code>YPF 100.000</code>\n"
        "\n"
        "📊 <b>CONSULTAS</b>\n"
        "   /gastos     → resumen del mes\n"
        "   /semana     → gastos de esta semana\n"
        "   /hoy        → gastos de hoy\n"
        "   /sincat     → gastos sin categoría\n"
        "\n"
        "✏️ <b>EDITAR</b>\n"
        "   <code>/editar ID monto 15000</code>\n"
        "   <code>/editar ID categoria Vehiculos</code>\n"
        "   <code>/recat papota Entretenimiento</code>\n"
        "\n"
        "🗑️ <b>BORRAR</b>\n"
        "   <code>/borrar ID</code>\n"
        "\n"
        "🏷️ <b>KEYWORDS</b>\n"
        "   <code>/add_keyword nafta Vehiculos</code>\n"
        "   /categorias → lista de categorías\n"
        "\n"
        "❓ <b>AYUDA</b>\n"
        "   /ayuda → este mensaje",
        parse_mode="HTML",
    )


# ── Helper: buscar categoría normalizando acentos ─────────────────────────────

def _find_category(name: str):
    """Busca una categoría por nombre ignorando mayúsculas y acentos."""
    normalized_input = categorizer.normalize(name)
    for cat in db.get_all_categories():
        if categorizer.normalize(cat["name"]) == normalized_input:
            return cat
    return None


# ── Comandos nuevos ───────────────────────────────────────────────────────────

async def cmd_sincat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    expenses = db.get_expenses_uncategorized()

    if not expenses:
        await update.message.reply_text("✅ No tenés gastos sin categoría.")
        return

    lines = [f"❓ <b>Gastos sin categoría</b> ({len(expenses)})\n"]
    for r in expenses:
        date_part = fmt_date(r["created_at"])[:5]  # DD/MM
        lines.append(
            f"<code>#{r['id']}</code>  {r['concept']}  "
            f"<b>{fmt_amount(r['amount'])}</b>  <i>({date_part})</i>"
        )
    lines.append(
        "\nUsá <code>/editar ID categoria NOMBRE</code> para corregirlos\n"
        "O <code>/recat CONCEPTO CATEGORÍA</code> para reasignar en masa"
    )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_editar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    # Necesitamos al menos: ID campo valor
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uso:\n"
            "  <code>/editar ID monto 15000</code>\n"
            "  <code>/editar ID categoria Vehiculos</code>",
            parse_mode="HTML",
        )
        return

    try:
        expense_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Debe ser un número.", parse_mode="HTML")
        return

    campo = context.args[1].lower()
    valor = " ".join(context.args[2:])

    # Verificar que el gasto existe
    expense = db.get_expense_by_id(expense_id)
    if expense is None:
        await update.message.reply_text(f"❌ Gasto <code>#{expense_id}</code> no encontrado.", parse_mode="HTML")
        return

    # Verificar propiedad
    if expense["user_id"] != user["id"]:
        await update.message.reply_text("❌ Solo podés editar tus propios gastos.", parse_mode="HTML")
        return

    if campo == "monto":
        amount = msg_parser._normalize_amount(valor)
        if amount is None:
            await update.message.reply_text("❌ Monto inválido. Ejemplos: <code>15000</code>, <code>2500,50</code>", parse_mode="HTML")
            return
        db.update_expense_amount(expense_id, user["id"], amount)
        await update.message.reply_text(
            f"✅ Gasto <code>#{expense_id}</code> actualizado — nuevo monto: <b>{fmt_amount(amount)}</b>",
            parse_mode="HTML",
        )

    elif campo == "categoria":
        cat = _find_category(valor)
        if cat is None:
            await update.message.reply_text(
                f"❌ Categoría <b>{valor}</b> no encontrada. "
                "Usá /categorias para ver las disponibles.",
                parse_mode="HTML",
            )
            return
        db.update_expense_category(expense_id, user["id"], cat["id"])
        await update.message.reply_text(
            f"✅ Gasto <code>#{expense_id}</code> actualizado — nueva categoría: {cat['icon']} <b>{cat['name']}</b>",
            parse_mode="HTML",
        )

    else:
        await update.message.reply_text(
            "❌ Campo inválido. Campos válidos: <code>monto</code>, <code>categoria</code>",
            parse_mode="HTML",
        )


async def cmd_recat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Uso: <code>/recat CONCEPTO CATEGORÍA</code>\n"
            "Ejemplo: <code>/recat papota Entretenimiento</code>",
            parse_mode="HTML",
        )
        return

    concept   = context.args[0]
    cat_name  = " ".join(context.args[1:])

    cat = _find_category(cat_name)
    if cat is None:
        await update.message.reply_text(
            f"❌ Categoría <b>{cat_name}</b> no encontrada. "
            "Usá /categorias para ver las disponibles.",
            parse_mode="HTML",
        )
        return

    count = db.recategorize_by_concept(concept, cat["id"])

    if count == 0:
        await update.message.reply_text(
            f"⚠️ No se encontraron gastos con concepto <b>{concept}</b>.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"✅ <b>{count}</b> gasto{'s' if count != 1 else ''} con concepto "
            f"<b>{concept}</b> reasignado{'s' if count != 1 else ''} a "
            f"{cat['icon']} <b>{cat['name']}</b>.",
            parse_mode="HTML",
        )


# ── Arranque ──────────────────────────────────────────────────────────────────

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("gastos",      cmd_gastos))
    app.add_handler(CommandHandler("semana",      cmd_semana))
    app.add_handler(CommandHandler("hoy",         cmd_hoy))
    app.add_handler(CommandHandler("sincat",      cmd_sincat))
    app.add_handler(CommandHandler("editar",      cmd_editar))
    app.add_handler(CommandHandler("recat",       cmd_recat))
    app.add_handler(CommandHandler("borrar",      cmd_borrar))
    app.add_handler(CommandHandler("add_keyword", cmd_add_keyword))
    app.add_handler(CommandHandler("categorias",  cmd_categorias))
    app.add_handler(CommandHandler("ayuda",       cmd_ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
