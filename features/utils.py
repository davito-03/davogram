#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/utils.py — Utilidades Prácticas Generales
  /gasto      - Gestor de gastos personales rápidos
  /gastos     - Ver resumen de gastos del mes actual
  /pass       - Generador de contraseñas seguras
  /qr         - Creador / Lector de QR (usando api.qrserver.com)
  /conv       - Conversor de Unidades
"""

import logging
import random
import string
import requests
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from config import DB_PATH

logger = logging.getLogger("DavogramBot.utils")

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ─────────────────────────────────────────────────────────────────────────────
# /gasto y /gastos — Gestor de Gastos (Finanzas Personales)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /gasto <cantidad> <concepto>. Ej: /gasto 3.50 Cafe"""
    tg_user = update.effective_user
    args = context.args

    if len(args) < 2:
        await update.message.reply_text("💡 Uso: `/gasto <cantidad> <concepto>`\nEjemplo: `/gasto 3.50 Cafe`", parse_mode="Markdown")
        return

    try:
        amount = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ La cantidad debe ser un número (puedes usar punto o coma).")
        return

    concept = " ".join(args[1:])
    try:
        c = _conn()
        c.execute("INSERT INTO expenses (user_id, amount, concept) VALUES (?, ?, ?)", (tg_user.id, amount, concept))
        c.commit()
        c.close()
        await update.message.reply_text(f"✅ Gasto añadido: **{amount:.2f}€ en {concept}**.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error añadiendo gasto: {e}")
        await update.message.reply_text("❌ Ocurrió un error guardando el gasto.")

async def cmd_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el resumen de gastos del mes actual."""
    tg_user = update.effective_user
    try:
        c = _conn()
        # Filtrar mes actual (Y-m)
        current_month = datetime.now().strftime("%Y-%m")
        rows = c.execute("SELECT amount, concept, date FROM expenses WHERE user_id=? AND date LIKE ? ORDER BY date DESC", (tg_user.id, f"{current_month}%")).fetchall()
        c.close()

        if not rows:
            await update.message.reply_text("📊 Aún no tienes gastos registrados este mes.")
            return

        total = sum(r["amount"] for r in rows)
        text = f"📊 **Resumen de Gastos ({current_month})**\n\n💰 **Total:** {total:.2f}€\n\n"
        for i, r in enumerate(rows[:10]):
            text += f"• `{r['date'][:10]}`: {r['amount']:.2f}€ - {r['concept']}\n"

        if len(rows) > 10:
            text += f"\n_...y {len(rows)-10} gastos más._"

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error leyendo gastos: {e}")
        await update.message.reply_text("❌ Ocurrió un error obteniendo tus gastos.")


# ─────────────────────────────────────────────────────────────────────────────
# /pass — Generador de contraseñas seguras
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /pass [length]. Por defecto 16 caracteres."""
    length = 16
    if context.args:
        try:
            length = int(context.args[0])
            if length < 8 or length > 64:
                length = 16 # Fallback seguro
        except ValueError:
            pass

    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    pwd = "".join(random.choice(chars) for _ in range(length))

    # Asegurar al menos uno de cada tipo
    pwd_list = list(pwd)
    pwd_list[0] = random.choice(string.ascii_lowercase)
    pwd_list[1] = random.choice(string.ascii_uppercase)
    pwd_list[2] = random.choice(string.digits)
    pwd_list[3] = random.choice("!@#$%^&*()-_=+")
    random.shuffle(pwd_list)
    pwd = "".join(pwd_list)

    msg = await update.message.reply_text(
        f"🔐 **Contraseña Generada:**\n\n`{pwd}`\n\n_Esta contraseña es segura y aleatoria. Guárdala pronto, este mensaje no se autodestruye pero por seguridad bórralo luego._",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /qr — Lector / Creador de QR C2
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genera un QR con el texto/url proporcionado, o lee un QR si es una respuesta."""
    # Para lectura: respondemos a una foto con /qr
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]
        try:
            file = await context.bot.get_file(photo.file_id)
            file_url = file.file_path
            # Usar API de escaneo
            res = requests.get(f"http://api.qrserver.com/v1/read-qr-code/?fileurl={file_url}")
            data = res.json()
            qr_content = data[0]["symbol"][0]["data"]
            if not qr_content:
                qr_content = "No se pudo detectar ningún QR en la imagen."
            await update.message.reply_text(f"📷 **Contenido del QR:**\n`{qr_content}`", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error leyendo QR: {e}")
            await update.message.reply_text("❌ Hubo un error procesando la imagen del QR.")
        return

    # Para creación:
    if not context.args:
        await update.message.reply_text("Para generar: `/qr <texto/url>`\nPara leer: Responde a una foto con `/qr`", parse_mode="Markdown")
        return

    text = " ".join(context.args)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={requests.utils.quote(text)}"
    
    await update.message.reply_photo(
        photo=qr_url,
        caption="📲 Aquí tienes tu código QR."
    )


# ─────────────────────────────────────────────────────────────────────────────
# /conv — Conversor Universal Básico
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ejemplos: /conv 10 km a mi | /conv 30 c a f | /conv 5 kg a lbs"""
    args = context.args
    # Simplificado: /conv 10 km mi o /conv 10 km a mi
    if len(args) < 3:
        await update.message.reply_text(
            "🔄 **Uso:** `/conv <cantidad> <origen> <destino>`\n"
            "Ejemplos:\n `/conv 10 km mi` (distancia)\n `/conv 30 c f` (temperatura)\n `/conv 5 kg lb` (peso)",
            parse_mode="Markdown"
        )
        return

    try:
        val = float(args[0].replace(",", "."))
    except ValueError:
        return await update.message.reply_text("❌ La cantidad debe ser un número.")

    # Parse symbols
    origin = args[1].lower().strip()
    dest = args[2].lower().strip() if args[2].lower() != "a" else (args[3].lower().strip() if len(args)>3 else "")

    conversions = {
        # Longitud
        ("km", "mi"): val * 0.621371,
        ("mi", "km"): val * 1.60934,
        ("m", "ft"): val * 3.28084,
        ("ft", "m"): val * 0.3048,
        ("cm", "in"): val * 0.393701,
        ("in", "cm"): val * 2.54,
        # Peso
        ("kg", "lb"): val * 2.20462,
        ("lb", "kg"): val * 0.453592,
        ("g", "oz"): val * 0.035274,
        ("oz", "g"): val * 28.3495,
        # Temperatura (Fórmula especial)
        ("c", "f"): (val * 9/5) + 32,
        ("f", "c"): (val - 32) * 5/9,
    }

    key = (origin, dest)
    res = conversions.get(key)
    
    if res is not None:
        await update.message.reply_text(f"🔄 **{val} {origin.upper()}** equivalen a **{res:.2f} {dest.upper()}**.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Conversión no soportada. (Ej: km<>mi, kg<>lb, c<>f)")


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    app.add_handler(CommandHandler("gasto",  cmd_gasto))
    app.add_handler(CommandHandler("gastos", cmd_gastos))
    app.add_handler(CommandHandler("pass",   cmd_pass))
    app.add_handler(CommandHandler("qr",     cmd_qr))
    app.add_handler(CommandHandler("conv",   cmd_conv))
    
    logger.info("✅ features/utils: /gasto /gastos /pass /qr /conv")
