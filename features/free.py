#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/free.py — Comandos gratuitos para todos los usuarios
  /noticias   Últimas noticias sobre cualquier tema (Tavily → Brave fallback)
  /cambio     Conversión de divisas
  /hora       Hora en cualquier ciudad del mundo
  /que_es     Definición de cualquier término o concepto
  /quiz       Trivia de cultura general (Open Trivia DB API + traducción IA)
  /trivia     Trivia por categorías con selector de temas
  /ahorcado   Juego del ahorcado (palabras en español)
  /wordle     Juego Wordle clásico en español
"""

import html
import sqlite3
import logging
import random
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import DB_PATH, TAVILY_API_KEY, BRAVE_API_KEY, GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger("DavogramBot.free")

# ─────────────────────────────────────────────────────────────────────────────
# DB helper local
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ─────────────────────────────────────────────────────────────────────────────
# /noticias — Noticias (cualquier tema)
# ─────────────────────────────────────────────────────────────────────────────

def _tavily_search(query: str, n: int = 5) -> list[dict]:
    """Búsqueda con Tavily. Devuelve lista de {title, url, content}."""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        res = client.search(query, max_results=n, search_depth="basic")
        return res.get("results", [])[:n]
    except Exception as e:
        logger.warning(f"Tavily error: {e}")
        return []


def _brave_search(query: str, n: int = 5) -> list[dict]:
    """Fallback: Búsqueda con Brave Search API."""
    try:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_API_KEY,
        }
        r = requests.get(url, headers=headers, params={"q": query, "count": n}, timeout=8)
        results = r.json().get("web", {}).get("results", [])
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("description", "")} for x in results[:n]]
    except Exception as e:
        logger.warning(f"Brave search error: {e}")
        return []


async def cmd_noticias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra las últimas noticias sobre cualquier tema (Tavily → Brave fallback)."""
    tema = " ".join(context.args) if context.args else "actualidad últimas noticias hoy"
    query = f"{tema} {datetime.now().strftime('%Y')}"

    await update.message.reply_text(f"📰 Buscando noticias sobre <b>{tema}</b>\u2026", parse_mode="HTML")

    resultados = _tavily_search(query) or _brave_search(query)

    if not resultados:
        await update.message.reply_text("❌ No pude obtener noticias en este momento. Inténtalo de nuevo.")
        return

    text = f"📰 <b>Noticias — {tema.title()}</b>\n\n"
    for i, r in enumerate(resultados[:5], 1):
        title = r.get("title", "Sin título")[:80]
        url   = r.get("url", "")
        text += f"{i}. <a href='{url}'>{title}</a>\n\n"
    text += "<i>Fuente: Tavily / Brave Search</i>"

    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────────────────────────
# /cambio — Conversión de divisas
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_cambio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /cambio <cantidad> <origen> <destino>
    Ejemplo: /cambio 100 EUR USD
    """
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: /cambio <cantidad> <moneda_origen> <moneda_destino>\n"
            "Ejemplo: /cambio 100 EUR USD"
        )
        return

    try:
        amount = float(args[0].replace(",", "."))
        src = args[1].upper()
        dst = args[2].upper()
    except ValueError:
        await update.message.reply_text("❌ La cantidad debe ser un número.")
        return

    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{src}", timeout=8)
        data = r.json()
        if data.get("result") != "success":
            raise ValueError("API error")
        rate = data["rates"].get(dst)
        if rate is None:
            raise ValueError(f"Moneda desconocida: {dst}")
        result = amount * rate
        await update.message.reply_text(
            f"💱 <b>{amount:,.2f} {src}</b> = <b>{result:,.4f} {dst}</b>\n"
            f"<i>Tasa: 1 {src} = {rate:.6f} {dst}</i>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Cambio error: {e}")
        await update.message.reply_text("❌ No pude obtener el tipo de cambio. Verifica los códigos de moneda (EUR, USD, BTC…).")


# ─────────────────────────────────────────────────────────────────────────────
# /hora — Hora mundial
# ─────────────────────────────────────────────────────────────────────────────

CITIES = {
    "madrid": ("Europe/Madrid", "🇪🇸 Madrid"),
    "barcelona": ("Europe/Madrid", "🇪🇸 Barcelona"),
    "london": ("Europe/London", "🇬🇧 Londres"),
    "londres": ("Europe/London", "🇬🇧 Londres"),
    "paris": ("Europe/Paris", "🇫🇷 París"),
    "berlin": ("Europe/Berlin", "🇩🇪 Berlín"),
    "roma": ("Europe/Rome", "🇮🇹 Roma"),
    "nyc": ("America/New_York", "🗽 Nueva York"),
    "new york": ("America/New_York", "🗽 Nueva York"),
    "miami": ("America/New_York", "🌴 Miami"),
    "chicago": ("America/Chicago", "🏙 Chicago"),
    "los angeles": ("America/Los_Angeles", "🎬 Los Ángeles"),
    "la": ("America/Los_Angeles", "🎬 Los Ángeles"),
    "toronto": ("America/Toronto", "🍁 Toronto"),
    "mexico": ("America/Mexico_City", "🇲🇽 Ciudad de México"),
    "bogota": ("America/Bogota", "🇨🇴 Bogotá"),
    "buenos aires": ("America/Argentina/Buenos_Aires", "🇦🇷 Buenos Aires"),
    "sao paulo": ("America/Sao_Paulo", "🇧🇷 São Paulo"),
    "dubai": ("Asia/Dubai", "🏙 Dubái"),
    "tokyo": ("Asia/Tokyo", "🇯🇵 Tokio"),
    "tokio": ("Asia/Tokyo", "🇯🇵 Tokio"),
    "shanghai": ("Asia/Shanghai", "🇨🇳 Shanghái"),
    "hong kong": ("Asia/Hong_Kong", "🇭🇰 Hong Kong"),
    "singapore": ("Asia/Singapore", "🇸🇬 Singapur"),
    "singapur": ("Asia/Singapore", "🇸🇬 Singapur"),
    "sydney": ("Australia/Sydney", "🇦🇺 Sídney"),
    "moscow": ("Europe/Moscow", "🇷🇺 Moscú"),
    "moscu": ("Europe/Moscow", "🇷🇺 Moscú"),
}

async def cmd_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /hora <ciudad>
    Ejemplo: /hora Tokyo
    """
    if not context.args:
        lista = ", ".join(k.title() for k in list(CITIES.keys())[:12])
        await update.message.reply_text(f"Uso: /hora <ciudad>\nCiudades: {lista}…")
        return

    import pytz
    city_key = " ".join(context.args).lower()
    entry = CITIES.get(city_key)

    if not entry:
        await update.message.reply_text(
            f"❌ Ciudad no reconocida: *{city_key.title()}*\n"
            "Prueba con: NYC, Tokyo, Dubai, London, Paris…",
            parse_mode="Markdown",
        )
        return

    tz_name, emoji_name = entry
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    await update.message.reply_text(
        f"🕐 {emoji_name}: <b>{now.strftime('%H:%M')}</b>\n"
        f"📅 {now.strftime('%A, %d de %B de %Y')}\n"
        f"<i>({tz_name}, UTC{now.strftime('%z')[:3]})</i>",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /que_es — Glosario de conocimiento general
# ─────────────────────────────────────────────────────────────────────────────

GLOSARIO = {
    "inteligencia artificial": "🤖 **IA (Inteligencia Artificial)**: Rama de la informática que crea sistemas capaces de realizar tareas que normalmente requieren inteligencia humana, como reconocer voz, imágenes o traducir idiomas.",
    "ia": "🤖 **IA**: Abreviatura de Inteligencia Artificial. Ver 'inteligencia artificial'.",
    "machine learning": "📊 **Machine Learning**: Subcampo de la IA en el que los sistemas aprenden automáticamente de datos sin ser programados explícitamente.",
    "blockchain": "⛓️ **Blockchain**: Cadena de bloques. Base de datos distribuida e inmutable donde los datos se organizan en bloques enlazados criptográficamente.",
    "algoritmo": "🔢 **Algoritmo**: Conjunto de instrucciones ordenadas para resolver un problema o realizar una tarea. La base de cualquier programa informático.",
    "nube": "☁️ **Computación en la nube**: Acceso a recursos informáticos (servidores, almacenamiento, software) a través de Internet en lugar de hardware propio.",
    "api": "🔌 **API (Application Programming Interface)**: Interfaz que permite que dos aplicaciones se comuniquen entre sí.",
    "python": "🐍 **Python**: Lenguaje de programación interpretado, muy popular por su sintaxis clara. Usado en IA, ciencia de datos, web y automatización.",
    "phishing": "🎣 **Phishing**: Tipo de ciberataque en el que se suplanta la identidad de una entidad para robar datos personales.",
    "vpn": "🔒 **VPN (Virtual Private Network)**: Red privada virtual que cifra tu conexión y oculta tu dirección IP.",
    "big data": "📦 **Big Data**: Conjunto de datos tan grande y complejo que requiere herramientas especiales para procesarlo y analizarlo.",
    "open source": "🌐 **Open Source**: Software de código abierto, cuyo código fuente es público y puede ser modificado libremente.",
    "agujero negro": "🕳️ **Agujero negro**: Región del espacio-tiempo donde la gravedad es tan intensa que nada, ni la luz, puede escapar.",
    "adn": "🧬 **ADN (Ácido Desoxirribonucleico)**: Molécula que contiene la información genética de los seres vivos.",
    "fotosintesis": "🌿 **Fotosíntesis**: Proceso por el que las plantas convierten la luz solar, agua y CO₂ en glucosa y oxígeno.",
    "relatividad": "⚡ **Teoría de la relatividad**: Teoría de Einstein que describe cómo el espacio, el tiempo y la gravedad están interrelacionados. E=mc².",
    "democracia": "🗳️ **Democracia**: Sistema de gobierno en que el poder reside en el pueblo, que lo ejerce directamente o a través de representantes elegidos.",
    "renacimiento": "🎨 **Renacimiento**: Movimiento cultural europeo (siglos XIV-XVI) que recuperó el arte y el pensamiento grecolatino. Destacan Leonardo da Vinci y Miguel Ángel.",
    "revolucion industrial": "⚙️ **Revolución Industrial**: Proceso de transformación económica y social iniciado en Inglaterra (siglo XVIII) con la mecanización de la producción.",
    "pib": "📊 **PIB (Producto Interior Bruto)**: Valor total de bienes y servicios producidos en un país en un período. Es el principal indicador del tamaño de una economía.",
    "inflacion": "📈 **Inflación**: Aumento generalizado y sostenido del nivel de precios de bienes y servicios. Reduce el poder adquisitivo del dinero.",
    "onu": "🌍 **ONU (Organización de las Naciones Unidas)**: Organismo internacional fundado en 1945 para promover la paz, la seguridad y la cooperación entre países.",
    "ue": "🇪🇺 **Unión Europea (UE)**: Organización política y económica de 27 países europeos que comparten mercado único, moneda (euro) y legislación común.",
    "ecosistema": "🌱 **Ecosistema**: Comunidad de seres vivos que interactúan entre sí y con su entorno físico (suelo, agua, clima).",
    "cambio climatico": "🌡️ **Cambio climático**: Variación a largo plazo de las temperaturas y patrones meteorológicos, acelerada actualmente por actividades humanas.",
    "atomo": "⚛️ **Átomo**: Unidad básica de la materia. Compuesto de núcleo (protones y neutrones) y electrones que orbitan a su alrededor.",
    "vacuna": "💉 **Vacuna**: Preparado biológico que proporciona inmunidad activa adquirida contra una enfermedad infecciosa.",
    "bit": "💾 **Bit**: Unidad mínima de información en informática. Puede valer 0 o 1. 8 bits = 1 byte.",
    "newton": "🍎 **Isaac Newton**: Físico y matemático inglés (1643-1727). Formuló las leyes del movimiento y la gravitación universal.",
    "shakespeare": "📜 **William Shakespeare**: Dramaturgo y poeta inglés (1564-1616). Autor de Hamlet, Romeo y Julieta y El sueño de una noche de verano.",
}

async def cmd_quees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /que_es <término>
    Ejemplo: /que_es blockchain
    """
    if not context.args:
        terminos = ", ".join(list(GLOSARIO.keys())[:10])
        await update.message.reply_text(f"Uso: /que_es <término>\nEjemplos: {terminos}\u2026")
        return

    termino = " ".join(context.args).lower().strip()
    definicion = GLOSARIO.get(termino)
    if not definicion:
        for k, v in GLOSARIO.items():
            if termino in k or k in termino:
                definicion = v
                break

    if definicion:
        await update.message.reply_text(definicion, parse_mode="Markdown")
    else:
        await update.message.reply_text(f"🔍 Buscando <b>{termino}</b>\u2026", parse_mode="HTML")
        try:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "Eres un asistente de conocimiento general. Define el término en 2-3 líneas en español, de forma clara y sin tecnicismos innecesarios."},
                    {"role": "user", "content": f"Define: {termino}"},
                ],
                max_tokens=200,
            )
            await update.message.reply_text(f"📖 <b>{termino.title()}</b>: {resp.choices[0].message.content}", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Groq quees error: {e}")
            await update.message.reply_text(f"❌ No encontré '{termino}'. Prueba con otro término o usa /ia para preguntar directamente.")


# ─────────────────────────────────────────────────────────────────────────────
# /quiz — Trivia de cultura general (Open Trivia DB API + traducción IA)
# ─────────────────────────────────────────────────────────────────────────────

# Categorías de Open Trivia DB relevantes (id: nombre ES)
OTDB_CATEGORIES = {
    9:  "Cultura General",
    17: "Ciencia y Naturaleza",
    23: "Historia",
    22: "Geografía",
    11: "Cine y Películas",
    12: "Música",
    18: "Informática",
    21: "Deportes",
    14: "Televisición",
    26: "Cómic",
}

# Almacena pregunta activa por usuario: {user_id: {q, ans, opts, message_id}}
_quiz_active: dict[int, dict] = {}


def _translate_text(text: str) -> str:
    """Traduce texto al español usando Groq si es necesario."""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Eres un traductor profesional. Detecta el idioma del texto. "
                    "Si ya está en español, devúelvelo tal cual sin modificar NADA. "
                    "Si está en otro idioma, tradúcelo al español de forma natural. "
                    "Devuelve SOLO el texto traducido/original, sin explicaciones."
                )},
                {"role": "user", "content": text},
            ],
            max_tokens=300,
            temperature=0.1,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return text


def _fetch_trivia_question(category_id: int | None = None) -> dict | None:
    """
    Obtiene una pregunta de la Open Trivia DB API y la traduce al español.
    Devuelve {q, opts: [str, str, str, str], ans: str (texto)} o None.
    """
    try:
        params = {"amount": 1, "type": "multiple", "difficulty": "medium"}
        if category_id:
            params["category"] = category_id
        r = requests.get("https://opentdb.com/api.php", params=params, timeout=8)
        data = r.json()
        if data.get("response_code") != 0 or not data.get("results"):
            return None
        item = data["results"][0]

        # Decodificar HTML entities
        question_raw  = html.unescape(item["question"])
        correct_raw   = html.unescape(item["correct_answer"])
        incorrect_raw = [html.unescape(x) for x in item["incorrect_answers"]]

        # Traducir al español
        question_es = _translate_text(question_raw)
        correct_es  = _translate_text(correct_raw)
        incorrect_es = [_translate_text(x) for x in incorrect_raw]

        # Mezclar opciones y asignar letras
        all_opts = incorrect_es + [correct_es]
        random.shuffle(all_opts)
        letters = ["A", "B", "C", "D"]
        opts_labeled = [f"{l}. {o}" for l, o in zip(letters, all_opts)]
        correct_letter = letters[all_opts.index(correct_es)]

        return {
            "q": question_es,
            "opts": opts_labeled,
            "ans": correct_letter,
            "ans_text": correct_es,
        }
    except Exception as e:
        logger.warning(f"Open Trivia DB error: {e}")
        return None


async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trivia de cultura general (Open Trivia DB API, traducida al español)."""
    tg_user = update.effective_user
    msg = await update.message.reply_text("🎲 Preparando pregunta…")

    q = _fetch_trivia_question()
    if not q:
        await msg.edit_text("❌ No pude obtener una pregunta. Inténtalo de nuevo.")
        return

    _quiz_active[tg_user.id] = {"ans": q["ans"], "ans_text": q["ans_text"], "q": q["q"]}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(q["opts"][0], callback_data="quiz_A"),
         InlineKeyboardButton(q["opts"][1], callback_data="quiz_B")],
        [InlineKeyboardButton(q["opts"][2], callback_data="quiz_C"),
         InlineKeyboardButton(q["opts"][3], callback_data="quiz_D")],
    ])
    await msg.edit_text(
        f"🧠 <b>Trivia</b>\n\n{q['q']}",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def cb_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la respuesta del quiz."""
    query = update.callback_query
    await query.answer()
    tg_user = query.from_user
    chosen = query.data.split("_")[1]  # A/B/C/D
    active = _quiz_active.get(tg_user.id)

    if not active:
        await query.edit_message_text("⚠️ Esta pregunta ya no está activa. Usa /quiz para una nueva.")
        return

    correct = active["ans"]
    correct_text = active.get("ans_text", correct)
    del _quiz_active[tg_user.id]

    if chosen == correct:
        await query.edit_message_text(
            f"✅ <b>¡Correcto!</b> La respuesta era <b>{correct}. {correct_text}</b>.\n\n"
            f"Pregunta: {active['q']}\n\n"
            "Usa /quiz para otra pregunta ó /trivia para elegir categoría.",
            parse_mode="HTML",
        )
    else:
        await query.edit_message_text(
            f"❌ <b>Incorrecto.</b> Elegiste <b>{chosen}</b>, la respuesta correcta era <b>{correct}. {correct_text}</b>.\n\n"
            f"Pregunta: {active['q']}\n\n"
            "Usa /quiz para intentarlo de nuevo.",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────────────────────
# /trivia — Trivia por categorías seleccionables
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra selector de categorías para una trivia específica."""
    rows = []
    cats = list(OTDB_CATEGORIES.items())
    for i in range(0, len(cats), 2):
        row = []
        for cat_id, cat_name in cats[i:i+2]:
            row.append(InlineKeyboardButton(cat_name, callback_data=f"trivia_cat_{cat_id}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🎲 Categoría aleatoria", callback_data="trivia_cat_0")])

    await update.message.reply_text(
        "🎮 <b>Trivia por categorías</b>\n\nElige un tema:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cb_trivia_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Carga una pregunta de la categoría seleccionada."""
    query = update.callback_query
    await query.answer()
    tg_user = query.from_user
    cat_id_str = query.data.replace("trivia_cat_", "")
    cat_id = int(cat_id_str) if cat_id_str != "0" else None
    cat_name = OTDB_CATEGORIES.get(cat_id, "Aleatoria") if cat_id else "Aleatoria"

    await query.edit_message_text(f"⏳ Buscando pregunta de <b>{cat_name}</b>…", parse_mode="HTML")

    q = _fetch_trivia_question(cat_id)
    if not q:
        await query.edit_message_text("❌ No pude obtener una pregunta. Inténtalo de nuevo con /trivia.")
        return

    _quiz_active[tg_user.id] = {"ans": q["ans"], "ans_text": q["ans_text"], "q": q["q"]}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(q["opts"][0], callback_data="quiz_A"),
         InlineKeyboardButton(q["opts"][1], callback_data="quiz_B")],
        [InlineKeyboardButton(q["opts"][2], callback_data="quiz_C"),
         InlineKeyboardButton(q["opts"][3], callback_data="quiz_D")],
    ])
    await query.edit_message_text(
        f"🎮 <b>Trivia — {cat_name}</b>\n\n{q['q']}",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─────────────────────────────────────────────────────────────────────────────
# /ahorcado — Juego del ahorcado con palabras en español
# ─────────────────────────────────────────────────────────────────────────────

_HANGMAN_STAGES = [
    "╔════╗\n║    ║\n║    ○\n║   /|\\ \n║   / \ \n║\n🔹 FAIL (6/6)",
    "╔════╗\n║    ║\n║    ○\n║   /|\ \n║   /   \n║\n🔸 (5/6)",
    "╔════╗\n║    ║\n║    ○\n║   /|\ \n║       \n║\n(4/6)",
    "╔════╗\n║    ║\n║    ○\n║   /|  \n║       \n║\n(3/6)",
    "╔════╗\n║    ║\n║    ○\n║    |  \n║       \n║\n(2/6)",
    "╔════╗\n║    ║\n║    ○\n║       \n║       \n║\n(1/6)",
    "╔════╗\n║    ║\n║       \n║       \n║       \n║\n(0/6) - libre",
]

# Banco de palabras en español (se pueden obtener más desde la API)
_SPANISH_WORDS = [
    "murciélago", "helicóptero", "paraguas", "biblioteca", "computadora",
    "mariposa", "chocolate", "dinosaurio", "elefante", "telescopio",
    "submarino", "ferrocarril", "acuario", "ciempiéés", "estación",
    "medicina", "arquitectura", "laboratorio", "expedición", "manzanilla",
    "calabaza", "ventilador", "cuestionario", "periodismo", "diccionario",
    "magnetismo", "pantanoso", "astronomia", "fotosintesis", "volcán",
    "canguro", "pingüino", "cocodrilo", "hipopótamo", "rinoceronte",
    "geometria", "quimica", "política", "economia", "filosofia",
    "revolucion", "democracia", "republica", "monarquia", "parlamento",
    "atletismo", "baloncesto", "natación", "gimnasia", "ciclismo",
    "primavera", "invierno", "otoño", "verano", "temporada",
    "catedral", "palacio", "castillo", "fortaleza", "monasterio",
    "acantilado", "cascada", "peninsula", "continente", "archipielago",
    "energia", "electricidad", "magnetico", "gravedad", "radiación",
]

# Estado del juego por usuario
_hangman_games: dict[int, dict] = {}


def _fetch_spanish_word() -> str:
    """Intenta obtener una palabra española de una API; si falla usa el banco local."""
    try:
        r = requests.get(
            "https://random-words-api.vercel.app/word/spanish",
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            word = data[0].get("word", "") if isinstance(data, list) else ""
            if word and len(word) >= 4 and word.isalpha():
                return word.lower()
    except Exception:
        pass
    return random.choice(_SPANISH_WORDS)


def _render_hangman(game: dict) -> str:
    """Renderiza el estado actual del ahorcado."""
    word    = game["word"]
    guessed = game["guessed"]
    wrong   = game["wrong"]
    lives   = game["lives"]

    stage = _HANGMAN_STAGES[min(6 - lives, len(_HANGMAN_STAGES) - 1)]
    displayed = " ".join(c if c in guessed else ("_" if c != "-" else "-") for c in word)
    wrong_str = " ".join(sorted(wrong)) if wrong else "—"

    return (
        f"<pre>{stage}</pre>\n\n"
        f"📝 <b>{displayed}</b>\n\n"
        f"❤️ Vidas: <b>{lives}</b>\n"
        f"❌ Letras falladas: <b>{wrong_str}</b>\n\n"
        "Responde con una letra (ej: <code>a</code>) o la palabra completa."
    )


async def cmd_ahorcado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia un nuevo juego del ahorcado."""
    tg_user = update.effective_user
    msg = await update.message.reply_text("🎮 Preparando palabra…")

    word = _fetch_spanish_word().lower()
    _hangman_games[tg_user.id] = {
        "word": word,
        "guessed": set(),
        "wrong": set(),
        "lives": 6,
        "chat_id": update.effective_chat.id,
    }

    await msg.edit_text(
        "🧩 <b>¡Nuevo Ahorcado!</b>\n\n" + _render_hangman(_hangman_games[tg_user.id]),
        parse_mode="HTML",
    )


async def msg_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa mensajes de texto del usuario. Si tiene una partida activa de ahorcado o wordle,
    interpreta el mensaje como respuesta de juego.
    """
    tg_user = update.effective_user
    texto = update.message.text.strip().lower()

    # ——— PROCESAR JUEGO WORDLE ———
    w_game = _wordle_games.get(tg_user.id)
    if w_game:
        # Validar si el texto es de exactamente 5 letras
        if len(texto) == 5 and texto.isalpha():
            await _process_wordle_guess(update, tg_user.id, w_game, texto)
            return

    # ——— PROCESAR JUEGO AHORCADO ———
    h_game = _hangman_games.get(tg_user.id)
    if not h_game:
        return

    word = h_game["word"]

    # Intento de adivinar la palabra completa
    if len(texto) > 1:
        if texto == word:
            del _hangman_games[tg_user.id]
            await update.message.reply_text(
                f"🎉 <b>¡Correcto! ¡Has ganado!</b>\nLa palabra era: <b>{word.upper()}</b>\n\nUsa /ahorcado para jugar de nuevo.",
                parse_mode="HTML",
            )
        else:
            h_game["lives"] -= 1
            if h_game["lives"] <= 0:
                del _hangman_games[tg_user.id]
                await update.message.reply_text(
                    f"💀 <b>¡Fallaste!</b> No era <b>{texto.upper()}</b>.\nLa palabra era: <b>{word.upper()}</b>\n\nUsa /ahorcado para jugar de nuevo.",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"❌ No era <b>{texto.upper()}</b>.\n\n" + _render_hangman(h_game),
                    parse_mode="HTML",
                )
        return

    # Letra individual
    if not texto.isalpha() or len(texto) != 1:
        return  # ignorar si no es una letra

    letra = texto
    if letra in h_game["guessed"] or letra in h_game["wrong"]:
        await update.message.reply_text(f"⚠️ Ya usaste la letra <b>{letra.upper()}</b>.", parse_mode="HTML")
        return

    if letra in word:
        h_game["guessed"].add(letra)
        # Comprobar victoria
        if all(c in h_game["guessed"] for c in word if c != "-"):
            del _hangman_games[tg_user.id]
            await update.message.reply_text(
                f"🎉 <b>¡Ganaste!</b> La palabra era: <b>{word.upper()}</b>\n\nUsa /ahorcado para jugar de nuevo.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"✅ ¡La letra <b>{letra.upper()}</b> está en la palabra!\n\n" + _render_hangman(h_game),
                parse_mode="HTML",
            )
    else:
        h_game["wrong"].add(letra)
        h_game["lives"] -= 1
        if h_game["lives"] <= 0:
            del _hangman_games[tg_user.id]
            await update.message.reply_text(
                f"💀 <b>¡Sin vidas!</b> La letra <b>{letra.upper()}</b> no estaba.\nLa palabra era: <b>{word.upper()}</b>\n\nUsa /ahorcado para jugar de nuevo.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ La letra <b>{letra.upper()}</b> no está en la palabra.\n\n" + _render_hangman(h_game),
                parse_mode="HTML",
            )


# ─────────────────────────────────────────────────────────────────────────────
# /wordle — Juego Wordle en español
# ─────────────────────────────────────────────────────────────────────────────

# Banco de palabras comunes de exactamente 5 letras (sin tildes)
_WORDLE_WORDS = [
    w for w in [
        "abajo", "abril", "abrir", "abuso", "acido", "actor", "acuso", "adios", "afijo", "agudo",
        "ahora", "aire", "ajazo", "alado", "alamo", "aldea", "alelo", "alero", "aleta", "alfaz",
        "altar", "amina", "amigo", "animo", "anual", "apuro", "arbol", "arena", "arido", "aroma", "arroz",
        "asado", "atajo", "atril", "avaro", "avena", "avion", "aviso", "bache", "bahia", "bailo", "bajar", "balon",
        "banco", "banda", "bañar", "barba", "barco", "barra", "barro", "basto", "baton", "besos", "bicho", "bingo",
        "blusa", "bocel", "bolsa", "bomba", "borde", "botar", "boton", "boxeo", "brisa", "broca", "broma", "brote",
        "brujo", "bueno", "busto", "buzon", "cacao", "caida", "caldo", "calle", "calor", "campo", "canal", "canto",
        "caoba", "capaz", "carta", "casco", "celda", "cerdo", "cerro", "cesta", "choza", "cielo", "ciego", "cinco",
        "circo", "cisne", "clase", "clave", "clima", "cloro", "cobre", "coche", "color", "coral", "coro", "corte",
        "crema", "cruel", "cuero", "cueva", "culpa", "curso", "dados", "danza", "deber", "debil", "decor", "dejar",
        "denso", "dicho", "dieta", "doble", "dolar", "dolor", "donde", "dosis", "droga", "ducha",
        "dudar", "dueño", "dulce", "ebano", "ellos", "enano", "enero", "enojo", "epoca", "error", "esqui", "estar",
        "etica", "exito", "extra", "facil", "falda", "falso", "farol", "fatal", "favor", "fecha", "feliz", "feria",
        "fibra", "fiera", "fijar", "final", "finca", "finta", "firma", "flaco", "flora", "flota", "flujo", "fondo",
        "forma", "forro", "fresa", "fruta", "fuego", "fuera", "furia", "gallo", "ganso", "garra", "gasto", "gato",
        "genio", "gente", "glove", "gordo", "gorra", "gotas", "grado", "grano", "grasa", "gripa", "grito", "grupo",
        "guapo", "guiar", "gusto", "hacer", "hacha", "hacia", "hasta", "hielo", "hiena", "hogar",
        "hongo", "hueso", "huevo", "humor", "hurto", "icono", "ideal", "idolo", "igual", "jabon", "jalar",
        "jalea", "jaula", "joven", "jugar", "junto", "justo", "koala", "labio", "largo", "latir", "leche", "legal",
        "lento", "leona", "letra", "leyes", "libro", "lider", "limon", "linea", "llano", "llave", "lleno", "lloro",
        "local", "lucha", "lugar", "lunar", "macon", "madre", "magia", "malla", "mando", "manga", "mango", "manta",
        "marca", "marea", "maria", "marzo", "melon", "menor", "mente", "merma", "meses", "metal", "metro",
        "miedo", "milla", "minar", "mirar", "mismo", "mitad", "molde", "moral", "morir", "motor", "mucho",
        "mujer", "multa", "mundo", "musgo", "nacer", "nadar", "nariz", "natal", "necio", "negro", "nieta",
        "nieto", "nieve", "niñez", "noche", "norte", "noved", "nubio", "nueva", "nuevo", "nunca", "obeso", "ocaso",
        "oeste", "oveja", "padre", "pagar", "palma", "panal", "pardo", "pared", "parte", "pasar", "paseo",
        "pasto", "pecho", "peine", "pelos", "penal", "perno", "perro", "peral", "pesca", "piano",
        "pieza", "pinar", "pinta", "pinza", "pique", "pista", "placa", "plano", "plata", "playa",
        "plaza", "pleno", "plomo", "pluma", "pobre", "poder", "poeta", "polvo", "poner", "poste", "potro",
        "prado", "preso", "primo", "prisa", "prosa", "pudor", "pulso", "punto", "purga", "quedo", "queja", "queso",
        "quien", "quita", "quito", "rabia", "racha", "radio", "rasgo", "raton", "rayos", "razon", "recto", "regar",
        "regla", "reloj", "renta", "resta", "resto", "reyes", "rigor", "ritmo", "roble", "rocas",
        "rollo", "rubio", "ruido", "rumor", "rural", "saber", "sabio", "sabor", "sacar", "salto", "salud", "santo",
        "savia", "sedal", "sello", "señal", "señor", "serie", "serio", "sigma", "siglo", "signo", "silla", "simio",
        "sirve", "sobre", "sobra", "socio", "sodio", "solar", "solos", "sordo", "suave", "subir", "sucio", "sudor",
        "suelo", "sueño", "super", "susto", "sutil", "tabla", "tacto", "tallo", "talon", "tapar", "tarde", "tarro",
        "tazon", "techo", "tejer", "telon", "temor", "tenis", "terco", "texto", "tibia", "tielo", "tinta", "tocar",
        "tomar", "tonto", "toser", "traer", "trago", "traje", "trato", "tribu", "trigo", "tripa", "trozo", "truco",
        "tumba", "tumor", "turbo", "urgir", "usado", "vacio", "vagon", "valor", "vapor", "vasco", "veces", "velar",
        "vello", "veloz", "venda", "venir", "verde", "verso", "veste", "viaje", "vidas", "viejo", "vigor",
        "viuda", "viudo", "vocal", "volar", "voraz", "vuelo", "yacer", "yemas", "yerno", "zanja", "zarza", "zebra",
        "zorro", "zueco"
    ] if len(w) == 5
]

# Estado del juego Wordle por usuario
_wordle_games: dict[int, dict] = {}


def _render_wordle(game: dict) -> str:
    """Renderiza el tablero actual de Wordle."""
    lines = []
    for guess, result in game["attempts"]:
        # result: "G" (green), "Y" (yellow), "B" (black/gray)
        squares = ""
        for r in result:
            if r == "G": squares += "🟩"
            elif r == "Y": squares += "🟨"
            else: squares += "⬛"
        # Mostrar las letras en mayúscula separadas por un espacio
        word_str = " ".join(guess.upper())
        lines.append(f"{squares}  <code>{word_str}</code>")
    
    # Rellenar intentos restantes
    rem = 6 - len(game["attempts"])
    for _ in range(rem):
        lines.append("⬜⬜⬜⬜⬜")

    return "\n".join(lines)


async def cmd_wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia un nuevo juego de Wordle."""
    tg_user = update.effective_user
    word = random.choice(_WORDLE_WORDS)
    
    _wordle_games[tg_user.id] = {
        "word": word,
        "attempts": []  # Lista de tuplas (palabra_intentada, resultado_colors)
    }

    await update.message.reply_text(
        "🔠 <b>¡Nuevo Wordle!</b>\n\n"
        "Adivina la palabra de 5 letras en 6 intentos.\n"
        "Escribe una palabra de 5 letras para empezar:\n\n" +
        _render_wordle(_wordle_games[tg_user.id]),
        parse_mode="HTML",
    )


async def _process_wordle_guess(update: Update, user_id: int, game: dict, guess: str):
    word = game["word"]
    
    # Calcular feedback (Green / Yellow / Black)
    result = ["B"] * 5
    word_chars = list(word)
    
    # 1º pasada: Encontrar verdes (posición correcta)
    for i in range(5):
        if guess[i] == word[i]:
            result[i] = "G"
            word_chars[i] = None  # Marcar como usado
            
    # 2º pasada: Encontrar amarillos (letra correcta, posición incorrecta)
    for i in range(5):
        if result[i] == "G":
            continue
        if guess[i] in word_chars:
            result[i] = "Y"
            # Remover de los caracteres disponibles para no contar doble
            word_chars[word_chars.index(guess[i])] = None

    game["attempts"].append((guess, "".join(result)))
    
    # Comprobar victoria
    if guess == word:
        del _wordle_games[user_id]
        await update.message.reply_text(
            f"🎉 <b>¡Impresionante! ¡Has ganado!</b>\n\n" +
            _render_wordle(game) +
            "\n\nLa palabra era: <b>" + word.upper() + "</b>\nUsa /wordle para jugar de nuevo.",
            parse_mode="HTML"
        )
        return

    # Comprobar derrota
    if len(game["attempts"]) >= 6:
        del _wordle_games[user_id]
        await update.message.reply_text(
            f"💀 <b>¡Se acabaron los intentos!</b>\n\n" +
            _render_wordle(game) +
            "\n\nLa palabra era: <b>" + word.upper() + "</b>\nUsa /wordle para intentarlo de nuevo.",
            parse_mode="HTML"
        )
        return

    # Continuar jugando
    await update.message.reply_text(
        _render_wordle(game),
        parse_mode="HTML"
    )


async def cmd_clima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /clima <ciudad>  Ejemplo: /clima Madrid"""
    if not context.args:
        await update.message.reply_text("Uso: /clima <ciudad>\nEjemplo: /clima Tokyo")
        return
    ciudad = "+".join(context.args)
    try:
        r = requests.get(f"https://wttr.in/{ciudad}?format=j1&lang=es", timeout=10)
        data = r.json()
        cur  = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        name = area.get("areaName", [{}])[0].get("value", ciudad)
        country = area.get("country", [{}])[0].get("value", "")
        fc = data.get("weather", [])
        forecast_lines = []
        for day in fc[1:3]:
            forecast_lines.append(
                f"  📅 {day['date']}: {day['mintempC']}°–{day['maxtempC']}° "
                f"{day['hourly'][4]['weatherDesc'][0]['value']}"
            )
        text = (
            f"🌤 <b>{name}, {country}</b>\n\n"
            f"🌡 <b>{cur['temp_C']}°C</b> (sensación {cur['FeelsLikeC']}°C)\n"
            f"☁️ {cur['weatherDesc'][0]['value']}\n"
            f"💧 Humedad: {cur['humidity']}%\n"
            f"💨 Viento: {cur['windspeedKmph']} km/h\n\n"
            f"<b>Próximos días:</b>\n" + "\n".join(forecast_lines)
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"clima error: {e}")
        await update.message.reply_text("❌ Ciudad no encontrada o servicio no disponible.")


# ─────────────────────────────────────────────────────────────────────────────
# /traducir — Traductor con IA
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_traducir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /traducir <texto>  (si es español → inglés, si no → español)"""
    if not context.args:
        await update.message.reply_text("Uso: /traducir <texto>\nEjemplo: /traducir Hello World")
        return
    texto = " ".join(context.args)
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Eres un traductor profesional. Detecta el idioma del texto. "
                    "Si está en español tradúcelo al inglés. Si está en otro idioma tradúcelo al español. "
                    "Devuelve SOLO la traducción, sin explicaciones."
                )},
                {"role": "user", "content": texto},
            ],
            max_tokens=500,
            temperature=0.1,
        )
        trad = resp.choices[0].message.content.strip()
        await update.message.reply_text(
            f"🌐 <b>Traducción:</b>\n{trad}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"traducir error: {e}")
        await update.message.reply_text("❌ Error al traducir. Inténtalo de nuevo.")


# ─────────────────────────────────────────────────────────────────────────────
# /calcula — Calculadora científica
# ─────────────────────────────────────────────────────────────────────────────

import math as _math

def _safe_eval(expr: str):
    expr = expr.replace("^", "**").replace(",", ".").strip()
    if len(expr) > 200:
        raise ValueError("Expresión demasiado larga")
    ns = {
        "__builtins__": {},
        "sqrt": _math.sqrt, "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
        "asin": _math.asin, "acos": _math.acos, "atan": _math.atan,
        "log": _math.log, "log10": _math.log10, "log2": _math.log2,
        "abs": abs, "round": round, "int": int, "float": float,
        "pi": _math.pi, "e": _math.e,
        "ceil": _math.ceil, "floor": _math.floor, "max": max, "min": min,
    }
    return eval(expr, ns)


async def cmd_calcula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /calcula <expresión>  Ejemplo: /calcula sqrt(144) + 2^8"""
    if not context.args:
        await update.message.reply_text(
            "🧮 Uso: /calcula <expresión>\n\n"
            "Ejemplos:\n  /calcula 2^10\n  /calcula sqrt(144)\n  /calcula sin(pi/2)\n  /calcula log(1000)"
        )
        return
    expr = " ".join(context.args)
    try:
        result = _safe_eval(expr)
        if isinstance(result, float):
            result_str = f"{result:,.10g}"
        else:
            result_str = str(result)
        await update.message.reply_text(
            f"🧮 <code>{expr}</code>\n= <b>{result_str}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: <code>{e}</code>\n\nFunciones disponibles: sqrt, sin, cos, tan, log, pi, e…", parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /dado y /sorteo
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_dado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /dado [caras]  Ejemplo: /dado 20"""
    caras = 6
    if context.args:
        try:
            caras = int(context.args[0])
            if caras < 2:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ El número de caras debe ser un entero ≥ 2.")
            return
    resultado = random.randint(1, caras)
    await update.message.reply_text(
        f"🎲 <b>d{caras}</b> → <b>{resultado}</b>",
        parse_mode="HTML",
    )


async def cmd_sorteo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /sorteo <mín> <máx>  Ejemplo: /sorteo 1 100"""
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /sorteo <mín> <máx>\nEjemplo: /sorteo 1 1000")
        return
    try:
        a, b = int(context.args[0]), int(context.args[1])
        if a >= b:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Introduce dos enteros donde mín < máx.")
        return
    resultado = random.randint(a, b)
    await update.message.reply_text(
        f"🎰 Sorteo <b>{a}</b>–<b>{b}</b>: 🏆 <b>{resultado}</b>",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /agenda — Próximos 7 días
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra recordatorios y eventos de los próximos 7 días."""
    tg_user = update.effective_user
    now  = datetime.now()
    fin  = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    rems = conn.execute(
        "SELECT * FROM reminders WHERE user_id=? AND sent=0 AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
        (tg_user.id, now_s, fin)
    ).fetchall()
    evts = conn.execute(
        "SELECT * FROM events WHERE creator_id=? AND event_date BETWEEN ? AND ? ORDER BY event_date",
        (tg_user.id, now_s, fin)
    ).fetchall()
    conn.close()

    if not rems and not evts:
        await update.message.reply_text(
            "📅 No tienes nada los próximos 7 días.\n"
            "Usa /recordatorio o /crear_evento para añadir algo."
        )
        return

    lines = [f"📅 <b>Agenda — próximos 7 días</b>\n"]
    if rems:
        lines.append("⏰ <b>Recordatorios:</b>")
        for r in rems:
            lines.append(f"  • {r['remind_at'][:16]} — {r['message']}")
    if evts:
        lines.append("\n📌 <b>Eventos:</b>")
        for e in evts:
            lines.append(f"  • {e['event_date'][:16]} — {e['title']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /acortar — Acortar URL con TinyURL
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_acortar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /acortar <url>"""
    if not context.args:
        await update.message.reply_text("Uso: /acortar <url>\nEjemplo: /acortar https://ejemplo.com/url-muy-larga")
        return
    url = context.args[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.get(f"https://tinyurl.com/api-create.php?url={url}", timeout=8)
        short = r.text.strip()
        if not short.startswith("http"):
            raise ValueError("API error")
        await update.message.reply_text(
            f"🔗 URL acortada:\n<code>{short}</code>",
            parse_mode="HTML",
        )
    except Exception:
        await update.message.reply_text("❌ No pude acortar la URL. Verifica que sea válida.")


# ─────────────────────────────────────────────────────────────────────────────
# Registro de handlers
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    """Registra todos los handlers de comandos gratuitos."""
    from telegram.ext import MessageHandler, filters
    app.add_handler(CommandHandler("noticias",  cmd_noticias))
    app.add_handler(CommandHandler("cambio",    cmd_cambio))
    app.add_handler(CommandHandler("hora",      cmd_hora))
    app.add_handler(CommandHandler("que_es",    cmd_quees))
    app.add_handler(CommandHandler("quiz",      cmd_quiz))
    app.add_handler(CommandHandler("trivia",    cmd_trivia))
    app.add_handler(CommandHandler("ahorcado",  cmd_ahorcado))
    app.add_handler(CommandHandler("wordle",    cmd_wordle))
    app.add_handler(CommandHandler("clima",     cmd_clima))
    app.add_handler(CommandHandler("traducir",  cmd_traducir))
    app.add_handler(CommandHandler("calcula",   cmd_calcula))
    app.add_handler(CommandHandler("dado",      cmd_dado))
    app.add_handler(CommandHandler("sorteo",    cmd_sorteo))
    app.add_handler(CommandHandler("agenda",    cmd_agenda))
    app.add_handler(CommandHandler("acortar",   cmd_acortar))
    app.add_handler(CallbackQueryHandler(cb_quiz_answer,    pattern=r"^quiz_[ABCD]$"))
    app.add_handler(CallbackQueryHandler(cb_trivia_category, pattern=r"^trivia_cat_\d+$"))
    # Handler de texto para juegos interactivos (baja prioridad, group=10)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        msg_games
    ), group=10)
    logger.info("✅ features/free: /noticias /cambio /hora /que_es /quiz /trivia /ahorcado /wordle /clima /traducir /calcula /dado /sorteo /agenda /acortar")

