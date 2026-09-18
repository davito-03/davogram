#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/ai.py — Funciones Avanzadas de IA y Multimedia
  (REQUIEREN PREMIUM)
  /transcribir - Extraer texto de notas de voz enviadas
  /vision      - Analizar una foto que hayas enviado
  /habla       - Texto a Voz (TTS)
  /meme        - Escribir texto sobre una imagen
"""

import logging
import os
import random
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, filters, ContextTypes
from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger("DavogramBot.ai")

# ─────────────────────────────────────────────────────────────────────────────
# Utils - Check Premium
# ─────────────────────────────────────────────────────────────────────────────
def _is_premium(update: Update) -> bool:
    from main import get_or_create_user
    tg_user = update.effective_user
    row = get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    return row["is_premium"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# /transcribir — Whisper (Voz a Texto)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_transcribir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_premium(update):
        await update.message.reply_text("⭐ Este comando es exclusivo para usuarios Premium.")
        return

    msg = update.message.reply_to_message
    if not msg or (not msg.voice and not msg.audio):
        await update.message.reply_text("💡 Debes responder a una nota de voz o audio con `/transcribir`.", parse_mode="Markdown")
        return

    progress = await update.message.reply_text("🎧 Descargando audio...", parse_mode="Markdown")
    try:
        file = await context.bot.get_file(msg.voice.file_id if msg.voice else msg.audio.file_id)
        # Bajar a disco (Groq Whisper requiere en disco o bytes formales)
        path = f"temp_voice_{update.effective_user.id}.ogg"
        await file.download_to_drive(path)

        await progress.edit_text("🎧 Transcribiendo con IA (Whisper)...", parse_mode="Markdown")
        
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        with open(path, "rb") as ogg:
            transcription = client.audio.transcriptions.create(
              file=(path, ogg.read()),
              model="whisper-large-v3-turbo",
              response_format="json",
              language="es"
            )

        text = transcription.text
        os.remove(path)

        if len(text) > 4000:
            text = text[:4000] + "..."

        await progress.edit_text(f"📝 **Transcripción:**\n\n_{text}_", parse_mode="Markdown")
    except ImportError:
        await progress.edit_text("❌ Librería `groq` no instalada.")
    except Exception as e:
        logger.error(f"Error transcribiendo: {e}")
        await progress.edit_text("❌ Error al transcribir. Asegúrate de que el audio sea corto y válido.")


# ─────────────────────────────────────────────────────────────────────────────
# /vision — Analizar Imágenes (Groq Llama Vision)
# ─────────────────────────────────────────────────────────────────────────────

import base64

async def cmd_vision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_premium(update):
        await update.message.reply_text("⭐ Este comando es exclusivo para usuarios Premium.")
        return

    msg = update.message.reply_to_message
    if not msg or not msg.photo:
        await update.message.reply_text("💡 Debes responder a una imagen con `/vision [pregunta]`.", parse_mode="Markdown")
        return

    prompt = " ".join(context.args) if context.args else "Describe detalladamente esta imagen."
    progress = await update.message.reply_text("👁️ Analizando imagen...", parse_mode="Markdown")

    try:
        photo = msg.photo[-1] # Mayor resolución
        file = await context.bot.get_file(photo.file_id)
        
        path = f"temp_vision_{update.effective_user.id}.jpg"
        await file.download_to_drive(path)

        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        with open(path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

        response = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                    ],
                }
            ],
            max_tokens=500,
        )

        res_text = response.choices[0].message.content
        os.remove(path)

        await progress.edit_text(f"👁️ **Análisis Visual:**\n\n{res_text}", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en visión: {e}")
        await progress.edit_text("❌ Hubo un error procesando la imagen.")


# ─────────────────────────────────────────────────────────────────────────────
# /habla — Text to Speech Básico (Google TTS o Groq TTS si existiera)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_habla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /habla Hola me llamo Davo"""
    if not _is_premium(update):
        await update.message.reply_text("⭐ Este comando es exclusivo para usuarios Premium.")
        return

    if not context.args:
        await update.message.reply_text("💡 Uso: `/habla <texto>`", parse_mode="Markdown")
        return

    text = " ".join(context.args)
    if len(text) > 500:
        await update.message.reply_text("❌ El texto es muy largo (máx. 500 caracteres).")
        return

    progress = await update.message.reply_text("🎙️ Generando voz...", parse_mode="Markdown")

    try:
        from gtts import gTTS
        tts = gTTS(text, lang='es')
        path = f"temp_tts_{update.effective_user.id}.ogg"
        tts.save(path)

        with open(path, "rb") as f:
            await update.message.reply_voice(voice=f)
        
        os.remove(path)
        await progress.delete()

    except ImportError:
        await progress.edit_text("❌ Librería `gtts` no instalada. (pip install gTTS)")
    except Exception as e:
        logger.error(f"Error TTS: {e}")
        await progress.edit_text("❌ Ocurrió un error generando el audio.")


# ─────────────────────────────────────────────────────────────────────────────
# /meme — Generador Rápido de Memes
# ─────────────────────────────────────────────────────────────────────────────
# Utilizaremos APIMeme (creador rápido)

async def cmd_meme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /meme [Arriba] | [Abajo] respondiendo a una foto, 
       o sobre plantillas predeterminadas si no se responde a foto (limitado)."""
    if not _is_premium(update):
        return await update.message.reply_text("⭐ Exclusivo Premium.")

    msg = update.message.reply_to_message
    if not msg or not msg.photo:
        await update.message.reply_text("💡 Debes responder a una foto tuya con el bot y usar:\n`/meme Texto Arriba | Texto Abajo`", parse_mode="Markdown")
        return

    # Dividir texto
    full_text = " ".join(context.args)
    if "|" in full_text:
        top, bottom = full_text.split("|", 1)
    else:
        top, bottom = full_text, ""

    progress = await update.message.reply_text("🎨 Pintando meme...", parse_mode="Markdown")

    try:
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        from PIL import Image, ImageDraw, ImageFont
        import requests
        from io import BytesIO

        # Bajar imagen
        res = requests.get(file.file_path)
        img = Image.open(BytesIO(res.content)).convert("RGBA")
        
        draw = ImageDraw.Draw(img)
        # Intentar cargar fuente, usar default
        try:
            # Impact no suele estar preinstalada en servers, usamos Default pero size es ignorable.
            # Idealmente tendrías un .ttf guardado.
            font = ImageFont.truetype("arial.ttf", int(img.width/10))
        except IOError:
            font = ImageFont.load_default()

        def draw_text_with_outline(text, pos):
            # Pos_y = pos "top" or "bottom"
            text = text.strip().upper()
            if not text: return
            # Para medir texto en Pillow moderno 
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            
            x = (img.width - text_w) / 2
            y = 10 if pos == "top" else (img.height - text_h - 20)

            # Contorno negro
            thick = 2
            for adj_x in [-thick, 0, thick]:
                for adj_y in [-thick, 0, thick]:
                    draw.text((x + adj_x, y + adj_y), text, font=font, fill="black")
            # Texto blanco
            draw.text((x, y), text, font=font, fill="white")

        draw_text_with_outline(top, "top")
        draw_text_with_outline(bottom, "bottom")

        path = f"temp_meme_{update.effective_user.id}.jpg"
        img.convert("RGB").save(path, format="JPEG")

        with open(path, "rb") as f:
            await update.message.reply_photo(photo=f)
            
        os.remove(path)
        await progress.delete()

    except ImportError:
        await progress.edit_text("❌ Falta la librería `Pillow` (pip install Pillow).")
    except Exception as e:
        logger.error(f"Error generando meme: {e}")
        await progress.edit_text("❌ Error al procesar tu meme.")


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    app.add_handler(CommandHandler("transcribir", cmd_transcribir))
    app.add_handler(CommandHandler("vision",      cmd_vision))
    app.add_handler(CommandHandler("habla",       cmd_habla))
    app.add_handler(CommandHandler("meme",        cmd_meme))
    logger.info("✅ features/ai: /transcribir /vision /habla /meme")
