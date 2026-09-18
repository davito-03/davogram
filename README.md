# DavoGram + userbot Drive

Bot de Telegram (DavoGram) y un **userbot** con Pyrogram que sube a Google Drive los archivos que mandas a Mensajes Guardados, sin comprimirlos.

Pensado para películas y archivos grandes: rclone primero, API de Drive como respaldo.

## Piezas

- `userbot_drive.py` — descarga desde Telegram y sube a Drive
- `main.py` — bot de Telegram (recordatorios, premium, etc.)
- `features/` — módulos del bot
- `config.py` — todo sale de variables de entorno

## No va en el repo

Sesión de Pyrogram (`*.session`), `cookies.txt`, `credentials.json`, `gdrive_credentials.json`, `token.json`, la SQLite. Sin eso el userbot no puede entrar en tu cuenta, y así debe ser.

## Arranque

1. `API_ID` y `API_HASH` en [my.telegram.org](https://my.telegram.org).
2. Token del bot con [@BotFather](https://t.me/BotFather) si usas `main.py`.
3. Credenciales de Google Drive / rclone aparte.

```bash
cp .env.example .env
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python userbot_drive.py
```

La primera vez Pyrogram pide el teléfono y el código; genera `my_account.session` en local. No lo subas.
