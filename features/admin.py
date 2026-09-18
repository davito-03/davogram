#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/admin.py — Comandos de administración extra
  /unban_user     Desbanear usuario
  /buscar_usuario Buscar usuario por @username o ID
  /trial_user     Dar premium temporal (trial)
  /ingresos       Resumen de pagos Stars recibidos
  /limpiar_bd     Eliminar usuarios inactivos (con confirmación)
"""

import sqlite3
import logging
import functools
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import DB_PATH, SUPERADMIN_USERNAME

logger = logging.getLogger("DavogramBot.admin")


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_admin_db():
    """Crea tabla de log de pagos si no existe."""
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            plan       TEXT NOT NULL,
            stars      INTEGER NOT NULL,
            paid_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Decorador require_admin local
# ─────────────────────────────────────────────────────────────────────────────

def _require_admin(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        tg_user = update.effective_user
        conn    = _conn()
        user    = conn.execute("SELECT is_admin FROM users WHERE user_id = ?", (tg_user.id,)).fetchone()
        conn.close()
        if not user or not user["is_admin"]:
            await update.effective_message.reply_text("⛔ No tienes permisos de administrador.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# /unban_user — Desbanear usuario
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /unban_user <user_id>"""
    if not context.args:
        await update.message.reply_text("Uso: /unban_user <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ El user_id debe ser un número.")
        return

    conn = _conn()
    rows = conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,)).rowcount
    conn.commit()
    conn.close()

    if rows:
        await update.message.reply_text(f"✅ Usuario `{target_id}` desbaneado.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ Tu cuenta ha sido *desbaneada*. Ya puedes usar el bot con normalidad.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(f"❌ Usuario `{target_id}` no encontrado.", parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# /buscar_usuario — Lookup de usuario
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_buscar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /buscar_usuario <@username o user_id>"""
    if not context.args:
        await update.message.reply_text("Uso: /buscar_usuario <@username o user_id>")
        return

    query = context.args[0].lstrip("@")
    conn  = _conn()

    if query.isdigit():
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (int(query),)).fetchone()
    else:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (query,)).fetchone()

    if not user:
        conn.close()
        await update.message.reply_text("❌ Usuario no encontrado en la base de datos.")
        return

    # Estadísticas del usuario
    rems  = conn.execute("SELECT COUNT(*) FROM reminders WHERE user_id = ? AND sent = 0", (user["user_id"],)).fetchone()[0]
    evts  = conn.execute("SELECT COUNT(*) FROM events WHERE creator_id = ?", (user["user_id"],)).fetchone()[0]
    pals  = conn.execute("SELECT COUNT(*) FROM price_alerts WHERE user_id = ? AND triggered = 0", (user["user_id"],)).fetchone()[0]
    conn.close()

    plan = "👑 Admin+Premium" if user["is_admin"] else ("⭐ Premium" if user["is_premium"] else "🆓 Free")
    banned = "🚫 Sí" if user["is_banned"] else "✅ No"

    exp = user["subscription_expires"][:10] if user["subscription_expires"] else ("♾️ Vitalicio" if user["is_premium"] else "—")

    await update.message.reply_text(
        f"👤 <b>Perfil de usuario</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: @{user['username'] or '—'}\n"
        f"📛 Nombre: {user['full_name'] or '—'}\n"
        f"📋 Plan: {plan}\n"
        f"📅 Registro: {user['join_date'][:10]}\n"
        f"⏳ Premium hasta: {exp}\n"
        f"🚫 Baneado: {banned}\n\n"
        f"📊 Actividad:\n"
        f"  ⏰ Recordatorios activos: {rems}\n"
        f"  📌 Eventos: {evts}\n"
        f"  🔔 Alertas de precio: {pals}",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /trial_user — Premium temporal (trial)
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_trial_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /trial_user <user_id> <dias>"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Uso: /trial_user <user_id> <días>")
        return
    try:
        target_id = int(args[0])
        days      = int(args[1])
        if days <= 0 or days > 365:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Parámetros inválidos. Días debe ser entre 1 y 365.")
        return

    expires  = datetime.now() + timedelta(days=days)
    exp_str  = expires.strftime("%Y-%m-%d %H:%M:%S")

    conn = _conn()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (target_id,)).fetchone()
    if not user:
        conn.close()
        await update.message.reply_text(f"❌ Usuario `{target_id}` no encontrado.", parse_mode="Markdown")
        return

    # Si ya es premium con más tiempo, extender desde su fecha actual
    if user["is_premium"] and user["subscription_expires"]:
        try:
            current_exp = datetime.strptime(user["subscription_expires"], "%Y-%m-%d %H:%M:%S")
            if current_exp > datetime.now():
                expires = current_exp + timedelta(days=days)
                exp_str = expires.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    conn.execute(
        "UPDATE users SET is_premium = 1, subscription_expires = ? WHERE user_id = ?",
        (exp_str, target_id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎁 Trial activado: `{target_id}` → Premium por *{days} días* (hasta {expires.strftime('%d/%m/%Y')}).",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎁 *¡Tienes un regalo!*\n\nEl administrador te ha otorgado *{days} días Premium* de trial.\n"
                 f"Acceso hasta el *{expires.strftime('%d/%m/%Y')}*. Usa /help para ver todo lo que puedes hacer.",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# /ingresos — Resumen de pagos Stars
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_ingresos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el resumen de Stars recibidas por plan."""
    conn  = _conn()
    total = conn.execute("SELECT SUM(stars), COUNT(*) FROM payments_log").fetchone()
    by_plan = conn.execute(
        "SELECT plan, SUM(stars) as stars, COUNT(*) as cnt FROM payments_log GROUP BY plan"
    ).fetchall()
    conn.close()

    total_stars = total[0] or 0
    total_ops   = total[1] or 0

    if total_ops == 0:
        await update.message.reply_text("📊 Aún no hay pagos registrados.")
        return

    lines = [f"💰 <b>Resumen de Ingresos</b>\n\n"
             f"⭐ Stars totales: <b>{total_stars:,}</b>\n"
             f"🧾 Pagos totales: <b>{total_ops}</b>\n\n"
             f"<b>Por plan:</b>"]
    for r in by_plan:
        lines.append(f"  • {r['plan'].title()}: {r['cnt']} pago(s) — {r['stars']:,} ⭐")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /limpiar_bd — Eliminar usuarios inactivos (con confirmación)
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_limpiar_bd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra cuántos usuarios inactivos (+180 días, sin premium, sin recordatorios) hay."""
    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S")
    conn   = _conn()
    count  = conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_premium = 0 AND is_admin = 0 AND join_date < ? "
        "AND user_id NOT IN (SELECT DISTINCT user_id FROM reminders WHERE sent = 0) "
        "AND user_id NOT IN (SELECT DISTINCT creator_id FROM events)",
        (cutoff,)
    ).fetchone()[0]
    conn.close()

    if count == 0:
        await update.message.reply_text("✅ No hay usuarios inactivos para limpiar.")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🗑 Sí, borrar {count} usuarios", callback_data=f"limpiar_confirm_{count}"),
        InlineKeyboardButton("❌ Cancelar", callback_data="limpiar_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ Se encontraron *{count} usuarios inactivos* (+180 días sin actividad, sin Premium).\n\n"
        "¿Confirmas que quieres eliminarlos de la base de datos?",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def cb_limpiar_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Verify admin
    conn = _conn()
    user = conn.execute("SELECT is_admin FROM users WHERE user_id = ?", (query.from_user.id,)).fetchone()
    if not user or not user["is_admin"]:
        await query.edit_message_text("⛔ Sin permisos.")
        conn.close()
        return

    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S")
    deleted = conn.execute(
        "DELETE FROM users WHERE is_premium = 0 AND is_admin = 0 AND join_date < ? "
        "AND user_id NOT IN (SELECT DISTINCT user_id FROM reminders WHERE sent = 0) "
        "AND user_id NOT IN (SELECT DISTINCT creator_id FROM events)",
        (cutoff,)
    ).rowcount
    conn.commit()
    conn.close()
    await query.edit_message_text(f"🗑 {deleted} usuarios inactivos eliminados de la base de datos.")


async def cb_limpiar_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Limpieza cancelada.")


# ─────────────────────────────────────────────────────────────────────────────
# /logs — Últimas N líneas del log
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /logs [n]  — muestra las últimas N líneas del log (defecto: 50)"""
    import os
    n = 50
    if context.args:
        try:
            n = min(int(context.args[0]), 200)
        except ValueError:
            pass
    log_path = "davogram.log"
    if not os.path.exists(log_path):
        await update.message.reply_text("❌ Archivo de log no encontrado.")
        return
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    last_lines = "".join(lines[-n:])
    if not last_lines.strip():
        await update.message.reply_text("📋 El log está vacío.")
        return
    # Enviar como documento si es largo, como texto si es corto
    if len(last_lines) > 3800:
        import io
        await update.message.reply_document(
            document=io.BytesIO(last_lines.encode("utf-8")),
            filename=f"davogram_last_{n}_lines.txt",
            caption=f"📋 Últimas {n} líneas del log.",
        )
    else:
        await update.message.reply_text(
            f"📋 <b>Últimas {n} líneas del log:</b>\n<pre>{last_lines[-3500:]}</pre>",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────────────────────
# /backup_bd — Enviar base de datos como archivo
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_backup_bd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el archivo davogram.db como documento."""
    import os, shutil
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("❌ Base de datos no encontrada.")
        return
    # Copiar a archivo temporal para evitar bloqueos
    tmp = DB_PATH + ".backup_tmp"
    shutil.copy2(DB_PATH, tmp)
    try:
        size_kb = os.path.getsize(tmp) // 1024
        with open(tmp, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="davogram_backup.db",
                caption=f"🗄 Backup de la BD — {size_kb} KB\n{datetime.now().strftime('%d/%m/%Y %H:%M')}",
            )
    finally:
        os.remove(tmp)


# ─────────────────────────────────────────────────────────────────────────────
# /set_admin — Dar o quitar admin a un usuario
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_set_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /set_admin <user_id> <1|0>"""
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_admin <user_id> <1|0>\n1 = dar admin, 0 = quitar admin")
        return
    try:
        target_id = int(context.args[0])
        value     = int(context.args[1])
        if value not in (0, 1):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Parámetros inválidos.")
        return
    conn = _conn()
    rows = conn.execute(
        "UPDATE users SET is_admin = ? WHERE user_id = ?", (value, target_id)
    ).rowcount
    conn.commit()
    conn.close()
    if rows:
        action = "otorgado" if value else "revocado"
        await update.message.reply_text(
            f"✅ Admin <b>{action}</b> al usuario <code>{target_id}</code>.",
            parse_mode="HTML",
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"{'👑 Te han concedido permisos de administrador.' if value else '⚠️ Tus permisos de administrador han sido revocados.'}",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(f"❌ Usuario <code>{target_id}</code> no encontrado.", parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /reiniciar — Reiniciar el proceso del bot
# ─────────────────────────────────────────────────────────────────────────────

@_require_admin
async def cmd_reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reinicia el proceso del bot."""
    import os, sys
    await update.message.reply_text("🔄 Reiniciando bot… vuelve en unos segundos.")
    logger.info(f"Bot reiniciado por admin {update.effective_user.id}")
    # Sustituir proceso actual (sin necesitar sudo)
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    """Registra handlers admin extra."""
    init_admin_db()
    app.add_handler(CommandHandler("unban_user",         cmd_unban_user))
    app.add_handler(CommandHandler("buscar_usuario",     cmd_buscar_usuario))
    app.add_handler(CommandHandler("trial_user",         cmd_trial_user))
    app.add_handler(CommandHandler("ingresos",           cmd_ingresos))
    app.add_handler(CommandHandler("limpiar_bd",         cmd_limpiar_bd))
    app.add_handler(CommandHandler("logs",               cmd_logs))
    app.add_handler(CommandHandler("backup_bd",          cmd_backup_bd))
    app.add_handler(CommandHandler("set_admin",          cmd_set_admin))
    app.add_handler(CommandHandler("reiniciar",          cmd_reiniciar))
    app.add_handler(CallbackQueryHandler(cb_limpiar_confirm, pattern=r"^limpiar_confirm_"))
    app.add_handler(CallbackQueryHandler(cb_limpiar_cancel,  pattern=r"^limpiar_cancel$"))
    logger.info("✅ features/admin: /unban_user /buscar_usuario /trial_user /ingresos /limpiar_bd /logs /backup_bd /set_admin /reiniciar")
