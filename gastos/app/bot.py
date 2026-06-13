import logging
import os
import re
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import db
import parser as msg_parser
import categorizer
import ocr as ocr_module
import audio as audio_module

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
BAIRES = timezone(timedelta(hours=-3))

MONTHS_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# Gastos pendientes de confirmación tras OCR, keyed por chat_id
pending_ocr: dict[int, dict] = {}
# Gastos pendientes de confirmación tras voice, keyed por chat_id
pending_voice: dict[int, dict] = {}
# Gastos esperando nuevo monto del usuario, keyed por chat_id → expense_id
pending_amount_edit: dict[int, int] = {}
# Gasto parseado esperando confirmación de match con gasto fijo
pending_fixed_match: dict[int, dict] = {}
# Monto pendiente para registrar un gasto fijo directamente desde /fijos
pending_fixed_direct: dict[int, dict] = {}
# Gasto esperando selección de subcategoría, keyed por chat_id → expense_id
pending_subcategory: dict[int, int] = {}


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


def fmt_usd(amount: float) -> str:
    """1000.0 → 'U$S 1.000'  |  500.5 → 'U$S 500,50'"""
    if amount == int(amount):
        return "U$S " + f"{int(amount):,}".replace(",", ".")
    formatted = f"{amount:,.2f}"
    int_part, dec_part = formatted.split(".")
    int_part = int_part.replace(",", ".")
    return f"U$S {int_part},{dec_part}"


def _parse_cambio_token(token: str) -> float | None:
    """Parse an amount token using Argentine conventions: '.' = thousands sep, ',' = decimal sep."""
    cleaned = token.replace(".", "").replace(",", ".")
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def fmt_date(dt_str: str) -> str:
    """'2026-05-03 14:32:00' → '03/05 14:32'"""
    try:
        date_part, time_part = dt_str.split(" ")
        y, m, d = date_part.split("-")
        h, mi, _ = time_part.split(":")
        return f"{d}/{m} {h}:{mi}"
    except Exception:
        return dt_str


# ── Teclados inline ───────────────────────────────────────────────────────────

_PAGE_SIZE = 8

def _sorted_categories():
    """Categorías ordenadas por uso descendente, sin 'Sin categoría'."""
    usage = db.get_expense_count_by_category()
    cats = [c for c in db.get_all_categories() if c["name"] != "Sin categoría"]
    return sorted(cats, key=lambda c: (-usage.get(c["id"], 0), c["name"]))


def _build_category_keyboard(expense_id: int, page: int = 0) -> InlineKeyboardMarkup:
    cats = _sorted_categories()
    start = page * _PAGE_SIZE
    page_cats = cats[start:start + _PAGE_SIZE]

    rows = []
    for i in range(0, len(page_cats), 2):
        row = [
            InlineKeyboardButton(
                f"{c['icon']} {c['name']}",
                callback_data=f"c:{expense_id}:{c['id']}"
            )
            for c in page_cats[i:i + 2]
        ]
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Atrás", callback_data=f"cb:{expense_id}:{page - 1}"))
    if start + _PAGE_SIZE < len(cats):
        nav.append(InlineKeyboardButton("➡️ Más", callback_data=f"cm:{expense_id}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("✏️ Editar monto", callback_data=f"ea:{expense_id}")])
    return InlineKeyboardMarkup(rows)


def _build_edit_only_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Editar monto", callback_data=f"ea:{expense_id}")
    ]])


def _build_subcategory_keyboard(expense_id: int, subcats: list) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(subcats), 2):
        row = [
            InlineKeyboardButton(s["name"], callback_data=f"sc:{expense_id}:{s['id']}")
            for s in subcats[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("— Sin subcategoría", callback_data=f"sc:{expense_id}:0")])
    return InlineKeyboardMarkup(rows)


def _cat_line(cat_icon: str, cat_name: str, subcategory_id) -> str:
    if subcategory_id:
        subcat = db.get_subcategory_by_id(subcategory_id)
        if subcat:
            return f"{cat_icon} {cat_name} › {subcat['name']}"
    return f"{cat_icon} {cat_name}"


# ── Gastos fijos — helpers ────────────────────────────────────────────────────

def _concept_words(concept: str) -> set[str]:
    """Words of 3+ chars from a concept, lowercased, punctuation stripped."""
    return {w for w in re.sub(r'[^\w\s]', '', concept.lower()).split() if len(w) >= 3}


def _find_fixed_matches(concept: str, fixed_expenses) -> list:
    words = _concept_words(concept)
    return [fe for fe in fixed_expenses if words & _concept_words(fe["concept"])]


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
        "¿Guardamos?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, guardar", callback_data="ocr:confirm"),
            InlineKeyboardButton("❌ Cancelar",    callback_data="ocr:cancel"),
        ]]),
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    openai_api_key = context.bot_data.get("openai_api_key", "")
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY no configurada; voice ignorado")
        await update.message.reply_text("🎙️ El procesamiento de audio no está configurado.")
        return

    anthropic_api_key = context.bot_data.get("anthropic_api_key", "")

    status_msg = await update.message.reply_text("🎙️ Procesando audio...")

    tg_file = await update.message.voice.get_file()
    audio_bytes = bytes(await tg_file.download_as_bytearray())

    try:
        data = audio_module.transcribe_and_extract(audio_bytes, openai_api_key, anthropic_api_key)
    except Exception as e:
        logger.error("Error procesando audio: %s", e)
        await status_msg.edit_text(
            "❌ No pude procesar el audio. Intentá de nuevo o cargá el gasto manualmente:\n"
            "<code>Comercio monto</code>",
            parse_mode="HTML",
        )
        return

    if not data["amount"]:
        await status_msg.edit_text(
            f"⚠️ Escuché: \"<i>{data['transcription']}</i>\"\n\n"
            "No pude detectar el monto. Cargá el gasto manualmente:\n"
            "<code>Comercio monto</code>",
            parse_mode="HTML",
        )
        return

    chat_id = update.message.chat_id
    keywords = db.get_all_keywords()
    category_id, subcategory_id = categorizer.categorize(data["concept"], keywords)
    categories = {r["id"]: r for r in db.get_all_categories()}
    cat = categories.get(category_id)
    cat_name = cat["name"] if cat else None
    cat_icon = cat["icon"] if cat else None

    pending_voice[chat_id] = {
        "concept": data["concept"],
        "amount": data["amount"],
        "category_id": category_id,
        "subcategory_id": subcategory_id,
        "transcription": data["transcription"],
    }

    cat_line = ""
    if cat_name:
        cat_line = f"\n📂 Categoría: {_cat_line(cat_icon, cat_name, subcategory_id)}"

    await status_msg.edit_text(
        f"🎙️ Escuché: \"<i>{data['transcription']}</i>\"\n\n"
        f"📝 Concepto: {data['concept']}\n"
        f"💰 Monto: {fmt_amount(data['amount'])}"
        f"{cat_line}\n\n"
        "¿Confirmar? /si o /no",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()

    if chat_id in pending_fixed_direct:
        fdata = pending_fixed_direct.pop(chat_id)
        amount = msg_parser._normalize_amount(text)
        if amount is None:
            parsed = msg_parser.parse_message(text)
            if parsed:
                amount = parsed["amount"]
        if amount is None:
            pending_fixed_direct[chat_id] = fdata
            await update.message.reply_text(
                "❌ Monto inválido. Ejemplos: <code>15000</code>, <code>2500,50</code>",
                parse_mode="HTML",
            )
            return
        fe = db.get_fixed_expense_by_id(fdata["fixed_expense_id"])
        now = datetime.now(BAIRES)
        date_str = now.strftime("%Y-%m-%d")
        category_id = fe["category_id"] if fe else None
        expense_id = db.create_expense_full(user["id"], category_id, fdata["concept"], amount, date_str)
        db.create_fixed_payment(fdata["fixed_expense_id"], expense_id, now.year, now.month)
        cat = db.get_category_by_id(category_id) if category_id else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        await update.message.reply_text(
            f"✅ <b>Gasto fijo registrado</b>\n"
            f"📋 {fdata['concept']}\n"
            f"💰 {fmt_amount(amount)}\n"
            f"{cat_icon} {cat_name}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=_build_edit_only_keyboard(expense_id),
        )
        return

    if chat_id in pending_amount_edit:
        expense_id = pending_amount_edit.pop(chat_id)
        amount = msg_parser._normalize_amount(text)
        if amount is None:
            parsed = msg_parser.parse_message(text)
            if parsed:
                amount = parsed["amount"]
        if amount is None:
            pending_amount_edit[chat_id] = expense_id
            await update.message.reply_text(
                "❌ Monto inválido. Ejemplos: <code>15000</code>, <code>2500,50</code>",
                parse_mode="HTML",
            )
            return
        db.update_expense_amount(expense_id, user["id"], amount)
        expense = db.get_expense_by_id(expense_id)
        cat = db.get_category_by_id(expense["category_id"]) if expense and expense["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        await update.message.reply_text(
            f"✅ <b>Gasto actualizado</b>\n"
            f"📋 {expense['concept']}\n"
            f"💰 {fmt_amount(amount)}\n"
            f"{cat_icon} {cat_name}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=_build_edit_only_keyboard(expense_id),
        )
        return

    if chat_id in pending_ocr:
        response = text.lower()
        if response in ("sí", "si", "s", "dale", "ok"):
            data = pending_ocr.pop(chat_id)
            concept = data["comercio"] or "Ticket"
            keywords = db.get_all_keywords()
            category_id, subcategory_id = categorizer.categorize(concept, keywords)
            categories = {r["id"]: r for r in db.get_all_categories()}
            cat = categories.get(category_id)
            cat_name = cat["name"] if cat else "Sin categoría"
            cat_icon = cat["icon"] if cat else "❓"
            try:
                expense_id = db.create_expense(
                    user_id=user["id"],
                    category_id=category_id,
                    subcategory_id=subcategory_id,
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
                f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
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

    if chat_id in pending_voice:
        response = text.lower()
        if response in ("sí", "si", "s", "dale", "ok"):
            data = pending_voice.pop(chat_id)
            cat = db.get_category_by_id(data["category_id"]) if data["category_id"] else None
            cat_name = cat["name"] if cat else "Sin categoría"
            cat_icon = cat["icon"] if cat else "❓"
            try:
                expense_id = db.create_expense(
                    user_id=user["id"],
                    category_id=data["category_id"],
                    subcategory_id=data["subcategory_id"],
                    concept=data["concept"],
                    amount=data["amount"],
                    raw_text=f"[VOZ] {data['transcription']}",
                )
            except Exception as e:
                logger.error("Error guardando gasto de voz: %s", e)
                await update.message.reply_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
                return
            keyboard = _build_category_keyboard(expense_id) if data["category_id"] is None else _build_edit_only_keyboard(expense_id)
            await update.message.reply_text(
                f"✅ <b>Gasto registrado</b>\n"
                f"📋 {data['concept']}\n"
                f"💰 {fmt_amount(data['amount'])}\n"
                f"{_cat_line(cat_icon, cat_name, data['subcategory_id'])}\n"
                f"👤 {user['name']}\n"
                f"<code>#ID{expense_id}</code>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        elif response in ("no", "n", "cancelar"):
            del pending_voice[chat_id]
            await update.message.reply_text("❌ Carga cancelada.")
        else:
            await update.message.reply_text(
                "Por favor respondé <b>sí</b> o <b>no</b>.", parse_mode="HTML"
            )
        return

    # Abandon any stale pending state if the user sends a new message
    pending_fixed_match.pop(chat_id, None)
    pending_subcategory.pop(chat_id, None)
    pending_voice.pop(chat_id, None)

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
    category_id, subcategory_id = categorizer.categorize(parsed["concept"], keywords)

    # Check for fixed expense matches before saving
    fixed_expenses = db.get_all_fixed_expenses()
    matches = _find_fixed_matches(parsed["concept"], fixed_expenses)

    if matches:
        pending_fixed_match[chat_id] = {
            "concept":        parsed["concept"],
            "amount":         parsed["amount"],
            "category_id":    category_id,
            "subcategory_id": subcategory_id,
            "raw_text":       text,
        }
        if len(matches) == 1:
            fe = matches[0]
            est_str = fmt_amount(fe["estimated_amount"]) if fe["estimated_amount"] else "sin estimado"
            msg = (
                f"💡 '<b>{parsed['concept']}</b>' coincide con tu gasto fijo "
                f"<b>{fe['concept']}</b> ({est_str}). ¿Cómo querés registrarlo?"
            )
            buttons = [[
                InlineKeyboardButton("✅ Como gasto fijo",    callback_data=f"fix:confirm:{fe['id']}"),
                InlineKeyboardButton("📝 Registrar normal",  callback_data="fix:normal"),
            ]]
        else:
            msg = f"💡 '<b>{parsed['concept']}</b>' coincide con varios gastos fijos. ¿Cuál es?"
            buttons = []
            for fe in matches:
                est_str = fmt_amount(fe["estimated_amount"]) if fe["estimated_amount"] else "sin est."
                buttons.append([InlineKeyboardButton(
                    f"✅ {fe['concept']} ({est_str})",
                    callback_data=f"fix:confirm:{fe['id']}",
                )])
            buttons.append([InlineKeyboardButton("📝 Registrar normal", callback_data="fix:normal")])

        await update.message.reply_text(
            msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # No fixed match — save normally
    categories = {r["id"]: r for r in db.get_all_categories()}
    cat = categories.get(category_id)
    cat_name = cat["name"] if cat else "Sin categoría"
    cat_icon = cat["icon"] if cat else "❓"

    try:
        expense_id = db.create_expense(
            user_id=user["id"],
            category_id=category_id,
            subcategory_id=subcategory_id,
            concept=parsed["concept"],
            amount=parsed["amount"],
            raw_text=text,
        )
    except Exception as e:
        logger.error("Error guardando gasto: %s", e)
        await update.message.reply_text("⚠️ Hubo un error al guardar el gasto. Intentá de nuevo.")
        return

    if category_id is None:
        keyboard = _build_category_keyboard(expense_id)
    else:
        keyboard = _build_edit_only_keyboard(expense_id)

    await update.message.reply_text(
        f"✅ <b>Gasto registrado</b>\n"
        f"📋 {parsed['concept']}\n"
        f"💰 {fmt_amount(parsed['amount'])}\n"
        f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
        f"👤 {user['name']}\n"
        f"<code>#ID{expense_id}</code>",
        parse_mode="HTML",
        reply_markup=keyboard,
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

    from datetime import datetime, date, timedelta, timezone
    BAIRES = timezone(timedelta(hours=-3))
    today = datetime.now(BAIRES).date()
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # domingo
    week_end = week_start + timedelta(days=6)                       # sábado
    expenses = db.get_expenses_by_week(week_start.isoformat(), week_end.isoformat())

    if not expenses:
        await update.message.reply_text("📭 No hay gastos esta semana.")
        return

    total = sum(r["amount"] for r in expenses)
    lines = [f"📅 <b>Gastos de la semana</b> — {fmt_amount(total)}\n"]
    for r in expenses:
        date_part = fmt_date(r["created_at"])[:5]  # dd/mm
        lines.append(
            f"{r['category_icon']} {date_part}  {r['concept']}  "
            f"<b>{fmt_amount(r['amount'])}</b>  {r['user_name']}"
            f"  <code>#ID{r['id']}</code>"
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


async def cmd_fijos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    now = datetime.now(BAIRES)
    rows = db.get_fixed_payments_for_month(now.year, now.month)

    if not rows:
        await update.message.reply_text(
            "📋 No tenés gastos fijos configurados.\n"
            "Podés agregar desde el dashboard web."
        )
        return

    count_paid = sum(1 for r in rows if r["paid"])
    month_name = MONTHS_ES[now.month]

    lines = [f"📋 <b>Fijos — {month_name} {now.year}</b>\n"]
    buttons = []

    for r in rows:
        if r["paid"]:
            actual = fmt_amount(r["actual_amount"]) if r["actual_amount"] else "—"
            lines.append(f"✅ {r['concept']} — {actual}")
        else:
            est = f"~{fmt_amount(r['estimated_amount'])}" if r["estimated_amount"] else "sin estimado"
            lines.append(f"⬜ {r['concept']} — {est}")
            buttons.append([InlineKeyboardButton(
                f"Registrar pago: {r['concept']}",
                callback_data=f"fix:direct_pay:{r['id']}",
            )])

    lines.append(f"\n{count_paid} de {len(rows)} pagados")

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=markup)


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
        "   /fijos      → estado gastos fijos del mes\n"
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


# ── Callback de botones inline ────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user = db.get_user_by_telegram_id(str(chat_id))
    if user is None:
        return

    cb = query.data

    if cb == "ocr:confirm":
        if chat_id not in pending_ocr:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        ocr_data = pending_ocr.pop(chat_id)
        concept = ocr_data["comercio"] or "Ticket"
        keywords = db.get_all_keywords()
        category_id, subcategory_id = categorizer.categorize(concept, keywords)
        categories = {r["id"]: r for r in db.get_all_categories()}
        cat = categories.get(category_id)
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        try:
            expense_id = db.create_expense(
                user_id=user["id"],
                category_id=category_id,
                subcategory_id=subcategory_id,
                concept=concept,
                amount=ocr_data["monto"],
                raw_text=f"[OCR] {concept} {ocr_data['monto']}",
            )
        except Exception as e:
            logger.error("Error guardando gasto OCR: %s", e)
            await query.edit_message_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
            return
        keyboard = _build_category_keyboard(expense_id) if category_id is None else _build_edit_only_keyboard(expense_id)
        await query.edit_message_text(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {concept}\n"
            f"💰 {fmt_amount(ocr_data['monto'])}\n"
            f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    elif cb == "ocr:cancel":
        pending_ocr.pop(chat_id, None)
        await query.edit_message_text("❌ Carga cancelada.")
        return

    if cb.startswith("c:"):
        _, expense_id_str, cat_id_str = cb.split(":")
        expense_id, cat_id = int(expense_id_str), int(cat_id_str)
        db.update_expense_category(expense_id, user["id"], cat_id)
        expense = db.get_expense_by_id(expense_id)
        if expense:
            db.add_keyword(categorizer.normalize(expense["concept"]), cat_id)
        cat = db.get_category_by_id(cat_id)
        cat_name = cat["name"] if cat else "?"
        cat_icon = cat["icon"] if cat else "❓"

        subcats = db.get_subcategories(cat_id)
        if subcats:
            pending_subcategory[chat_id] = expense_id
            await query.edit_message_text(
                f"✅ <b>{cat_icon} {cat_name}</b> asignada\n"
                f"📋 {expense['concept']}\n"
                f"💰 {fmt_amount(expense['amount'])}\n\n"
                f"¿Querés agregar una subcategoría?",
                parse_mode="HTML",
                reply_markup=_build_subcategory_keyboard(expense_id, subcats),
            )
        else:
            await query.edit_message_text(
                f"✅ <b>Gasto registrado</b>\n"
                f"📋 {expense['concept']}\n"
                f"💰 {fmt_amount(expense['amount'])}\n"
                f"{cat_icon} {cat_name}\n"
                f"👤 {user['name']}\n"
                f"<code>#ID{expense_id}</code>",
                parse_mode="HTML",
                reply_markup=_build_edit_only_keyboard(expense_id),
            )

    elif cb.startswith("sc:"):
        parts = cb.split(":")
        expense_id = int(parts[1])
        subcat_id = int(parts[2]) or None

        pending_subcategory.pop(chat_id, None)

        expense = db.get_expense_by_id(expense_id)
        cat = db.get_category_by_id(expense["category_id"]) if expense and expense["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"

        if subcat_id is not None:
            db.update_expense_subcategory(expense_id, subcat_id)
            if expense:
                db.add_keyword(
                    categorizer.normalize(expense["concept"]),
                    expense["category_id"],
                    subcat_id,
                )

        await query.edit_message_text(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {expense['concept']}\n"
            f"💰 {fmt_amount(expense['amount'])}\n"
            f"{_cat_line(cat_icon, cat_name, subcat_id)}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=_build_edit_only_keyboard(expense_id),
        )

    elif cb.startswith("cm:"):
        _, expense_id_str, page_str = cb.split(":")
        await query.edit_message_reply_markup(
            reply_markup=_build_category_keyboard(int(expense_id_str), int(page_str))
        )

    elif cb.startswith("cb:"):
        _, expense_id_str, page_str = cb.split(":")
        await query.edit_message_reply_markup(
            reply_markup=_build_category_keyboard(int(expense_id_str), int(page_str))
        )

    elif cb.startswith("fix:confirm:"):
        fixed_expense_id = int(cb.split(":")[2])
        if chat_id not in pending_fixed_match:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        data = pending_fixed_match.pop(chat_id)
        now = datetime.now(BAIRES)
        try:
            expense_id = db.create_expense(
                user_id=user["id"],
                category_id=data["category_id"],
                subcategory_id=data.get("subcategory_id"),
                concept=data["concept"],
                amount=data["amount"],
                raw_text=data["raw_text"],
            )
        except Exception as e:
            logger.error("Error guardando gasto fijo: %s", e)
            await query.edit_message_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
            return
        db.create_fixed_payment(fixed_expense_id, expense_id, now.year, now.month)
        fe = db.get_fixed_expense_by_id(fixed_expense_id)
        cat = db.get_category_by_id(data["category_id"]) if data["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        keyboard = _build_category_keyboard(expense_id) if data["category_id"] is None else _build_edit_only_keyboard(expense_id)
        await query.edit_message_text(
            f"✅ <b>Gasto fijo registrado</b>\n"
            f"📋 {data['concept']}\n"
            f"💰 {fmt_amount(data['amount'])}\n"
            f"{_cat_line(cat_icon, cat_name, data.get('subcategory_id'))}\n"
            f"👤 {user['name']}\n"
            f"📌 {fe['concept'] if fe else 'Gasto fijo'}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif cb == "fix:normal":
        if chat_id not in pending_fixed_match:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        data = pending_fixed_match.pop(chat_id)
        cat = db.get_category_by_id(data["category_id"]) if data["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        try:
            expense_id = db.create_expense(
                user_id=user["id"],
                category_id=data["category_id"],
                subcategory_id=data.get("subcategory_id"),
                concept=data["concept"],
                amount=data["amount"],
                raw_text=data["raw_text"],
            )
        except Exception as e:
            logger.error("Error guardando gasto normal: %s", e)
            await query.edit_message_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
            return
        keyboard = _build_category_keyboard(expense_id) if data["category_id"] is None else _build_edit_only_keyboard(expense_id)
        await query.edit_message_text(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {data['concept']}\n"
            f"💰 {fmt_amount(data['amount'])}\n"
            f"{_cat_line(cat_icon, cat_name, data.get('subcategory_id'))}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif cb.startswith("fix:direct_pay:"):
        fixed_expense_id = int(cb.split(":")[2])
        fe = db.get_fixed_expense_by_id(fixed_expense_id)
        if not fe:
            await query.answer("Gasto fijo no encontrado.", show_alert=True)
            return
        pending_fixed_direct[chat_id] = {
            "fixed_expense_id": fixed_expense_id,
            "concept":          fe["concept"],
        }
        est_str = f" (estimado: {fmt_amount(fe['estimated_amount'])})" if fe["estimated_amount"] else ""
        await query.message.reply_text(
            f"💰 ¿Cuánto pagaste por <b>{fe['concept']}</b>?{est_str}\nEnviá el monto:",
            parse_mode="HTML",
        )

    elif cb.startswith("ea:"):
        expense_id = int(cb.split(":")[1])
        pending_amount_edit[chat_id] = expense_id
        await query.message.reply_text("💰 Enviá el nuevo monto:")


# ── CambioDolar ───────────────────────────────────────────────────────────────

async def handle_cambiodolar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    text = update.message.text.strip()
    rest = text[len("cambiodolar"):].strip()
    tokens = rest.split()

    _fmt_error = (
        "❌ Formato incorrecto. Usá: <code>CambioDolar &lt;monto_usd&gt; &lt;cotizacion&gt;</code>\n"
        "Ejemplo: <code>CambioDolar 1000 1400</code>"
    )

    if len(tokens) != 2:
        await update.message.reply_text(_fmt_error, parse_mode="HTML")
        return

    monto_usd  = _parse_cambio_token(tokens[0])
    cotizacion = _parse_cambio_token(tokens[1])

    if monto_usd is None or cotizacion is None:
        await update.message.reply_text(_fmt_error, parse_mode="HTML")
        return

    monto_ars    = monto_usd * cotizacion
    fecha_str    = datetime.now(BAIRES).strftime("%Y-%m-%d")
    fecha_display = datetime.now(BAIRES).strftime("%d/%m/%Y")
    db.registrar_cambio(fecha_str, monto_usd, cotizacion, user["name"])

    await update.message.reply_text(
        f"✅ Cambio registrado\n"
        f"💵 USD: {fmt_usd(monto_usd)}\n"
        f"💱 Cotización: {fmt_amount(cotizacion)}\n"
        f"💰 ARS obtenidos: {fmt_amount(monto_ars)}\n"
        f"📅 Fecha: {fecha_display}",
        parse_mode="HTML",
    )


# ── Arranque ──────────────────────────────────────────────────────────────────

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("gastos",      cmd_gastos))
    app.add_handler(CommandHandler("semana",      cmd_semana))
    app.add_handler(CommandHandler("hoy",         cmd_hoy))
    app.add_handler(CommandHandler("sincat",      cmd_sincat))
    app.add_handler(CommandHandler("fijos",       cmd_fijos))
    app.add_handler(CommandHandler("editar",      cmd_editar))
    app.add_handler(CommandHandler("recat",       cmd_recat))
    app.add_handler(CommandHandler("borrar",      cmd_borrar))
    app.add_handler(CommandHandler("add_keyword",       cmd_add_keyword))
    app.add_handler(CommandHandler("categorias",        cmd_categorias))
    app.add_handler(CommandHandler("nueva_categoria",   cmd_nueva_categoria))
    app.add_handler(CommandHandler("ayuda",             cmd_ayuda))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    _cambiodolar_filter = filters.TEXT & filters.Regex(r'(?i)^cambiodolar\b')
    app.add_handler(MessageHandler(_cambiodolar_filter, handle_cambiodolar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~_cambiodolar_filter, handle_message))

    return app
