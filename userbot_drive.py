#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Userbot Independiente con Pyrogram para descargas grandes (>20MB) en Telegram.
Sube los archivos que envíes a "Mensajes Guardados" **directamente** a Google Drive
usando el nombre que tú elijas (sin optimización/compresión).

Ideal para películas y archivos que quieres conservar con calidad original + nombre personalizado.
"""

import os
import logging
import asyncio
import socket
import json
import shutil
import glob

# Eliminado socket.setdefaulttimeout porque causa cortes abruptos en pyrogram/asyncio, resultando en archivos de 0 bytes al perder conexión

download_lock = asyncio.Lock()
upload_semaphore = asyncio.Semaphore(5)
QUEUE_FILE = "queue.json"

def load_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_queue(queue):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def add_to_queue(task_dict):
    q = load_queue()
    q.append(task_dict)
    save_queue(q)

def remove_from_queue(task_id):
    q = load_queue()
    q = [x for x in q if x.get("task_id") != task_id]
    save_queue(q)

DASHBOARD_STATE_FILE = "dashboard_state.json"
_dashboard_lock = asyncio.Lock()
_last_dashboard_update = 0
_current_download_info = {}
_current_upload_info = {}

def load_dashboard_state():
    if os.path.exists(DASHBOARD_STATE_FILE):
        try:
            with open(DASHBOARD_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"dashboard_msg_id": None, "completed": []}

def save_dashboard_state(state):
    try:
        with open(DASHBOARD_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error guardando dashboard_state: {e}")

def get_drive_filenames():
    """Obtiene los nombres de archivos en la carpeta de películas de Google Drive usando rclone."""
    try:
        res = subprocess.run(["rclone", "lsf", "gdrive:Jellyfin/Peliculas"], capture_output=True, text=True, timeout=40)
        if res.returncode == 0:
            names = set()
            for line in res.stdout.splitlines():
                f = line.strip()
                if f:
                    names.add(f.lower())
                    names.add(os.path.splitext(f)[0].lower())
            return names
    except Exception as e:
        logger.error(f"Error obteniendo lista de drive: {e}")
    return set()

def render_dashboard_text():
    state = load_dashboard_state()
    queue = load_queue()
    completed = state.get("completed", [])
    
    current_active = set()
    if _current_download_info.get("name"):
        current_active.add(_current_download_info["name"])
    if _current_upload_info.get("name"):
        current_active.add(_current_upload_info["name"])
        
    pending = [t["custom_name"] for t in queue if t.get("custom_name") not in current_active]
    
    total_batch = len(completed) + len(queue)
    completed_count = len(completed)
    pct_total = int((completed_count / total_batch * 100)) if total_batch > 0 else 100
    
    total_blocks = int(pct_total / 10)
    total_bar = "█" * total_blocks + "░" * (10 - total_blocks)
    
    lines = [
        "🎬 **PANEL DE PROGRESO GLOBAL — COLA ESPECIAL**",
        "───────────────────────────────",
        f"📊 **Progreso Total:** [{total_bar}] **{pct_total}%** ({completed_count}/{total_batch} películas)",
        "───────────────────────────────"
    ]
    
    # 📥 Descargando
    if _current_download_info.get("name"):
        name = _current_download_info['name']
        pct = _current_download_info.get('pct', 0)
        cur_mb = _current_download_info.get('cur_mb', 0)
        tot_mb = _current_download_info.get('tot_mb', 0)
        blocks = int(pct / 10)
        bar = "█" * blocks + "░" * (10 - blocks)
        lines.append("📥 **Descargando actualmente:**")
        lines.append(f"• `{name}`")
        lines.append(f"  [{bar}] {pct}% ({cur_mb} MB / {tot_mb} MB)")
    else:
        lines.append("📥 **Descarga:** Inactiva / esperando")
    
    lines.append("")
    
    # 📤 Subiendo
    if _current_upload_info.get("name"):
        name = _current_upload_info['name']
        status = _current_upload_info.get('status', 'Subiendo con rclone a Google Drive...')
        lines.append("📤 **Subiendo a Google Drive:**")
        lines.append(f"• `{name}`")
        lines.append(f"  ⚡ {status}")
    else:
        lines.append("📤 **Subida:** Inactiva / esperando")
        
    lines.append("")
    
    # ⏳ En cola
    lines.append(f"⏳ **En cola ({len(pending)} pendientes):**")
    if pending:
        for p in pending[:8]:
            lines.append(f"• `{p}`")
        if len(pending) > 8:
            lines.append(f"• *... y {len(pending) - 8} más en espera.*")
    else:
        lines.append("• *(No quedan películas pendientes en cola)*")
        
    lines.append("")
    
    # ✅ Completadas
    lines.append(f"✅ **Completadas con éxito ({len(completed)}):**")
    if completed:
        for c in completed[-6:]:
            lines.append(f"• {c}")
        if len(completed) > 6:
            lines.append(f"• *... y {len(completed) - 6} completadas antes.*")
    else:
        lines.append("• *(Aún ninguna completada en esta sesión)*")
        
    lines.append("───────────────────────────────")
    import datetime
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    
    if len(queue) == 0 and not _current_download_info.get("name") and not _current_upload_info.get("name"):
        lines.append("🎉 **¡COLA COMPLETADA! Todas las películas están en Google Drive.**")
    else:
        lines.append(f"🔄 *Actualizándose en tiempo real | {now_str}*")
        
    return "\n".join(lines)

async def update_dashboard(force=False):
    global _last_dashboard_update
    now = asyncio.get_event_loop().time()
    if not force and (now - _last_dashboard_update < 5):
        return
    _last_dashboard_update = now
    
    async with _dashboard_lock:
        state = load_dashboard_state()
        msg_id = state.get("dashboard_msg_id")
        text = render_dashboard_text()
        
        try:
            if msg_id:
                try:
                    await app.edit_message_text("me", msg_id, text)
                    return
                except errors.MessageNotModified:
                    return
                except Exception as e:
                    logger.warning(f"No se pudo editar panel {msg_id}: {e}. Creando nuevo...")
            
            sent = await app.send_message("me", text)
            state["dashboard_msg_id"] = sent.id
            save_dashboard_state(state)
            try:
                await sent.pin(disable_notification=True)
            except Exception:
                pass
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception as e:
            logger.error(f"Error en update_dashboard: {e}")


def clean_temp_files():
    """Crea la carpeta downloads si no existe y limpia únicamente carpetas temporales huérfanas."""
    try:
        os.makedirs("downloads", exist_ok=True)
        for prefix in ("tor_", "yt_", "tera_"):
            for d in glob.glob(os.path.join("downloads", f"{prefix}*")):
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

import subprocess
from pyrogram import Client, filters, errors
from pyrogram.types import Message

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Cargar configuraciones (importamos del config.py del bot principal)
from config import GDRIVE_FOLDER_ID

# ─── CREDENCIALES DE DESARROLLO DE TELEGRAM ──────────────────────────────
# ‼️ Reemplaza "TU_API_ID" y "TU_API_HASH" con los datos de my.telegram.org
API_ID = os.environ.get("API_ID", "")
API_HASH = os.environ.get("API_HASH", "")
# ─────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("UserBotDrive")

# ─── CLIENTE GOOGLE DRIVE ────────────────────────────────────────────────
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _init_drive_service():
    """Inicializa el cliente de Google Drive con OAuth 2.0 (token.json)."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", _SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Guardar el token renovado
                with open("token.json", "w") as token_file:
                    token_file.write(creds.to_json())
            except Exception as e:
                logger.error(f"❌ Error al refrescar el token: {e}. Genera un nuevo token con generar_token.py.")
                return None
        else:
            logger.error("❌ Archivo token.json no encontrado o inválido. Genera el token primero con generar_token.py")
            return None
            
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        logger.info("✅ Cliente de Google Drive inicializado (OAuth 2.0).")
        return service
    except Exception as e:
        logger.error(f"❌ Error al iniciar Google Drive: {e}")
        return None

_drive = _init_drive_service()


def _upload_to_drive_rclone(filepath: str, filename: str) -> str | None:
    """Sube un archivo a Google Drive usando rclone (método preferido y más robusto)."""
    dest = f"gdrive:Jellyfin/Peliculas/{filename}"
    logger.info(f"🚀 [rclone] Subiendo '{filename}' a Google Drive (gdrive:Jellyfin/Peliculas)...")
    cmd = [
        "rclone", "copyto",
        filepath,
        dest,
        "--drive-chunk-size", "64M",
        "--retries", "5",
        "--low-level-retries", "10",
        "--stats", "15s",
        "--stats-one-line",
        "-v"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if res.returncode != 0:
            logger.error(f"❌ [rclone] Error al subir '{filename}': {res.stderr}")
            return None
        logger.info(f"✅ [rclone] Subida completada para '{filename}'. Obteniendo enlace...")

        # Obtener enlace público/de visualización con rclone link
        link_cmd = ["rclone", "link", dest]
        link_res = subprocess.run(link_cmd, capture_output=True, text=True, timeout=60)
        link = link_res.stdout.strip() if link_res.returncode == 0 else ""
        return link or f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    except Exception as e:
        logger.error(f"❌ [rclone] Excepción al ejecutar rclone para '{filename}': {e}")
        return None


def _upload_to_drive_api(filepath: str, filename: str, mimetype: str) -> str | None:
    """Fallback con Google Drive API usando resumable chunks y reintentos automáticos."""
    global _drive
    if _drive is None:
        _drive = _init_drive_service()
    if _drive is None:
        return None

    try:
        file_metadata = {
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
        }
        chunksize = 32 * 1024 * 1024
        media = MediaFileUpload(filepath, mimetype=mimetype, chunksize=chunksize, resumable=True)
        request = (
            _drive.files()
            .create(
                body=file_metadata, 
                media_body=media, 
                fields="id, webViewLink, name",
                supportsAllDrives=True
            )
        )
        response = None
        retries = 0
        max_retries = 10
        while response is None:
            try:
                status, response = request.next_chunk(num_retries=3)
                if status:
                    logger.info(f"📊 [API Drive] Subiendo '{filename}': {int(status.progress() * 100)}%")
                retries = 0
            except Exception as chunk_err:
                retries += 1
                if retries > max_retries:
                    raise chunk_err
                import time
                time.sleep(min(60, 2 ** retries))

        file_id = response.get("id", "")
        link = response.get("webViewLink", "")
        
        try:
            _drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True
            ).execute()
        except Exception:
            pass

        return link or f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    except Exception as e:
        logger.error(f"❌ [API Drive] Error subiendo a Drive: {e}")
        return None


def _upload_to_drive(filepath: str, filename: str, mimetype: str = "video/mp4") -> str | None:
    """Sube un archivo a Google Drive (rclone como primario, API como fallback)."""
    link = _upload_to_drive_rclone(filepath, filename)
    if link:
        return link

    logger.warning(f"⚠️ [rclone] falló para '{filename}'. Intentando fallback con API de Google Drive...")
    return _upload_to_drive_api(filepath, filename, mimetype)

# ─── OPTIMIZACIÓN ELIMINADA ─────────────────────────────────────────────
# Se quitó todo el proceso de optimización (ffmpeg + Pillow) porque el usuario
# prefiere subir las películas tal cual con el nombre personalizado elegido.
# ─────────────────────────────────────────────────────────────────────────


# ─── CLIENTE PYROGRAM ────────────────────────────────────────────────────
# Crea la sesión con el nombre "my_account"
app = Client("my_account", api_id=API_ID, api_hash=API_HASH)

async def progress_callback(current, total, status_msg: Message, action: str):
    """Muestra el progreso de descarga o subida en Telegram y en el panel general."""
    try:
        percent = int(current * 100 / total)
        cur_mb = current // 1024 // 1024
        tot_mb = total // 1024 // 1024
        # Actualizamos el mensaje de estado solo cada 10% para no spamear la API
        if percent % 10 == 0:
            await status_msg.edit_text(f"⏳ {action} {percent}% ({cur_mb}MB / {tot_mb}MB)")
        _current_download_info["pct"] = percent
        _current_download_info["cur_mb"] = cur_mb
        _current_download_info["tot_mb"] = tot_mb
        await update_dashboard(force=(percent == 100))
    except Exception:
        pass


pending_files = {}

def bdecode(data):
    def decode(idx):
        if idx >= len(data): raise ValueError("EOF")
        char = chr(data[idx])
        if char == 'i':
            end = data.find(b'e', idx)
            return int(data[idx+1:end]), end + 1
        elif char == 'l':
            lst = []
            idx += 1
            while chr(data[idx]) != 'e':
                val, idx = decode(idx)
                lst.append(val)
            return lst, idx + 1
        elif char == 'd':
            dct = {}
            idx += 1
            while chr(data[idx]) != 'e':
                key, idx = decode(idx)
                val, idx = decode(idx)
                dct[key] = val
            return dct, idx + 1
        elif char in '0123456789':
            colon = data.find(b':', idx)
            length = int(data[idx:colon])
            start = colon + 1
            return data[start:start+length], start+length
        else:
            raise ValueError(f"Invalid bencode at {idx}")
    return decode(0)[0]

def parse_torrent_files(torrent_path):
    with open(torrent_path, 'rb') as f:
        data = f.read()
    decoded = bdecode(data)
    info = decoded.get(b'info', {})
    files = []
    
    if b'files' in info:
        for idx, file_info in enumerate(info[b'files']):
            path_parts = [p.decode('utf-8', 'ignore') for p in file_info[b'path']]
            filename = "/".join(path_parts)
            length = file_info.get(b'length', 0)
            files.append({"index": idx, "name": filename, "size": length})
    else:
        name = info.get(b'name', b'unknown').decode('utf-8', 'ignore')
        length = info.get(b'length', 0)
        files.append({"index": 0, "name": name, "size": length})
        
    return files

async def process_media_for_drive(download_path: str, custom_name: str, mimetype: str, status_msg: Message) -> bool:
    """Sube directamente el archivo a Google Drive usando rclone (con reintentos).
    Solo elimina el archivo local si la subida fue exitosa.
    """
    loop = asyncio.get_running_loop()
    size_mb = round(os.path.getsize(download_path) / (1024 * 1024), 2)

    await status_msg.edit_text(f"📤 Subiendo '{custom_name}' ({size_mb} MB) a Google Drive vía rclone...")
    _current_upload_info["name"] = custom_name
    _current_upload_info["status"] = f"Subiendo ({size_mb} MB) vía rclone..."
    await update_dashboard(force=True)

    link = None
    try:
        async with upload_semaphore:
            for attempt in range(1, 4):
                link = await loop.run_in_executor(None, _upload_to_drive, download_path, custom_name, mimetype)
                if link:
                    break
                if attempt < 3:
                    await status_msg.edit_text(f"⚠️ Reintentando subida ({attempt}/3) para '{custom_name}' en 10s...")
                    await asyncio.sleep(10)

        _current_upload_info.clear()
        if link:
            await status_msg.edit_text(
                f"✅ **Archivo subido con éxito a Google Drive**\n\n"
                f"📄 Nombre: `{custom_name}`\n"
                f"📦 Tamaño: {size_mb} MB\n"
                f"🔗 [Ver archivo en Drive]({link})",
                disable_web_page_preview=True
            )
            # Marcar completada en panel
            state = load_dashboard_state()
            if custom_name not in state.get("completed", []):
                state.setdefault("completed", []).append(custom_name)
                save_dashboard_state(state)
            await update_dashboard(force=True)

            try:
                if os.path.exists(download_path):
                    os.remove(download_path)
            except Exception as e:
                logger.warning(f"No se pudo borrar temporal: {e}")
            return True
        else:
            await status_msg.edit_text(f"❌ Error subiendo '{custom_name}' a Google Drive tras múltiples intentos. Se conserva localmente.")
            await update_dashboard(force=True)
            return False

    except Exception as e:
        logger.error(f"Error procesando subida para {custom_name}: {e}")
        _current_upload_info.clear()
        await status_msg.edit_text(f"❌ Error durante la subida: {e}")
        await update_dashboard(force=True)
        return False


pending_files = {}

@app.on_message(filters.chat("me") & (filters.video | filters.document))
async def handle_saved_messages_media(client: Client, message: Message):
    if _drive is None:
        await message.reply_text("❌ Google Drive no está configurado.")
        return

    media_obj = message.video or message.document
    if not media_obj:
        return

    filename = getattr(media_obj, "file_name", None) or f"media_{message.id}.mp4"
    mimetype = getattr(media_obj, "mime_type", None) or "video/mp4"

    is_torrent = filename.lower().endswith(".torrent") or mimetype == "application/x-bittorrent"

    if not is_torrent and media_obj.file_size and media_obj.file_size < 1024 * 50:
        return

    # ================= LOGICA TORRENT =================
    if is_torrent:
        status_msg = await message.reply_text(f"⤵️ Detectado archivo torrent: `{filename}`. Leyendo metadatos...", quote=True)
        try:
            download_path = await message.download(
                progress=progress_callback,
                progress_args=(status_msg, "Descargando .torrent localmente:")
            )
            
            files = parse_torrent_files(download_path)
            video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts', '.wmv')
            videos = [f for f in files if f["name"].lower().endswith(video_exts)]
            
            if not videos:
                await status_msg.edit_text("❌ No encontré archivos de vídeo en este torrent.")
                os.remove(download_path)
                return
                
            if len(videos) == 1:
                chosen_video = videos[0]
                prompt_msg = await status_msg.edit_text(
                    f"🎬 Encontré 1 vídeo en el torrent: `{chosen_video['name']}`\n\n"
                    "✏️ **Responde a este mensaje** con el nombre final que le quieres poner "
                    "(o escribe `/skip` para usar el nombre extraído del torrent)."
                )
                pending_files[prompt_msg.id] = {
                    "type": "torrent_single",
                    "media_msg": message,
                    "torrent_path": download_path,
                    "chosen_index": chosen_video["index"],
                    "filename": os.path.basename(chosen_video["name"])
                }
            else:
                text = f"🗂️ Encontré {len(videos)} vídeos en este torrent. **Responde a este mensaje con el NÚMERO** del que quieras descargar:\n\n"
                for i, v in enumerate(videos, 1):
                    size_mb = round(v["size"] / (1024*1024), 2)
                    text += f"**{i}.** `{os.path.basename(v['name'])}` ({size_mb} MB)\n"
                
                prompt_msg = await status_msg.edit_text(text)
                pending_files[prompt_msg.id] = {
                    "type": "torrent_multi",
                    "media_msg": message,
                    "torrent_path": download_path,
                    "videos": videos
                }
        except Exception as e:
            await status_msg.edit_text(f"❌ Error procesando el torrent: {e}")
            logger.error(f"Error procesando torrent: {e}")
        return

    # ================= LOGICA ARCHIVO NORMAL =================
    prompt_msg = await message.reply_text(
        f"⤵️ Archivo detectado: `{filename}`\n"
        "✏️ **Responde a este mensaje** con el nombre que quieres para el archivo final "
        "(o escribe `/skip` para mantener el original).",
        quote=True
    )
    
    pending_files[prompt_msg.id] = {
        "type": "normal",
        "media_msg": message,
        "media_obj": media_obj,
        "filename": filename,
        "mimetype": mimetype
    }


@app.on_message(filters.chat("me") & filters.text & ~filters.reply)
async def handle_saved_messages_text(client: Client, message: Message):
    text = message.text.strip()
    if text.startswith("http") and ("youtube.com" in text or "youtu.be" in text):
        status_msg = await message.reply_text(f"⤵️ Detectado enlace de YouTube. Obteniendo metadatos...", quote=True)
        try:
            import yt_dlp
            ydl_opts = {'quiet': True, 'simulate': True}
            
            def get_info(url):
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
                    
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, get_info, text)
            
            title = info.get('title', 'video_yt')
            ext = info.get('ext', 'mp4')
            filename = f"{title}.{ext}"
            
            prompt_msg = await status_msg.edit_text(
                f"🎬 Vídeo de YouTube detectado: `{title}`\n\n"
                "✏️ **Responde a este mensaje** con el nombre final que le quieres poner "
                "(o escribe `/skip` para usar el título original)."
            )
            pending_files[prompt_msg.id] = {
                "type": "youtube",
                "media_msg": message,
                "url": text,
                "filename": filename
            }
        except Exception as e:
            await status_msg.edit_text(f"❌ Error al procesar el enlace de YouTube: {e}")

    elif text.startswith("http") and any(d in text for d in ["terabox.com", "teraboxapp.com", "1024tera.com", "4funbox.com", "nephobox.com"]):
        status_msg = await message.reply_text(f"⤵️ Detectado enlace de Terabox. Obteniendo enlace directo...", quote=True)
        try:
            import requests
            
            # API pública para evitar necesidad de Cookies
            api_url = f"https://teraboxvideodownloader.nepcoderdevs.workers.dev/?url={text}"
            
            def get_tera_info(u):
                resp = requests.get(u, timeout=15)
                resp.raise_for_status()
                return resp.json()
                
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, get_tera_info, api_url)
            
            if not isinstance(info, list) or len(info) == 0:
                raise Exception("La API no devolvió archivos válidos o requiere inicio de sesión (Cookie).")
                
            chosen_file = info[0]
            title = chosen_file.get("title", "terabox_video.mp4")
            download_url = chosen_file.get("url")
            
            if not download_url:
                raise Exception("No se encontró URL de descarga en la respuesta de la API.")
            
            prompt_msg = await status_msg.edit_text(
                f"🎬 Vídeo de Terabox detectado: `{title}`\n\n"
                "✏️ **Responde a este mensaje** con el nombre final que le quieres poner "
                "(o escribe `/skip` para usar el título original)."
            )
            pending_files[prompt_msg.id] = {
                "type": "terabox",
                "media_msg": message,
                "url": download_url,
                "filename": title
            }
        except Exception as e:
            await status_msg.edit_text(f"❌ Error al procesar enlace Terabox (la API puede estar inactiva): {e}")



async def process_queue_item(task_dict):
    task_id = task_dict["task_id"]
    custom_name = task_dict["custom_name"]
    state_type = task_dict["state_type"]
    mimetype = task_dict.get("mimetype")

    download_path_to_process = None
    mimetype_to_process = mimetype

    async with download_lock:
        try:
            # Obtener los mensajes justo antes de procesar para garantizar referencias de archivo (file_reference) frescas
            status_msg = await app.get_messages("me", task_dict["status_msg_id"])
            media_msg = await app.get_messages("me", task_dict["media_msg_id"])
        except Exception as e:
            remove_from_queue(task_id)
            return

        try:
            # Re-download torrent if missing
            if state_type == "torrent_single":
                torrent_path = task_dict.get("torrent_path")
                if not torrent_path or not os.path.exists(torrent_path):
                    try:
                        torrent_path = await media_msg.download()
                        task_dict["torrent_path"] = torrent_path
                    except Exception:
                        await status_msg.edit_text("❌ Error re-descargando el .torrent")
                        remove_from_queue(task_id)
                        return

            _current_download_info["name"] = custom_name
            _current_download_info["pct"] = 0
            _current_download_info["cur_mb"] = 0
            _current_download_info["tot_mb"] = 0
            await update_dashboard(force=True)
            await status_msg.edit_text(f"⤵️ Empezando a procesar `{custom_name}`...")
        
            if state_type == "normal":
                expected_path = os.path.join("downloads", custom_name)
                if os.path.exists(expected_path) and os.path.getsize(expected_path) > 1024 * 1024:
                    logger.info(f"📂 Archivo ya descargado localmente: '{expected_path}'. Saltando descarga de Telegram...")
                    download_path_to_process = expected_path
                else:
                    download_path = None
                    for dl_attempt in range(1, 4):
                        try:
                            if dl_attempt > 1:
                                try:
                                    media_msg = await app.get_messages("me", task_dict["media_msg_id"])
                                except Exception:
                                    pass
                                await status_msg.edit_text(f"⚠️ Reintentando descarga ({dl_attempt}/3) para `{custom_name}`...")
                                await asyncio.sleep(5)

                            download_path = await media_msg.download(
                                file_name=expected_path,
                                progress=progress_callback,
                                progress_args=(status_msg, "Descargando de Telegram:")
                            )
                            if download_path and os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                                break
                        except errors.FloodWait as fw:
                            logger.warning(f"FloodWait de {fw.value}s descargando {custom_name}")
                            await asyncio.sleep(fw.value + 2)
                        except Exception as e:
                            logger.error(f"Error descarga intento {dl_attempt}/3 para {custom_name}: {e}")
                            if dl_attempt == 3:
                                await status_msg.edit_text(f"❌ Error en la descarga tras varios intentos: {e}")
                                return
        
                    if not download_path or not os.path.exists(download_path):
                        await status_msg.edit_text("❌ No se pudo descargar el archivo localmente tras varios intentos.")
                        return
                        
                    if os.path.getsize(download_path) == 0:
                        try:
                            os.remove(download_path)
                        except:
                            pass
                        await status_msg.edit_text("❌ Error: Telegram devolvió un archivo vacío de 0 bytes (timeout o conexión interrumpida).")
                        remove_from_queue(task_id)
                        return
                        
                    download_path_to_process = download_path
        
            elif state_type == "torrent_single":
                torrent_path = task_dict.get("torrent_path")
                file_index = task_dict["chosen_index"]
                
                await status_msg.edit_text(f"⤵️ Empezando descarga P2P con webtorrent... (Puede demorar)")
                out_dir = os.path.join("downloads", f"tor_{task_id}")
                os.makedirs(out_dir, exist_ok=True)
                
                webtorrent_exe = "webtorrent.cmd" if os.name == "nt" else "webtorrent"
                cmd = [
                    webtorrent_exe, "download", os.path.abspath(torrent_path), 
                    "--select", str(file_index),
                    "-o", os.path.abspath(out_dir)
                ]
                
                # Como webtorrent a veces no se cierra solo al terminar 1 archivo:
                cmd.append("--quit")
                
                try:
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    try:
                        # Tiempo límite de 2 horas (7200 segundos) para no quedarse colgado
                        await asyncio.wait_for(process.wait(), timeout=7200)
                    except asyncio.TimeoutError:
                        process.kill()
                        await status_msg.edit_text("❌ Se canceló la descarga del torrent: tardaba demasiado.")
                        remove_from_queue(task_id)
                        return
                except FileNotFoundError:
                    await status_msg.edit_text("❌ No se encontró 'webtorrent'. Instálalo en el VPS con: `npm install -g webtorrent-cli`")
                    remove_from_queue(task_id)
                    return
                except Exception as e:
                    await status_msg.edit_text(f"❌ Error al ejecutar webtorrent: {e}")
                    remove_from_queue(task_id)
                    return
                
                try:
                     os.remove(torrent_path)
                except:
                     pass
                     
                if process.returncode != 0:
                    stderr = (await process.stderr.read()).decode()
                    await status_msg.edit_text(f"❌ Error en webtorrent: {stderr}")
                    remove_from_queue(task_id)
                    return
                    
                downloaded_file = None
                for root, dirs, files in os.walk(out_dir):
                    for file in files:
                        path = os.path.join(root, file)
                        if downloaded_file is None or os.path.getsize(path) > os.path.getsize(downloaded_file):
                            downloaded_file = path
                            
                if not downloaded_file:
                     await status_msg.edit_text("❌ No se encontró el archivo descargado por webtorrent.")
                     remove_from_queue(task_id)
                     return
                     
                mimetype_to_process = "video/mp4" if custom_name.endswith(('.mp4', '.mkv', '.avi')) else "application/octet-stream"
                download_path_to_process = downloaded_file
        
            elif state_type == "youtube":
                url = task_dict["url"]
                await status_msg.edit_text(f"⤵️ Empezando descarga desde YouTube... (Puede demorar)")
                out_dir = os.path.join("downloads", f"yt_{task_id}")
                os.makedirs(out_dir, exist_ok=True)
                out_tmpl = os.path.join(out_dir, "%(title)s.%(ext)s")
                
                def download_yt(yt_url, tmpl):
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                        'outtmpl': tmpl,
                        'quiet': True,
                        'no_warnings': True
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(yt_url, download=True)
                        return ydl.prepare_filename(info)
                        
                loop = asyncio.get_running_loop()
                try:
                    download_path = await loop.run_in_executor(None, download_yt, url, out_tmpl)
                    if not download_path or not os.path.exists(download_path):
                        raise Exception("Archivo no encontrado tras descarga")
                except Exception as e:
                    await status_msg.edit_text(f"❌ Error al descargar de YouTube: {e}")
                    remove_from_queue(task_id)
                    return
                     
                mimetype_to_process = "video/mp4"
                download_path_to_process = download_path
        
            elif state_type == "terabox":
                download_url = task_dict["url"]
                await status_msg.edit_text(f"⤵️ Empezando descarga directa desde Terabox... (Puede demorar)")
                out_dir = os.path.join("downloads", f"tera_{task_id}")
                os.makedirs(out_dir, exist_ok=True)
                download_path = os.path.join(out_dir, custom_name)
                
                def download_tera(url, path):
                    import requests
                    with requests.get(url, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        with open(path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024*1024):
                                f.write(chunk)
                    return path
                    
                loop = asyncio.get_running_loop()
                try:
                    actual_path = await loop.run_in_executor(None, download_tera, download_url, download_path)
                except Exception as e:
                    await status_msg.edit_text(f"❌ Error durante la descarga de Terabox: {e}")
                    remove_from_queue(task_id)
                    return
                    
                mimetype_to_process = "video/mp4" if custom_name.endswith(('.mp4', '.mkv', '.avi')) else "application/octet-stream"
                download_path_to_process = actual_path

        except Exception as e:
            _current_download_info.clear()
            await update_dashboard(force=True)
            remove_from_queue(task_id)
            return

    # Fuera del download_lock, actualizamos el panel y ejecutamos procesamiento e upload
    _current_download_info.clear()
    await update_dashboard(force=True)
    if download_path_to_process:
        success = False
        try:
            success = await process_media_for_drive(download_path_to_process, custom_name, mimetype_to_process, status_msg)
        finally:
            try:
                # Limpiamos también las carpetas residuales
                for prefix in ("tor_", "yt_", "tera_"):
                    dir_path = os.path.join("downloads", f"{prefix}{task_id}")
                    if os.path.exists(dir_path):
                        import shutil
                        shutil.rmtree(dir_path, ignore_errors=True)
            except Exception:
                pass
            if success:
                remove_from_queue(task_id)
            else:
                logger.error(f"❌ La tarea {task_id} ('{custom_name}') falló en subida. Se conserva en queue.json.")

@app.on_message(filters.chat("me") & filters.reply & filters.text)
async def handle_filename_reply(client: Client, message: Message):
    replied_id = message.reply_to_message.id
    if replied_id not in pending_files:
        return
        
    data = pending_files[replied_id]
    state_type = data.get("type", "normal")
    
    if state_type == "torrent_multi":
        try:
            choice = int(message.text.strip())
            videos = data["videos"]
            if not 1 <= choice <= len(videos):
                raise ValueError()
                
            chosen_video = videos[choice - 1]
            prompt_msg = await message.reply_text(
                f"🎬 Has elegido: `{os.path.basename(chosen_video['name'])}`\n\n"
                "✏️ **Responde a este mensaje** con el nombre final que le quieres poner "
                "(o escribe `/skip` para usar el nombre extraído).",
                quote=True
            )
            pending_files.pop(replied_id)
            pending_files[prompt_msg.id] = {
                "type": "torrent_single",
                "media_msg": data["media_msg"],
                "torrent_path": data["torrent_path"],
                "chosen_index": chosen_video["index"],
                "filename": os.path.basename(chosen_video["name"])
            }
        except ValueError:
            await message.reply_text("❌ Número inválido. Responde con un número de la lista.", quote=True)
        return

    # Común para "normal" y "torrent_single": determinar custom_name
    pending_files.pop(replied_id)
    orig_filename = data["filename"]
    custom_name = message.text.strip()
    
    if custom_name.lower() == "/skip":
        custom_name = orig_filename
    else:
        if "." not in custom_name and "." in orig_filename:
            custom_name += os.path.splitext(orig_filename)[1]


    status_msg = await message.reply_text(f"⏳ Añadido a la cola de procesamiento en VPS: `{custom_name}`", quote=True)

    task_dict = {
        "task_id": message.id,
        "status_msg_id": status_msg.id,
        "media_msg_id": data["media_msg"].id,
        "custom_name": custom_name,
        "state_type": state_type,
        "mimetype": data.get("mimetype"),
        "chosen_index": data.get("chosen_index"),
        "url": data.get("url"),
        "torrent_path": data.get("torrent_path")
    }

    add_to_queue(task_dict)
    asyncio.create_task(update_dashboard(force=True))
    asyncio.create_task(process_queue_item(task_dict))

@app.on_message(filters.chat("me") & filters.text & filters.regex(r"^/(panel|status|dashboard)"))
async def send_new_panel(client: Client, message: Message):
    """Permite al usuario solicitar un nuevo panel de progreso actualizado en cualquier momento."""
    state = load_dashboard_state()
    text = render_dashboard_text()
    sent = await message.reply_text(text, quote=True)
    state["dashboard_msg_id"] = sent.id
    save_dashboard_state(state)
    try:
        await sent.pin(disable_notification=True)
    except Exception:
        pass

async def recover_failed_and_unprocessed_movies():
    """
    Escanea 'Mensajes Guardados' (me) para detectar cualquier película
    que no se haya procesado o que haya fallado históricamente, y la encola.
    """
    logger.info("🔍 Escaneando historial de 'Mensajes Guardados' para recuperar películas pendientes...")
    try:
        drive_files = await asyncio.get_running_loop().run_in_executor(None, get_drive_filenames)
        state = load_dashboard_state()
        completed_set = set(c.lower() for c in state.get("completed", []))
        queue = load_queue()
        queued_ids = {t.get("media_msg_id") for t in queue}
        queued_names = {t.get("custom_name", "").lower() for t in queue}
        
        messages = []
        async for m in app.get_chat_history("me", limit=300):
            messages.append(m)
            
        reply_map = {}
        for m in messages:
            if m.reply_to_message_id and m.text:
                reply_map[m.reply_to_message_id] = m.text.strip()
                
        recovered_count = 0
        for m in reversed(messages):
            media = m.video or m.document
            if not media:
                continue
            file_size = getattr(media, "file_size", 0) or 0
            if file_size < 30 * 1024 * 1024:
                continue
                
            orig_filename = getattr(media, "file_name", None) or f"video_{m.id}.mp4"
            if not orig_filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.ts', '.wmv')):
                continue
                
            if m.id in queued_ids:
                continue
                
            # Determinar nombre asignado
            chosen_name = None
            for p in messages:
                if p.reply_to_message_id == m.id and p.id in reply_map:
                    val = reply_map[p.id]
                    if val.lower() != "/skip":
                        chosen_name = val
                    break
            if not chosen_name and m.id in reply_map:
                val = reply_map[m.id]
                if val.lower() != "/skip":
                    chosen_name = val
            if not chosen_name:
                chosen_name = orig_filename
                
            if "Archivo detectado:" in chosen_name:
                import re
                m_search = re.search(r"Archivo detectado:\s*([^\n\r]+)", chosen_name)
                if m_search:
                    chosen_name = m_search.group(1).strip()

            if "." not in chosen_name:
                ext = os.path.splitext(orig_filename)[1] or ".mp4"
                chosen_name += ext
                
            base_name = os.path.splitext(chosen_name)[0].lower()
            if chosen_name.lower() in queued_names or base_name in queued_names:
                continue
            if chosen_name.lower() in completed_set or base_name in completed_set:
                continue
            if chosen_name.lower() in drive_files or base_name in drive_files:
                logger.info(f"⏭️ '{chosen_name}' ya está en Google Drive. Añadido a completadas.")
                if chosen_name not in state.get("completed", []):
                    state.setdefault("completed", []).append(chosen_name)
                    completed_set.add(chosen_name.lower())
                    completed_set.add(base_name)
                continue
                
            # Encolar película recuperada
            logger.info(f"📥 Recuperando película pendiente: '{chosen_name}' (msg_id: {m.id})")
            status_msg = await app.send_message("me", f"⏳ Película recuperada y añadida a la cola: `{chosen_name}`", reply_to_message_id=m.id)
            task = {
                "task_id": m.id,
                "status_msg_id": status_msg.id,
                "media_msg_id": m.id,
                "custom_name": chosen_name,
                "state_type": "normal",
                "mimetype": getattr(media, "mime_type", "video/mp4"),
                "chosen_index": None,
                "url": None,
                "torrent_path": None
            }
            add_to_queue(task)
            queued_ids.add(m.id)
            queued_names.add(chosen_name.lower())
            recovered_count += 1
            asyncio.create_task(process_queue_item(task))
            
        save_dashboard_state(state)
        logger.info(f"✅ Escaneo completado. {recovered_count} películas recuperadas.")
        await update_dashboard(force=True)
    except Exception as e:
        logger.error(f"Error en recover_failed_and_unprocessed_movies: {e}")

async def resume_queue():
    q = load_queue()
    if q:
        logger.info(f"🔄 Recuperando {len(q)} tareas de procesamiento pendientes de queue.json (VPS)...")
        for task_dict in q:
            asyncio.create_task(process_queue_item(task_dict))
    asyncio.create_task(recover_failed_and_unprocessed_movies())
    asyncio.create_task(update_dashboard(force=True))

async def main():
    if API_ID == "TU_API_ID" or API_HASH == "TU_API_HASH":
        print("🛑 ERROR: Debes editar userbot_drive.py y poner tu API_ID y API_HASH.")
        exit(1)

    print("🚀 Arrancando Userbot de descargas (VPS con cola persistente y panel de progreso)...")
    clean_temp_files()
    await app.start()
    await resume_queue()
    print("Userbot iniciado con éxito.")
    from pyrogram import idle
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
