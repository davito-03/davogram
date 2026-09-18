<h1 align="center">DavoGram + userbot Drive</h1>

<p align="center">
  Userbot de Telegram que sube multimedia a Google Drive <strong>sin comprimir</strong>.<br>
  Pyrogram + rclone. Lo opero en el mismo VPS que el resto del homelab.
</p>

<p align="center">
  <a href="https://davito.es/proyectos/davogram">Ficha</a>
  ·
  <a href="https://davito.es/proyectos">Portfolio</a>
  ·
  <a href="https://github.com/davito-03">@davito-03</a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="Pyrogram" src="https://img.shields.io/badge/Pyrogram-Telegram-26A5E4?logo=telegram&logoColor=white">
  <img alt="Drive" src="https://img.shields.io/badge/Google%20Drive-rclone-4285F4?logo=googledrive&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-yellow">
</p>

## Qué hace

Envías un archivo a **Mensajes Guardados** (películas, vídeo, lo que sea). El userbot lo descarga y lo sube a Drive con el nombre que elijas. rclone va primero; la API de Drive es el respaldo.

`main.py` es el bot de Telegram (recordatorios, premium, etc.). `userbot_drive.py` es la pieza de descargas grandes. Diagrama: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Lo que no va en el repo

Sesión de Pyrogram (`*.session`), cookies, `credentials.json`, `gdrive_credentials.json`, `token.json`, SQLite. Sin eso no entra en la cuenta, y así debe ser. Todo lo sensible sale de `.env`.

## Arranque

```bash
cp .env.example .env
# API_ID / API_HASH en my.telegram.org
# BOT_TOKEN con @BotFather si usas main.py
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python userbot_drive.py
```

La primera vez Pyrogram pide el teléfono y genera `my_account.session`. No lo subas.

## Relacionado

- [davito.es/proyectos](https://davito.es/proyectos)
- Homelab: [homepage](https://github.com/davito-03/homepage)
