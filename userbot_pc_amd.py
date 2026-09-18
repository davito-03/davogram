#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Userbot Independiente con Pyrogram para descargas grandes (>20MB) en Telegram.
Sube los archivos que envíes a "Mensajes Guardados" **directamente** a Google Drive
usando el nombre que tú elijas (sin optimización/compresión).

Ideal para películas y archivos que quieres conservar con calidad original + nombre personalizado.
"""

import os
import ctypes
import socket
import json
import shutil
import glob

# Establecer un timeout global de 5 minutos para evitar cuelgues eternos en sockets de Google Drive o similares
socket.setdefaulttimeout(300)

# Forzar siempre el directorio correcto, esencial para cuando se ejecuta desde el acceso directo de Windows
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import logging
import asyncio
# Parche Windows/Python 3.14: Pyrogram necesita que exista un event_loop antes de importar
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# --- CONTROL DE CONCURRENCIA Y ENERGÍA ---
processing_lock = asyncio.Lock()

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

def clean_temp_files():
    try:
        if os.path.exists("downloads"):
            import shutil
            shutil.rmtree("downloads")
    except Exception:
        pass
    # Ya no eliminamos *_opt.* (optimización eliminada)


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

def prevent_sleep():
    """Evita que el sistema operativo se suspenda mientras la app trabaja"""
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

def allow_sleep():
    """Permite de nuevo la suspensión del sistema operativo una vez terminado el trabajo"""
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

import subprocess
from pyrogram import Client, filters

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


def _upload_to_drive(filepath: str, filename: str, mimetype: str) -> str | None:
    """Sube un archivo a Google Drive y devuelve el enlace."""
    if _drive is None:
        return None

    try:
        file_metadata = {
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
        }
        media = MediaFileUpload(filepath, mimetype=mimetype, resumable=True)
        uploaded = (
            _drive.files()
            .create(
                body=file_metadata, 
                media_body=media, 
                fields="id, webViewLink, name",
                supportsAllDrives=True
            )
            .execute()
        )
        file_id = uploaded.get("id", "")
        link = uploaded.get("webViewLink", "")
        
        # Otorga permisos públicos de lectura al link generado
        _drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True
        ).execute()

        return link
    except Exception as e:
        logger.error(f"Error subiendo a Drive: {e}")
        return None

# ─── OPTIMIZACIÓN ELIMINADA ─────────────────────────────────────────────
# Se quitó todo el proceso de optimización (ffmpeg + Pillow) porque el usuario
# prefiere subir las películas tal cual con el nombre personalizado elegido.
# ─────────────────────────────────────────────────────────────────────────


# ─── CLIENTE PYROGRAM ────────────────────────────────────────────────────
# Crea la sesión con el nombre "my_account"
app = Client("my_account", api_id=API_ID, api_hash=API_HASH)

async def progress_callback(current, total, status_msg: Message, action: str):
    """Muestra el progreso de descarga o subida en Telegram."""
    try:
        percent = int(current * 100 / total)
        # Actualizamos el mensaje de estado solo cada 10% para no spamear la API
        if percent % 10 == 0:
            await status_msg.edit_text(f"⏳ {action} {percent}% ({current//1024//1024}MB / {total//1024//1024}MB)")
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

async def process_media_for_drive(download_path: str, custom_name: str, mimetype: str, status_msg: Message):
    """Sube directamente el archivo a Google Drive usando exactamente el nombre que eligió el usuario.
    Sin optimización ni compresión (para preservar calidad original de películas).
    """
    loop = asyncio.get_running_loop()
    size_mb = round(os.path.getsize(download_path) / (1024 * 1024), 2)

    await status_msg.edit_text(f"📤 Subiendo '{custom_name}' ({size_mb} MB) a Google Drive...")

    link = await loop.run_in_executor(None, _upload_to_drive, download_path, custom_name, mimetype)

    try:
        if os.path.exists(download_path):
            os.remove(download_path)
    except Exception as e:
        logger.warning(f"No se pudo borrar temporal: {e}")

    if link:
        await status_msg.edit_text(
            f"✅ **Archivo subido con éxito a Google Drive**\n\n"
            f"📄 Nombre: `{custom_name}`\n"
            f"📦 Tamaño: {size_mb} MB\n"
            f"🔗 [Ver archivo en Drive]({link})",
            disable_web_page_preview=True
        )
    else:
        await status_msg.edit_text("❌ Error subiendo el archivo a Google Drive.")


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
    
    try:
        status_msg = await app.get_messages("me", task_dict["status_msg_id"])
        media_msg = await app.get_messages("me", task_dict["media_msg_id"])
    except Exception as e:
        remove_from_queue(task_id)
        return

    async with processing_lock:
        prevent_sleep()
        try:
            await status_msg.edit_text(f"⤵️ Empezando a procesar `{custom_name}`...")

            if state_type == "normal":
                try:
                    download_path = await media_msg.download(
                        progress=progress_callback,
                        progress_args=(status_msg, "Descargando de Telegram:")
                    )
                except Exception as e:
                    await status_msg.edit_text(f"❌ Error en la descarga: {e}")
                    remove_from_queue(task_id)
                    return

                if not download_path or not os.path.exists(download_path):
                    await status_msg.edit_text("❌ No se pudo descargar el archivo localmente.")
                    remove_from_queue(task_id)
                    return
                    
                await process_media_for_drive(download_path, custom_name, mimetype, status_msg)

            elif state_type == "torrent_single":
                torrent_path = task_dict.get("torrent_path")
                if not torrent_path or not os.path.exists(torrent_path):
                    try:
                        torrent_path = await media_msg.download()
                    except Exception:
                        await status_msg.edit_text("❌ Error re-descargando el .torrent")
                        remove_from_queue(task_id)
                        return

                file_index = task_dict["chosen_index"]
                
                await status_msg.edit_text(f"⤵️ Empezando descarga P2P con webtorrent... (Puede demorar)")
                out_dir = os.path.join("downloads", f"tor_{task_id}")
                os.makedirs(out_dir, exist_ok=True)
                
                webtorrent_exe = "webtorrent.cmd" if os.name == "nt" else "webtorrent"
                cmd = [
                    webtorrent_exe, "download", os.path.abspath(torrent_path), 
                    "--select", str(file_index),
                    "-o", os.path.abspath(out_dir),
                    "--quit"
                ]
                
                try:
                    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=creation_flags
                    )
                    try:
                        await asyncio.wait_for(process.wait(), timeout=7200)
                    except asyncio.TimeoutError:
                        process.kill()
                        await status_msg.edit_text("❌ Se canceló la descarga del torrent: tardaba demasiado.")
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
                    await status_msg.edit_text(f"❌ Error en webtorrent al descargar")
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
                     
                mimetype = "video/mp4" if custom_name.endswith(('.mp4', '.mkv', '.avi')) else "application/octet-stream"
                await process_media_for_drive(downloaded_file, custom_name, mimetype, status_msg)

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
                     
                mimetype = "video/mp4"
                await process_media_for_drive(download_path, custom_name, mimetype, status_msg)

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
                    
                mimetype = "video/mp4" if custom_name.endswith(('.mp4', '.mkv', '.avi')) else "application/octet-stream"
                await process_media_for_drive(actual_path, custom_name, mimetype, status_msg)
                
            remove_from_queue(task_id)
        except Exception as e:
            remove_from_queue(task_id)
        finally:
            allow_sleep()

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


    status_msg = await message.reply_text(f"⏳ Añadido a la cola de procesamiento: `{custom_name}`", quote=True)

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
    asyncio.create_task(process_queue_item(task_dict))




async def resume_queue():
    q = load_queue()
    if q:
        print(f"🔄 Recuperando {len(q)} tareas de procesamiento pendientes de queue.json...")
        for task_dict in q:
            asyncio.create_task(process_queue_item(task_dict))

async def main():
    if API_ID == "TU_API_ID" or API_HASH == "TU_API_HASH":
        print("🛑 ERROR: Debes editar userbot_drive.py y poner tu API_ID y API_HASH.")
        exit(1)

    print("🚀 Arrancando Userbot de descargas (Pyrogram)...")
    clean_temp_files()
    await app.start()
    await resume_queue()
    print("Envía cualquier vídeo o archivo .torrent a 'Mensajes Guardados' de Telegram para probarlo.")
    from pyrogram import idle
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
