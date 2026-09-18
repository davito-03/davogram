#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║           DAVOGRAM BOT  —  Modelo Freemium + Inversiones         ║
║                   by @davito_03  (SuperAdmin)                    ║
╚══════════════════════════════════════════════════════════════════╝

Características:
  • Modelo Freemium: usuarios Free vs. Premium
  • SuperAdmin vitalicio: @davito_03 (is_premium + is_admin)
  • Recordatorios personales con fecha/hora
  • Agenda de Eventos (grupales o privados)
  • Alertas de Inversión (avisos importantes)
  • Comandos de administración (ban, broadcast, etc.)

Dependencias:
  pip install python-telegram-bot apscheduler

Arrancar:
  python main.py
"""

import sqlite3
import logging
import os
import functools
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import yfinance as yf
import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN GLOBAL
# ─────────────────────────────────────────────────────────────────────────────

# ‼️ REEMPLAZA ESTE TOKEN con el que te da @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Username del SuperAdmin (sin @). Siempre premium + admin.
SUPERADMIN_USERNAME = "davito_03"

# Ruta de la base de datos SQLite
DB_PATH = "davogram.db"

# Límites Free vs. Premium
FREE_REMINDER_LIMIT = 3       # máx. recordatorios activos en Free
PREMIUM_REMINDER_LIMIT = 50   # máx. recordatorios activos en Premium
FREE_EVENT_LIMIT = 2          # máx. eventos que puede crear en Free
PREMIUM_EVENT_LIMIT = 100     # máx. eventos en Premium
PREMIUM_PALERT_LIMIT = 20     # máx. alertas de precio activas en Premium

# URL del sitio web del administrador
WEB_URL = "https://davito.es"

# ─────────────────────────────────────────────────────────────────────────────
# PAGOS — Telegram Stars (XTR) ─ sin necesidad de Stripe ni datos personales
# ─────────────────────────────────────────────────────────────────────────────
# Con Stars NO necesitas ningún proveedor externo.
# El token de pago es una cadena vacía ("") y la moneda es XTR.
# Los usuarios compran Stars directamente a Telegram desde su app.
# Puedes retirar tus Stars a TON en https://fragment.com
PAYMENT_TOKEN = ""  # vacío = Telegram Stars nativo

# Precios en Stars (XTR). Referencia aproximada: 50 Stars ≈ $0.99
# Telegram se queda un 30% de comisión sobre las Stars recibidas.
# Puedes ajustar los precios a tu criterio.
PLANS = {
    "mensual": {
        "label": "📅 1 Mes Premium",
        "price": 75,         # ~1 € en Stars
        "days": 30,
        "description": "Acceso Premium completo durante 30 días.",
    },
    "anual": {
        "label": "🗓 1 Año Premium",
        "price": 550,        # ~8 € en Stars
        "days": 365,
        "description": "Acceso Premium completo durante 365 días.",
    },
    "vitalicio": {
        "label": "👑 Vitalicio (pago único)",
        "price": 1500,       # ~20 € en Stars
        "days": -1,
        "description": "Acceso Premium de por vida. Sin renovaciones.",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

from logging.handlers import RotatingFileHandler

# Handler con rotación: máximo 5 MB por archivo, guarda 3 copias históricas
_rotating = RotatingFileHandler(
    "davogram.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB
    backupCount=3,
    encoding="utf-8",
)
_rotating.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

# Nivel raíz: solo INFO para nuestro código
logging.basicConfig(level=logging.WARNING, handlers=[_rotating, _console])

# Silenciar librerías ruidosas (httpx loguea CADA petición HTTP al bot)
for _noisy in (
    "httpx", "httpcore", "httpcore.http11", "httpcore.connection",
    "apscheduler.executors", "apscheduler.scheduler",
    "telegram", "telegram.ext", "telegram.ext.updater",
    "yfinance", "urllib3", "charset_normalizer",
    "peewee", "asyncio",
):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

# Logger propio del bot en INFO
logger = logging.getLogger("DavogramBot")
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# BASE DE DATOS — Inicialización y helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Devuelve una conexión con Row Factory para acceso por nombre de columna."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Crea todas las tablas si no existen. Se llama al arrancar."""
    conn = get_connection()
    c = conn.cursor()

    # — Usuarios —
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id              INTEGER PRIMARY KEY,
            username             TEXT,
            full_name            TEXT,
            is_premium           INTEGER NOT NULL DEFAULT 0,
            is_admin             INTEGER NOT NULL DEFAULT 0,
            is_banned            INTEGER NOT NULL DEFAULT 0,
            subscription_expires TEXT,
            join_date            TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Migración: añade la columna si la BD ya existe sin ella
    try:
        c.execute("ALTER TABLE users ADD COLUMN subscription_expires TEXT")
    except Exception:
        pass  # ya existe, ignorar

    # — Recordatorios personales —
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            message     TEXT NOT NULL,
            remind_at   TEXT NOT NULL,
            sent        INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # — Eventos (públicos o privados) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id   INTEGER NOT NULL,
            title        TEXT NOT NULL,
            description  TEXT,
            event_date   TEXT NOT NULL,
            is_public    INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (creator_id) REFERENCES users(user_id)
        )
    """)

    # — Gastos Personales (Utils) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL NOT NULL,
            concept     TEXT NOT NULL,
            date        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # — Sistema de Niveles/XP (Social) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            user_id     INTEGER NOT NULL,
            guild_id    TEXT NOT NULL,
            xp          INTEGER NOT NULL DEFAULT 0,
            level       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, guild_id)
        )
    """)

    # — Sorteos/Giveaways (Social) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id           TEXT NOT NULL,
            message_id        TEXT NOT NULL,
            prize             TEXT NOT NULL,
            end_time          TEXT NOT NULL,
            participants_json TEXT NOT NULL DEFAULT '[]'
        )
    """)

    # — Alertas de inversión (Admin) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS investment_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id  INTEGER NOT NULL,
            asset       TEXT NOT NULL,
            message     TEXT NOT NULL,
            priority    TEXT NOT NULL DEFAULT 'NORMAL',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (creator_id) REFERENCES users(user_id)
        )
    """)

    # — Alertas de precio personalizadas (Premium) —
    c.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            target      REAL NOT NULL,
            triggered   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    conn.commit()
    conn.close()
    logger.info("✅ Base de datos inicializada correctamente.")


# ─────────────────────────────────────────────────────────────────────────────
# SISTEMA DE USUARIOS
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_user(user_id: int, username: str | None, full_name: str) -> sqlite3.Row:
    """
    Devuelve la fila del usuario. Si no existe, lo crea.
    REGLA DE ORO: si su username es @davito_03 → premium + admin vitalicio.
    """
    conn = get_connection()
    c = conn.cursor()

    # Determinar si es el SuperAdmin
    clean_username = (username or "").lower().lstrip("@")
    is_super = 1 if clean_username == SUPERADMIN_USERNAME.lower() else 0

    # Insertar si no existe
    c.execute("""
        INSERT OR IGNORE INTO users (user_id, username, full_name, is_premium, is_admin)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, full_name, is_super, is_super))

    # Si el usuario YA existía pero es el superadmin, reforzar permisos
    if is_super:
        c.execute("""
            UPDATE users SET is_premium = 1, is_admin = 1
            WHERE user_id = ?
        """, (user_id,))

    conn.commit()
    user = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def fetch_user(user_id: int) -> sqlite3.Row | None:
    """Obtiene un usuario de la BD o None si no existe."""
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def set_premium(user_id: int, value: bool):
    """Activa o desactiva el flag premium de un usuario."""
    conn = get_connection()
    conn.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (int(value), user_id))
    conn.commit()
    conn.close()


def set_banned(user_id: int, value: bool):
    """Banea o desbanea un usuario."""
    conn = get_connection()
    conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (int(value), user_id))
    conn.commit()
    conn.close()


def get_all_users() -> list[sqlite3.Row]:
    """Devuelve todos los usuarios registrados."""
    conn = get_connection()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return users


# ─────────────────────────────────────────────────────────────────────────────
# DECORADORES DE SEGURIDAD
# ─────────────────────────────────────────────────────────────────────────────

def require_not_banned(func):
    """Bloquea usuarios baneados."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        tg_user = update.effective_user
        user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
        if user["is_banned"]:
            await update.effective_message.reply_text(
                "🚫 Tu cuenta ha sido suspendida. Contacta con el administrador."
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_premium(func):
    """
    Decorador que verifica si el usuario tiene el flag is_premium
    Y que su suscripción no ha expirado (salvo admin/vitalicio).
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        tg_user = update.effective_user
        user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

        if user["is_banned"]:
            await update.effective_message.reply_text(
                "🚫 Tu cuenta ha sido suspendida."
            )
            return

        # Admins tienen acceso vitalicio sin comprobar fecha
        if user["is_admin"]:
            return await func(update, context, *args, **kwargs)

        # Verificar si la suscripción ha expirado
        if user["is_premium"] and user["subscription_expires"]:
            expires = datetime.strptime(user["subscription_expires"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expires:
                # Revocar premium expirado
                conn = get_connection()
                conn.execute("UPDATE users SET is_premium = 0 WHERE user_id = ?", (tg_user.id,))
                conn.commit()
                conn.close()
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Renovar Premium", callback_data="upgrade_info")
                ]])
                await update.effective_message.reply_text(
                    "⏰ *Tu suscripción Premium ha expirado.*\n\n"
                    "Usa /upgrade para renovarla y seguir disfrutando de todas las ventajas.",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
                return

        if not user["is_premium"]:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⭐ Ver planes Premium", callback_data="upgrade_info")
            ]])
            await update.effective_message.reply_text(
                "🔒 *Función exclusiva Premium*\n\n"
                "Esta función no está disponible en el plan gratuito.\n"
                "Escribe /upgrade para suscribirte directamente desde aquí. 🚀",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

        return await func(update, context, *args, **kwargs)
    return wrapper


def require_admin(func):
    """Decorador que solo permite la ejecución a administradores del bot."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        tg_user = update.effective_user
        user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

        if not user["is_admin"]:
            await update.effective_message.reply_text(
                "⛔ No tienes permisos de administrador para usar este comando."
            )
            return

        return await func(update, context, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# ── COMANDOS GENERALES ──
# ─────────────────────────────────────────────────────────────────────────────

@require_not_banned
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bienvenida y registro automático del usuario."""
    tg_user = update.effective_user
    user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    plan_badge = "👑 *SuperAdmin*" if user["is_admin"] else ("⭐ *Premium*" if user["is_premium"] else "🆓 *Free*")

    text = (
        f"👋 ¡Hola, {tg_user.first_name}!\n\n"
        f"Bienvenido a *DavoGram Bot* — tu asistente personal de productividad e inteligencia artificial.\n\n"
        f"📋 Tu plan actual: {plan_badge}\n\n"
        "🔎 Usa /help para ver todos los comandos disponibles."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@require_not_banned
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias de /menu para compatibilidad."""
    await cmd_menu(update, context)


# Menú por secciones con botones inline
_MENU_FREE = (
    "🆓 <b>Comandos Gratuitos</b>\n\n"
    "⚙️ <b>General</b>\n"
    "  /start – Bienvenida / registro\n"
    "  /miperfil – Tu perfil y plan\n"
    "  /upgrade – Ver planes Premium\n"
    "  /web – Visitar davito.es\n\n"
    "📅 <b>Productividad</b>\n"
    "  /recordatorio – Crear recordatorio (máx. 3)\n"
    "  /mis_recordatorios – Ver tus recordatorios\n"
    "  /eventos_publicos – Eventos públicos\n"
    "  /agenda – Próximos recordatorios y eventos\n\n"
    "🎮 <b>Juegos</b>\n"
    "  /quiz – Trivia de cultura general\n"
    "  /trivia – Trivia por categorías\n"
    "  /ahorcado – Juego del ahorcado\n"
    "  /wordle – Juego Wordle de 5 letras\n"
    "  /tictactoe – Tres en Raya\n"
    "  /buscaminas – Buscaminas 5x5\n"
    "  /pptls – Piedra, Papel, ...\n"
    "  /ruleta – Ruleta rusa (grupo)\n"
    "  /adivina – Adivina el número\n\n"
    "🛠️ <b>Utilidades Rápidas</b>\n"
    "  /clima &lt;ciudad&gt; – Tiempo meteorológico\n"
    "  /traducir &lt;texto&gt; – Traducir con IA\n"
    "  /calcula &lt;expr&gt; – Calculadora científica\n"
    "  /dado – Tirar un dado\n"
    "  /sorteo &lt;min&gt; &lt;max&gt; – Número aleatorio\n"
    "  /acortar &lt;url&gt; – Acortar enlace\n"
    "  /gasto – Añadir gasto rápido\n"
    "  /gastos – Ver resumen de gastos\n"
    "  /pass – Crear contraseña segura\n"
    "  /qr – Generar/Leer QR\n"
    "  /conv – Conversor de unidades\n\n"
    "👥 <b>Social (Grupos)</b>\n"
    "  /nivel – Tu nivel y XP\n"
    "  /top – Ranking del grupo\n"
    "  /giveaway – Crear sorteo (Administradores)"
)

_MENU_PREMIUM = (
    "⭐ <b>Comandos Premium</b>\n\n"
    "📅 <b>Productividad Avanzada</b>\n"
    "  /recordatorio – Sin límite (máx. 50)\n"
    "  /recordar – Recordatorios recurrentes\n"
    "  /smart &lt;texto&gt; – Crear recordatorios con IA\n"
    "  /crear_evento – Crear evento\n"
    "  /mis_eventos – Ver tus eventos\n"
    "  /nota add/ver/del – Notas personales\n"
    "  /lista add/ver/tick/del – Listas de tareas\n"
    "  /habito – Seguimiento de hábitos\n"
    "  /exportar – Exportar datos a CSV\n\n"
    "🤖 <b>Inteligencia Artificial y Multimedia</b>\n"
    "  /ia &lt;pregunta&gt; – Chat libre con IA\n"
    "  /ia_reset – Borrar historial de IA\n"
    "  /resumen_web &lt;url&gt; – Resumir página web\n"
    "  /imagen &lt;desc&gt; – Generar imagen con IA\n"
    "  /transcribir – Voz a Texto (respondiendo a audio)\n"
    "  /vision – Analizar imagen (respondiendo a foto)\n"
    "  /habla &lt;texto&gt; – Generar nota de voz TTS\n"
    "  /meme – Crear meme (respondiendo a foto)"
)

_MENU_ADMIN = (
    "👑 <b>Comandos Admin</b>\n\n"
    "  /upgrade_user &lt;id&gt; – Dar premium\n"
    "  /trial_user &lt;id&gt; &lt;días&gt; – Trial temporal\n"
    "  /ban_user – Banear usuario\n"
    "  /unban_user – Desbanear usuario\n"
    "  /buscar_usuario – Buscar usuario\n"
    "  /broadcast – Mensaje a todos\n"
    "  /dm_todos – DM personalizado a todos\n"
    "  /ingresos – Resumen de pagos Stars\n"
    "  /limpiar_bd – Borrar usuarios inactivos\n"
    "  /stats – Estadísticas del bot\n"
    "  /alerta – Alta prioridad (Broadcast global)"
)


def _menu_main_kb(is_premium: bool, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧩 Comandos gratuitos", callback_data="menu_free")],
    ]
    if is_premium:
        rows.append([InlineKeyboardButton("⭐ Comandos Premium", callback_data="menu_premium")])
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Comandos Admin", callback_data="menu_admin")])
    return InlineKeyboardMarkup(rows)


@require_not_banned
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menú principal con botones de navegación por categoría."""
    tg_user  = update.effective_user
    user     = fetch_user(tg_user.id)
    is_prem  = bool(user and (user["is_premium"] or user["is_admin"]))
    is_admin = bool(user and user["is_admin"])
    plan_tag = "👑 Admin+Premium" if is_admin else ("⭐ Premium" if is_prem else "🆓 Gratuito")

    await update.message.reply_text(
        f"🤖 <b>DavoGram Bot</b> — {plan_tag}\n\n"
        "Selecciona una categoría para ver los comandos disponibles:",
        parse_mode="HTML",
        reply_markup=_menu_main_kb(is_prem, is_admin),
    )


async def cb_menu_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el listado de una sección del menú."""
    query = update.callback_query
    await query.answer()
    section = query.data  # menu_free / menu_premium / menu_admin

    texts   = {"menu_free": _MENU_FREE, "menu_premium": _MENU_PREMIUM, "menu_admin": _MENU_ADMIN}
    content = texts.get(section, "Sección desconocida")

    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Volver al menú", callback_data="menu_back")]])
    await query.edit_message_text(content, parse_mode="HTML", reply_markup=back_kb)


async def cb_menu_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vuelve al menú principal."""
    query    = update.callback_query
    await query.answer()
    tg_user  = query.from_user
    conn     = get_connection()
    user     = conn.execute("SELECT * FROM users WHERE user_id = ?", (tg_user.id,)).fetchone()
    conn.close()
    is_prem  = bool(user and (user["is_premium"] or user["is_admin"]))
    is_admin = bool(user and user["is_admin"])
    plan_tag = "👑 Admin+Premium" if is_admin else ("⭐ Premium" if is_prem else "🆓 Gratuito")

    await query.edit_message_text(
        f"🤖 <b>DavoGram Bot</b> — {plan_tag}\n\n"
        "Selecciona una categoría para ver los comandos disponibles:",
        parse_mode="HTML",
        reply_markup=_menu_main_kb(is_prem, is_admin),
    )


@require_not_banned
async def cmd_miperfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el perfil del usuario."""
    tg_user = update.effective_user
    user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    plan = "👑 SuperAdmin" if user["is_admin"] else ("⭐ Premium" if user["is_premium"] else "🆓 Free")

    conn = get_connection()
    rem_count = conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND sent = 0", (user["user_id"],)
    ).fetchone()[0]
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE creator_id = ?", (user["user_id"],)
    ).fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"👤 *Tu Perfil*\n\n"
        f"🆔 ID: `{user['user_id']}`\n"
        f"👤 Usuario: @{user['username'] or 'sin usuario'}\n"
        f"📛 Nombre: {user['full_name']}\n"
        f"📋 Plan: {plan}\n"
        f"⏰ Recordatorios activos: {rem_count}\n"
        f"📅 Eventos creados: {ev_count}\n"
        f"📆 Registro: {user['join_date'][:10]}",
        parse_mode="Markdown",
    )


@require_not_banned
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los planes Premium con botones de pago nativos de Telegram."""
    tg_user = update.effective_user
    user = fetch_user(tg_user.id)

    # Si ya es premium, mostrar estado de suscripción
    if user and user["is_premium"]:
        if user["is_admin"] or not user["subscription_expires"]:
            exp_text = "♾️ *Vitalicia* (no caduca)"
        else:
            exp_text = f"📅 Expira el `{user['subscription_expires'][:10]}`"
        await update.message.reply_text(
            f"✅ *Ya eres usuario Premium*\n\n"
            f"Estado de tu suscripción: {exp_text}\n\n"
            "Si quieres ampliar o renovar, elige un plan:",
            parse_mode="Markdown",
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 1 Mes — 1,00 €",      callback_data="buy_mensual")],
        [InlineKeyboardButton("🗓 1 Año — 8,00 €",      callback_data="buy_anual")],
        [InlineKeyboardButton("👑 Vitalicio — 20,00 €", callback_data="buy_vitalicio")],
    ])
    await update.message.reply_text(
        "⭐ *Planes Premium — DavoGram Bot*\n\n"
        "Con Premium desbloqueas:\n"
        "  ✅ Hasta 50 recordatorios activos\n"
        "  ✅ Chat con IA avanzado (búsqueda web en tiempo real)\n"
        "  ✅ Generar imágenes con IA\n"
        "  ✅ Resumir páginas web, notas, listas y hábitos\n"
        "  ✅ Crear y gestionar eventos privados\n\n"
        "Elige tu plan y paga directamente desde Telegram:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def cb_upgrade_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback del botón 'Ver planes Premium' en el decorador require_premium."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💡 Escribe /upgrade para ver los planes y pagar directamente.")


async def cb_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback de los botones de compra. Envía la factura (invoice) nativa de Telegram.
    El usuario paga sin salir del chat.
    """
    query = update.callback_query
    await query.answer()
    plan_key = query.data.replace("buy_", "")  # mensual / anual / vitalicio
    plan = PLANS.get(plan_key)
    if not plan:
        return

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=plan["label"],
        description=plan["description"],
        payload=f"premium_{plan_key}",
        provider_token=PAYMENT_TOKEN,   # "" = Telegram Stars, sin proveedor externo
        currency="XTR",                 # XTR = Telegram Stars
        prices=[{"label": plan["label"], "amount": plan["price"]}],
        start_parameter=f"premium-{plan_key}",
    )


async def handler_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram llama a este handler antes de procesar el pago.
    OBLIGATORIO responder con answer_pre_checkout_query en menos de 10 segundos.
    """
    query = update.pre_checkout_query
    # Validar que el payload es uno de nuestros planes
    valid_payloads = {f"premium_{k}" for k in PLANS}
    if query.invoice_payload in valid_payloads:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Plan desconocido. Contacta con @davito_03.")


async def handler_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram confirma el pago exitoso aquí.
    Activamos el premium según el plan pagado, calculando la fecha de expiración.
    """
    tg_user = update.effective_user
    payment = update.message.successful_payment
    payload = payment.invoice_payload          # ej: "premium_mensual"
    plan_key = payload.replace("premium_", "")  # ej: "mensual"
    plan = PLANS.get(plan_key)
    if not plan:
        logger.error(f"Pago recibido con payload desconocido: {payload}")
        return

    days = plan["days"]
    conn = get_connection()

    if days == -1:
        # Vitalicio: subscription_expires = NULL
        conn.execute(
            "UPDATE users SET is_premium = 1, subscription_expires = NULL WHERE user_id = ?",
            (tg_user.id,)
        )
        expires_text = "♾️ *vitalicio* (nunca caduca)"
    else:
        # Calcular fecha de expiración extendiendo desde ahora (o desde la actual)
        user = fetch_user(tg_user.id)
        if user and user["subscription_expires"] and user["is_premium"]:
            # Extender desde la fecha actual (si ya era premium vigente)
            try:
                base = datetime.strptime(user["subscription_expires"], "%Y-%m-%d %H:%M:%S")
                if base > datetime.now():
                    new_expires = base + timedelta(days=days)
                else:
                    new_expires = datetime.now() + timedelta(days=days)
            except Exception:
                new_expires = datetime.now() + timedelta(days=days)
        else:
            new_expires = datetime.now() + timedelta(days=days)

        conn.execute(
            "UPDATE users SET is_premium = 1, subscription_expires = ? WHERE user_id = ?",
            (new_expires.strftime("%Y-%m-%d %H:%M:%S"), tg_user.id)
        )
        expires_text = f"📅 hasta el *{new_expires.strftime('%d/%m/%Y')}*"

    conn.commit()
    conn.close()
    logger.info(f"💰 Pago exitoso: {tg_user.username} → plan {plan_key} ({payment.total_amount/100:.2f} EUR)")

    await update.message.reply_text(
        f"🎉 *¡Pago recibido! Bienvenido a Premium*\n\n"
        f"Plan: {plan['label']}\n"
        f"Acceso: {expires_text}\n\n"
        "Ya tienes acceso a todas las funciones exclusivas. Usa /help para verlas.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ── RECORDATORIOS ──
# ─────────────────────────────────────────────────────────────────────────────

# Estados del ConversationHandler para recordatorios
REM_MSG, REM_DATE = range(2)


@require_not_banned
async def cmd_recordatorio_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo para crear un recordatorio."""
    tg_user = update.effective_user
    user = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    # Verificar límite según plan
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND sent = 0", (tg_user.id,)
    ).fetchone()[0]
    conn.close()

    limit = PREMIUM_REMINDER_LIMIT if user["is_premium"] else FREE_REMINDER_LIMIT

    if count >= limit:
        await update.message.reply_text(
            f"⚠️ Has alcanzado el límite de {limit} recordatorios activos para tu plan.\n"
            f"{'Borra algunos con /mis_recordatorios.' if user['is_premium'] else 'Hazte Premium con /upgrade para ampliar el límite.'}"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "⏰ *Nuevo Recordatorio*\n\n"
        "¿Qué quieres que te recuerde? Escribe el mensaje:",
        parse_mode="Markdown",
    )
    return REM_MSG


async def rem_get_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el mensaje del recordatorio y pide la fecha/hora."""
    context.user_data["rem_msg"] = update.message.text
    await update.message.reply_text(
        "📅 Ahora escribe la fecha y hora del recordatorio en este formato:\n\n"
        "`DD/MM/YYYY HH:MM`\n\nEjemplo: `25/12/2025 09:00`",
        parse_mode="Markdown",
    )
    return REM_DATE


async def rem_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Valida la fecha/hora y guarda el recordatorio en la BD."""
    raw = update.message.text.strip()
    try:
        remind_at = datetime.strptime(raw, "%d/%m/%Y %H:%M")
    except ValueError:
        await update.message.reply_text(
            "❌ Formato incorrecto. Intenta de nuevo: `DD/MM/YYYY HH:MM`",
            parse_mode="Markdown",
        )
        return REM_DATE

    if remind_at <= datetime.now():
        await update.message.reply_text(
            "⚠️ La fecha debe ser futura. Inténtalo de nuevo."
        )
        return REM_DATE

    tg_user = update.effective_user
    msg = context.user_data.get("rem_msg", "Recordatorio sin mensaje")

    conn = get_connection()
    conn.execute(
        "INSERT INTO reminders (user_id, message, remind_at) VALUES (?, ?, ?)",
        (tg_user.id, msg, remind_at.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *Recordatorio guardado*\n\n"
        f"📝 Mensaje: _{msg}_\n"
        f"📅 Fecha: `{remind_at.strftime('%d/%m/%Y %H:%M')}`\n\n"
        f"Te avisaré en el momento indicado. ⏰",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def rem_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela el flujo de recordatorio."""
    await update.message.reply_text("❌ Recordatorio cancelado.")
    return ConversationHandler.END


@require_not_banned
async def cmd_mis_recordatorios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista los recordatorios activos del usuario con opción de borrar."""
    tg_user = update.effective_user
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND sent = 0 ORDER BY remind_at",
        (tg_user.id,),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No tienes recordatorios activos.")
        return

    text = "⏰ *Tus Recordatorios Activos*\n\n"
    buttons = []
    for r in rows:
        dt = r["remind_at"][:16]
        text += f"🔔 [{r['id']}] `{dt}` — {r['message']}\n"
        buttons.append([InlineKeyboardButton(
            f"🗑 Borrar [{r['id']}]", callback_data=f"del_rem_{r['id']}"
        )])

    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cb_delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para borrar un recordatorio."""
    query = update.callback_query
    await query.answer()
    rem_id = int(query.data.split("_")[-1])
    tg_user = query.from_user

    conn = get_connection()
    conn.execute(
        "DELETE FROM reminders WHERE id = ? AND user_id = ?", (rem_id, tg_user.id)
    )
    conn.commit()
    conn.close()

    await query.edit_message_text(f"🗑 Recordatorio #{rem_id} eliminado.")


# ─────────────────────────────────────────────────────────────────────────────
# ── EVENTOS ──
# ─────────────────────────────────────────────────────────────────────────────

# Estados del ConversationHandler para eventos
EVT_TITLE, EVT_DESC, EVT_DATE, EVT_PUBLIC = range(4)


@require_not_banned
@require_premium
async def cmd_crear_evento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo para crear un evento (sólo Premium)."""
    tg_user = update.effective_user
    user = fetch_user(tg_user.id)
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE creator_id = ?", (tg_user.id,)
    ).fetchone()[0]
    conn.close()

    limit = PREMIUM_EVENT_LIMIT if user["is_premium"] else FREE_EVENT_LIMIT
    if count >= limit:
        await update.message.reply_text(
            f"⚠️ Has alcanzado el límite de {limit} eventos."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📅 *Nuevo Evento*\n\n¿Cuál es el título del evento?",
        parse_mode="Markdown",
    )
    return EVT_TITLE


async def evt_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["evt_title"] = update.message.text
    await update.message.reply_text("✏️ Escribe una descripción (o /skip para omitir):")
    return EVT_DESC


async def evt_get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["evt_desc"] = update.message.text
    await update.message.reply_text(
        "📅 Fecha del evento (`DD/MM/YYYY HH:MM`):", parse_mode="Markdown"
    )
    return EVT_DATE


async def evt_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["evt_desc"] = ""
    await update.message.reply_text(
        "📅 Fecha del evento (`DD/MM/YYYY HH:MM`):", parse_mode="Markdown"
    )
    return EVT_DATE


async def evt_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        dt = datetime.strptime(raw, "%d/%m/%Y %H:%M")
    except ValueError:
        await update.message.reply_text("❌ Formato incorrecto. Usa `DD/MM/YYYY HH:MM`", parse_mode="Markdown")
        return EVT_DATE
    context.user_data["evt_date"] = dt

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Público", callback_data="evt_public_1"),
         InlineKeyboardButton("🔒 Privado", callback_data="evt_public_0")]
    ])
    await update.message.reply_text("¿El evento es público o privado?", reply_markup=kb)
    return EVT_PUBLIC


async def evt_get_public(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    is_public = int(query.data.split("_")[-1])
    tg_user = query.from_user
    context.user_data["evt_public"] = is_public

    conn = get_connection()
    conn.execute(
        "INSERT INTO events (creator_id, title, description, event_date, is_public) VALUES (?,?,?,?,?)",
        (
            tg_user.id,
            context.user_data["evt_title"],
            context.user_data.get("evt_desc", ""),
            context.user_data["evt_date"].strftime("%Y-%m-%d %H:%M:%S"),
            is_public,
        ),
    )
    conn.commit()
    conn.close()

    vis = "🌐 Público" if is_public else "🔒 Privado"
    await query.edit_message_text(
        f"✅ *Evento creado*\n\n"
        f"📌 Título: {context.user_data['evt_title']}\n"
        f"📅 Fecha: {context.user_data['evt_date'].strftime('%d/%m/%Y %H:%M')}\n"
        f"👁 Visibilidad: {vis}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def evt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Creación de evento cancelada.")
    return ConversationHandler.END


@require_not_banned
async def cmd_eventos_publicos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra todos los eventos públicos futuros."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM events WHERE is_public = 1 AND event_date >= ? ORDER BY event_date LIMIT 15",
        (now,),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No hay eventos públicos próximos.")
        return

    text = "📅 *Próximos Eventos Públicos*\n\n"
    for e in rows:
        text += (
            f"🔹 *{e['title']}*\n"
            f"   📅 {e['event_date'][:16]}\n"
            f"   📝 {e['description'] or 'Sin descripción'}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


@require_not_banned
@require_premium
async def cmd_mis_eventos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista los eventos del usuario Premium."""
    tg_user = update.effective_user
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM events WHERE creator_id = ? ORDER BY event_date", (tg_user.id,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No has creado ningún evento todavía.")
        return

    text = "📅 *Tus Eventos*\n\n"
    buttons = []
    for e in rows:
        vis = "🌐" if e["is_public"] else "🔒"
        text += f"{vis} *{e['title']}* — {e['event_date'][:16]}\n"
        buttons.append([InlineKeyboardButton(
            f"🗑 Borrar '{e['title']}'", callback_data=f"del_evt_{e['id']}"
        )])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para borrar un evento."""
    query = update.callback_query
    await query.answer()
    evt_id = int(query.data.split("_")[-1])
    tg_user = query.from_user

    conn = get_connection()
    conn.execute(
        "DELETE FROM events WHERE id = ? AND creator_id = ?", (evt_id, tg_user.id)
    )
    conn.commit()
    conn.close()
    await query.edit_message_text(f"🗑 Evento #{evt_id} eliminado.")


# ─────────────────────────────────────────────────────────────────────────────
# ── ALERTAS DE INVERSIÓN ──
# ─────────────────────────────────────────────────────────────────────────────

# Estado del ConversationHandler para nueva alerta
ALERT_ASSET, ALERT_MSG, ALERT_PRIORITY = range(3)


@require_not_banned
@require_admin
async def cmd_nueva_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo para publicar una alerta de inversión (solo admin)."""
    await update.message.reply_text(
        "📊 *Nueva Alerta de Inversión*\n\n¿Sobre qué activo es la alerta? (ej: BTC, ETH, S&P500, ORO…)",
        parse_mode="Markdown",
    )
    return ALERT_ASSET


async def alert_get_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alert_asset"] = update.message.text.upper()
    await update.message.reply_text("📝 Escribe el mensaje de la alerta:")
    return ALERT_MSG


async def alert_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alert_msg"] = update.message.text
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BAJA", callback_data="alert_prio_BAJA"),
         InlineKeyboardButton("🟡 NORMAL", callback_data="alert_prio_NORMAL"),
         InlineKeyboardButton("🔴 URGENTE", callback_data="alert_prio_URGENTE")],
    ])
    await update.message.reply_text("Selecciona la prioridad:", reply_markup=kb)
    return ALERT_PRIORITY


async def alert_get_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    priority = query.data.split("_")[-1]
    tg_user = query.from_user

    conn = get_connection()
    conn.execute(
        "INSERT INTO investment_alerts (creator_id, asset, message, priority) VALUES (?,?,?,?)",
        (tg_user.id, context.user_data["alert_asset"], context.user_data["alert_msg"], priority),
    )
    conn.commit()
    conn.close()

    # Emojis de prioridad
    prio_emoji = {"BAJA": "🟢", "NORMAL": "🟡", "URGENTE": "🔴"}.get(priority, "⚪")

    # Broadcast a todos los usuarios premium
    app = context.application
    all_users = get_all_users()
    sent = 0
    for u in all_users:
        if u["is_premium"] and not u["is_banned"]:
            try:
                await app.bot.send_message(
                    chat_id=u["user_id"],
                    text=(
                        f"🚨 *ALERTA DE INVERSIÓN* {prio_emoji}\n\n"
                        f"📊 Activo: *{context.user_data['alert_asset']}*\n"
                        f"⚡ Prioridad: *{priority}*\n\n"
                        f"📢 {context.user_data['alert_msg']}\n\n"
                        f"_— DavoGram Bot_"
                    ),
                    parse_mode="Markdown",
                )
                sent += 1
            except Exception as e:
                logger.warning(f"No se pudo enviar alerta a {u['user_id']}: {e}")

    await query.edit_message_text(
        f"✅ Alerta publicada y enviada a {sent} usuario(s) Premium.\n\n"
        f"📊 Activo: *{context.user_data['alert_asset']}* | Prioridad: {prio_emoji} {priority}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def alert_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Creación de alerta cancelada.")
    return ConversationHandler.END


@require_not_banned
@require_premium
async def cmd_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra las últimas 10 alertas de inversión (solo Premium)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM investment_alerts ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No hay alertas de inversión publicadas aún.")
        return

    text = "📊 *Últimas Alertas de Inversión*\n\n"
    for a in rows:
        prio_emoji = {"BAJA": "🟢", "NORMAL": "🟡", "URGENTE": "🔴"}.get(a["priority"], "⚪")
        text += (
            f"{prio_emoji} *{a['asset']}* — {a['priority']}\n"
            f"📝 {a['message']}\n"
            f"🕐 {a['created_at'][:16]}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# ── ALERTAS DE PRECIO PERSONALIZADAS (Premium) ──
# Símbolos de ejemplo:
#   Crypto : BTC-USD, ETH-USD, SOL-USD, BNB-USD
#   Índices: ^IBEX (IBEX35), ^GSPC (S&P500), ^DJI (Dow Jones)
#   Acciones: AAPL, MSFT, SAN.MC, TEF.MC, etc.
# ─────────────────────────────────────────────────────────────────────────────

PA_SYMBOL, PA_DIR, PA_PRICE = range(3)  # estados del ConversationHandler


def get_current_price(symbol: str) -> float | None:
    """Obtiene el precio actual de un activo via yfinance. Devuelve None si falla."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"yfinance error ({symbol}): {e}")
    return None


@require_not_banned
@require_premium
async def cmd_alerta_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo para crear una alerta de precio personalizada (Premium)."""
    tg_user = update.effective_user
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM price_alerts WHERE user_id = ? AND triggered = 0",
        (tg_user.id,)
    ).fetchone()[0]
    conn.close()

    if count >= PREMIUM_PALERT_LIMIT:
        await update.message.reply_text(
            f"⚠️ Tienes {PREMIUM_PALERT_LIMIT} alertas de precio activas (límite máximo).\n"
            "Elimina alguna con /mis_alertas_precio."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📈 *Nueva Alerta de Precio*\n\n"
        "Escribe el símbolo del activo. Ejemplos:\n"
        "  • Crypto: `BTC-USD`, `ETH-USD`, `SOL-USD`\n"
        "  • IBEX35: `^IBEX` | S&P500: `^GSPC`\n"
        "  • Acciones: `AAPL`, `MSFT`, `SAN.MC`\n\n"
        "👉 Escribe el símbolo:",
        parse_mode="Markdown",
    )
    return PA_SYMBOL


async def pa_get_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Valida el símbolo y pide la dirección de la alerta."""
    symbol = update.message.text.strip().upper()
    price = get_current_price(symbol)
    if price is None:
        await update.message.reply_text(
            f"❌ No pude obtener el precio de `{symbol}`. Verifica el símbolo e inténtalo de nuevo.",
            parse_mode="Markdown",
        )
        return PA_SYMBOL

    context.user_data["pa_symbol"] = symbol
    context.user_data["pa_current"] = price

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📈 Supera precio (sube)", callback_data="pa_dir_above"),
        InlineKeyboardButton("📉 Baja de precio (cae)", callback_data="pa_dir_below"),
    ]])
    await update.message.reply_text(
        f"✅ Activo: *{symbol}* — Precio actual: `{price:,.4f}`\n\n"
        "¿Cuándo quieres recibir la alerta?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return PA_DIR


async def pa_get_dir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda la dirección y pide el precio objetivo."""
    query = update.callback_query
    await query.answer()
    direction = query.data.split("_")[-1]  # 'above' o 'below'
    context.user_data["pa_dir"] = direction
    symbol = context.user_data["pa_symbol"]
    current = context.user_data["pa_current"]
    arrow = "📈" if direction == "above" else "📉"
    label = "por encima de" if direction == "above" else "por debajo de"
    await query.edit_message_text(
        f"{arrow} Recibirás alerta cuando *{symbol}* llegue *{label}* el precio indicado.\n"
        f"💡 Precio actual: `{current:,.4f}`\n\n"
        "Escribe el *precio objetivo* (solo números, usa punto para decimales):",
        parse_mode="Markdown",
    )
    return PA_PRICE


async def pa_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Valida y guarda la alerta de precio en la BD."""
    raw = update.message.text.strip().replace(",", ".")
    try:
        target = float(raw)
        if target <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Precio inválido. Escribe un número positivo (ej: `45000.50`):", parse_mode="Markdown")
        return PA_PRICE

    tg_user = update.effective_user
    symbol = context.user_data["pa_symbol"]
    direction = context.user_data["pa_dir"]
    current = context.user_data["pa_current"]
    arrow = "📈" if direction == "above" else "📉"
    label = "suba a" if direction == "above" else "baje a"

    conn = get_connection()
    conn.execute(
        "INSERT INTO price_alerts (user_id, symbol, direction, target) VALUES (?,?,?,?)",
        (tg_user.id, symbol, direction, target),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{arrow} *Alerta de precio guardada*\n\n"
        f"📊 Activo: *{symbol}*\n"
        f"🎯 Te avisaré cuando {label} `{target:,.4f}`\n"
        f"💡 Precio actual: `{current:,.4f}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def pa_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Alerta de precio cancelada.")
    return ConversationHandler.END


@require_not_banned
@require_premium
async def cmd_mis_alertas_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista las alertas de precio activas del usuario con botón para borrar."""
    tg_user = update.effective_user
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_alerts WHERE user_id = ? AND triggered = 0 ORDER BY created_at",
        (tg_user.id,)
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No tienes alertas de precio activas. Crea una con /alerta_precio.")
        return

    text = "📊 *Tus Alertas de Precio Activas*\n\n"
    buttons = []
    for a in rows:
        arrow = "📈" if a["direction"] == "above" else "📉"
        cond = "≥" if a["direction"] == "above" else "≤"
        text += f"{arrow} *{a['symbol']}* {cond} `{a['target']:,.4f}`\n"
        buttons.append([InlineKeyboardButton(
            f"🗑 Borrar {a['symbol']} {cond} {a['target']:,.2f}",
            callback_data=f"del_pa_{a['id']}"
        )])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_delete_palert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para borrar una alerta de precio."""
    query = update.callback_query
    await query.answer()
    pa_id = int(query.data.split("_")[-1])
    tg_user = query.from_user
    conn = get_connection()
    conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (pa_id, tg_user.id))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"🗑 Alerta de precio #{pa_id} eliminada.")


async def check_price_alerts(app):
    """
    Tarea programada cada 5 minutos.
    Revisa todas las alertas de precio activas y envía notificación si se cumple la condición.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_alerts WHERE triggered = 0"
    ).fetchall()
    conn.close()

    # Agrupar por símbolo para hacer una sola llamada por activo
    symbols: dict[str, float | None] = {}
    for a in rows:
        if a["symbol"] not in symbols:
            symbols[a["symbol"]] = get_current_price(a["symbol"])

    for a in rows:
        current = symbols.get(a["symbol"])
        if current is None:
            continue
        triggered = (
            (a["direction"] == "above" and current >= a["target"]) or
            (a["direction"] == "below" and current <= a["target"])
        )
        if triggered:
            arrow = "📈" if a["direction"] == "above" else "📉"
            cond = "superado" if a["direction"] == "above" else "bajado de"
            try:
                await app.bot.send_message(
                    chat_id=a["user_id"],
                    text=(
                        f"🚨 {arrow} *¡ALERTA DE PRECIO ACTIVADA!*\n\n"
                        f"📊 Activo: *{a['symbol']}*\n"
                        f"🎯 Objetivo {cond}: `{a['target']:,.4f}`\n"
                        f"💰 Precio actual: `{current:,.4f}`\n\n"
                        f"_— DavoGram Bot_"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Error enviando alerta de precio {a['id']}: {e}")
            finally:
                conn2 = get_connection()
                conn2.execute("UPDATE price_alerts SET triggered = 1 WHERE id = ?", (a["id"],))
                conn2.commit()
                conn2.close()


# ─────────────────────────────────────────────────────────────────────────────
# ── DM MASIVO A TODOS (Admin) ──
# ─────────────────────────────────────────────────────────────────────────────

DM_MSG = 0  # estado del ConversationHandler


@require_not_banned
@require_admin
async def cmd_dm_todos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo para enviar un DM personalizado a todos los usuarios."""
    await update.message.reply_text(
        "✉️ *DM a todos los usuarios*\n\n"
        "Escribe el mensaje que quieres enviar a *TODOS* los usuarios registrados.\n"
        "Admite Markdown. Cancela con /cancel.",
        parse_mode="Markdown",
    )
    return DM_MSG


async def dm_get_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el DM a todos los usuarios no baneados."""
    mensaje = update.message.text
    all_users = get_all_users()
    sent, failed = 0, 0
    for u in all_users:
        if u["is_banned"]:
            continue
        try:
            await context.bot.send_message(
                chat_id=u["user_id"],
                text=f"📩 *Mensaje de @davito_03*\n\n{mensaje}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ DM enviado.\n📤 Exitosos: {sent} | ❌ Fallidos: {failed}"
    )
    return ConversationHandler.END


async def dm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Envío de DM cancelado.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# ── COMANDO WEB ──
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra un botón inline para acceder directamente a davito.es."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌐 Visitar davito.es", url=WEB_URL)
    ]])
    await update.message.reply_text(
        "🌐 *davito.es*\n\nVisita la web del administrador del bot:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ── COMANDOS ADMIN ──
# ─────────────────────────────────────────────────────────────────────────────

@require_not_banned
@require_admin
async def cmd_upgrade_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dar premium a un usuario.
    Uso: /upgrade_user <user_id>
    """
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /upgrade_user <user_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ El user_id debe ser un número.")
        return

    target = fetch_user(target_id)
    if not target:
        await update.message.reply_text("❌ Usuario no encontrado en la BD.")
        return

    set_premium(target_id, True)
    await update.message.reply_text(f"✅ Usuario `{target_id}` ahora es Premium.", parse_mode="Markdown")

    # Notificar al usuario
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="🎉 ¡Felicidades! Tu cuenta ha sido *activada como Premium*.\nYa tienes acceso a todas las funciones exclusivas. /help",
            parse_mode="Markdown",
        )
    except Exception:
        pass


@require_not_banned
@require_admin
async def cmd_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Banear a un usuario.
    Uso: /ban_user <user_id>
    """
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /ban_user <user_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ El user_id debe ser un número.")
        return

    set_banned(target_id, True)
    await update.message.reply_text(f"🚫 Usuario `{target_id}` ha sido baneado.", parse_mode="Markdown")


@require_not_banned
@require_admin
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Envía un mensaje a todos los usuarios registrados.
    Uso: /broadcast <mensaje>
    """
    if not context.args:
        await update.message.reply_text("Uso: /broadcast <mensaje>")
        return

    msg = " ".join(context.args)
    all_users = get_all_users()
    sent, failed = 0, 0

    for u in all_users:
        if u["is_banned"]:
            continue
        try:
            await context.bot.send_message(
                chat_id=u["user_id"],
                text=f"📢 *Mensaje del Administrador*\n\n{msg}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast completado.\n📤 Enviados: {sent} | ❌ Fallidos: {failed}"
    )


@require_not_banned
@require_admin
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Estadísticas generales del bot."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    premium = conn.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    banned = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
    reminders = conn.execute("SELECT COUNT(*) FROM reminders WHERE sent=0").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    alerts = conn.execute("SELECT COUNT(*) FROM investment_alerts").fetchone()[0]
    conn.close()

    await update.message.reply_text(
        "📊 *Estadísticas del Bot*\n\n"
        f"👥 Usuarios totales: {total}\n"
        f"⭐ Usuarios Premium: {premium}\n"
        f"🆓 Usuarios Free: {total - premium}\n"
        f"🚫 Usuarios baneados: {banned}\n"
        f"⏰ Recordatorios activos: {reminders}\n"
        f"📅 Eventos totales: {events}\n"
        f"📊 Alertas de inversión: {alerts}",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────────────────────────────────────
# ── SCHEDULER — Envío automático de recordatorios ──
# ─────────────────────────────────────────────────────────────────────────────

async def check_and_send_reminders(app):
    """
    Tarea programada que corre cada minuto.
    Revisa la BD y envía los recordatorios que ya vencieron.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    due = conn.execute(
        "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ?", (now,)
    ).fetchall()
    conn.close()

    for r in due:
        try:
            await app.bot.send_message(
                chat_id=r["user_id"],
                text=(
                    f"🔔 *¡Recordatorio!*\n\n"
                    f"📝 {r['message']}\n\n"
                    f"_Configurado para: {r['remind_at'][:16]}_"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Error enviando recordatorio {r['id']}: {e}")
        finally:
            # Marcar como enviado independientemente del resultado
            conn2 = get_connection()
            conn2.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (r["id"],))
            conn2.commit()
            conn2.close()


async def revoke_expired_subscriptions(app):
    """
    Tarea programada cada hora.
    Revoca automáticamente las suscripciones Premium que han expirado
    y notifica al usuario para que renueve.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    expired = conn.execute(
        "SELECT * FROM users WHERE is_premium = 1 AND is_admin = 0 "
        "AND subscription_expires IS NOT NULL AND subscription_expires <= ?",
        (now,)
    ).fetchall()

    for u in expired:
        conn.execute("UPDATE users SET is_premium = 0 WHERE user_id = ?", (u["user_id"],))
        try:
            await app.bot.send_message(
                chat_id=u["user_id"],
                text=(
                    "⏰ *Tu suscripción Premium ha finalizado.*\n\n"
                    "Gracias por haber sido Premium 🙏\n"
                    "Si quieres seguir disfrutando de las funciones exclusivas, "
                    "usa /upgrade para renovar tu plan en cualquier momento."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"No se pudo notificar expiración a {u['user_id']}: {e}")
        logger.info(f"🔒 Suscripción expirada revocada: user_id={u['user_id']}")

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ── MAIN — Punto de entrada ──
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # 1. Inicializar la base de datos
    init_database()
    logger.info("🤖 Arrancando DavoGram Bot…")

    # 2. Crear la aplicación de Telegram
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── Conversation Handlers ──

    # Recordatorio
    reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("recordatorio", cmd_recordatorio_start)],
        states={
            REM_MSG:  [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_get_message)],
            REM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_get_date)],
        },
        fallbacks=[CommandHandler("cancel", rem_cancel)],
    )

    # Evento
    event_conv = ConversationHandler(
        entry_points=[CommandHandler("crear_evento", cmd_crear_evento)],
        states={
            EVT_TITLE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, evt_get_title)],
            EVT_DESC:   [
                MessageHandler(filters.TEXT & ~filters.COMMAND, evt_get_desc),
                CommandHandler("skip", evt_skip_desc),
            ],
            EVT_DATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, evt_get_date)],
            EVT_PUBLIC: [CallbackQueryHandler(evt_get_public, pattern="^evt_public_")],
        },
        fallbacks=[CommandHandler("cancel", evt_cancel)],
    )

    # Alerta de inversión
    alert_conv = ConversationHandler(
        entry_points=[CommandHandler("nueva_alerta", cmd_nueva_alerta)],
        states={
            ALERT_ASSET:    [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_get_asset)],
            ALERT_MSG:      [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_get_msg)],
            ALERT_PRIORITY: [CallbackQueryHandler(alert_get_priority, pattern="^alert_prio_")],
        },
        fallbacks=[CommandHandler("cancel", alert_cancel)],
    )

    # ── ConversationHandler: Alerta de precio ──
    palert_conv = ConversationHandler(
        entry_points=[CommandHandler("alerta_precio", cmd_alerta_precio)],
        states={
            PA_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, pa_get_symbol)],
            PA_DIR:    [CallbackQueryHandler(pa_get_dir, pattern="^pa_dir_")],
            PA_PRICE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, pa_get_price)],
        },
        fallbacks=[CommandHandler("cancel", pa_cancel)],
    )

    # ── ConversationHandler: DM a todos (admin) ──
    dm_conv = ConversationHandler(
        entry_points=[CommandHandler("dm_todos", cmd_dm_todos_start)],
        states={
            DM_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, dm_get_message)],
        },
        fallbacks=[CommandHandler("cancel", dm_cancel)],
    )

    # ── Registrar todos los handlers ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("miperfil", cmd_miperfil))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("mis_recordatorios", cmd_mis_recordatorios))
    app.add_handler(CommandHandler("eventos_publicos", cmd_eventos_publicos))
    app.add_handler(CommandHandler("mis_eventos", cmd_mis_eventos))
    app.add_handler(CommandHandler("alertas", cmd_alertas))
    app.add_handler(CommandHandler("mis_alertas_precio", cmd_mis_alertas_precio))
    app.add_handler(CommandHandler("upgrade_user", cmd_upgrade_user))
    app.add_handler(CommandHandler("ban_user", cmd_ban_user))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("web", cmd_web))

    app.add_handler(reminder_conv)
    app.add_handler(event_conv)
    app.add_handler(alert_conv)
    app.add_handler(palert_conv)
    app.add_handler(dm_conv)

    # Callbacks inline
    app.add_handler(CallbackQueryHandler(cb_upgrade_info,    pattern="^upgrade_info$"))
    app.add_handler(CallbackQueryHandler(cb_buy_plan,        pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(cb_delete_reminder, pattern="^del_rem_"))
    app.add_handler(CallbackQueryHandler(cb_delete_event,    pattern="^del_evt_"))
    app.add_handler(CallbackQueryHandler(cb_delete_palert,   pattern="^del_pa_"))

    # Menú de navegación por botones
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(cb_menu_section, pattern="^menu_(free|premium|admin)$"))
    app.add_handler(CallbackQueryHandler(cb_menu_back,    pattern="^menu_back$"))

    # ── Handlers de pago nativo de Telegram ──
    from telegram.ext import PreCheckoutQueryHandler
    app.add_handler(PreCheckoutQueryHandler(handler_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handler_successful_payment))

    # ── Scheduler ──
    scheduler = AsyncIOScheduler()
    # Recordatorios cada 60 segundos
    scheduler.add_job(check_and_send_reminders, trigger="interval", seconds=60,  args=[app])
    # Alertas de precio cada 5 minutos
    scheduler.add_job(check_price_alerts,        trigger="interval", seconds=300, args=[app])
    # Revocar suscripciones expiradas cada hora
    scheduler.add_job(revoke_expired_subscriptions, trigger="interval", hours=1,  args=[app])
    
    from features.social import _resolve_giveaways
    # Resolver sorteos expirados cada 60 segundos
    scheduler.add_job(_resolve_giveaways, trigger="interval", seconds=60, args=[app])
    
    scheduler.start()
    # ── Registrar módulos de features ──
    from features.free    import register as register_free
    from features.premium import register as register_premium
    from features.admin   import register as register_admin
    from features.games   import register as register_games
    from features.utils   import register as register_utils
    from features.social  import register as register_social
    from features.ai      import register as register_ai

    register_free(app)
    register_premium(app, scheduler)  # también registra schedulers diarios
    register_admin(app)
    register_games(app)
    register_utils(app)
    register_social(app)
    register_ai(app)

    # 3. Arrancar el bot (polling)
    logger.info("✅ Bot en marcha. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
