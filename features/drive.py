#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/drive.py — Subida automática de vídeos a Google Drive

Comportamiento:
  - Solo actúa cuando el remitente es @davito_03 (SuperAdmin).
  - Al recibir un vídeo (directo o reenviado), lo descarga de Telegram
    y lo sube a la carpeta de Drive configurada en config.py.
  - Responde con un mensaje de confirmación y el link al archivo.
  - Cualquier otro usuario es ignorado silenciosamente.

Requiere:
  pip install google-api-python-client google-auth
  gdrive_credentials.json  (Service Account key en el directorio raíz del bot)
"""

import os
import logging
import tempfile

from telegram import Update
from telegram.ext import MessageHandler, filters, ContextTypes

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

from config import GDRIVE_FOLDER_ID, GDRIVE_CREDENTIALS_FILE, SUPERADMIN_USERNAME

logger = logging.getLogger("DavogramBot.drive")

# ─────────────────────────────────────────────────────────────────────────────
# Cliente de Google Drive (inicializado una sola vez al importar el módulo)
# ─────────────────────────────────────────────────────────────────────────────

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

try:
    _creds = service_account.Credentials.from_service_account_file(
        GDRIVE_CREDENTIALS_FILE, scopes=_SCOPES
    )
    _drive = build("drive", "v3", credentials=_creds, cache_discovery=False)
    logger.info("✅ Cliente de Google Drive inicializado correctamente.")
except Exception as _e:
    _drive = None
    logger.error(f"❌ No se pudo inicializar Google Drive: {_e}")


# ─────────────────────────────────────────────────────────────────────────────
# Función de subida
# ─────────────────────────────────────────────────────────────────────────────

def _upload_to_drive(filepath: str, filename: str, mimetype: str = "video/mp4") -> str | None:
    """
    Sube un archivo local a la carpeta de Drive configurada.
    Devuelve el link web al archivo, o None si falla.
    """
    if _drive is None:
        logger.error("Google Drive no está inicializado.")
        return None

    try:
        file_metadata = {
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
        }
        media = MediaFileUpload(filepath, mimetype=mimetype, resumable=True)
        uploaded = (
            _drive.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink, name")
            .execute()
        )
        link = uploaded.get("webViewLink", "")
        file_id = uploaded.get("id", "")
        logger.info(f"📤 Vídeo subido a Drive: {uploaded.get('name')} (id={file_id})")

        # Hacer el archivo accesible con el link (reader para cualquiera con el link)
        _drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return link
    except Exception as e:
        logger.error(f"Error subiendo a Drive: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Handler de Telegram
# ─────────────────────────────────────────────────────────────────────────────

async def handler_video_to_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Se ejecuta cuando el bot recibe un mensaje con vídeo.
    Solo procesa el vídeo si lo envía @davito_03.
    """
    tg_user = update.effective_user
    if not tg_user:
        return

    # Verificar que es el SuperAdmin
    username = (tg_user.username or "").lower().lstrip("@")
    if username != SUPERADMIN_USERNAME.lower():
        return  # Ignorar silenciosamente a cualquier otro usuario

    if _drive is None:
        await update.message.reply_text(
            "❌ *Error de configuración:* No se pudo conectar con Google Drive.\n"
            "Revisa que `gdrive_credentials.json` está en la carpeta del bot.",
            parse_mode="Markdown",
        )
        return

    video = update.message.video
    if not video:
        return

    # Nombre del archivo: usar el file_name original si existe, o generar uno
    original_name = getattr(video, "file_name", None) or f"video_{video.file_unique_id}.mp4"
    mime_type = getattr(video, "mime_type", None) or "video/mp4"

    # Notificar al usuario que se está procesando
    processing_msg = await update.message.reply_text(
        f"⏳ Descargando y subiendo *{original_name}* a Google Drive…",
        parse_mode="Markdown",
    )

    # Descargar el vídeo de Telegram a un archivo temporal
    try:
        tg_file = await context.bot.get_file(video.file_id)
    except Exception as e:
        logger.error(f"Error obteniendo archivo de Telegram: {e}")
        await processing_msg.edit_text("❌ No se pudo descargar el vídeo de Telegram.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, original_name)

        try:
            await tg_file.download_to_drive(local_path)
        except Exception as e:
            logger.error(f"Error descargando vídeo: {e}")
            await processing_msg.edit_text("❌ Error al descargar el vídeo.")
            return

        # Subir a Google Drive
        link = _upload_to_drive(local_path, original_name, mime_type)

    if link:
        size_mb = round(video.file_size / (1024 * 1024), 2) if video.file_size else "?"
        await processing_msg.edit_text(
            f"✅ *Vídeo subido a Google Drive*\n\n"
            f"📄 Nombre: `{original_name}`\n"
            f"📦 Tamaño: {size_mb} MB\n"
            f"🔗 [Ver en Drive]({link})",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        logger.info(f"✅ Vídeo '{original_name}' subido correctamente por @{username}.")
    else:
        await processing_msg.edit_text(
            "❌ *Error al subir el vídeo a Google Drive.*\n"
            "Revisa los logs del bot para más detalles.",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    """Registra el handler de vídeos en la aplicación de Telegram."""
    app.add_handler(
        MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, handler_video_to_drive)
    )
    logger.info("📹 Handler de subida a Drive registrado (solo chat privado).")
