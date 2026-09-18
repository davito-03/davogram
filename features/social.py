#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/social.py — Funciones Sociales y de Moderación
  [Automático] - Sistema de Niveles/XP por escribir en grupos
  /nivel      - Ver tu nivel y XP
  /top        - Ver el ranking de niveles del grupo
  /giveaway   - Crear un sorteo transparente (Admin solo por seguridad)
"""

import logging
import sqlite3
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import DB_PATH

logger = logging.getLogger("DavogramBot.social")

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Sistema de Niveles y XP
# ─────────────────────────────────────────────────────────────────────────────

def _calc_level(xp: int) -> int:
    """Fórmula simple de XP: Nivel = (XP / 100) ^ 0.5 o algo similar."""
    # Para llegar al nivel N, necesitas 100 * (N^2) XP
    return int((xp / 100) ** 0.5) + 1

async def handler_xp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suma XP a los usuarios que escriben en grupos."""
    # Solo en grupos
    if not update.effective_chat or update.effective_chat.type == "private":
        return
    if not update.effective_user or update.effective_user.is_bot:
        return

    tg_user = update.effective_user
    chat_id = str(update.effective_chat.id)

    # 5 a 15 XP aleatorio por mensaje
    import random
    xp_gain = random.randint(5, 15)

    try:
        c = _conn()
        c.execute("""
            INSERT INTO levels (user_id, guild_id, xp, level)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET xp = xp + ?
        """, (tg_user.id, chat_id, xp_gain, 1, xp_gain))
        c.commit()

        # Comprobar si subió de nivel
        row = c.execute("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", (tg_user.id, chat_id)).fetchone()
        c.close()

        if row:
            current_xp = row["xp"]
            current_lvl = row["level"]
            new_lvl = _calc_level(current_xp)
            if new_lvl > current_lvl:
                c = _conn()
                c.execute("UPDATE levels SET level=? WHERE user_id=? AND guild_id=?", (new_lvl, tg_user.id, chat_id))
                c.commit()
                c.close()
                await update.message.reply_text(f"🎉 ¡Felicidades, {tg_user.mention_html()}! Has subido al **Nivel {new_lvl}**.", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error sumando XP: {e}")


async def cmd_nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el nivel actual del usuario en el grupo."""
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ El sistema de niveles solo funciona en grupos.")
        return

    tg_user = update.effective_user
    chat_id = str(update.effective_chat.id)

    try:
        c = _conn()
        row = c.execute("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", (tg_user.id, chat_id)).fetchone()
        c.close()

        if not row:
            await update.message.reply_text(f"📊 {tg_user.mention_html()}, aún no tienes nivel. ¡Escribe algo!", parse_mode="HTML")
            return

        xp = row["xp"]
        lvl = row["level"]
        next_lvl_xp = 100 * (lvl ** 2)
        
        await update.message.reply_text(
            f"🏅 **Nivel de {tg_user.mention_html()}**\n\n"
            f"🔹 **Nivel:** {lvl}\n"
            f"🔹 **XP:** {xp} / {next_lvl_xp}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error cmd_nivel: {e}")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el top 10 del grupo."""
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ El ranking solo funciona en grupos.")
        return

    chat_id = str(update.effective_chat.id)

    try:
        c = _conn()
        rows = c.execute("SELECT user_id, xp, level FROM levels WHERE guild_id=? ORDER BY xp DESC LIMIT 10", (chat_id,)).fetchall()
        c.close()

        if not rows:
            await update.message.reply_text("📊 Aún no hay nadie en el ranking de este grupo.")
            return

        text = f"🏆 **Top 10 - Nivel del Grupo**\n\n"
        for i, r in enumerate(rows, 1):
            text += f"{i}. User `{r['user_id']}` - Nivel {r['level']} ({r['xp']} XP)\n"
            # TODO: Idealmente mapear user_id a nombre consultando la tabla users, 
            # pero como `users` puede no tener los nicks actualizados del grupo...

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error top: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /giveaway — Sorteos
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia un sorteo: /giveaway 24h Premio épico"""
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Los sorteos se hacen en grupos.")
        return

    # Verificar si es admin del grupo
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if chat_member.status not in ["creator", "administrator"]:
        await update.message.reply_text("❌ Solo administradores del grupo pueden crear sorteos.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("💡 Uso: `/giveaway <horas> <premio>`\nEj: `/giveaway 24 Nitro Classic`", parse_mode="Markdown")
        return

    try:
        horas = float(args[0].replace('h',''))
    except ValueError:
        return await update.message.reply_text("❌ El tiempo debe ser un número u horas (Ej: 24).")

    prize = " ".join(args[1:])
    end_time = (datetime.now() + timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")

    # Enviar mensaje de sorteo
    msg = await update.message.reply_text(
        f"🎉 **¡SORTEO!** 🎉\n\n"
        f"🎁 **Premio:** {prize}\n"
        f"⏳ **Termina:** {end_time} UTC\n\n"
        f"👇 ¡Pulsa el botón para participar!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎉 Participar (0)", callback_data="gw_join")]])
    )

    # Registrar en DB
    try:
        c = _conn()
        c.execute("INSERT INTO giveaways (chat_id, message_id, prize, end_time, participants_json) VALUES (?, ?, ?, ?, ?)",
                  (str(update.effective_chat.id), str(msg.message_id), prize, end_time, "[]"))
        c.commit()
        c.close()
    except Exception as e:
        logger.error(f"Error creando giveaway: {e}")
        await update.message.reply_text("Ocurrió un error guardando el sorteo.")


async def cb_giveaway_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el botón de participar en el sorteo."""
    query = update.callback_query
    tg_user = query.from_user

    chat_id = str(query.message.chat.id)
    msg_id = str(query.message.message_id)

    try:
        c = _conn()
        row = c.execute("SELECT * FROM giveaways WHERE chat_id=? AND message_id=?", (chat_id, msg_id)).fetchone()
        if not row:
            c.close()
            return await query.answer("Este sorteo ha expirado o ya no existe.")

        ends = datetime.strptime(row["end_time"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() > ends:
            # Podríamos disparar el finalizador aquí, pero el APScheduler se encarga
            c.close()
            return await query.answer("El sorteo ha finalizado.", show_alert=True)

        parts = json.loads(row["participants_json"])
        
        user_info = {"id": tg_user.id, "name": tg_user.full_name}
        
        if any(p["id"] == tg_user.id for p in parts):
            # Ya participa, salir (o quitarse)
            parts = [p for p in parts if p["id"] != tg_user.id]
            resp = "Te has salido del sorteo."
        else:
            parts.append(user_info)
            resp = "¡Estás participando en el sorteo!"

        # Actualizar DB
        c.execute("UPDATE giveaways SET participants_json=? WHERE id=?", (json.dumps(parts), row["id"]))
        c.commit()
        c.close()

        # Actualizar botón
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🎉 Participar ({len(parts)})", callback_data="gw_join")]])
        await query.edit_message_reply_markup(reply_markup=kb)
        await query.answer(resp)

    except Exception as e:
        logger.error(f"Error cb_giveaway: {e}")
        await query.answer("Error al procesar.")


async def _resolve_giveaways(context: ContextTypes.DEFAULT_TYPE):
    """Llamado periódicamente por apscheduler para resolver sorteos caducados."""
    try:
        c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Seleccionar sorteos pasados de fecha
        ended = c.execute("SELECT * FROM giveaways WHERE end_time <= ?", (now,)).fetchall()
        for gw in ended:
            parts = json.loads(gw["participants_json"])
            if not parts:
                winner_msg = "\n❌ Sorteo cancelado: No hubo participantes."
            else:
                import random
                winner = random.choice(parts)
                winner_msg = f"\n🏆 **¡GANADOR!** 🎉\n¡Felicidades <a href='tg://user?id={winner['id']}'>{winner['name']}</a>!"

            # Enviar mensaje final al chat origen
            try:
                await context.bot.send_message(
                    chat_id=int(gw["chat_id"]),
                    reply_to_message_id=int(gw["message_id"]),
                    text=f"🎁 Sorteo finalizado: **{gw['prize']}**" + winner_msg,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"No se pudo enviar resultado de GW {gw['id']}: {e}")

            # Borrar sorteo
            c.execute("DELETE FROM giveaways WHERE id=?", (gw["id"],))
        c.commit()
        c.close()

    except Exception as e:
        logger.error(f"Error resolvent giveaways: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    app.add_handler(CommandHandler("nivel",    cmd_nivel))
    app.add_handler(CommandHandler("top",      cmd_top))
    app.add_handler(CommandHandler("giveaway", cmd_giveaway))
    app.add_handler(CallbackQueryHandler(cb_giveaway_join, pattern=r"^gw_join$"))
    # Capturar TODOS los mensajes de texto para el XP (baja prioridad, group=20)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handler_xp), group=20)
    logger.info("✅ features/social: /nivel /top /giveaway (+XP Tracking)")
