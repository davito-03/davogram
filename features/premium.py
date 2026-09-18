#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/premium.py — Comandos exclusivos Premium
  /ia             Chat con IA (Groq → Cohere → Cloudflare)
  /grafico        Gráfico de precio de cualquier activo
  /cartera        Seguimiento de activos (add/ver/del)
  /exportar       Exportar recordatorios+eventos a CSV
  /activar_resumen / /desactivar_resumen
  /smart          Crear recordatorios y alertas desde texto libre
  Schedulers: resumen_diario (8:00) y agenda_semanal (lunes 9:00)
"""

import io
import csv
import sqlite3
import logging
import requests
import functools
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import (
    DB_PATH, GROQ_API_KEY, GROQ_MODEL, COHERE_API_KEY,
    CF_API_TOKEN, CF_ACCOUNT_ID, CF_MODEL,
    TAVILY_API_KEY, BRAVE_API_KEY,
)

logger = logging.getLogger("DavogramBot.premium")

# historial de conversación en memoria: {user_id: [{"role":..,"content":..}]}
_ia_history: dict[int, list[dict]] = {}

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_premium_db():
    """Crea tablas extra y migraciones para funciones Premium."""
    conn = _conn()
    c = conn.cursor()
    # Tabla de cartera
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            symbol      TEXT NOT NULL,
            qty         REAL NOT NULL,
            avg_price   REAL NOT NULL,
            added_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol)
        )
    """)
    # Columna resumen_activo en users (migración segura)
    try:
        c.execute("ALTER TABLE users ADD COLUMN resumen_activo INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    conn.close()
    logger.info("✅ premium DB inicializada (portfolio, resumen_activo)")


# ─────────────────────────────────────────────────────────────────────────────
# Decorador require_premium local (lee directamente de la BD)
# ─────────────────────────────────────────────────────────────────────────────

def _require_premium(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        tg_user = update.effective_user
        conn = _conn()
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (tg_user.id,)).fetchone()
        conn.close()
        if not user or user["is_banned"]:
            await update.effective_message.reply_text("🚫 Tu cuenta ha sido suspendida.")
            return
        # Admin siempre pasa
        if user["is_admin"]:
            return await func(update, context, *args, **kwargs)
        # Comprobar expiración
        if user["is_premium"] and user["subscription_expires"]:
            try:
                expires = datetime.strptime(user["subscription_expires"], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expires:
                    await update.effective_message.reply_text(
                        "⏰ *Tu suscripción Premium ha expirado.*\nUsa /upgrade para renovar.",
                        parse_mode="Markdown",
                    )
                    return
            except Exception:
                pass
        if not user["is_premium"]:
            await update.effective_message.reply_text(
                "🔒 *Función exclusiva Premium*\nUsa /upgrade para suscribirte.",
                parse_mode="Markdown",
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# /ia — Chat con IA financiera
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "Eres Davogram, un asistente inteligente y versátil creado por @davito_03. "
    "Puedes ayudar con cualquier tema: tecnología, ciencia, historia, cultura, programación, "
    "finanzas, inversiones, idiomas, recetas, viajes, salud, entretenimiento y mucho más. "
    "Respondes siempre en español, de forma clara, concisa y útil. "
    "Usas emojis y listas cuando mejoran la legibilidad. "
    "Cuando se te proporcionan fragmentos de [CONTEXTO WEB ACTUAL], úsalos como fuente "
    "principal para responder, indicando que la información es actualizada."
)

MAX_HISTORY = 6  # mensajes por usuario guardados en memoria


def _groq_chat(messages: list[dict]) -> str:
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=600,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def _cohere_chat(messages: list[dict]) -> str:
    import cohere
    co = cohere.Client(api_key=COHERE_API_KEY)
    history = [{"role": m["role"], "message": m["content"]} for m in messages[:-1]]
    resp = co.chat(message=messages[-1]["content"], chat_history=history, preamble=SYSTEM_PROMPT)
    return resp.text.strip()


def _cloudflare_chat(messages: list[dict]) -> str:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    r = requests.post(url, headers=headers, json={"messages": messages}, timeout=20)
    return r.json()["result"]["response"].strip()


def _tavily_context(query: str) -> str:
    """Obtiene contexto web para preguntas sobre precios o noticias recientes."""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        res = client.search(query, max_results=3, search_depth="basic")
        snippets = [r.get("content", "")[:300] for r in res.get("results", [])]
        return "\n".join(snippets) if snippets else ""
    except Exception:
        return ""


# Palabras que activan búsqueda web siempre
KEYWORDS_SEARCH = [
    # Tiempo / actualidad
    "hoy", "ahora", "actual", "actualmente", "últimas", "último", "última",
    "reciente", "recientes", "recientemente", "esta semana", "este mes", "este año",
    "hoy en día", "en este momento", "a día de hoy", "fecha", "cuando",
    # Noticias / eventos
    "noticias", "noticia", "novedades", "novedad", "sucedió", "pasó", "ocurrió",
    "aconteció", "anunció", "anunciaron", "presentaron", "lanzaron", "lanzó",
    "publicaron", "publicó", "aprobaron", "aprobó", "firmaron", "firmó",
    # Precios / finanzas
    "precio", "cotiza", "cotización", "cotizando", "vale", "cuesta", "coste",
    "mercado", "bolsa", "crypto", "bitcoin", "ethereum", "acción", "acciones",
    # Descubrimientos / investigación
    "descubrieron", "descubrió", "inventaron", "inventó", "crearon", "creó",
    "desarrollaron", "desarrolló", "estudio", "estudian", "investigación",
    # Política / deportes / cultura
    "presidenta", "presidente", "gobierno", "elecciones", "ganó", "perdió",
    "clasificó", "campeón", "campeona", "copa", "mundial", "liga", "temporada",
    "película", "serie", "álbum", "estreno", "estrena",
    # Preguntas factuales directas
    "quién es", "quién fue", "qué es", "dónde está", "cuándo fue", "cuánto vale",
    "cuál es el", "cuáles son",
]

# Señales de que la pregunta necesita datos externos aunque no haya keyword
def _needs_search(text: str) -> bool:
    """Devuelve True si la pregunta probablemente requiere información actualizada."""
    tl = text.lower()
    if any(k in tl for k in KEYWORDS_SEARCH):
        return True
    # Preguntas con '?' que no son sobre conceptos atemporales
    TIMELESS = ["cómo funciona", "qué significa", "explica", "define", "diferencia entre",
                "cuál es la diferencia", "por qué", "para qué", "historia de", "origen"]
    if "?" in text and not any(t in tl for t in TIMELESS):
        return True
    return False


@_require_premium
async def cmd_ia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat con IA. Uso: /ia <pregunta>"""
    if not context.args:
        await update.message.reply_text(
            "🤖 <b>DavoBot IA</b>\n\n"
            "Puedo ayudarte con cualquier tema: ciencia, historia, tecnología, programación, idiomas, recetas, viajes, cine, deporte y mucho más.\n\n"
            "Uso: /ia &lt;pregunta&gt;\n"
            "Ejemplo: /ia ¿Cómo funciona un agujero negro?\n\n"
            "Usa /ia_reset para borrar el historial de conversación.",
            parse_mode="HTML",
        )
        return

    pregunta = " ".join(context.args)
    tg_user  = update.effective_user
    thinking = await update.message.reply_text("🤖 Escribiendo…")

    # Contexto web si la pregunta parece necesitarlo
    extra_context = ""
    if _needs_search(pregunta):
        extra_context = _tavily_context(pregunta)

    # Construir historial
    history = _ia_history.get(tg_user.id, [])
    if not history:
        history = [{"role": "system", "content": SYSTEM_PROMPT}]

    if extra_context:
        history.append({"role": "user", "content": f"[CONTEXTO WEB ACTUAL]:\n{extra_context}\n\n{pregunta}"})
    else:
        history.append({"role": "user", "content": pregunta})

    # Intentar Groq → Cohere → Cloudflare
    respuesta = None
    for provider, fn in [("Groq", _groq_chat), ("Cohere", _cohere_chat), ("Cloudflare", _cloudflare_chat)]:
        try:
            respuesta = fn(history)
            logger.info(f"/ia respondido por {provider}")
            break
        except Exception as e:
            logger.warning(f"{provider} error: {e}")

    if not respuesta:
        await thinking.edit_text("❌ No pude conectar con ningún modelo de IA. Inténtalo más tarde.")
        return

    history.append({"role": "assistant", "content": respuesta})
    # Mantener máximo MAX_HISTORY mensajes (+ system)
    if len(history) > MAX_HISTORY + 1:
        history = [history[0]] + history[-(MAX_HISTORY):]
    _ia_history[tg_user.id] = history

    await thinking.edit_text(f"🤖 {respuesta}")


async def cmd_ia_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Borra el historial de conversación del usuario."""
    tg_user = update.effective_user
    _ia_history.pop(tg_user.id, None)
    await update.message.reply_text("🗑 Historial de conversación borrado. /ia para empezar de nuevo.")


# ─────────────────────────────────────────────────────────────────────────────
# /grafico — Gráfico de precio PNG
# ─────────────────────────────────────────────────────────────────────────────

VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"}


@_require_premium
async def cmd_grafico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /grafico <símbolo> [periodo]
    Ejemplo: /grafico BTC-USD 1mo
    Periodos: 1d 5d 1mo 3mo 6mo 1y 2y
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /grafico <símbolo> [periodo]\n"
            "Ejemplo: /grafico BTC-USD 1mo\n"
            "Periodos: 1d 5d 1mo 3mo 6mo 1y 2y"
        )
        return

    symbol = args[0].upper()
    period = args[1].lower() if len(args) > 1 else "1mo"
    if period not in VALID_PERIODS:
        period = "1mo"

    msg = await update.message.reply_text(f"📊 Generando gráfico de *{symbol}* ({period})…", parse_mode="Markdown")

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period=period)
        if hist.empty:
            await msg.edit_text(f"❌ No encontré datos para *{symbol}*. Verifica el símbolo.", parse_mode="Markdown")
            return

        # Calcular variación total del período
        pct_change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        color_line = "#00e676" if pct_change >= 0 else "#ff5252"

        # Plot
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor("#1a1a2e")
        ax.set_facecolor("#16213e")
        ax.plot(hist.index, hist["Close"], color=color_line, linewidth=2, label="Precio de cierre")
        ax.fill_between(hist.index, hist["Close"], alpha=0.15, color=color_line)
        ax.set_title(f"{symbol} — {period}  ({pct_change:+.2f}%)", color="white", fontsize=14, pad=10)
        ax.set_xlabel("Fecha", color="#aaaaaa")
        ax.set_ylabel("Precio", color="#aaaaaa")
        ax.tick_params(colors="#aaaaaa")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()
        ax.grid(color="#2a2a4a", linestyle="--", linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_edgecolor("#2a2a4a")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        buf.seek(0)
        plt.close(fig)

        await msg.delete()
        await update.message.reply_photo(
            photo=buf,
            caption=f"📊 *{symbol}* — {period} | Variación: *{pct_change:+.2f}%*",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"grafico error ({symbol}): {e}")
        await msg.edit_text("❌ Error generando el gráfico. Verifica el símbolo e inténtalo de nuevo.")


# ─────────────────────────────────────────────────────────────────────────────
# /cartera — Seguimiento de cartera personal
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_cartera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Subcomandos:
      /cartera add <símbolo> <cantidad> <precio_medio>
      /cartera ver
      /cartera del <símbolo>
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "📂 <b>Cartera de Inversión</b>\n\n"
            "<b>Subcomandos:</b>\n"
            "  /cartera add BTC-USD 0.5 42000  — añadir posición\n"
            "  /cartera ver                    — ver cartera con P&amp;L\n"
            "  /cartera del BTC-USD            — eliminar posición",
            parse_mode="HTML",
        )
        return

    sub = args[0].lower()
    tg_user = update.effective_user

    if sub == "add":
        await _cartera_add(update, tg_user.id, args[1:])
    elif sub == "ver":
        await _cartera_ver(update, tg_user.id)
    elif sub == "del":
        await _cartera_del(update, tg_user.id, args[1:])
    else:
        await update.message.reply_text("Subcomandos válidos: add, ver, del")


async def _cartera_add(update, user_id: int, args: list):
    if len(args) < 3:
        await update.message.reply_text("Uso: /cartera add <símbolo> <cantidad> <precio_medio>")
        return
    try:
        symbol    = args[0].upper()
        qty       = float(args[1].replace(",", "."))
        avg_price = float(args[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Cantidad y precio deben ser números.")
        return

    conn = _conn()
    conn.execute(
        "INSERT INTO portfolio (user_id, symbol, qty, avg_price) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id, symbol) DO UPDATE SET qty=excluded.qty, avg_price=excluded.avg_price",
        (user_id, symbol, qty, avg_price)
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ Posición guardada:\n"
        f"  📊 {symbol} — {qty} uds @ {avg_price:,.2f}",
    )


async def _cartera_ver(update, user_id: int):
    conn = _conn()
    rows = conn.execute("SELECT * FROM portfolio WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📂 Tu cartera está vacía. Usa /cartera add para añadir posiciones.")
        return

    msg = await update.message.reply_text("📊 Obteniendo precios actuales…")

    import yfinance as yf
    lines = ["📂 <b>Tu Cartera</b>\n"]
    total_inv = total_act = 0.0

    for r in rows:
        try:
            ticker  = yf.Ticker(r["symbol"])
            hist    = ticker.history(period="1d", interval="1m")
            current = float(hist["Close"].iloc[-1]) if not hist.empty else None
        except Exception:
            current = None

        inv = r["qty"] * r["avg_price"]
        total_inv += inv

        if current:
            actual  = r["qty"] * current
            pnl     = actual - inv
            pnl_pct = (pnl / inv) * 100 if inv else 0
            total_act += actual
            arrow   = "📈" if pnl >= 0 else "📉"
            lines.append(
                f"{arrow} <b>{r['symbol']}</b>  {r['qty']} uds\n"
                f"   Precio: {current:,.4f} | Medio: {r['avg_price']:,.4f}\n"
                f"   P&amp;L: {pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            )
        else:
            lines.append(f"⚪ <b>{r['symbol']}</b>  {r['qty']} uds — precio no disponible\n")

    if total_act:
        total_pnl = total_act - total_inv
        pnl_pct   = (total_pnl / total_inv) * 100 if total_inv else 0
        arrow = "📈" if total_pnl >= 0 else "📉"
        lines.append(f"\n{arrow} <b>TOTAL:</b> invertido {total_inv:,.2f} | actual {total_act:,.2f} | P&amp;L {total_pnl:+,.2f} ({pnl_pct:+.2f}%)")

    await msg.edit_text("\n".join(lines), parse_mode="HTML")


async def _cartera_del(update, user_id: int, args: list):
    if not args:
        await update.message.reply_text("Uso: /cartera del <símbolo>")
        return
    symbol = args[0].upper()
    conn = _conn()
    deleted = conn.execute("DELETE FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, symbol)).rowcount
    conn.commit()
    conn.close()
    if deleted:
        await update.message.reply_text(f"🗑 Posición {symbol} eliminada.")
    else:
        await update.message.reply_text(f"❌ No tienes ninguna posición en {symbol}.")


# ─────────────────────────────────────────────────────────────────────────────
# /exportar — CSV de recordatorios y eventos
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exporta los recordatorios y eventos del usuario como archivo CSV."""
    tg_user = update.effective_user
    conn    = _conn()
    reminders = conn.execute("SELECT * FROM reminders WHERE user_id = ? AND sent = 0", (tg_user.id,)).fetchall()
    events    = conn.execute("SELECT * FROM events WHERE creator_id = ?", (tg_user.id,)).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tipo", "Mensaje / Título", "Fecha"])
    for r in reminders:
        writer.writerow(["Recordatorio", r["message"], r["remind_at"][:16]])
    for e in events:
        writer.writerow(["Evento", e["title"], e["event_date"][:16]])

    output.seek(0)
    filename = f"davogram_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode("utf-8-sig")),
        filename=filename,
        caption=f"📂 {len(reminders)} recordatorios + {len(events)} eventos exportados.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /activar_resumen / /desactivar_resumen
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_activar_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa el resumen diario de mercado a las 8:00."""
    tg_user = update.effective_user
    conn = _conn()
    conn.execute("UPDATE users SET resumen_activo = 1 WHERE user_id = ?", (tg_user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        "✅ *Resumen diario activado* ☀️\n"
        "Recibirás un briefing de mercado cada día a las 8:00h con precios del IBEX, S&P500, BTC y noticias.",
        parse_mode="Markdown",
    )


async def cmd_desactivar_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Desactiva el resumen diario."""
    tg_user = update.effective_user
    conn = _conn()
    conn.execute("UPDATE users SET resumen_activo = 0 WHERE user_id = ?", (tg_user.id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🔕 Resumen diario desactivado.")


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler jobs
# ─────────────────────────────────────────────────────────────────────────────

_MARKET_SYMBOLS = {
    "IBEX 35":  "^IBEX",
    "S&P 500":  "^GSPC",
    "BTC":      "BTC-USD",
    "EUR/USD":  "EURUSD=X",
}


def _get_market_snapshot() -> str:
    """Obtiene variación % de los principales activos."""
    import yfinance as yf
    lines = []
    for name, sym in _MARKET_SYMBOLS.items():
        try:
            t = yf.Ticker(sym)
            h = t.history(period="2d")
            if len(h) >= 2:
                prev  = h["Close"].iloc[-2]
                curr  = h["Close"].iloc[-1]
                chg   = (curr / prev - 1) * 100
                arrow = "📈" if chg >= 0 else "📉"
                lines.append(f"{arrow} <b>{name}</b>: {curr:,.2f} ({chg:+.2f}%)")
        except Exception:
            lines.append(f"⚪ <b>{name}</b>: sin datos")
    return "\n".join(lines)


def _get_headlines(n: int = 3) -> str:
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        res    = client.search("noticias actualidad hoy últimas", max_results=n, search_depth="basic")
        items  = res.get("results", [])[:n]
        return "\n".join(f"• <a href='{r['url']}'>{r['title'][:70]}</a>" for r in items)
    except Exception:
        return "• Sin noticias disponibles"


async def job_resumen_diario(app):
    """Scheduler: cada día a las 8:00, envía briefing a usuarios con resumen_activo=1."""
    conn  = _conn()
    users = conn.execute("SELECT user_id FROM users WHERE resumen_activo = 1 AND is_banned = 0").fetchall()
    conn.close()

    if not users:
        return

    snapshot  = _get_market_snapshot()
    headlines = _get_headlines()
    fecha     = datetime.now().strftime("%d/%m/%Y")

    text = (
        f"☀️ <b>Resumen del día — {fecha}</b>\n\n"
        f"<b>Titulares</b>\n{headlines}\n\n"
        f"<i>— DavoGram Bot</i>"
    )

    for u in users:
        try:
            await app.bot.send_message(chat_id=u["user_id"], text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"resumen_diario error user {u['user_id']}: {e}")


async def job_agenda_semanal(app):
    """Scheduler: cada lunes 9:00, envía agenda de la semana a todos los Premium."""
    conn  = _conn()
    users = conn.execute("SELECT user_id FROM users WHERE is_premium = 1 AND is_banned = 0").fetchall()
    conn.close()

    now     = datetime.now()
    fin_sem = now + timedelta(days=7)

    for u in users:
        conn = _conn()
        rems = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND sent = 0 AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
            (u["user_id"], now.strftime("%Y-%m-%d %H:%M:%S"), fin_sem.strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchall()
        evts = conn.execute(
            "SELECT * FROM events WHERE creator_id = ? AND event_date BETWEEN ? AND ? ORDER BY event_date",
            (u["user_id"], now.strftime("%Y-%m-%d %H:%M:%S"), fin_sem.strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchall()
        conn.close()

        if not rems and not evts:
            continue  # No enviar si no hay nada

        lines = [f"📅 <b>Tu Agenda — semana del {now.strftime('%d/%m')}</b>\n"]
        if rems:
            lines.append("<b>⏰ Recordatorios:</b>")
            for r in rems:
                lines.append(f"  • {r['remind_at'][:16]} — {r['message']}")
        if evts:
            lines.append("\n<b>📌 Eventos:</b>")
            for e in evts:
                lines.append(f"  • {e['event_date'][:16]} — {e['title']}")

        try:
            await app.bot.send_message(chat_id=u["user_id"], text="\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.warning(f"agenda_semanal error user {u['user_id']}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# /smart — Crear recordatorios y alertas de precio desde texto libre (IA)
# ─────────────────────────────────────────────────────────────────────────────

import json

SMART_SYSTEM = """Eres un extractor de tareas y alertas. A partir del texto del usuario, extrae:
1. Recordatorios con fecha y hora (resuelve fechas relativas según la fecha actual indicada).
2. Alertas de precio (símbolo de activo, si el precio debe superar o bajar de cierto valor).

Devuelve ÚNICAMENTE un JSON válido con este formato exacto (sin explicaciones, sin markdown):
{
  "reminders": [
    {"message": "texto del recordatorio", "datetime": "YYYY-MM-DD HH:MM"}
  ],
  "price_alerts": [
    {"symbol": "TICKER", "direction": "above|below", "target": 12345.6}
  ]
}

Si no hay elementos de un tipo, usa array vacío [].
Usa símbolos de yfinance: BTC-USD, ETH-USD, ^IBEX, ^GSPC, AAPL, SAN.MC, etc.
Si el usuario menciona "bitcoin" usa BTC-USD, "ethereum" usa ETH-USD, "ibex" usa ^IBEX, "sp500" usa ^GSPC."""

# Almacén temporal: {user_id: {reminders: [...], price_alerts: [...]}}
_smart_pending: dict[int, dict] = {}


def _parse_smart(text: str, now: datetime) -> dict | None:
    """Llama a Groq para extraer reminders y price_alerts del texto."""
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SMART_SYSTEM},
            {"role": "user",   "content": f"Fecha y hora actual: {now.strftime('%Y-%m-%d %H:%M')}\n\nTexto: {text}"},
        ],
        max_tokens=600,
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip()
    # Limpiar posible markdown ```json ... ```
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.split("```")[0].strip()
    return json.loads(raw)


@_require_premium
async def cmd_smart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Uso: /smart <texto libre>
    Ejemplo: /smart Reunión con cliente mañana a las 10:30 y avisar si Bitcoin baja de 80000
    La IA extrae automáticamente recordatorios y alertas de precio.
    """
    if not context.args:
        await update.message.reply_text(
            "🧠 <b>Smart — IA extrae recordatorios y alertas</b>\n\n"
            "Escribe tu texto en lenguaje natural y la IA creará los recordatorios y alertas automáticamente.\n\n"
            "<b>Ejemplos:</b>\n"
            "  /smart Reunión el viernes a las 16:00 y el día 1 del mes que viene avisar del alquiler\n"
            "  /smart Alerta si Bitcoin supera 100000 o si Ethereum baja de 2000\n"
            "  /smart Llamar al médico mañana a las 9, comprar regalo para el cumple el 28 a las 18h",
            parse_mode="HTML",
        )
        return

    texto   = " ".join(context.args)
    tg_user = update.effective_user
    msg     = await update.message.reply_text("🧠 Analizando tu texto…")

    try:
        resultado = _parse_smart(texto, datetime.now())
    except Exception as e:
        logger.warning(f"/smart parse error: {e}")
        await msg.edit_text("❌ No pude entender el texto. Prueba a ser más específico con las fechas y activos.")
        return

    reminders    = resultado.get("reminders", [])
    price_alerts = resultado.get("price_alerts", [])

    if not reminders and not price_alerts:
        await msg.edit_text("🤔 No encontré recordatorios ni alertas en tu texto. Intenta ser más concreto.")
        return

    # Guardar pendiente para confirmación
    _smart_pending[tg_user.id] = {"reminders": reminders, "price_alerts": price_alerts}

    # Construir resumen visual
    lines = ["🧠 <b>He detectado lo siguiente — ¿Lo creo todo?</b>\n"]

    if reminders:
        lines.append("⏰ <b>Recordatorios:</b>")
        for r in reminders:
            lines.append(f"  • <b>{r.get('datetime', '??')}</b> — {r.get('message', '')}")

    if price_alerts:
        lines.append("\n🔔 <b>Alertas de precio:</b>")
        for a in price_alerts:
            arrow = "📈 suba a" if a.get("direction") == "above" else "📉 baje de"
            lines.append(f"  • <b>{a.get('symbol', '??')}</b> cuando {arrow} {a.get('target', 0):,.2f}")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Crear todo", callback_data="smart_confirm"),
        InlineKeyboardButton("❌ Cancelar",   callback_data="smart_cancel"),
    ]])

    await msg.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def cb_smart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crea todos los recordatorios y alertas de precio detectados."""
    query  = update.callback_query
    await query.answer()
    tg_user = query.from_user
    pending = _smart_pending.pop(tg_user.id, None)

    if not pending:
        await query.edit_message_text("⚠️ No hay nada pendiente. Usa /smart para empezar.")
        return

    conn      = _conn()
    created_r = created_a = 0
    errors    = []

    for r in pending.get("reminders", []):
        try:
            # Normalizar datetime
            dt_str = r["datetime"]
            # Acepta YYYY-MM-DD HH:MM o YYYY-MM-DD HH:MM:SS
            if len(dt_str) == 16:
                dt_str += ":00"
            datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")  # validar
            conn.execute(
                "INSERT INTO reminders (user_id, message, remind_at) VALUES (?,?,?)",
                (tg_user.id, r["message"], dt_str)
            )
            created_r += 1
        except Exception as e:
            errors.append(f"Recordatorio '{r.get('message', '')}': {e}")

    for a in pending.get("price_alerts", []):
        try:
            conn.execute(
                "INSERT INTO price_alerts (user_id, symbol, direction, target) VALUES (?,?,?,?)",
                (tg_user.id, a["symbol"].upper(), a["direction"], float(a["target"]))
            )
            created_a += 1
        except Exception as e:
            errors.append(f"Alerta '{a.get('symbol', '')}': {e}")

    conn.commit()
    conn.close()

    result_text = f"✅ <b>¡Creado!</b>\n\n⏰ {created_r} recordatorio(s)\n🔔 {created_a} alerta(s) de precio"
    if errors:
        result_text += f"\n\n⚠️ Errores ({len(errors)}):\n" + "\n".join(f"• {e}" for e in errors)

    await query.edit_message_text(result_text, parse_mode="HTML")


async def cb_smart_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _smart_pending.pop(query.from_user.id, None)
    await query.edit_message_text("❌ Cancelado. Usa /smart para intentarlo de nuevo.")


# ─────────────────────────────────────────────────────────────────────────────
# /nota — Bloc de notas personal
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_nota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /nota add|ver|del <texto|id>"""
    args    = context.args
    tg_user = update.effective_user
    conn    = _conn()

    if not args or args[0].lower() not in ("add", "ver", "del"):
        await update.message.reply_text(
            "📝 <b>Notas personales</b>\n\n"
            "  /nota add &lt;texto&gt; — guardar nota\n"
            "  /nota ver — ver todas las notas\n"
            "  /nota del &lt;id&gt; — borrar nota",
            parse_mode="HTML",
        )
        conn.close()
        return

    sub = args[0].lower()

    if sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Uso: /nota add <texto>")
            conn.close(); return
        content = " ".join(args[1:])
        conn.execute("INSERT INTO notes (user_id, content) VALUES (?,?)", (tg_user.id, content))
        conn.commit()
        await update.message.reply_text("📝 Nota guardada.")

    elif sub == "ver":
        rows = conn.execute(
            "SELECT id, content, created_at FROM notes WHERE user_id=? ORDER BY id DESC", (tg_user.id,)
        ).fetchall()
        if not rows:
            await update.message.reply_text("📝 Sin notas. Usa /nota add <texto>.")
        else:
            lines = ["📝 <b>Tus notas</b>\n"]
            for n in rows:
                lines.append(f"  <b>[{n['id']}]</b> {n['content']}\n  <i>{n['created_at'][:10]}</i>\n")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif sub == "del":
        if len(args) < 2:
            await update.message.reply_text("Uso: /nota del <id>")
            conn.close(); return
        try:
            note_id = int(args[1])
        except ValueError:
            await update.message.reply_text("❌ El ID debe ser un número.")
            conn.close(); return
        deleted = conn.execute(
            "DELETE FROM notes WHERE id=? AND user_id=?", (note_id, tg_user.id)
        ).rowcount
        conn.commit()
        if deleted:
            await update.message.reply_text(f"🗑 Nota [{note_id}] eliminada.")
        else:
            await update.message.reply_text("❌ Nota no encontrada.")

    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# /lista — Listas de tareas con checkboxes inline
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /lista <nombre> add|ver|del [ítem]"""
    args    = context.args
    tg_user = update.effective_user

    if not args:
        await update.message.reply_text(
            "📋 <b>Listas de tareas</b>\n\n"
            "  /lista <nombre> add &lt;ítem&gt;\n"
            "  /lista <nombre> ver\n"
            "  /lista <nombre> del",
            parse_mode="HTML",
        )
        return

    list_name = args[0].lower()
    sub = args[1].lower() if len(args) > 1 else "ver"
    conn = _conn()

    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("Uso: /lista <nombre> add <ítem>")
            conn.close(); return
        item = " ".join(args[2:])
        conn.execute(
            "INSERT INTO lists (user_id, list_name, item) VALUES (?,?,?)",
            (tg_user.id, list_name, item)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ «{item}» añadido a <b>{list_name}</b>.", parse_mode="HTML")

    elif sub == "ver":
        items = conn.execute(
            "SELECT id, item, done FROM lists WHERE user_id=? AND list_name=? ORDER BY id",
            (tg_user.id, list_name)
        ).fetchall()
        conn.close()
        if not items:
            await update.message.reply_text(f"📋 Lista <b>{list_name}</b> vacía.", parse_mode="HTML")
            return
        kb = [
            [InlineKeyboardButton(
                f"{'✅' if it['done'] else '⬜'} {it['item']}",
                callback_data=f"lt_{it['id']}"
            )]
            for it in items
        ]
        await update.message.reply_text(
            f"📋 <b>{list_name}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif sub == "del":
        deleted = conn.execute(
            "DELETE FROM lists WHERE user_id=? AND list_name=?", (tg_user.id, list_name)
        ).rowcount
        conn.commit()
        conn.close()
        if deleted:
            await update.message.reply_text(f"🗑 Lista <b>{list_name}</b> eliminada ({deleted} ítems).", parse_mode="HTML")
        else:
            await update.message.reply_text(f"❌ Lista <b>{list_name}</b> no encontrada.", parse_mode="HTML")


async def cb_lista_tick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alterna el estado done/undone de un ítem de lista."""
    query   = update.callback_query
    await query.answer()
    item_id = int(query.data.split("_")[1])
    conn    = _conn()
    row     = conn.execute("SELECT * FROM lists WHERE id=? AND user_id=?", (item_id, query.from_user.id)).fetchone()
    if not row:
        conn.close(); return
    conn.execute("UPDATE lists SET done=? WHERE id=?", (0 if row["done"] else 1, item_id))
    conn.commit()
    items = conn.execute(
        "SELECT id, item, done FROM lists WHERE user_id=? AND list_name=? ORDER BY id",
        (query.from_user.id, row["list_name"])
    ).fetchall()
    conn.close()
    kb = [
        [InlineKeyboardButton(
            f"{'✅' if it['done'] else '⬜'} {it['item']}",
            callback_data=f"lt_{it['id']}"
        )]
        for it in items
    ]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))


# ─────────────────────────────────────────────────────────────────────────────
# /habito — Tracker de hábitos con rachas
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_habito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /habito add|ver|check|del <nombre>"""
    args    = context.args
    tg_user = update.effective_user
    conn    = _conn()

    if not args or args[0].lower() not in ("add", "ver", "check", "del"):
        await update.message.reply_text(
            "🔄 <b>Tracker de hábitos</b>\n\n"
            "  /habito add &lt;nombre&gt;\n"
            "  /habito ver\n"
            "  /habito check &lt;nombre&gt; — marcar hoy\n"
            "  /habito del &lt;nombre&gt;",
            parse_mode="HTML",
        )
        conn.close(); return

    sub = args[0].lower()

    if sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Uso: /habito add <nombre>")
            conn.close(); return
        name = " ".join(args[1:]).lower()
        try:
            conn.execute("INSERT INTO habits (user_id, name) VALUES (?,?)", (tg_user.id, name))
            conn.commit()
            await update.message.reply_text(f"✅ Hábito <b>{name}</b> registrado.", parse_mode="HTML")
        except Exception:
            await update.message.reply_text(f"⚠️ Ya existe el hábito <b>{name}</b>.", parse_mode="HTML")

    elif sub == "ver":
        rows = conn.execute(
            "SELECT * FROM habits WHERE user_id=? ORDER BY streak DESC", (tg_user.id,)
        ).fetchall()
        if not rows:
            await update.message.reply_text("🔄 Sin hábitos. Usa /habito add <nombre>.")
        else:
            lines = ["🔄 <b>Tus hábitos</b>\n"]
            for h in rows:
                last  = h["last_check"][:10] if h["last_check"] else "nunca"
                fire  = "🔥" if h["streak"] >= 3 else "⭕"
                lines.append(f"  {fire} <b>{h['name']}</b> — {h['streak']} días | último: {last}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif sub == "check":
        if len(args) < 2:
            await update.message.reply_text("Uso: /habito check <nombre>")
            conn.close(); return
        name  = " ".join(args[1:]).lower()
        habit = conn.execute(
            "SELECT * FROM habits WHERE user_id=? AND name=?", (tg_user.id, name)
        ).fetchone()
        if not habit:
            conn.close()
            await update.message.reply_text(f"❌ Hábito <b>{name}</b> no encontrado.", parse_mode="HTML")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if habit["last_check"] and habit["last_check"][:10] == today:
            conn.close()
            await update.message.reply_text(f"✅ Ya marcaste <b>{name}</b> hoy. 🔥 Racha: {habit['streak']} días.", parse_mode="HTML")
            return
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if habit["last_check"] and habit["last_check"][:10] == yesterday:
            new_streak = habit["streak"] + 1
        else:
            new_streak = 1
        conn.execute("UPDATE habits SET streak=?, last_check=? WHERE id=?", (new_streak, today, habit["id"]))
        conn.commit()
        msg = f"✅ <b>{name}</b> marcado. 🔥 Racha: <b>{new_streak} días</b>"
        if new_streak == 1 and habit["streak"] > 1:
            msg += f"\n⚠️ Racha anterior rota ({habit['streak']} días)."
        await update.message.reply_text(msg, parse_mode="HTML")

    elif sub == "del":
        if len(args) < 2:
            await update.message.reply_text("Uso: /habito del <nombre>")
            conn.close(); return
        name    = " ".join(args[1:]).lower()
        deleted = conn.execute("DELETE FROM habits WHERE user_id=? AND name=?", (tg_user.id, name)).rowcount
        conn.commit()
        if deleted:
            await update.message.reply_text(f"🗑 Hábito <b>{name}</b> eliminado.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"❌ Hábito <b>{name}</b> no encontrado.", parse_mode="HTML")

    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# /recordar — Recordatorios recurrentes
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_recordar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /recordar diario|semanal|mensual <mensaje>"""
    args = context.args
    if len(args) < 2 or args[0].lower() not in ("diario", "semanal", "mensual"):
        await update.message.reply_text(
            "⏰ <b>Recordatorio recurrente</b>\n\n"
            "Uso: /recordar &lt;intervalo&gt; &lt;mensaje&gt;\n"
            "Intervalos: <code>diario</code>, <code>semanal</code>, <code>mensual</code>\n\n"
            "Ejemplo: /recordar diario Beber agua 💧\n\n"
            "Gestiona con /mis_recurrentes",
            parse_mode="HTML",
        )
        return
    interval = args[0].lower()
    message  = " ".join(args[1:])
    tg_user  = update.effective_user
    now      = datetime.now()
    if interval == "diario":
        next_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif interval == "semanal":
        next_at = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        y, m = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
        next_at = now.replace(year=y, month=m, day=1, hour=9, minute=0, second=0, microsecond=0)
    conn = _conn()
    conn.execute(
        "INSERT INTO recurring_reminders (user_id, message, interval, next_at) VALUES (?,?,?,?)",
        (tg_user.id, message, interval, next_at.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"⏰ Recordatorio <b>{interval}</b> creado:\n«{message}»\n\n"
        f"Próximo aviso: {next_at.strftime('%d/%m/%Y %H:%M')}",
        parse_mode="HTML",
    )


@_require_premium
async def cmd_mis_recurrentes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    conn    = _conn()
    rows    = conn.execute(
        "SELECT * FROM recurring_reminders WHERE user_id=? AND active=1 ORDER BY id", (tg_user.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("⏰ Sin recordatorios recurrentes. Usa /recordar para crear uno.")
        return
    lines = ["⏰ <b>Recordatorios recurrentes</b>\n"]
    icons = {"diario": "📅", "semanal": "📆", "mensual": "🗓"}
    for r in rows:
        lines.append(
            f"  [{r['id']}] {icons[r['interval']]} <b>{r['interval']}</b> — {r['message']}\n"
            f"  Próximo: {r['next_at'][:16]}"
        )
    lines.append("\n/cancelar_recurrente &lt;id&gt; para eliminar.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@_require_premium
async def cmd_cancelar_recurrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /cancelar_recurrente <id>")
        return
    try:
        rem_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ El ID debe ser un número.")
        return
    tg_user = update.effective_user
    conn    = _conn()
    deleted = conn.execute(
        "DELETE FROM recurring_reminders WHERE id=? AND user_id=?", (rem_id, tg_user.id)
    ).rowcount
    conn.commit()
    conn.close()
    if deleted:
        await update.message.reply_text(f"🗑 Recordatorio recurrente [{rem_id}] cancelado.")
    else:
        await update.message.reply_text("❌ No encontré ese recordatorio.")


async def job_check_recurring(app):
    """Scheduler cada hora: envía y reagenda recordatorios recurrentes vencidos."""
    now  = datetime.now()
    conn = _conn()
    due  = conn.execute(
        "SELECT * FROM recurring_reminders WHERE active=1 AND next_at<=?",
        (now.strftime("%Y-%m-%d %H:%M:%S"),)
    ).fetchall()
    for r in due:
        try:
            await app.bot.send_message(
                chat_id=r["user_id"],
                text=f"⏰ <b>Recordatorio {r['interval']}</b>\n\n{r['message']}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"recurring uid={r['user_id']}: {e}")
        # Reagendar
        nxt = datetime.strptime(r["next_at"], "%Y-%m-%d %H:%M:%S")
        if r["interval"] == "diario":
            nxt += timedelta(days=1)
        elif r["interval"] == "semanal":
            nxt += timedelta(weeks=1)
        else:
            y, m = (nxt.year, nxt.month + 1) if nxt.month < 12 else (nxt.year + 1, 1)
            nxt  = nxt.replace(year=y, month=m, day=1)
        conn.execute("UPDATE recurring_reminders SET next_at=? WHERE id=?",
                     (nxt.strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
    if due:
        conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# /resumen_web — Resumir una URL con IA
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_resumen_web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /resumen_web <url>"""
    if not context.args:
        await update.message.reply_text(
            "🌐 Uso: /resumen_web &lt;url&gt;\nEjemplo: /resumen_web https://bbc.com/news",
            parse_mode="HTML",
        )
        return
    url = context.args[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    msg = await update.message.reply_text("🔍 Leyendo y resumiendo…")
    try:
        import requests as _rq
        from tavily import TavilyClient
        client  = TavilyClient(api_key=TAVILY_API_KEY)
        result  = client.extract(urls=[url])
        content = result.get("results", [{}])[0].get("raw_content", "")[:3000]
        if not content:
            await msg.edit_text("❌ No pude leer el contenido de esa URL.")
            return
        from groq import Groq
        gr   = Groq(api_key=GROQ_API_KEY)
        resp = gr.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "Resume el siguiente texto en 5-8 puntos clave con emojis, en español. Sin introducción."},
                {"role": "user", "content": content},
            ],
            max_tokens=500,
        )
        resumen = resp.choices[0].message.content.strip()
        short   = url[:60] + ("…" if len(url) > 60 else "")
        await msg.edit_text(
            f"🌐 <b>Resumen de</b> <a href='{url}'>{short}</a>\n\n{resumen}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning(f"resumen_web error: {e}")
        await msg.edit_text("❌ Error al procesar la URL. Asegúrate de que sea accesible.")


# ─────────────────────────────────────────────────────────────────────────────
# /imagen — Generación de imágenes con Cloudflare AI
# ─────────────────────────────────────────────────────────────────────────────

@_require_premium
async def cmd_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /imagen <descripción en inglés>"""
    if not context.args:
        await update.message.reply_text(
            "🎨 <b>Generador de imágenes</b>\n\n"
            "Uso: /imagen &lt;descripción&gt;\n"
            "Ejemplo: /imagen a cat on the moon, watercolor style\n\n"
            "<i>Powered by Cloudflare AI · Flux Schnell</i>",
            parse_mode="HTML",
        )
        return
    import base64, requests as _rq
    desc = " ".join(context.args)
    msg  = await update.message.reply_text(f"🎨 Generando imagen…")
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    # Try Flux Schnell first (returns base64 JSON)
    try:
        url_cf = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
        r = _rq.post(url_cf, headers=headers, json={"prompt": desc, "num_steps": 4}, timeout=60)
        img_b64 = r.json().get("result", {}).get("image", "")
        if not img_b64:
            raise ValueError("No image")
        img_bytes = base64.b64decode(img_b64)
    except Exception:
        # Fallback: SDXL (returns raw bytes)
        try:
            url_cf2 = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0"
            r2 = _rq.post(url_cf2, headers=headers, json={"prompt": desc}, timeout=60)
            img_bytes = r2.content
        except Exception as e2:
            logger.warning(f"/imagen error: {e2}")
            await msg.edit_text("❌ No pude generar la imagen. Inténtalo de nuevo.")
            return
    await msg.delete()
    await update.message.reply_photo(
        photo=io.BytesIO(img_bytes),
        caption=f"🎨 {desc[:200]}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def _init_new_premium_tables(conn):
    """Crea las nuevas tablas Premium si no existen."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            list_name TEXT NOT NULL,
            item TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            streak INTEGER NOT NULL DEFAULT 0,
            last_check TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS recurring_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            interval TEXT NOT NULL,
            next_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def register(app, scheduler):
    """Registra handlers Premium y añade jobs al scheduler."""
    import pytz
    tz = pytz.timezone("Europe/Madrid")

    init_premium_db()
    conn = _conn()
    _init_new_premium_tables(conn)
    conn.close()

    app.add_handler(CommandHandler("ia",                    cmd_ia))
    app.add_handler(CommandHandler("ia_reset",              cmd_ia_reset))
    app.add_handler(CommandHandler("grafico",               cmd_grafico))
    app.add_handler(CommandHandler("cartera",               cmd_cartera))
    app.add_handler(CommandHandler("exportar",              cmd_exportar))
    app.add_handler(CommandHandler("activar_resumen",       cmd_activar_resumen))
    app.add_handler(CommandHandler("desactivar_resumen",    cmd_desactivar_resumen))
    app.add_handler(CommandHandler("smart",                 cmd_smart))
    app.add_handler(CommandHandler("nota",                  cmd_nota))
    app.add_handler(CommandHandler("lista",                 cmd_lista))
    app.add_handler(CommandHandler("habito",                cmd_habito))
    app.add_handler(CommandHandler("recordar",              cmd_recordar))
    app.add_handler(CommandHandler("mis_recurrentes",       cmd_mis_recurrentes))
    app.add_handler(CommandHandler("cancelar_recurrente",   cmd_cancelar_recurrente))
    app.add_handler(CommandHandler("resumen_web",           cmd_resumen_web))
    app.add_handler(CommandHandler("imagen",                cmd_imagen))

    # Callbacks smart
    app.add_handler(CallbackQueryHandler(cb_smart_confirm, pattern="^smart_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_smart_cancel,  pattern="^smart_cancel$"))
    # Callbacks lista
    app.add_handler(CallbackQueryHandler(cb_lista_tick, pattern=r"^lt_\d+$"))

    # Resumen diario a las 8:00 CET
    scheduler.add_job(job_resumen_diario,   trigger="cron", hour=8,  minute=0,  timezone=tz, args=[app])
    # Agenda semanal: lunes 9:00
    scheduler.add_job(job_agenda_semanal,   trigger="cron", day_of_week="mon", hour=9, minute=0, timezone=tz, args=[app])
    # Recordatorios recurrentes: cada hora
    scheduler.add_job(job_check_recurring,  trigger="interval", hours=1, args=[app])

    logger.info("✅ features/premium: /ia /grafico /cartera /exportar /smart /nota /lista /habito /recordar /resumen_web /imagen + schedulers")
