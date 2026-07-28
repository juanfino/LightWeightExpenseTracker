import asyncio
import logging
import os
import re
import time
import traceback
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import db
import parser as msg_parser
import categorizer
import ocr as ocr_module
import audio as audio_module
import dolar as dolar_module
import intent as intent_module
import fixed_matcher
import pgcompat
import auth
import llm_limits

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
BAIRES = timezone(timedelta(hours=-3))

# Umbral de confianza para registrar automáticamente (voz y dólar) sin pedir
# confirmación. Conservador: solo lo muy claro se guarda solo.
AUTOSAVE_CONFIDENCE = 0.9

MONTHS_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# Gastos pendientes de confirmación tras OCR, keyed por chat_id
pending_ocr: dict[int, dict] = {}
# Gastos pendientes de confirmación tras voice, keyed por chat_id.
# Structure: {"queue": [expense_dict, ...], "total": N, "done": 0}
pending_voice: dict[int, dict] = {}
# Audio pendiente de reintento tras fallo de transcripción/extracción, keyed por chat_id.
# Se sobreescribe con cada nuevo audio; no requiere TTL más allá de eso.
pending_voice_retry: dict[int, bytes] = {}
# Gastos esperando nuevo monto del usuario, keyed por chat_id → expense_id
pending_amount_edit: dict[int, int] = {}
# Monto pendiente para registrar un gasto fijo directamente desde /fijos (o desde el
# fallback "ninguno de estos" de la búsqueda de candidatos de "ya lo pagué")
pending_fixed_direct: dict[int, dict] = {}
# Gasto esperando selección de subcategoría, keyed por chat_id → expense_id
pending_subcategory: dict[int, int] = {}
# Operación de dólar (compra/venta) pendiente de confirmación, keyed por chat_id.
# Structure: {"tipo": str, "monto_usd": float, "cotizacion": float}
pending_dolar: dict[int, dict] = {}
# Mutación en lenguaje natural pendiente de confirmación (edición o taxonomía),
# keyed por chat_id. Structure: {"kind": "edit"|"category"|"subcategory", ...}
pending_nl_confirm: dict[int, dict] = {}
# Selección entre varios gastos candidatos para una edición NL, keyed por chat_id.
# Structure: {"changes": {...}, "candidates": [expense_id, ...]}
pending_nl_pick: dict[int, dict] = {}
# Ventana deslizante de historial conversacional para la capa de intención, keyed
# por chat_id. Structure: [{"role": "user"|"assistant", "content": str, "ts": float}, ...].
# Se recorta a los últimos NL_HISTORY_TTL_SECONDS segundos y NL_HISTORY_MAX_MESSAGES
# mensajes en cada acceso — así una consulta como "dame el desglose por persona"
# puede resolver a qué reporte anterior se refiere, sin mantener memoria indefinida.
pending_nl_history: dict[int, list[dict]] = {}
NL_HISTORY_TTL_SECONDS = 300
NL_HISTORY_MAX_MESSAGES = 10


# ── Formateo ──────────────────────────────────────────────────────────────────

def fmt_amount(amount: float, currency: str = "ARS") -> str:
    """2500.5 → '$2.500,50'  |  150000.0 → '$150.000'"""
    prefix = "U$S " if currency == "USD" else "$"
    if amount == int(amount):
        # whole number: use dot as thousands separator
        return prefix + f"{int(amount):,}".replace(",", ".")
    # has decimals: format with 2 decimal places, swap separators
    formatted = f"{amount:,.2f}"          # "2,500.50"
    int_part, dec_part = formatted.split(".")
    int_part = int_part.replace(",", ".")  # "2.500"
    return f"{prefix}{int_part},{dec_part}"


def fmt_usd(amount: float) -> str:
    """1000.0 → 'U$S 1.000'  |  500.5 → 'U$S 500,50'"""
    if amount == int(amount):
        return "U$S " + f"{int(amount):,}".replace(",", ".")
    formatted = f"{amount:,.2f}"
    int_part, dec_part = formatted.split(".")
    int_part = int_part.replace(",", ".")
    return f"U$S {int_part},{dec_part}"


def _separate_totals(rows) -> str:
    totals = {currency: sum(r["amount"] for r in rows if r["currency"] == currency)
              for currency in ("ARS", "USD")}
    return " · ".join(fmt_amount(totals[c], c) for c in ("ARS", "USD") if totals[c]) or "$0"


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


def _parse_fecha_ddmmyyyy(text: str) -> str | None:
    """'15/06/2026' → '2026-06-15' (None if not a valid DD/MM/AAAA date)."""
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _fmt_fecha_ddmmyyyy(date_str: str) -> str:
    """'2026-06-15' → '15/06/2026' (returns the input unchanged if not YYYY-MM-DD)."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return date_str


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

    rows.append([
        InlineKeyboardButton("✏️ Editar monto", callback_data=f"ea:{expense_id}"),
        InlineKeyboardButton("💱 Moneda", callback_data=f"ec:{expense_id}"),
        InlineKeyboardButton("🔗 Gasto fijo",   callback_data=f"fl:{expense_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def _build_edit_only_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Editar monto", callback_data=f"ea:{expense_id}"),
        InlineKeyboardButton("💱 Moneda", callback_data=f"ec:{expense_id}"),
        InlineKeyboardButton("🔗 Gasto fijo",   callback_data=f"fl:{expense_id}"),
    ]])


def _build_currency_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("$ ARS", callback_data=f"ecset:{expense_id}:ARS"),
        InlineKeyboardButton("U$S USD", callback_data=f"ecset:{expense_id}:USD"),
    ]])


def _build_fixed_link_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    """Picker to attach/detach an existing expense to a fixed expense — same interaction
    language as the category picker, but single-page since fixed-expense lists are short."""
    expense = db.get_expense_by_id(expense_id)
    fixed_expenses = [fe for fe in db.get_all_fixed_expenses()
                      if expense and fe["currency"] == expense["currency"]]
    rows = []
    for i in range(0, len(fixed_expenses), 2):
        row = [
            InlineKeyboardButton(fe["concept"][:24], callback_data=f"flset:{expense_id}:{fe['id']}")
            for fe in fixed_expenses[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("— Ninguno —", callback_data=f"flset:{expense_id}:none")])
    return InlineKeyboardMarkup(rows)


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


# ── Capa de intención — enrutado ──────────────────────────────────────────────

# Señales de que un mensaje NO es un simple "concepto monto" y conviene escalarlo
# a la capa de intención (ediciones, taxonomía, consultas, frases más ricas o con
# slang). El camino rápido determinista se reserva para lo que no dispara esto.
_INTENT_TRIGGER_RE = re.compile(
    r"(?i)(?:\b("
    r"cu[aá]nto|cu[aá]nta|cu[aá]les?|"                      # preguntas
    r"edit[aá]|editar|corrig[eií]|corregir|cambi[aá]|cambiar|actualiz[aá]|actualizar|modific\w*|"  # edición
    r"equivoqu[eé]|"                                        # corrección
    r"[uú]ltim[oa]s?|ante[uú]ltim\w*|pen[uú]ltim\w*|"       # referencias
    r"categor[ií]as?|subcategor[ií]as?|"                    # taxonomía
    r"agreg[aá]|agregar|cre[aá]|crear|"                     # taxonomía (verbos)
    r"anot[aá]|anotame|anotar|"                             # "anotame ..."
    r"cobr[eé]|recib[ií]|depositaron|pagaron|reintegro|ingreso|"  # ingresos
    r"lucas?|palos?|"                                       # slang de montos
    r"trimestre|reporte|resumen|"                           # períodos/reportes
    r"falta|faltan|comprar|compr[eé]|necesito|necesitamos|lista"  # compras
    r")\b)"
    r"|pon[eé]\s+que"                                       # "poné que ..."
    r"|\bgasto\s+\d+"                                       # "el gasto 124"
)


def _needs_intent(text: str) -> bool:
    """True si el mensaje tiene señales de intención conversacional (edición,
    taxonomía, consulta o frase rica) y debe ir a la capa de intención en vez del
    parser determinista."""
    return bool(_INTENT_TRIGGER_RE.search(text or ""))


# ── Gastos fijos — helpers ────────────────────────────────────────────────────

def _expense_period(expense) -> tuple[int, int]:
    """Period (year, month) an expense's own date falls in, in ART."""
    return fixed_matcher.expense_period(expense["created_at"], BAIRES)


async def _maybe_offer_fixed_link(chat_id: int, bot, expense_id: int, concept: str, currency: str = "ARS") -> None:
    """Runs after ANY expense is saved, on every input path (plain text, NL, voice, OCR) —
    the single downstream seam that offers linking a newly logged expense to a matching
    fixed expense. Never links on its own; always asks. No-op if nothing matches, so most
    expenses see no extra message."""
    fixed_expenses = db.get_all_fixed_expenses()
    matches = fixed_matcher.find_fixed_expense_matches(concept, fixed_expenses, currency)
    if not matches:
        return

    expense = db.get_expense_by_id(expense_id)
    if not expense:
        return
    year, month = _expense_period(expense)

    if len(matches) == 1:
        fe = matches[0]
        payments = db.get_fixed_payments_for_period(year, month)
        already_paid = next((p["paid"] for p in payments if p["id"] == fe["id"]), False)
        verb = "¿Es un segundo pago de tu gasto fijo" if already_paid else "¿Es el pago de tu gasto fijo"
        msg = f"💡 {verb} <b>{fe['concept']}</b>?"
        buttons = [[
            InlineKeyboardButton("✅ Sí",  callback_data=f"fixlink:{expense_id}:{fe['id']}"),
            InlineKeyboardButton("❌ No",  callback_data=f"fixlink:{expense_id}:none"),
        ]]
    else:
        msg = "💡 Este gasto coincide con varios gastos fijos. ¿Es el pago de alguno?"
        buttons = [
            [InlineKeyboardButton(f"✅ {fe['concept']}", callback_data=f"fixlink:{expense_id}:{fe['id']}")]
            for fe in matches
        ]
        buttons.append([InlineKeyboardButton("❌ Ninguno", callback_data=f"fixlink:{expense_id}:none")])

    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


# ── Guard de usuario ──────────────────────────────────────────────────────────

async def _get_authorized_user(update: Update):
    """Retorna el row del usuario o None. Responde con rechazo si no autorizado."""
    message = update.effective_message
    telegram_id = str(update.effective_chat.id)
    user = db.get_user_by_telegram_id(telegram_id)
    if user is None:
        dashboard_url = os.environ.get(
            "PUBLIC_DASHBOARD_URL", "https://mangoteca.juampifinochietto.com"
        ).rstrip("/")
        await message.reply_text(
            "👋 Para usar este bot, primero conectá tu cuenta desde el dashboard:\n"
            f"{dashboard_url}/vincular-telegram"
        )
    else:
        pgcompat.select_pool("bot")
        pgcompat.set_family_id(user["family_id"])
        pgcompat.set_user_id(user["id"])
    return user


async def _routine_quota_exhausted(message) -> bool:
    usage = llm_limits.routine_usage()
    if usage["remaining"] > 0:
        return False
    await message.reply_text(
        "⏳ Tu familia alcanzó el límite diario de IA. "
        "Se habilita de nuevo mañana a las 00:00. "
        "Mientras tanto podés seguir cargando gastos como: Supermercado 15000"
    )
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "👋 Por ahora funciono solamente en chats privados. Abrime directamente y conectá tu cuenta desde el dashboard."
        )
        return
    token = context.args[0] if context.args else ""
    if token:
        try:
            linked = auth.consume_telegram_link_token(token, str(update.effective_chat.id))
        except ValueError as exc:
            await update.effective_message.reply_text(f"⚠️ {exc}")
            return
        pgcompat.select_pool("bot")
        pgcompat.set_family_id(linked["family_id"])
        pgcompat.set_user_id(linked["id"])
        await update.effective_message.reply_text(
            f"✅ ¡Listo, {linked['name']}! Tu Telegram quedó conectado.\n\n"
            "Probá ahora con:\nSupermercado 15000"
        )
        return
    user = await _get_authorized_user(update)
    if user:
        await update.effective_message.reply_text(
            "👋 Ya estás conectado. Probá cargar un gasto así:\nSupermercado 15000"
        )


async def reject_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 Todavía no admito grupos. Escribime por privado para que cada gasto quede asociado a la persona correcta."
    )
    raise ApplicationHandlerStop


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _get_authorized_user(update)


async def handle_bot_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    family_id = pgcompat.current_family_id()
    user_id = pgcompat.current_user_id()
    details = "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    )
    logger.error(
        "Excepción no manejada family_id=%s user_id=%s\n%s",
        family_id, user_id, details,
    )
    db.record_system_error("telegram", context.error, details)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return
    if await _routine_quota_exhausted(update.effective_message):
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

    data["currency"] = "ARS"  # OCR is deliberately ARS-first; the confirmation can correct it.
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
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Sí, guardar", callback_data="ocr:confirm"),
                InlineKeyboardButton("❌ Cancelar", callback_data="ocr:cancel"),
            ],
            [
                InlineKeyboardButton("$ ARS", callback_data="ocr:currency:ARS"),
                InlineKeyboardButton("U$S USD", callback_data="ocr:currency:USD"),
            ],
        ]),
    )


async def _send_next_voice_confirmation(chat_id: int, bot) -> None:
    """Pop the next expense from pending_voice and send an inline confirmation message."""
    state = pending_voice.get(chat_id)
    if not state or not state["queue"]:
        pending_voice.pop(chat_id, None)
        return

    item = state["queue"].pop(0)
    state["done"] += 1
    index = state["done"]
    total = state["total"]

    cat = db.get_category_by_id(item["category_id"]) if item["category_id"] else None
    cat_name = cat["name"] if cat else None
    cat_icon = cat["icon"] if cat else None

    counter = f"🎙️ Gasto {index} de {total}\n" if total > 1 else "🎙️ \n"
    cat_line = f"\n📂 Categoría: {_cat_line(cat_icon, cat_name, item['subcategory_id'])}" if cat_name else ""

    # Store current item so handle_callback can read it without re-popping
    state["current"] = item

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"{counter}"
            f"📋 Concepto: {item['concept']}\n"
            f"💰 Monto: {fmt_amount(item['amount'], item.get('currency', 'ARS'))}"
            f"{cat_line}\n\n"
            "¿Guardamos?"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, guardar", callback_data="voice:confirm"),
            InlineKeyboardButton("❌ Cancelar",    callback_data="voice:cancel"),
        ]]),
    )


async def _register_voice_expense(chat_id: int, bot, user: dict, item: dict) -> None:
    """Registra directo un gasto de voz de alta confianza y avisa (con teclado para
    editar/re-categorizar si algo salió mal)."""
    cat = db.get_category_by_id(item["category_id"]) if item["category_id"] else None
    cat_name = cat["name"] if cat else "Sin categoría"
    cat_icon = cat["icon"] if cat else "❓"
    try:
        expense_id = db.create_expense(
            user_id=user["id"],
            category_id=item["category_id"],
            subcategory_id=item["subcategory_id"],
            concept=item["concept"],
            amount=item["amount"],
            currency=item.get("currency", "ARS"),
            raw_text=f"[VOZ] {item['transcription']}",
        )
    except Exception as e:
        logger.error("Error guardando gasto de voz automático: %s", e)
        await bot.send_message(chat_id=chat_id, text="⚠️ Error al guardar un gasto. Intentá de nuevo.")
        return
    keyboard = _build_category_keyboard(expense_id) if item["category_id"] is None else _build_edit_only_keyboard(expense_id)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {item['concept']}\n"
            f"💰 {fmt_amount(item['amount'], item.get('currency', 'ARS'))}\n"
            f"{_cat_line(cat_icon, cat_name, item['subcategory_id'])}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await _maybe_offer_fixed_link(chat_id, bot, expense_id, item["concept"], item.get("currency", "ARS"))


async def _process_voice_audio(chat_id: int, bot, user: dict, audio_bytes: bytes,
                                status_msg, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe → extrae gastos → responde. Usado por handle_voice() y por el
    callback "voice:retry" para poder reintentar sin volver a grabar."""
    openai_api_key = context.bot_data.get("openai_api_key", "")
    anthropic_api_key = context.bot_data.get("anthropic_api_key", "")

    async def _fail(e: Exception, where: str) -> None:
        logger.error("Error %s audio: %s", where, e)
        pending_voice_retry[chat_id] = audio_bytes
        await status_msg.edit_text(
            "❌ No pude procesar el audio. Intentá de nuevo o cargá el gasto manualmente:\n"
            "<code>Comercio monto</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Reintentar", callback_data="voice:retry"),
            ]]),
        )

    try:
        transcription = await asyncio.to_thread(audio_module.transcribe, audio_bytes, openai_api_key)
    except Exception as e:
        await _fail(e, "transcribiendo")
        return

    # Si el audio suena a operación de dólar, intentar interpretarlo como tal.
    if dolar_module.looks_like_dolar(transcription):
        op = dolar_module.parse_dolar(transcription, anthropic_api_key)
        if op is not None:
            pending_voice_retry.pop(chat_id, None)
            await status_msg.delete()
            await _handle_dolar_operation(chat_id, bot, user, op)
            return

    # Si la transcripción tiene señales de intención conversacional (edición,
    # taxonomía, consulta), pasarla a la capa de intención en vez del extractor
    # de gastos, que no sabe responder preguntas ni editar/administrar taxonomía.
    if anthropic_api_key and _needs_intent(transcription):
        pending_voice_retry.pop(chat_id, None)
        await status_msg.delete()
        await _handle_intent_message(chat_id, bot, user, transcription, anthropic_api_key)
        return

    try:
        expenses = await asyncio.to_thread(audio_module.extract_expenses, transcription, anthropic_api_key)
    except Exception as e:
        await _fail(e, "extrayendo gastos de")
        return

    pending_voice_retry.pop(chat_id, None)

    valid = [e for e in expenses if e["amount"] is not None]
    skipped = [e for e in expenses if e["amount"] is None]

    if not valid:
        await status_msg.edit_text(
            f"⚠️ Escuché: \"<i>{transcription}</i>\"\n\n"
            "No pude detectar ningún monto. Cargá los gastos manualmente:\n"
            "<code>Comercio monto</code>",
            parse_mode="HTML",
        )
        return

    # Categorize valid expenses and split by confidence: los muy claros se
    # registran directo, los dudosos se confirman uno por uno.
    keywords = db.get_all_keywords()
    auto_items, confirm_items = [], []
    for expense in valid:
        category_id, subcategory_id = categorizer.categorize(expense["concept"], keywords)
        item = {
            "concept": expense["concept"],
            "amount": expense["amount"],
            "currency": expense.get("currency", "ARS"),
            "category_id": category_id,
            "subcategory_id": subcategory_id,
            "transcription": transcription,
        }
        if expense["confidence"] >= AUTOSAVE_CONFIDENCE:
            auto_items.append(item)
        else:
            confirm_items.append(item)

    # Delete the "Procesando audio..." status message before sending results
    await status_msg.delete()

    # Warn about skipped items (no amount detected)
    if skipped:
        names = ", ".join(e["concept"] for e in skipped)
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ No pude detectar el monto de: <b>{names}</b>. Lo salteé.",
            parse_mode="HTML",
        )

    # Transcription summary
    parts = []
    if auto_items:
        parts.append(f"registré <b>{len(auto_items)}</b> automáticamente")
    if confirm_items:
        parts.append(f"revisemos <b>{len(confirm_items)}</b>")
    summary = " y ".join(parts) if parts else ""
    await bot.send_message(
        chat_id=chat_id,
        text=f"🎙️ Escuché: \"<i>{transcription}</i>\"\n{summary.capitalize()}:",
        parse_mode="HTML",
    )

    # Auto-registrar los de alta confianza
    for item in auto_items:
        await _register_voice_expense(chat_id, bot, user, item)

    # Encolar los dudosos para confirmación uno por uno
    if confirm_items:
        pending_voice[chat_id] = {
            "queue": confirm_items,
            "total": len(confirm_items),
            "done": 0,
            "current": None,
        }
        await _send_next_voice_confirmation(chat_id, bot)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return
    if await _routine_quota_exhausted(update.effective_message):
        return

    openai_api_key = context.bot_data.get("openai_api_key", "")
    if not openai_api_key:
        logger.warning("OPENAI_API_KEY no configurada; voice ignorado")
        await update.message.reply_text("🎙️ El procesamiento de audio no está configurado.")
        return

    status_msg = await update.message.reply_text("🎙️ Procesando audio...")
    chat_id = update.message.chat_id

    try:
        tg_file = await update.message.voice.get_file()
        audio_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        # Descarga desde Telegram puede colgarse con mala señal; sin este guard
        # la excepción quedaba sin manejar y el "Procesando audio..." colgado
        # para siempre (no hay error handler registrado a nivel Application).
        logger.error("Error descargando audio de voz: %s", e)
        await status_msg.edit_text("❌ No pude descargar el audio (problema de red). Probá enviarlo de nuevo.")
        return

    await _process_voice_audio(chat_id, context.bot, user, audio_bytes, status_msg, context)


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
        now = datetime.now(BAIRES)
        date_str = now.strftime("%Y-%m-%d")
        expense_id = db.create_expense_full(
            user["id"], None, fdata["concept"], amount, date_str,
            currency=fdata.get("currency", "ARS"),
        )
        db.link_expense_to_fixed(expense_id, fdata["fixed_expense_id"], now.year, now.month)
        expense = db.get_expense_by_id(expense_id)
        cat = db.get_category_by_id(expense["category_id"]) if expense["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        await update.message.reply_text(
            f"✅ <b>Gasto fijo registrado</b>\n"
            f"📋 {fdata['concept']}\n"
            f"💰 {fmt_amount(amount, fdata.get('currency', 'ARS'))}\n"
            f"{_cat_line(cat_icon, cat_name, expense['subcategory_id'])}\n"
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
            f"💰 {fmt_amount(amount, expense['currency'])}\n"
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
                currency=data.get("currency", "ARS"),
                raw_text=f"[OCR] {concept} {data['monto']}",
                )
            except Exception as e:
                logger.error("Error guardando gasto OCR: %s", e)
                await update.message.reply_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
                return
            await update.message.reply_text(
                f"✅ <b>Gasto registrado</b>\n"
                f"📋 {concept}\n"
                f"💰 {fmt_amount(data['monto'], data.get('currency', 'ARS'))}\n"
                f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
                f"👤 {user['name']}\n"
                f"<code>#ID{expense_id}</code>",
                parse_mode="HTML",
            )
            await _maybe_offer_fixed_link(
                chat_id, context.bot, expense_id, concept, data.get("currency", "ARS")
            )
        elif response in ("no", "n", "cancelar"):
            del pending_ocr[chat_id]
            await update.message.reply_text("❌ Carga cancelada.")
        else:
            await update.message.reply_text(
                "Por favor respondé <b>sí</b> o <b>no</b>.", parse_mode="HTML"
            )
        return

    # Abandon any stale pending state if the user sends a new message
    pending_subcategory.pop(chat_id, None)
    pending_nl_confirm.pop(chat_id, None)
    pending_nl_pick.pop(chat_id, None)

    # Si el mensaje suena a operación de dólar, intentar interpretarlo como tal.
    anthropic_api_key = context.bot_data.get("anthropic_api_key", "")

    # Fast path explícito para ingresos. El formato neutro "concepto monto"
    # conserva para siempre su significado histórico de gasto.
    income_match = re.match(r"(?i)^ingreso\s*:\s*(.+)$", text)
    if income_match:
        parsed_income = msg_parser.parse_message(income_match.group(1))
        if parsed_income is None:
            await update.message.reply_text(
                "❓ No pude entender el ingreso. Ejemplo: "
                "<code>Ingreso: sueldo 1.500.000</code>",
                parse_mode="HTML",
            )
            return
        result = {
            "concept": parsed_income["concept"],
            "amount": parsed_income["amount"],
            "currency": parsed_income.get("currency", "ARS"),
            "date": datetime.now(BAIRES).strftime("%Y-%m-%d"),
        }
        await _nl_do_income(chat_id, context.bot, user, result)
        return

    if anthropic_api_key and dolar_module.looks_like_dolar(text):
        if await _routine_quota_exhausted(update.effective_message):
            return
        op = dolar_module.parse_dolar(text, anthropic_api_key)
        if op is not None:
            await _handle_dolar_operation(chat_id, context.bot, user, op)
            return

    parsed = msg_parser.parse_message(text)

    # Camino rápido determinista: "concepto monto" simple, sin señales de intención
    # conversacional → se guarda al instante (comportamiento histórico). Todo lo demás
    # (ediciones, taxonomía, consultas, frases más ricas) pasa por la capa de intención.
    if anthropic_api_key and (parsed is None or _needs_intent(text)):
        if await _routine_quota_exhausted(update.effective_message):
            return
        await _handle_intent_message(chat_id, context.bot, user, text, anthropic_api_key)
        return

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
            currency=parsed.get("currency", "ARS"),
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
        f"💰 {fmt_amount(parsed['amount'], parsed.get('currency', 'ARS'))}\n"
        f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
        f"👤 {user['name']}\n"
        f"<code>#ID{expense_id}</code>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    await _maybe_offer_fixed_link(chat_id, context.bot, expense_id, parsed["concept"], parsed.get("currency", "ARS"))


async def cmd_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await _get_authorized_user(update)
    if user is None:
        return

    from datetime import datetime
    now = datetime.now()
    summary = db.get_expenses_summary_by_category(now.year, now.month, currency="ARS")
    summary_usd = db.get_expenses_summary_by_category(now.year, now.month, currency="USD")

    if not summary and not summary_usd:
        await update.message.reply_text("📭 No hay gastos registrados este mes.")
        return

    total = sum(r["total"] for r in summary)
    month_name = now.strftime("%B %Y").capitalize()

    usd_total = sum(r["total"] for r in summary_usd)
    lines = [f"📊 <b>Gastos de {month_name}</b>\n", f"💰 Total: <b>{fmt_amount(total)}" + (f" · {fmt_amount(usd_total, 'USD')}" if usd_total else "") + "</b>\n"]
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

    lines = [f"📅 <b>Gastos de la semana</b> — {_separate_totals(expenses)}\n"]
    for r in expenses:
        date_part = fmt_date(r["created_at"])[:5]  # dd/mm
        lines.append(
            f"{r['category_icon']} {date_part}  {r['concept']}  "
            f"<b>{fmt_amount(r['amount'], r['currency'])}</b>  {r['user_name']}"
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

    lines = [f"🗓 <b>Gastos de hoy</b> — {_separate_totals(expenses)}\n"]
    for r in expenses:
        lines.append(
            f"{r['category_icon']} {r['concept']}  "
            f"<b>{fmt_amount(r['amount'], r['currency'])}</b>  {r['user_name']}"
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
    rows = db.get_fixed_payments_for_period(now.year, now.month)

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
            lines.append(f"✅ {r['concept']} — {fmt_amount(r['total_paid'], r['currency'])}")
            # Secondary, less prominent action: a legitimate second payment in the same
            # period (e.g. a utility billing error) must stay reachable even once "paid".
            buttons.append([InlineKeyboardButton(
                f"+ Otro pago: {r['concept']}",
                callback_data=f"fix:direct_pay:{r['id']}",
            )])
        else:
            est = f"~{fmt_amount(r['estimated_amount'], r['currency'])}" if r["estimated_amount"] else "sin estimado"
            lines.append(f"⬜ {r['concept']} — {est}")
            buttons.append([InlineKeyboardButton(
                f"Registrar pago: {r['concept']}",
                callback_data=f"fix:direct_pay:{r['id']}",
            )])
            buttons.append([InlineKeyboardButton(
                f"✓ Ya lo pagué: {r['concept']}",
                callback_data=f"fix:already:{r['id']}",
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
        "🎙️ <b>POR VOZ</b>\n"
        "   Mandá un audio: \"gasté 30 mil en la verdulería\".\n"
        "   Si es claro, se registra solo; si no, te pido confirmar.\n"
        "\n"
        "💵 <b>DÓLARES (compra/venta)</b>\n"
        "   Escribí o mandá audio en lenguaje natural:\n"
        "   \"vendí 500 dólares a 1700\"\n"
        "   \"compré 1000 dólares a 1550 cada uno\"\n"
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
        "   <code>/editar ID fecha 15/06/2026</code>\n"
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


def _find_fixed_expense(name: str):
    """Find an ACTIVE fixed expense by concept, ignoring accents/case. Used to resolve the
    NL edit flow's 'fixed_expense' change — no fuzzy NL creation of new fixed expenses,
    that stays a dashboard-only action."""
    target = categorizer.normalize((name or "").strip())
    if not target:
        return None
    for fe in db.get_all_fixed_expenses():
        if categorizer.normalize(fe["concept"]) == target:
            return fe
    return None


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
            f"<b>{fmt_amount(r['amount'], r['currency'])}</b>  <i>({date_part})</i>"
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
            "  <code>/editar ID moneda USD</code>\n"
            "  <code>/editar ID categoria Vehiculos</code>\n"
            "  <code>/editar ID fecha 15/06/2026</code>",
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
            f"✅ Gasto <code>#{expense_id}</code> actualizado — nuevo monto: "
            f"<b>{fmt_amount(amount, expense['currency'])}</b>",
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

    elif campo == "fecha":
        date_str = _parse_fecha_ddmmyyyy(valor)
        if date_str is None:
            await update.message.reply_text(
                "❌ Fecha inválida. Formato: <code>DD/MM/AAAA</code>", parse_mode="HTML"
            )
            return
        db.update_expense_fields(expense_id, user["id"], date_str=date_str)
        await update.message.reply_text(
            f"✅ Gasto <code>#{expense_id}</code> actualizado — nueva fecha: <b>{valor}</b>",
            parse_mode="HTML",
        )

    elif campo in ("moneda", "currency"):
        currency = valor.upper()
        if currency not in db.SUPPORTED_CURRENCIES:
            await update.message.reply_text("❌ Moneda inválida. Usá <code>ARS</code> o <code>USD</code>.", parse_mode="HTML")
            return
        if not db.update_expense_fields(expense_id, user["id"], currency=currency):
            await update.message.reply_text("🔒 No se puede cambiar la moneda de un gasto vinculado a un fijo.", parse_mode="HTML")
            return
        await update.message.reply_text(
            f"✅ Gasto <code>#{expense_id}</code> actualizado — moneda: <b>{currency}</b>", parse_mode="HTML"
        )

    else:
        await update.message.reply_text(
            "❌ Campo inválido. Campos válidos: <code>monto</code>, <code>moneda</code>, <code>categoria</code>, <code>fecha</code>",
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

    if cb.startswith("ocr:currency:"):
        data = pending_ocr.get(chat_id)
        if not data:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        data["currency"] = cb.rsplit(":", 1)[1]
        await query.answer(f"Moneda: {data['currency']}")
        return

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
                currency=ocr_data.get("currency", "ARS"),
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
            f"💰 {fmt_amount(ocr_data['monto'], ocr_data.get('currency', 'ARS'))}\n"
            f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await _maybe_offer_fixed_link(chat_id, context.bot, expense_id, concept, ocr_data.get("currency", "ARS"))
        return

    elif cb == "ocr:cancel":
        pending_ocr.pop(chat_id, None)
        await query.edit_message_text("❌ Carga cancelada.")
        return

    if cb == "voice:confirm":
        state = pending_voice.get(chat_id)
        if not state or not state.get("current"):
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        item = state.pop("current")
        cat = db.get_category_by_id(item["category_id"]) if item["category_id"] else None
        cat_name = cat["name"] if cat else "Sin categoría"
        cat_icon = cat["icon"] if cat else "❓"
        try:
            expense_id = db.create_expense(
                user_id=user["id"],
                category_id=item["category_id"],
                subcategory_id=item["subcategory_id"],
                concept=item["concept"],
                amount=item["amount"],
                currency=item.get("currency", "ARS"),
                raw_text=f"[VOZ] {item['transcription']}",
            )
        except Exception as e:
            logger.error("Error guardando gasto de voz: %s", e)
            await query.edit_message_text("⚠️ Error al guardar el gasto. Intentá de nuevo.")
            return
        keyboard = _build_category_keyboard(expense_id) if item["category_id"] is None else _build_edit_only_keyboard(expense_id)
        await query.edit_message_text(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {item['concept']}\n"
            f"💰 {fmt_amount(item['amount'], item.get('currency', 'ARS'))}\n"
            f"{_cat_line(cat_icon, cat_name, item['subcategory_id'])}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await _maybe_offer_fixed_link(
            chat_id, context.bot, expense_id, item["concept"], item.get("currency", "ARS")
        )
        if state["queue"]:
            await _send_next_voice_confirmation(chat_id, context.bot)
        else:
            pending_voice.pop(chat_id, None)
        return

    elif cb == "voice:cancel":
        state = pending_voice.get(chat_id)
        if not state:
            await query.edit_message_text("❌ Gasto salteado.")
            return
        state.pop("current", None)
        await query.edit_message_text("❌ Gasto salteado.")
        if state["queue"]:
            await _send_next_voice_confirmation(chat_id, context.bot)
        else:
            pending_voice.pop(chat_id, None)
        return

    elif cb == "voice:retry":
        audio_bytes = pending_voice_retry.get(chat_id)
        if not audio_bytes:
            await query.answer("⏱ Ya no tengo el audio para reintentar.", show_alert=True)
            return
        await query.edit_message_text("🎙️ Procesando audio...", reply_markup=None)
        await _process_voice_audio(chat_id, context.bot, user, audio_bytes, query.message, context)
        return

    if cb == "dolar:confirm":
        op = pending_dolar.pop(chat_id, None)
        if not op:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        fecha_str = datetime.now(BAIRES).strftime("%Y-%m-%d")
        db.registrar_cambio(fecha_str, op["monto_usd"], op["cotizacion"], user["name"], tipo=op["tipo"])
        titulo = "Venta registrada" if op["tipo"] == "venta" else "Compra registrada"
        await query.edit_message_text(
            f"✅ <b>{titulo}</b>\n{_dolar_summary(op['tipo'], op['monto_usd'], op['cotizacion'])}",
            parse_mode="HTML",
        )
        return

    elif cb == "dolar:cancel":
        pending_dolar.pop(chat_id, None)
        await query.edit_message_text("❌ Operación cancelada.")
        return

    if cb == "nl:cancel":
        pending_nl_confirm.pop(chat_id, None)
        pending_nl_pick.pop(chat_id, None)
        await query.edit_message_text("❌ Cancelado.")
        return

    if cb == "nl:ok":
        data = pending_nl_confirm.pop(chat_id, None)
        if not data:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        await _nl_apply(query, user, data)
        return

    if cb.startswith("nl:pick:"):
        pick = pending_nl_pick.pop(chat_id, None)
        if not pick:
            await query.answer("⏱ Esta confirmación ya expiró.", show_alert=True)
            return
        expense_id = int(cb.split(":")[2])
        if expense_id not in pick["candidates"]:
            await query.answer("⏱ Opción inválida.", show_alert=True)
            return
        expense = db.get_expense_by_id(expense_id)
        if not expense or expense["user_id"] != user["id"]:
            await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
            return
        await query.edit_message_text(f"✏️ Editando <code>#ID{expense_id}</code>…", parse_mode="HTML")
        await _nl_prepare_edit_confirm(chat_id, context.bot, user, expense, pick["changes"])
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
                f"💰 {fmt_amount(expense['amount'], expense['currency'])}\n\n"
                f"¿Querés agregar una subcategoría?",
                parse_mode="HTML",
                reply_markup=_build_subcategory_keyboard(expense_id, subcats),
            )
        else:
            await query.edit_message_text(
                f"✅ <b>Gasto registrado</b>\n"
                f"📋 {expense['concept']}\n"
                f"💰 {fmt_amount(expense['amount'], expense['currency'])}\n"
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
            f"💰 {fmt_amount(expense['amount'], expense['currency'])}\n"
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

    elif cb.startswith("fixlink:"):
        # Post-save "is this the payment for fixed expense X?" offer (_maybe_offer_fixed_link).
        _, expense_id_str, fe_part = cb.split(":")
        expense_id = int(expense_id_str)
        if fe_part == "none":
            await query.edit_message_text("👍 Gasto registrado como normal.")
            return
        expense = db.get_expense_by_id(expense_id)
        if not expense or expense["user_id"] != user["id"]:
            await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
            return
        fe_id = int(fe_part)
        year, month = _expense_period(expense)
        db.link_expense_to_fixed(expense_id, fe_id, year, month)
        fe = db.get_fixed_expense_by_id(fe_id)
        await query.edit_message_text(
            f"✅ Vinculado a tu gasto fijo <b>{fe['concept'] if fe else 'desconocido'}</b>.",
            parse_mode="HTML",
        )

    elif cb.startswith("fix:already:"):
        # "✓ Ya lo pagué" from /fijos: search already-logged, unlinked expenses this period.
        fixed_expense_id = int(cb.split(":")[2])
        fe = db.get_fixed_expense_by_id(fixed_expense_id)
        if not fe:
            await query.answer("Gasto fijo no encontrado.", show_alert=True)
            return
        now = datetime.now(BAIRES)
        unlinked = db.get_unlinked_expenses_for_period(now.year, now.month)
        candidates = fixed_matcher.find_candidate_expenses(fe, unlinked)
        if not candidates:
            pending_fixed_direct[chat_id] = {"fixed_expense_id": fixed_expense_id, "concept": fe["concept"], "currency": fe["currency"]}
            est_str = f" (estimado: {fmt_amount(fe['estimated_amount'], fe['currency'])})" if fe["estimated_amount"] else ""
            await query.message.reply_text(
                f"🔍 No encontré ningún gasto sin vincular que coincida con <b>{fe['concept']}</b>.\n"
                f"💰 ¿Cuánto pagaste?{est_str}\nEnviá el monto:",
                parse_mode="HTML",
            )
            return
        buttons = [
            [InlineKeyboardButton(
                f"{(exp['concept'] or '')[:24]} · {fmt_amount(exp['amount'], exp['currency'])}",
                callback_data=f"fixpick:{fixed_expense_id}:{exp['id']}",
            )]
            for exp in candidates
        ]
        buttons.append([InlineKeyboardButton("❌ Ninguno de estos", callback_data=f"fixpick:{fixed_expense_id}:none")])
        await query.message.reply_text(
            f"🔍 ¿Alguno de estos es el pago de <b>{fe['concept']}</b>?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif cb.startswith("fixpick:"):
        _, fe_id_str, target = cb.split(":")
        fe_id = int(fe_id_str)
        fe = db.get_fixed_expense_by_id(fe_id)
        if target == "none":
            pending_fixed_direct[chat_id] = {"fixed_expense_id": fe_id, "concept": fe["concept"] if fe else "", "currency": fe["currency"] if fe else "ARS"}
            est_str = f" (estimado: {fmt_amount(fe['estimated_amount'], fe['currency'])})" if fe and fe["estimated_amount"] else ""
            await query.edit_message_text(
                f"💰 ¿Cuánto pagaste por <b>{fe['concept'] if fe else 'este gasto fijo'}</b>?{est_str}\nEnviá el monto:",
                parse_mode="HTML",
            )
            return
        expense_id = int(target)
        expense = db.get_expense_by_id(expense_id)
        if not expense or expense["user_id"] != user["id"]:
            await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
            return
        year, month = _expense_period(expense)
        db.link_expense_to_fixed(expense_id, fe_id, year, month)
        await query.edit_message_text(
            f"✅ Vinculado <code>#ID{expense_id}</code> a <b>{fe['concept'] if fe else 'gasto fijo'}</b>.",
            parse_mode="HTML",
        )

    elif cb.startswith("fl:"):
        # Open the fixed-expense picker from an existing expense's edit keyboard.
        expense_id = int(cb.split(":")[1])
        await query.message.reply_text(
            "🔗 ¿A qué gasto fijo vinculamos este gasto?",
            reply_markup=_build_fixed_link_keyboard(expense_id),
        )

    elif cb.startswith("flset:"):
        _, expense_id_str, target = cb.split(":")
        expense_id = int(expense_id_str)
        expense = db.get_expense_by_id(expense_id)
        if not expense or expense["user_id"] != user["id"]:
            await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
            return
        if target == "none":
            db.unlink_expense_from_fixed(expense_id)
            await query.edit_message_text(f"🔓 <code>#ID{expense_id}</code> desvinculado de gastos fijos.", parse_mode="HTML")
            return
        fe_id = int(target)
        year, month = _expense_period(expense)
        db.link_expense_to_fixed(expense_id, fe_id, year, month)
        fe = db.get_fixed_expense_by_id(fe_id)
        await query.edit_message_text(
            f"✅ <code>#ID{expense_id}</code> vinculado a <b>{fe['concept'] if fe else 'gasto fijo'}</b>.",
            parse_mode="HTML",
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
            "currency":         fe["currency"],
        }
        est_str = f" (estimado: {fmt_amount(fe['estimated_amount'], fe['currency'])})" if fe["estimated_amount"] else ""
        await query.message.reply_text(
            f"💰 ¿Cuánto pagaste por <b>{fe['concept']}</b>?{est_str}\nEnviá el monto:",
            parse_mode="HTML",
        )

    elif cb.startswith("ec:"):
        expense_id = int(cb.split(":")[1])
        expense = db.get_expense_by_id(expense_id)
        if not expense or expense["user_id"] != user["id"]:
            await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
            return
        if expense["fixed_expense_id"] is not None:
            await query.edit_message_text("🔒 Este gasto está vinculado a un fijo; su moneda se hereda del fijo.")
            return
        await query.message.reply_text("💱 Elegí la moneda:", reply_markup=_build_currency_keyboard(expense_id))

    elif cb.startswith("ecset:"):
        _, expense_id_str, currency = cb.split(":")
        expense_id = int(expense_id_str)
        if not db.update_expense_fields(expense_id, user["id"], currency=currency):
            await query.edit_message_text("🔒 No pude cambiar la moneda: el gasto puede estar vinculado a un fijo.")
            return
        expense = db.get_expense_by_id(expense_id)
        await query.edit_message_text(
            f"✅ Moneda actualizada: <b>{expense['currency']}</b> — {fmt_amount(expense['amount'], expense['currency'])}",
            parse_mode="HTML",
            reply_markup=_build_edit_only_keyboard(expense_id),
        )

    elif cb.startswith("ea:"):
        expense_id = int(cb.split(":")[1])
        pending_amount_edit[chat_id] = expense_id
        await query.message.reply_text("💰 Enviá el nuevo monto:")


# ── Dólar (compra / venta) ─────────────────────────────────────────────────────

def _dolar_summary(tipo: str, monto_usd: float, cotizacion: float) -> str:
    """Texto del cuerpo de una operación de dólar (sin encabezado)."""
    monto_ars = monto_usd * cotizacion
    verbo = "Vendiste" if tipo == "venta" else "Compraste"
    ars_label = "ARS obtenidos" if tipo == "venta" else "ARS pagados"
    fecha_display = datetime.now(BAIRES).strftime("%d/%m/%Y")
    return (
        f"💵 {verbo}: {fmt_usd(monto_usd)}\n"
        f"💱 Cotización: {fmt_amount(cotizacion)}\n"
        f"💰 {ars_label}: {fmt_amount(monto_ars)}\n"
        f"📅 Fecha: {fecha_display}"
    )


async def _register_dolar_and_announce(chat_id: int, bot, user: dict, op: dict) -> None:
    """Registra una operación de dólar y avisa."""
    fecha_str = datetime.now(BAIRES).strftime("%Y-%m-%d")
    db.registrar_cambio(fecha_str, op["monto_usd"], op["cotizacion"], user["name"], tipo=op["tipo"])
    titulo = "Venta registrada" if op["tipo"] == "venta" else "Compra registrada"
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ <b>{titulo}</b>\n{_dolar_summary(op['tipo'], op['monto_usd'], op['cotizacion'])}",
        parse_mode="HTML",
    )


async def _handle_dolar_operation(chat_id: int, bot, user: dict, op: dict) -> None:
    """Registra directo si la confianza es alta; si no, pide confirmación inline."""
    if op["confidence"] >= AUTOSAVE_CONFIDENCE:
        await _register_dolar_and_announce(chat_id, bot, user, op)
        return

    pending_dolar[chat_id] = {
        "tipo": op["tipo"],
        "monto_usd": op["monto_usd"],
        "cotizacion": op["cotizacion"],
    }
    titulo = "venta" if op["tipo"] == "venta" else "compra"
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"💱 Entendí una <b>{titulo}</b> de dólares:\n"
            f"{_dolar_summary(op['tipo'], op['monto_usd'], op['cotizacion'])}\n\n"
            "¿Registramos?"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, registrar", callback_data="dolar:confirm"),
            InlineKeyboardButton("❌ Cancelar",      callback_data="dolar:cancel"),
        ]]),
    )


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

    await _register_dolar_and_announce(
        update.message.chat_id, context.bot, user,
        {"tipo": "venta", "monto_usd": monto_usd, "cotizacion": cotizacion},
    )


# ── Capa de intención — handlers ──────────────────────────────────────────────

def _nl_yes_no() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Sí", callback_data="nl:ok"),
        InlineKeyboardButton("❌ No", callback_data="nl:cancel"),
    ]])


def _nl_history_get(chat_id: int) -> list[dict]:
    """Historial de la capa de intención para un chat, recortado a los últimos
    NL_HISTORY_TTL_SECONDS segundos y NL_HISTORY_MAX_MESSAGES mensajes — ventana
    deslizante, sin necesidad de un evento explícito de reset."""
    now = time.monotonic()
    entries = [e for e in pending_nl_history.get(chat_id, []) if now - e["ts"] <= NL_HISTORY_TTL_SECONDS]
    entries = entries[-NL_HISTORY_MAX_MESSAGES:]
    pending_nl_history[chat_id] = entries
    return entries


def _nl_history_append(chat_id: int, user_text: str, assistant_text: str) -> None:
    now = time.monotonic()
    entries = _nl_history_get(chat_id)
    entries.append({"role": "user", "content": user_text, "ts": now})
    entries.append({"role": "assistant", "content": assistant_text, "ts": now})
    pending_nl_history[chat_id] = entries[-NL_HISTORY_MAX_MESSAGES:]


def _nl_summarize_result(kind: str, result: dict) -> str:
    """Resumen corto de lo que se entendió/propuso, guardado como turno del
    asistente en el historial — da continuidad sin tener que reconstruir el
    flujo completo de confirmación en cada acceso."""
    if kind == "log":
        return f"Registré {result['concept']} por {fmt_amount(result['amount'], result.get('currency', 'ARS'))}."
    if kind == "income":
        return f"Registré el ingreso {result['concept']} por {fmt_amount(result['amount'], result.get('currency', 'ARS'))}."
    if kind == "shopping_add":
        return f"Agregué {result.get('name', '')} a la lista."
    if kind == "shopping_bought":
        return f"Marqué {result.get('name', '')} como comprado."
    if kind == "shopping_list":
        return "Mostré la lista de compras."
    if kind == "edit":
        n = len(result.get("candidate_ids") or [])
        if n == 1:
            return "Encontré el gasto para editar."
        if n == 0:
            return "No encontré ningún gasto que coincida."
        return f"Encontré {n} gastos candidatos para editar."
    if kind == "category":
        return f"Propuse crear la categoría {result['name']}."
    if kind == "subcategory":
        return f"Propuse crear la subcategoría {result['name']} en {result['parent']}."
    return result.get("text", "")


async def _handle_intent_message(chat_id: int, bot, user, text: str, anthropic_api_key: str) -> None:
    """Corre la capa de intención en un thread (llamada bloqueante al LLM) y
    despacha el resultado al flujo correspondiente."""
    history = [
        {"role": e["role"], "content": e["content"]} for e in _nl_history_get(chat_id)
    ]
    try:
        result = await asyncio.to_thread(
            intent_module.route_intent, text, dict(user), anthropic_api_key, history
        )
    except Exception as e:
        logger.error("Error en capa de intención: %s", e)
        await bot.send_message(chat_id=chat_id, text="⚠️ Hubo un error interpretando el mensaje. Probá de nuevo.")
        return

    kind = result.get("kind")
    _nl_history_append(chat_id, text, _nl_summarize_result(kind, result))

    if kind == "log":
        await _nl_do_log(chat_id, bot, user, text, result)
    elif kind == "income":
        await _nl_do_income(chat_id, bot, user, result)
    elif kind == "shopping_add":
        await _nl_shopping_add(chat_id, bot, user, result)
    elif kind == "shopping_bought":
        await _nl_shopping_bought(chat_id, bot, user, result)
    elif kind == "shopping_list":
        await _nl_shopping_list(chat_id, bot)
    elif kind == "edit":
        await _nl_start_edit(chat_id, bot, user, result)
    elif kind == "category":
        await _nl_propose_category(chat_id, bot, result["name"])
    elif kind == "subcategory":
        await _nl_propose_subcategory(chat_id, bot, result["parent"], result["name"])
    elif kind in ("report", "reply", "error"):
        # Texto libre redactado por el modelo (o error): plano, sin parse_mode, para
        # no romper con caracteres especiales.
        await bot.send_message(chat_id=chat_id, text=result.get("text", "🤔 No te entendí."))
    else:
        await bot.send_message(chat_id=chat_id, text="🤔 No te entendí. ¿Podés reformularlo?")


def _resolve_log_category(concept: str, cat_name: str | None, subcat_name: str | None):
    """Devuelve (category_id, subcategory_id). Si el modelo indicó una categoría
    existente, la usa; si no, cae al categorizador por keywords."""
    category_id, subcategory_id = None, None
    if cat_name:
        cat = db.find_category_normalized(cat_name)
        if cat:
            category_id = cat["id"]
            if subcat_name:
                sub = db.find_subcategory_normalized(category_id, subcat_name)
                if sub:
                    subcategory_id = sub["id"]
    if category_id is None:
        keywords = db.get_all_keywords()
        category_id, subcategory_id = categorizer.categorize(concept, keywords)
    return category_id, subcategory_id


def _resolve_income_category(concept: str, category_name: str | None = None):
    if category_name:
        category = db.find_income_category_normalized(category_name)
        if category:
            return category["id"]
    normalized = categorizer.normalize(concept)
    for category in db.get_income_categories():
        if categorizer.normalize(category["name"]).split(" / ")[0] in normalized:
            return category["id"]
    other = db.find_income_category_normalized("Otros")
    return other["id"] if other else None


async def _nl_do_income(chat_id: int, bot, user, result: dict) -> None:
    date_str = result.get("date") or datetime.now(BAIRES).strftime("%Y-%m-%d")
    category_id = _resolve_income_category(result["concept"], result.get("category"))
    income_id = db.create_income(
        user["id"], result["concept"], result["amount"],
        result.get("currency", "ARS"), date_str, category_id,
    )
    category = db.get_income_category(category_id) if category_id else None
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ <b>Ingreso registrado</b>\n"
            f"📋 {result['concept']}\n"
            f"💰 {fmt_amount(result['amount'], result.get('currency', 'ARS'))}\n"
            f"{category['icon'] if category else '💵'} {category['name'] if category else 'Sin categoría'}\n"
            f"👤 {user['name']}\n<code>#ING{income_id}</code>"
        ),
        parse_mode="HTML",
    )


async def _nl_shopping_add(chat_id: int, bot, user, result: dict) -> None:
    name = (result.get("name") or "").strip()
    if not name:
        await bot.send_message(chat_id=chat_id, text="🤔 ¿Qué agrego a la lista?")
        return
    category_id, _ = categorizer.categorize(name, db.get_all_keywords())
    _, created = db.add_shopping_item(user["id"], name, result.get("quantity"), category_id)
    text = ("🛒 Agregué" if created else "🛒 Ya estaba pendiente") + f": <b>{name}</b>"
    if result.get("quantity"):
        text += f" · {result['quantity']}"
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def _nl_shopping_bought(chat_id: int, bot, user, result: dict) -> None:
    name = (result.get("name") or "").strip()
    item = db.mark_shopping_item_bought_by_name(name, user["id"]) if name else None
    text = (f"✅ Marqué <b>{item['name']}</b> como comprado." if item else
            f"🤔 No encontré <b>{name}</b> entre los pendientes.")
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def _nl_shopping_list(chat_id: int, bot) -> None:
    items = db.get_shopping_items(False)
    if not items:
        await bot.send_message(chat_id=chat_id, text="✅ La lista está vacía.")
        return
    groups = {}
    for item in items:
        groups.setdefault(item["category_name"] or "Sin categoría", []).append(item)
    lines = ["🛒 <b>Lista de compras</b>"]
    for category_name, category_items in groups.items():
        lines.append(f"\n{category_items[0]['category_icon'] or '❓'} <b>{category_name}</b>")
        lines.extend(
            f"• {item['name']}" + (f" — {item['quantity']}" if item["quantity"] else "")
            for item in category_items
        )
    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")


async def _nl_do_log(chat_id: int, bot, user, text: str, result: dict) -> None:
    """Registra un gasto interpretado en lenguaje natural (auto-guardado, con
    teclado de editar/categoría — nada queda irreversible)."""
    concept = result["concept"]
    amount = result["amount"]
    currency = result.get("currency", "ARS")
    date_str = result.get("date")
    category_id, subcategory_id = _resolve_log_category(concept, result.get("category"), result.get("subcategory"))
    try:
        if date_str:
            expense_id = db.create_expense_full(
                user["id"], category_id, concept, amount, date_str, subcategory_id=subcategory_id, currency=currency
            )
        else:
            expense_id = db.create_expense(
                user_id=user["id"], category_id=category_id, subcategory_id=subcategory_id,
                concept=concept, amount=amount, currency=currency, raw_text=f"[NL] {text}",
            )
    except Exception as e:
        logger.error("Error guardando gasto NL: %s", e)
        await bot.send_message(chat_id=chat_id, text="⚠️ Hubo un error al guardar el gasto. Intentá de nuevo.")
        return

    cat = db.get_category_by_id(category_id) if category_id else None
    cat_name = cat["name"] if cat else "Sin categoría"
    cat_icon = cat["icon"] if cat else "❓"
    keyboard = _build_category_keyboard(expense_id) if category_id is None else _build_edit_only_keyboard(expense_id)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ <b>Gasto registrado</b>\n"
            f"📋 {concept}\n"
            f"💰 {fmt_amount(amount, currency)}\n"
            f"{_cat_line(cat_icon, cat_name, subcategory_id)}\n"
            f"👤 {user['name']}\n"
            f"<code>#ID{expense_id}</code>"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await _maybe_offer_fixed_link(chat_id, bot, expense_id, concept, currency)


async def _nl_start_edit(chat_id: int, bot, user, result: dict) -> None:
    """Resuelve los candidatos de una edición (filtrando por dueño) y decide entre
    confirmar (1), pedir aclaración (0) o mostrar un selector (varios)."""
    changes = result.get("changes") or {}
    candidate_ids = result.get("candidate_ids") or []

    owned = []
    for eid in candidate_ids:
        exp = db.get_expense_by_id(eid)
        if exp and exp["user_id"] == user["id"]:
            owned.append(exp)

    if not owned:
        await bot.send_message(chat_id=chat_id, text="🤔 No encontré un gasto tuyo que coincida. ¿Podés ser más específico?")
        return

    if len(owned) == 1:
        await _nl_prepare_edit_confirm(chat_id, bot, user, owned[0], changes)
        return

    pending_nl_pick[chat_id] = {"changes": changes, "candidates": [e["id"] for e in owned]}
    buttons = []
    for e in owned[:10]:
        concept_short = (e["concept"] or "")[:24]
        buttons.append([InlineKeyboardButton(
            f"#{e['id']} · {concept_short} · {fmt_amount(e['amount'], e['currency'])}",
            callback_data=f"nl:pick:{e['id']}",
        )])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="nl:cancel")])
    await bot.send_message(
        chat_id=chat_id,
        text="Encontré varios gastos. ¿Cuál querés editar?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def _is_unlink_token(fe_name: str) -> bool:
    return fe_name.strip().lower() in ("ninguno", "ninguna", "none")


def _describe_changes(expense, changes: dict) -> list[str]:
    """Líneas de preview de una edición propuesta."""
    lines = []
    if "amount" in changes:
        target_currency = changes.get("currency", expense["currency"])
        lines.append(f"💰 Monto → {fmt_amount(changes['amount'], target_currency)}")
    if "currency" in changes:
        lines.append(f"💱 Moneda → {changes['currency']}")
    if "concept" in changes:
        lines.append(f"📋 Concepto → {changes['concept']}")
    if "date" in changes:
        lines.append(f"📅 Fecha → {_fmt_fecha_ddmmyyyy(changes['date'])}")
    cat_name = changes.get("category")
    if cat_name:
        cat = db.find_category_normalized(cat_name)
        if cat:
            lines.append(f"🏷 Categoría → {cat['icon']} {cat['name']}")
        else:
            lines.append(f"🏷 Categoría → {cat_name} (nueva, se va a crear)")
    if changes.get("subcategory"):
        lines.append(f"↳ Subcategoría → {changes['subcategory']}")
    fe_name = changes.get("fixed_expense")
    if fe_name:
        if _is_unlink_token(fe_name):
            lines.append("🔗 Gasto fijo → (desvincular)")
        else:
            fe = _find_fixed_expense(fe_name)
            lines.append(f"🔗 Gasto fijo → {fe['concept'] if fe else fe_name + ' (no encontrado)'}")
    return lines


async def _nl_prepare_edit_confirm(chat_id: int, bot, user, expense, changes: dict) -> None:
    change_lines = _describe_changes(expense, changes)
    if not change_lines:
        await bot.send_message(chat_id=chat_id, text="🤔 No entendí qué querés cambiar del gasto.")
        return
    pending_nl_confirm[chat_id] = {"kind": "edit", "expense_id": expense["id"], "changes": changes}
    body = (
        f"✏️ <b>Voy a editar este gasto</b>\n"
        f"<code>#ID{expense['id']}</code> — {expense['concept']} "
        f"({fmt_amount(expense['amount'], expense['currency'])})\n\n"
        + "\n".join(change_lines)
        + "\n\n¿Confirmás?"
    )
    await bot.send_message(chat_id=chat_id, text=body, parse_mode="HTML", reply_markup=_nl_yes_no())


async def _nl_propose_category(chat_id: int, bot, name: str) -> None:
    existing = db.find_category_normalized(name)
    if existing:
        await bot.send_message(chat_id=chat_id, text=f"ℹ️ La categoría <b>{existing['name']}</b> ya existe.", parse_mode="HTML")
        return
    pending_nl_confirm[chat_id] = {"kind": "category", "name": name}
    await bot.send_message(chat_id=chat_id, text=f"🏷 ¿Creo la categoría <b>{name}</b>?", parse_mode="HTML", reply_markup=_nl_yes_no())


async def _nl_propose_subcategory(chat_id: int, bot, parent: str, name: str) -> None:
    cat = db.find_category_normalized(parent)
    if not cat:
        await bot.send_message(chat_id=chat_id, text=f"🤔 No existe la categoría <b>{parent}</b>. Creála primero.", parse_mode="HTML")
        return
    existing = db.find_subcategory_normalized(cat["id"], name)
    if existing:
        await bot.send_message(chat_id=chat_id, text=f"ℹ️ La subcategoría <b>{existing['name']}</b> ya existe en {cat['name']}.", parse_mode="HTML")
        return
    pending_nl_confirm[chat_id] = {"kind": "subcategory", "category_id": cat["id"], "category_name": cat["name"], "name": name}
    await bot.send_message(
        chat_id=chat_id,
        text=f"🏷 ¿Creo la subcategoría <b>{name}</b> en <b>{cat['name']}</b>?",
        parse_mode="HTML", reply_markup=_nl_yes_no(),
    )


async def _nl_apply(query, user, data: dict) -> None:
    """Aplica una mutación NL confirmada (edición o taxonomía)."""
    kind = data.get("kind")
    if kind == "category":
        cat_id = db.create_category(data["name"], "💰", "#6366f1")
        if cat_id is None:
            await query.edit_message_text(f"ℹ️ La categoría <b>{data['name']}</b> ya existía.", parse_mode="HTML")
        else:
            await query.edit_message_text(f"✅ Categoría <b>{data['name']}</b> creada.", parse_mode="HTML")
        return

    if kind == "subcategory":
        if db.find_subcategory_normalized(data["category_id"], data["name"]):
            await query.edit_message_text(f"ℹ️ La subcategoría <b>{data['name']}</b> ya existía.", parse_mode="HTML")
            return
        db.add_subcategory(data["category_id"], data["name"])
        await query.edit_message_text(
            f"✅ Subcategoría <b>{data['name']}</b> creada en <b>{data['category_name']}</b>.", parse_mode="HTML"
        )
        return

    if kind == "edit":
        await _nl_apply_edit(query, user, data["expense_id"], data["changes"])
        return


async def _nl_apply_edit(query, user, expense_id: int, changes: dict) -> None:
    # Re-chequeo de ownership antes de cualquier UPDATE.
    expense = db.get_expense_by_id(expense_id)
    if not expense or expense["user_id"] != user["id"]:
        await query.edit_message_text("🤔 No encontré ese gasto (o no es tuyo).")
        return

    fields: dict = {}
    if "amount" in changes:
        fields["amount"] = changes["amount"]
    if "concept" in changes:
        fields["concept"] = changes["concept"]
    if "date" in changes:
        fields["date_str"] = changes["date"]
    if "currency" in changes:
        if expense["fixed_expense_id"] is not None:
            await query.edit_message_text("🔒 No se puede cambiar la moneda de un gasto vinculado a un fijo.")
            return
        fields["currency"] = changes["currency"]

    target_cat_id = expense["category_id"]
    cat_name = changes.get("category")
    if cat_name:
        cat = db.find_category_normalized(cat_name)
        if cat is None:
            new_id = db.create_category(cat_name, "💰", "#6366f1")
            cat = db.get_category_by_id(new_id) if new_id else db.find_category_normalized(cat_name)
        if cat:
            fields["category_id"] = cat["id"]
            target_cat_id = cat["id"]

    sub_name = changes.get("subcategory")
    if sub_name and target_cat_id:
        sub = db.find_subcategory_normalized(target_cat_id, sub_name)
        if sub:
            fields["subcategory_id"] = sub["id"]
        else:
            fields["subcategory_id"] = db.add_subcategory(target_cat_id, sub_name)

    changed_anything = False
    if fields:
        changed_anything = db.update_expense_fields(expense_id, user["id"], **fields)

    fe_name = changes.get("fixed_expense")
    if fe_name:
        if _is_unlink_token(fe_name):
            db.unlink_expense_from_fixed(expense_id)
            changed_anything = True
        else:
            fe = _find_fixed_expense(fe_name)
            if fe:
                current = db.get_expense_by_id(expense_id)
                if current["currency"] != fe["currency"]:
                    await query.edit_message_text(
                        "🔒 No se puede vincular: el gasto y el fijo tienen distinta moneda."
                    )
                    return
                year, month = _expense_period(current)
                changed_anything = db.link_expense_to_fixed(
                    expense_id, fe["id"], year, month
                ) or changed_anything

    if not changed_anything:
        await query.edit_message_text("⚠️ No pude editar el gasto.")
        return

    updated = db.get_expense_by_id(expense_id)
    cat = db.get_category_by_id(updated["category_id"]) if updated["category_id"] else None
    cat_name_d = cat["name"] if cat else "Sin categoría"
    cat_icon = cat["icon"] if cat else "❓"
    await query.edit_message_text(
        f"✅ <b>Gasto actualizado</b>\n"
        f"📋 {updated['concept']}\n"
        f"💰 {fmt_amount(updated['amount'], updated['currency'])}\n"
        f"{_cat_line(cat_icon, cat_name_d, updated['subcategory_id'])}\n"
        f"👤 {user['name']}\n"
        f"<code>#ID{expense_id}</code>",
        parse_mode="HTML",
        reply_markup=_build_edit_only_keyboard(expense_id),
    )


# ── Arranque ──────────────────────────────────────────────────────────────────

def build_app():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()

    app.add_handler(MessageHandler(filters.ChatType.GROUPS, reject_group), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
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
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(handle_bot_error)

    return app
