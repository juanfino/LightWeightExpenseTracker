import os
import secrets
import base64
import io
import traceback
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, g, got_request_exception, render_template, request, jsonify, redirect, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import qrcode

import db
import auth
import backup as backup_module
import fixed_matcher
import categorizer
import report
import pgcompat
import llm_limits
import export_data

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("AUTH_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_NAME="gastos_oauth_state",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),
)
oauth = OAuth(app)
TURNSTILE_SITE_KEY = "0x4AAAAAAD_DcrwEDapvydY6"
if os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
    oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "email profile"},
    )

BAIRES = timezone(timedelta(hours=-3))


@got_request_exception.connect_via(app)
def _report_web_exception(_sender, exception, **_extra):
    current_user = getattr(g, "current_user", None) or {}
    user_id = current_user.get("id")
    family_id = current_user.get("family_id")
    details = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    app.logger.error(
        "Excepción web no manejada family_id=%s user_id=%s\n%s",
        family_id, user_id, details,
    )
    db.record_system_error("web", exception, details)


@app.before_request
def _resolve_web_identity():
    pgcompat.select_pool("web")
    pgcompat.set_user_id(None)
    pgcompat.set_family_id(None)
    g.current_user = auth.resolve_session(request.cookies.get(auth.SESSION_COOKIE))
    if g.current_user:
        pgcompat.set_user_id(g.current_user["id"])
        pgcompat.set_family_id(g.current_user["family_id"])

    public = {
        "landing", "login", "register", "verify_otp", "google_start",
        "google_callback", "family_join", "privacy", "terms", "static",
    }
    if request.endpoint not in public and not g.current_user:
        if request.path.startswith("/api/") or request.path.startswith("/admin/"):
            return jsonify({"error": "Autenticación requerida"}), 401
        return redirect(url_for("login", next=request.full_path.rstrip("?")))

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        expected = (
            g.current_user["csrf_token"] if g.current_user
            else session.get("preauth_csrf")
        )
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            if request.path.startswith("/api/") or request.path.startswith("/admin/"):
                return jsonify({"error": "Token CSRF inválido"}), 403
            abort(403)


@app.context_processor
def _auth_template_context():
    if "preauth_csrf" not in session:
        session["preauth_csrf"] = secrets.token_urlsafe(32)
    return {
        "current_user": getattr(g, "current_user", None),
        "csrf_token": (
            g.current_user["csrf_token"]
            if getattr(g, "current_user", None)
            else session["preauth_csrf"]
        ),
        "turnstile_site_key": TURNSTILE_SITE_KEY,
        "shopping_pending_count": (
            db.shopping_pending_count() if getattr(g, "current_user", None) else 0
        ),
    }


@app.after_request
def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com https://cdn.jsdelivr.net; font-src 'self' "
        "https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; "
        "connect-src 'self' https://challenges.cloudflare.com; frame-src "
        "https://challenges.cloudflare.com; frame-ancestors 'none'; base-uri 'self'",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.endpoint in {"login", "register", "verify_otp", "google_callback"}:
        response.headers["Cache-Control"] = "no-store"
    return response

MONTHS_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def _month_label(year: int, month: int) -> str:
    return f"{MONTHS_ES[month]} {year}"


def _to_baires_str(value) -> str:
    """Serialize a PostgreSQL/legacy UTC timestamp for dashboard JavaScript.

    psycopg returns ``datetime`` objects. Passing those directly to ``jsonify``
    produces an RFC 1123 string, while the existing frontend contract is
    ``YYYY-MM-DD HH:MM:SS``.
    """
    if not value:
        return value
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_ba = dt.astimezone(BAIRES)
        return dt_ba.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(value)


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("created_at"):
        d["created_at"] = _to_baires_str(d["created_at"])
    return d


def _currency_arg() -> str:
    """Validate dashboard currency input; ARS remains the default surface."""
    body = request.get_json(silent=True) or {}
    return db.normalize_currency(request.args.get("currency") or body.get("currency") or "ARS")


def _require_superadmin():
    if not g.current_user.get("is_superadmin"):
        abort(403)


# ── Páginas ───────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if g.current_user:
        return redirect(url_for("index"))
    return render_template("landing.html")


@app.route("/dashboard")
def index():
    now = datetime.now()
    family_data = auth.get_family_management(g.current_user["family_id"])
    invitations = (
        auth.list_family_invitations(g.current_user["family_id"])
        if g.current_user["role"] == "owner" else []
    )
    onboarding = {
        "has_expense": db.has_expenses(),
        "telegram_connected": auth.telegram_link_status(g.current_user["id"]),
        "family_invited": (
            sum(1 for member in family_data["members"] if member["active"]) > 1
            or bool(invitations)
        ),
    }
    onboarding["complete"] = all(onboarding.values())
    return render_template(
        "index.html", year=now.year, month=now.month,
        month_label=_month_label(now.year, now.month), onboarding=onboarding,
    )


@app.route("/superadmin")
def superadmin():
    _require_superadmin()
    data = db.get_superadmin_dashboard()
    data["llm_daily"] = [
        {"day": row["day"].isoformat(), "calls": row["calls"], "cost": float(row["cost"])}
        for row in data["llm_daily"]
    ]
    totals = {
        "families": len(data["families"]),
        "users": sum(row["users"] for row in data["families"]),
        "active_30d": sum(row["active_30d"] for row in data["families"]),
        "expenses": sum(row["expenses"] for row in data["families"]),
        "llm_cost_30d": sum(row["cost"] for row in data["llm_by_family"]),
    }
    return render_template("superadmin.html", data=data, totals=totals)


@app.route("/admin/families/<int:family_id>/quotas", methods=["POST"])
def admin_family_quotas(family_id):
    _require_superadmin()
    body = request.get_json(silent=True) or {}

    def optional_positive(name):
        raw = body.get(name)
        if raw in (None, ""):
            return None
        value = int(raw)
        if value <= 0 or value > 100000:
            raise ValueError
        return value

    try:
        routine = optional_positive("routine_daily_limit")
        summaries = optional_positive("summary_monthly_limit")
    except (TypeError, ValueError):
        return jsonify({"error": "Los límites deben ser enteros positivos."}), 400
    if not db.set_family_quota_override(family_id, routine, summaries):
        return jsonify({"error": "Familia no encontrada."}), 404
    return jsonify({"ok": True})


@app.route("/admin/costs/<provider>", methods=["POST"])
def admin_cost(provider):
    _require_superadmin()
    body = request.get_json(silent=True) or {}
    try:
        rate = Decimal(str(body.get("unit_rate_usd", "")))
        volume = Decimal(str(body.get("monthly_volume", "")))
        if not rate.is_finite() or not volume.is_finite() or rate < 0 or volume < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return jsonify({"error": "Tarifa y volumen deben ser números no negativos."}), 400
    unit_label = str(body.get("unit_label", "")).strip()
    if not unit_label:
        return jsonify({"error": "Indicá la unidad medida."}), 400
    updated = db.update_infrastructure_cost(
        provider,
        unit_label[:100],
        rate,
        volume,
        str(body.get("notes", "")).strip()[:500] or None,
    )
    if not updated:
        return jsonify({"error": "Proveedor desconocido."}), 404
    return jsonify({"ok": True})


def _client_ip() -> str:
    return (request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown").strip()


def _safe_next(target: str | None) -> str:
    if target:
        base = urlparse(request.host_url)
        candidate = urlparse(urljoin(request.host_url, target))
        if candidate.scheme in {"http", "https"} and candidate.netloc == base.netloc:
            return candidate.path + (f"?{candidate.query}" if candidate.query else "")
    return url_for("index")


def _set_auth_cookie(response, token: str):
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 86400,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        login_user = None
        email = auth.normalize_email(request.form.get("email", ""))
        ip = _client_ip()
        if not auth.consume_rate_limit(f"otp-ip:{ip}", limit=10, window_seconds=3600):
            error = "Demasiados intentos. Probá de nuevo más tarde."
        elif not auth.consume_rate_limit(f"otp-email:{email}", limit=5, window_seconds=3600):
            error = "Demasiados códigos pedidos para ese email. Probá más tarde."
        elif not auth.verify_turnstile(request.form.get("cf-turnstile-response"), ip):
            error = "No pudimos verificar que seas una persona. Intentá de nuevo."
        else:
            login_user = auth.find_user_by_email(email)
        if error:
            pass
        elif not login_user:
            error = "No existe una cuenta con ese email. Podés crear una ahora."
        elif not auth.user_has_active_membership(login_user["id"]):
            error = "Ya no pertenecés a una familia. Podés crear una nueva desde Registro."
        else:
            try:
                code = auth.issue_otp(email, flow="login")
                auth.send_otp(email, code)
                return render_template("verify_otp.html", email=email, next=_safe_next(request.args.get("next")))
            except Exception:
                app.logger.exception("No se pudo enviar OTP de login")
                error = "No pudimos enviar el código. Intentá de nuevo."
    return render_template("login.html", error=error, next=_safe_next(request.args.get("next")))


@app.route("/registro", methods=["GET", "POST"])
def register():
    if g.current_user:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        existing_user = None
        email = auth.normalize_email(request.form.get("email", ""))
        name = request.form.get("name", "").strip()
        family_name = request.form.get("family_name", "").strip()
        ip = _client_ip()
        if not email or not name or not family_name:
            error = "Completá todos los campos."
        else:
            existing_user = auth.find_user_by_email(email)
        if error:
            pass
        elif existing_user and auth.user_has_active_membership(existing_user["id"]):
            error = "Ya existe una cuenta activa con ese email. Iniciá sesión."
        elif not auth.consume_rate_limit(f"register-ip:{ip}", limit=5, window_seconds=3600):
            error = "Demasiados intentos. Probá de nuevo más tarde."
        elif not auth.consume_rate_limit(f"register-email:{email}", limit=3, window_seconds=3600):
            error = "Demasiados intentos para ese email. Probá más tarde."
        elif not auth.verify_turnstile(request.form.get("cf-turnstile-response"), ip):
            error = "No pudimos verificar que seas una persona. Intentá de nuevo."
        else:
            try:
                code = auth.issue_otp(email, flow="register", name=name, family_name=family_name)
                auth.send_otp(email, code)
                return render_template("verify_otp.html", email=email, next=url_for("index"))
            except Exception:
                app.logger.exception("No se pudo enviar OTP de registro")
                error = "No pudimos enviar el código. Intentá de nuevo."
    return render_template("register.html", error=error)


@app.route("/auth/otp/verify", methods=["POST"])
def verify_otp():
    email = auth.normalize_email(request.form.get("email", ""))
    result = auth.consume_otp(email, request.form.get("code", ""))
    if not result:
        return render_template(
            "verify_otp.html", email=email, next=_safe_next(request.form.get("next")),
            error="El código es inválido, venció o superó los 5 intentos.",
        ), 400
    try:
        if result["flow"] == "register":
            existing_user = auth.find_user_by_email(result["email"])
            if existing_user:
                user_id = existing_user["id"]
                auth.create_family_for_existing_user(
                    user_id, result["family_name"] or ""
                )
            else:
                user_id = auth.create_account(
                    result["email"], result["name"] or "", result["family_name"] or ""
                )
        elif result["flow"] == "invite":
            user = auth.find_user_by_email(result["email"])
            user_id = (
                user["id"] if user
                else auth.create_user_without_family(result["email"], result["name"] or "")
            )
            auth.accept_invitation(result["invitation_id"], user_id)
        else:
            user = auth.find_user_by_email(result["email"])
            if not user:
                raise ValueError("La cuenta ya no existe")
            user_id = user["id"]
        token, _csrf = auth.create_session(user_id, request.user_agent.string, _client_ip())
    except auth.MembershipConflict as exc:
        invitation = auth.get_invitation(invitation_id=result.get("invitation_id"))
        return render_template(
            "join_family.html", invitation=invitation, error=str(exc)
        ), 409
    except Exception:
        app.logger.exception("No se pudo completar autenticación OTP")
        return render_template(
            "verify_otp.html", email=email, next=url_for("index"),
            error="No pudimos completar el acceso. Intentá de nuevo.",
        ), 400
    return _set_auth_cookie(redirect(_safe_next(request.form.get("next"))), token)


@app.route("/auth/google", methods=["GET", "POST"])
def google_start():
    if not getattr(oauth, "google", None):
        return redirect(url_for("login", error="google_unavailable"))
    flow = request.values.get("flow", "login")
    if flow == "register":
        ip = _client_ip()
        family_name = request.form.get("family_name", "").strip()
        if request.method != "POST" or not family_name:
            return redirect(url_for("register"))
        if not auth.consume_rate_limit(f"register-google-ip:{ip}", limit=5, window_seconds=3600):
            return render_template("register.html", error="Demasiados intentos. Probá más tarde."), 429
        if not auth.verify_turnstile(request.form.get("cf-turnstile-response"), ip):
            return render_template("register.html", error="No pudimos verificar que seas una persona."), 400
        session["google_family_name"] = family_name
    elif flow == "invite":
        invitation = auth.get_invitation(request.form.get("invitation_token", ""))
        if request.method != "POST" or not invitation or not invitation["valid"]:
            return render_template(
                "join_family.html", invitation=invitation,
                error="La invitación no es válida o ya venció.",
            ), 400
        session["google_invitation_id"] = invitation["id"]
    session["google_flow"] = flow
    session["google_next"] = _safe_next(request.values.get("next"))
    return oauth.google.authorize_redirect(
        url_for("google_callback", _external=True),
        prompt="select_account",
    )


@app.route("/auth/google/callback")
def google_callback():
    if not getattr(oauth, "google", None):
        abort(404)
    token_data = oauth.google.authorize_access_token()
    profile = token_data.get("userinfo")
    if not profile:
        profile = oauth.google.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            token=token_data,
        ).json()
    email = auth.normalize_email(profile.get("email", ""))
    if not email or str(profile.get("email_verified")).lower() not in {"true", "1"}:
        return render_template("login.html", error="Google no confirmó el email de la cuenta."), 400
    flow = session.get("google_flow")
    existing = auth.find_user_by_email(email)
    if not existing and flow not in {"register", "invite"}:
        return render_template("login.html", error="No existe una cuenta con ese email. Registrate primero."), 400
    if flow == "invite":
        user_id = auth.link_google_identity_for_invite(
            str(profile["sub"]), email, profile.get("name") or email.split("@")[0]
        )
        invitation_id = session.get("google_invitation_id")
        try:
            auth.accept_invitation(invitation_id, user_id)
        except (auth.InvitationError, auth.MembershipConflict) as exc:
            invitation = auth.get_invitation(invitation_id=invitation_id)
            return render_template(
                "join_family.html", invitation=invitation, error=str(exc)
            ), 409
    else:
        user_id = auth.link_google_identity(
            str(profile["sub"]), email, profile.get("name") or email.split("@")[0],
            session.get("google_family_name") or None,
        )
    token, _csrf = auth.create_session(user_id, request.user_agent.string, _client_ip())
    destination = session.pop("google_next", url_for("index"))
    session.pop("google_flow", None)
    session.pop("google_family_name", None)
    session.pop("google_invitation_id", None)
    return _set_auth_cookie(redirect(destination), token)


@app.route("/logout", methods=["POST"])
def logout():
    auth.revoke_session(request.cookies.get(auth.SESSION_COOKIE))
    response = redirect(url_for("landing"))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.route("/privacidad")
@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terminos")
@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/unirme/<token>", methods=["GET", "POST"])
def family_join(token):
    invitation = auth.get_invitation(token)
    if not invitation or not invitation["valid"]:
        return render_template(
            "join_family.html", invitation=invitation,
            error="La invitación no es válida, fue revocada o ya venció.",
        ), 410
    if g.current_user:
        return render_template(
            "join_family.html", invitation=invitation,
            error=(
                f"Ya pertenecés a {g.current_user['family_name']}. "
                "Tenés que salir de ahí primero."
            ),
        ), 409
    error = None
    if request.method == "POST":
        email = auth.normalize_email(request.form.get("email", ""))
        name = request.form.get("name", "").strip()
        ip = _client_ip()
        if not email or (not name and not auth.find_user_by_email(email)):
            error = "Ingresá tu nombre y email."
        elif not auth.consume_rate_limit(f"invite-ip:{ip}", limit=10, window_seconds=3600):
            error = "Demasiados intentos. Probá de nuevo más tarde."
        elif not auth.consume_rate_limit(f"invite-email:{email}", limit=5, window_seconds=3600):
            error = "Demasiados códigos pedidos para ese email. Probá más tarde."
        else:
            try:
                code = auth.issue_otp(
                    email,
                    flow="invite",
                    name=name or (auth.find_user_by_email(email) or {}).get("name"),
                    invitation_id=invitation["id"],
                )
                auth.send_otp(email, code)
                return render_template(
                    "verify_otp.html", email=email, next=url_for("index")
                )
            except Exception:
                app.logger.exception("No se pudo enviar OTP de invitación")
                error = "No pudimos enviar el código. Intentá de nuevo."
    return render_template(
        "join_family.html", invitation=invitation, invitation_token=token, error=error
    )


def _owner_required():
    if not g.current_user or g.current_user["role"] != "owner":
        abort(403)


@app.route("/familia", methods=["GET", "POST"])
def family():
    error = None
    success = request.args.get("success")
    invitation_url = None
    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action in {
                "invite", "revoke_invite", "rename", "remove",
                "transfer", "delete",
            }:
                _owner_required()
            if action == "invite":
                raw_token, _created = auth.create_invitation(
                    g.current_user["family_id"], g.current_user["id"]
                )
                invitation_url = url_for("family_join", token=raw_token, _external=True)
                success = "Invitación creada. Copiá el enlace ahora."
            elif action == "revoke_invite":
                auth.revoke_invitation(
                    int(request.form["invitation_id"]), g.current_user["family_id"]
                )
                success = "Invitación revocada."
            elif action == "rename":
                auth.rename_family(
                    g.current_user["family_id"], request.form.get("name", "")
                )
                g.current_user["family_name"] = request.form.get("name", "").strip()
                success = "Nombre actualizado."
            elif action == "remove":
                target_id = int(request.form["user_id"])
                if target_id == g.current_user["id"]:
                    raise ValueError("Usá la opción para salir de la familia.")
                auth.remove_family_member(g.current_user["family_id"], target_id)
                success = "Miembro removido. Sus gastos históricos se conservaron."
            elif action == "transfer":
                auth.transfer_family_ownership(
                    g.current_user["family_id"], g.current_user["id"],
                    int(request.form["user_id"]),
                )
                return redirect(url_for("family", success="Propiedad transferida."))
            elif action == "leave":
                auth.leave_family(g.current_user["family_id"], g.current_user["id"])
                response = redirect(url_for("register"))
                response.delete_cookie(auth.SESSION_COOKIE, path="/")
                return response
            elif action == "unlink_telegram":
                auth.unlink_telegram(g.current_user["id"])
                g.current_user["telegram_id"] = None
                success = "Telegram desconectado."
            elif action == "delete":
                auth.delete_family(
                    g.current_user["family_id"], g.current_user["id"],
                    request.form.get("confirmation", ""),
                )
                response = redirect(url_for("landing"))
                response.delete_cookie(auth.SESSION_COOKIE, path="/")
                return response
            else:
                raise ValueError("Acción desconocida.")
        except (ValueError, KeyError) as exc:
            error = str(exc)
    family_data = auth.get_family_management(g.current_user["family_id"])
    invitations = (
        auth.list_family_invitations(g.current_user["family_id"])
        if g.current_user["role"] == "owner" else []
    )
    return render_template(
        "family.html", family=family_data, invitations=invitations,
        invitation_url=invitation_url, error=error, success=success,
    )


@app.route("/vincular-telegram")
def telegram_link():
    if auth.telegram_link_status(g.current_user["id"]):
        return render_template("telegram_link.html", connected=True)
    token = auth.create_telegram_link_token(g.current_user["id"])
    bot_username = os.environ["TELEGRAM_BOT_USERNAME"].lstrip("@")
    deep_link = f"https://t.me/{bot_username}?start={token}"
    qr = qrcode.make(deep_link)
    image = io.BytesIO()
    qr.save(image, format="PNG")
    qr_data = base64.b64encode(image.getvalue()).decode()
    return render_template(
        "telegram_link.html", connected=False, deep_link=deep_link,
        qr_data=qr_data,
    )


@app.route("/api/telegram-link/status")
def api_telegram_link_status():
    return jsonify({"connected": auth.telegram_link_status(g.current_user["id"])})


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

    try:
        week_start = datetime.fromisocalendar(year, week, 1).date()
    except ValueError:
        return jsonify({"error": "Semana ISO inválida"}), 400
    week_end = week_start + timedelta(days=6)
    rows = db.get_expenses_by_week(week_start.isoformat(), week_end.isoformat())
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


@app.route("/admin/backup-now", methods=["POST"])
def admin_backup_now():
    ts = backup_module.create_backup()
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


@app.route("/ingresos")
def incomes_page():
    now = datetime.now()
    return render_template("incomes.html", categories=[dict(r) for r in db.get_income_categories()],
                           year=now.year, month=now.month)


def _shopping_category(name: str):
    category_id, _ = categorizer.categorize(name, db.get_all_keywords())
    return category_id


@app.route("/lista")
def shopping_page():
    return render_template("shopping.html")


@app.route("/exportar")
def export_page():
    return render_template("export.html")


@app.route("/exportar/<dataset>.csv")
def export_csv(dataset: str):
    files = export_data.datasets(g.current_user["timezone"])
    if dataset not in files:
        abort(404)
    return app.response_class(
        files[dataset], mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dataset}.csv"'},
    )


@app.route("/exportar/todo.zip")
def export_zip():
    content = export_data.zip_bytes(export_data.datasets(g.current_user["timezone"]))
    filename = f"mangoteca-export-{datetime.now().date().isoformat()}.zip"
    return app.response_class(
        content, mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/shopping-items", methods=["GET", "POST"])
def api_shopping_items():
    if request.method == "GET":
        return jsonify([_row_to_dict(r) for r in db.get_shopping_items()])
    data = request.get_json(silent=True) or {}
    name, quantity = (data.get("name") or "").strip(), (data.get("quantity") or "").strip() or None
    if not name:
        return jsonify({"ok": False, "error": "Falta el producto"}), 400
    item_id, created = db.add_shopping_item(
        g.current_user["id"], name, quantity, _shopping_category(name)
    )
    return jsonify({"ok": True, "id": item_id, "created": created}), 201 if created else 200


@app.route("/api/shopping-items/<int:item_id>", methods=["PUT"])
def api_shopping_item_update(item_id: int):
    data = request.get_json(silent=True) or {}
    name, quantity = (data.get("name") or "").strip(), (data.get("quantity") or "").strip() or None
    if not name:
        return jsonify({"ok": False, "error": "Falta el producto"}), 400
    ok = db.update_shopping_item(item_id, name, quantity, _shopping_category(name))
    return jsonify({"ok": ok}), 200 if ok else 404


@app.route("/api/shopping-items/<int:item_id>/bought", methods=["POST"])
def api_shopping_item_bought(item_id: int):
    ok = db.mark_shopping_item_bought(item_id, g.current_user["id"])
    return jsonify({"ok": ok}), 200 if ok else 404


@app.route("/api/shopping-items/<int:item_id>/readd", methods=["POST"])
def api_shopping_item_readd(item_id: int):
    ok = db.readd_shopping_item(item_id, g.current_user["id"])
    return jsonify({"ok": ok}), 200 if ok else 404


@app.route("/api/shopping-items/clear-bought", methods=["POST"])
def api_shopping_items_clear():
    return jsonify({"ok": True, "deleted": db.clear_bought_shopping_items()})


@app.route("/api/incomes", methods=["GET", "POST"])
def api_incomes():
    if request.method == "GET":
        try:
            year = int(request.args["year"]) if request.args.get("year") else None
            month = int(request.args["month"]) if request.args.get("month") else None
        except ValueError:
            return jsonify({"error": "Período inválido"}), 400
        return jsonify([_row_to_dict(r) for r in db.get_incomes(year, month)])
    payload = _validated_income_payload(request.get_json(silent=True) or {})
    if payload is None:
        return jsonify({"ok": False, "error": "Datos de ingreso inválidos"}), 400
    return jsonify({"ok": True, "id": db.create_income(g.current_user["id"], *payload)}), 201


def _validated_income_payload(data):
    concept, date_str = (data.get("concept") or "").strip(), (data.get("date") or "").strip()
    try:
        amount = float(data.get("amount"))
        if amount <= 0:
            raise ValueError
        datetime.strptime(date_str, "%Y-%m-%d")
        currency = db.normalize_currency(data.get("currency"))
        category_id = int(data["income_category_id"]) if data.get("income_category_id") else None
    except (TypeError, ValueError):
        return None
    if not concept or (category_id and not db.get_income_category(category_id)):
        return None
    return concept, amount, currency, date_str, category_id


@app.route("/api/incomes/<int:income_id>", methods=["PUT", "DELETE"])
def api_income_mutate(income_id: int):
    income = db.get_income(income_id)
    if income is None:
        return jsonify({"ok": False, "error": "Ingreso no encontrado"}), 404
    if income["user_id"] != g.current_user["id"]:
        return jsonify({"ok": False, "error": "Solo podés modificar tus propios ingresos"}), 403
    if request.method == "DELETE":
        return jsonify({"ok": db.delete_income(income_id, g.current_user["id"])})
    payload = _validated_income_payload(request.get_json(silent=True) or {})
    if payload is None:
        return jsonify({"ok": False, "error": "Datos de ingreso inválidos"}), 400
    return jsonify({"ok": db.update_income(income_id, g.current_user["id"], *payload)})


@app.route("/api/income-categories", methods=["GET", "POST"])
def api_income_categories():
    if request.method == "GET":
        return jsonify([dict(r) for r in db.get_income_categories()])
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Falta el nombre"}), 400
    category_id = db.create_income_category(name, data.get("icon", "💵"), data.get("color", "#22c55e"))
    if category_id is None:
        return jsonify({"ok": False, "error": "Ya existe esa categoría"}), 409
    return jsonify({"ok": True, "id": category_id}), 201


@app.route("/api/income-categories/<int:category_id>", methods=["PUT", "DELETE"])
def api_income_category_mutate(category_id: int):
    if request.method == "DELETE":
        ok, error = db.delete_income_category(category_id)
        return jsonify({"ok": ok, "error": error}), 200 if ok else 400
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Falta el nombre"}), 400
    ok = db.update_income_category(category_id, name, data.get("icon", "💵"), data.get("color", "#22c55e"))
    return jsonify({"ok": ok}), 200 if ok else 409


@app.route("/api/incomes/month-totals")
def api_income_month_totals():
    now = datetime.now()
    try:
        year, month = int(request.args.get("year", now.year)), int(request.args.get("month", now.month))
    except ValueError:
        return jsonify({"error": "Período inválido"}), 400
    return jsonify(db.get_income_month_totals(year, month))


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
    return jsonify({
        "report": _serialize_report(report.get_report(year, month)),
        "quota": llm_limits.summary_usage(),
    })


@app.route("/api/resumenes/<int:year>/<int:month>/generate", methods=["POST"])
def api_resumen_generate(year: int, month: int):
    try:
        with llm_limits.summary_generation():
            generated = report.generate_report(year, month)
    except llm_limits.QuotaExceeded as exc:
        return jsonify({"error": str(exc), "quota": llm_limits.summary_usage()}), 429
    return jsonify({
        "report": _serialize_report(generated),
        "quota": llm_limits.summary_usage(),
    })


def run_dashboard():
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    # threaded=True so a ~40-60s report generation request doesn't block other
    # dashboard tabs/users for the duration.
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
