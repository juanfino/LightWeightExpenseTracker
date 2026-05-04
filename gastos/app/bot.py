import logging
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

import db
import parser as msg_parser
import categorizer
import ocr as ocr_module

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# Gastos pendientes de confirmación tras OCR, keyed por chat_id
pending_ocr: dict[int, dict] = {}


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

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    api_key = context.bot_data.get("anthropic_api_key", "")
    if not api_key:
        await update.message.reply_text(
            "⚠️ OCR no configurado. Agregá la Anthropic API key en las opciones del add-on."
        )
        return

    status_msg = await update.message.reply_text("📸 Analizando ticket...")

    if update.message.photo:
        tg_file = await update.message.photo[-1].get_file()
    else:
        tg_file = await update.message.document.get_file()

    image_bytes = bytes(await tg_file.download_as_bytearray())
    data = ocr_module.extract_ticket_data(image_bytes, api_key)

    chat_id = update.message.chat_id

    if data is None:
        await status_msg.edit_text(
            "❌ No pude analizar el ticket. Intentá cargar el gasto manualmente.\n"
            "Formato: <code>Comercio monto</code>",
            parse_mode="HTML",
        )
        return

    if data["monto"] is None:
        comercio_info = f" Detecté el comercio: <b>{data['comercio']}</b>." if data["comercio"] else ""
        await status_msg.edit_text(
            f"⚠️ No pude detectar el monto total del ticket.{comercio_info}\n"
            "Cargá el gasto manualmente: <code>Comercio monto</code>",
            parse_mode="HTML",
        )
        return

    pending_ocr[chat_id] = data

    fecha_str = data["fecha"].strftime("%d/%m/%Y")
    fecha_note = " <i>(fecha no detectada, se usó hoy)</i>" if data["fecha_inferida"] else ""
    comercio_str = data["comercio"] or "Desconocido"

    await status_msg.edit_text(
        f"🧾 <b>Ticket detectado</b>\n"
        f"🏪 Comercio: {comercio_str}\n"
        f"💰 Monto: {fmt_amount(data['monto'])}\n"
        f"📅 Fecha: {fecha_str}{fecha_note}\n\n"
        "¿Guardamos? Respondé <b>sí</b> o <b>no</b>",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()

    if chat_id in pending_ocr:
        response = text.lower()
        if response in ("sí", "si", "s", "dale", "ok"):
            data = pending_ocr.pop(chat_id)
            concept = data["comercio"] or "Ticket"
            keywords = db.get_all_keywords()
            category_id = categorizer.categorize(concept, keywords)
            categories = {r["id"]: r for r in db.get_all_categories()}
            cat = categories.get(category_id)
            cat_name = cat["name"] if cat else "Sin categoría"
            cat_icon = cat["icon"] if cat else "❓"
            try:
                expense_id = db.create_expense(
                    user_id=user["id"],
                    category_id=category_id,
                    concept=concept,
                    amount=data["monto"],
                    raw_text=f"[OCR] {concept} {data['monto']}",
                )
            except Exception as e:
                logger.error("Error guardando gasto OCR: %s", e)
                await update.message.reply_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
                return
            await update.message.reply_text(
                f"✅ <b>Gasto registrado</b>\n"
                f"📋 {concept}\n"
                f"💰 {fmt_amount(data['monto'])}\n"
                f"{cat_icon} {cat_name}\n"
                f"👤 {user['name']}\n"
                f"<code>#ID{expense_id}</code>",
                parse_mode="HTML",
            )
        elif response in ("no", "n", "cancelar"):
            del pending_ocr[chat_id]
            await update.message.reply_text("❌ Carga cancelada.")
        else:
            await update.message.reply_text(
                "Por favor respondé <b>sí</b> o <b>no</b>.", parse_mode="HTML"
            )
        return

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

    status = db.add_keyword(keyword, cat["id"])
    if status == "new":
        await update.message.reply_text(
            f"✅ Keyword <b>{keyword}</b> agregada a <b>{cat['name']}</b>.",
            parse_mode="HTML",
        )
    elif status == "remapped":
        await update.message.reply_text(
            f"✅ Keyword <b>{keyword}</b> remapeada a <b>{cat['name']}</b>.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"⚠️ La keyword <b>{keyword}</b> ya está asignada a <b>{cat['name']}</b>.",
            parse_mode="HTML",
        )


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
        "⚙️ <b>CATEGORÍAS</b>\n"
        "   <code>/nueva_categoria Mascotas 🐶 #f59e0b</code>\n"
        "   (gestión completa en el dashboard web)\n"
        "\n"
        "❓ <b>AYUDA</b>\n"
        "   /ayuda → este mensaje",
        parse_mode="HTML",
    )


async def cmd_nueva_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    if not context.args:
        await update.message.reply_text(
            "Uso: <code>/nueva_categoria Nombre Emoji Color</code>\n"
            "Ejemplo: <code>/nueva_categoria Mascotas 🐶 #f59e0b</code>\n"
            "Emoji y color son opcionales.",
            parse_mode="HTML",
        )
        return

    icon = "💰"
    color = "#6366f1"
    name_parts = []

    for arg in context.args:
        if arg.startswith("#") and len(arg) == 7:
            color = arg
        elif any(ord(c) > 127 for c in arg):
            icon = arg
        else:
            name_parts.append(arg)

    name = " ".join(name_parts).strip()
    if not name:
        await update.message.reply_text(
            "❌ El nombre de la categoría es obligatorio.",
            parse_mode="HTML",
        )
        return

    cat_id = db.create_category(name, icon, color)
    if cat_id is None:
        await update.message.reply_text(
            f"❌ Ya existe una categoría llamada <b>{name}</b>.",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        f"✅ Categoría creada: {icon} <b>{name}</b>",
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

        # Aprender el concepto como keyword de la categoría elegida
        normalized_concept = categorizer.normalize(expense["concept"])
        kw_status = db.add_keyword(normalized_concept, cat["id"])
        if kw_status == "new":
            kw_line = f'🏷️ <code>{normalized_concept}</code> agregado como keyword de <b>{cat["name"]}</b>'
        elif kw_status == "remapped":
            kw_line = f'🏷️ <code>{normalized_concept}</code> remapeado a <b>{cat["name"]}</b>'
        else:
            kw_line = ""

        reply = (
            f"✅ Gasto <code>#{expense_id}</code> actualizado — nueva categoría: {cat['icon']} <b>{cat['name']}</b>"
            + (f"\n{kw_line}" if kw_line else "")
        )
        await update.message.reply_text(reply, parse_mode="HTML")

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
    app.add_handler(CommandHandler("add_keyword",       cmd_add_keyword))
    app.add_handler(CommandHandler("categorias",        cmd_categorias))
    app.add_handler(CommandHandler("nueva_categoria",   cmd_nueva_categoria))
    app.add_handler(CommandHandler("ayuda",             cmd_ayuda))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
