#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
features/games.py — Módulo de minijuegos interactivos
  /tictactoe   - Tres en Raya (Inline vs Bot)
  /buscaminas  - Minijuego de Buscaminas Inline
  /pptls       - Piedra, Papel, Tijera, Lagarto, Spock
  /ruleta      - Ruleta Rusa para grupos
  /adivina     - Adivina el Número (Frío/Caliente)
"""

import logging
import random
import json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

logger = logging.getLogger("DavogramBot.games")

# ─────────────────────────────────────────────────────────────────────────────
# /tictactoe — Tres en Raya contra el Bot
# ─────────────────────────────────────────────────────────────────────────────

# Estado in-memory: { user_id: [ '','','' , '','','' , '','','' ] }
# 'X' = User, 'O' = Bot, '' = Vacío
_ttt_games: dict[int, list[str]] = {}

def _check_ttt_winner(board: list[str]) -> str | None:
    lines = [
        (0,1,2), (3,4,5), (6,7,8), # horiz
        (0,3,6), (1,4,7), (2,5,8), # vert
        (0,4,8), (2,4,6)           # diag
    ]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if "" not in board:
        return "DRAW"
    return None

def _get_ttt_kb(board: list[str]) -> InlineKeyboardMarkup:
    emojis = {"": "⬜", "X": "❌", "O": "⭕"}
    rows = []
    for i in range(0, 9, 3):
        row = [
            InlineKeyboardButton(emojis[board[i]],   callback_data=f"ttt_{i}"),
            InlineKeyboardButton(emojis[board[i+1]], callback_data=f"ttt_{i+1}"),
            InlineKeyboardButton(emojis[board[i+2]], callback_data=f"ttt_{i+2}"),
        ]
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def cmd_tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    _ttt_games[tg_user.id] = [""] * 9
    await update.message.reply_text(
        "🎮 <b>Tres en Raya</b>\n\nTú eres ❌. ¡Empieza!",
        parse_mode="HTML",
        reply_markup=_get_ttt_kb(_ttt_games[tg_user.id])
    )

async def cb_tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    await query.answer()

    board = _ttt_games.get(tg_user.id)
    if not board:
        await query.edit_message_text("⚠️ Partida expirada. Usa /tictactoe para una nueva.")
        return

    pos = int(query.data.split("_")[1])
    if board[pos] != "":
        return  # Casilla ocupada

    # Turno Jugador
    board[pos] = "X"
    winner = _check_ttt_winner(board)
    if winner:
        del _ttt_games[tg_user.id]
        msg = "🎉 <b>¡Ganaste!</b>" if winner == "X" else "🤝 <b>¡Empate!</b>"
        await query.edit_message_text(f"🎮 <b>Tres en Raya</b>\n\n{msg}", parse_mode="HTML", reply_markup=_get_ttt_kb(board))
        return

    # Turno Bot (Aleatorio por ahora)
    empty = [i for i, v in enumerate(board) if v == ""]
    if empty:
        bot_pos = random.choice(empty)
        board[bot_pos] = "O"
        winner = _check_ttt_winner(board)
        if winner:
            del _ttt_games[tg_user.id]
            msg = "💀 <b>¡Gané yo!</b>" if winner == "O" else "🤝 <b>¡Empate!</b>"
            await query.edit_message_text(f"🎮 <b>Tres en Raya</b>\n\n{msg}", parse_mode="HTML", reply_markup=_get_ttt_kb(board))
            return

    await query.edit_message_text("🎮 <b>Tres en Raya</b>\n\nTu turno...", parse_mode="HTML", reply_markup=_get_ttt_kb(board))


# ─────────────────────────────────────────────────────────────────────────────
# /buscaminas — Inline Minesweeper (simplificado 5x5)
# ─────────────────────────────────────────────────────────────────────────────

# { user_id: {"mines": [int,int...], "revealed": [int,int...], "lives": 1, "size": 25} }
_ms_games: dict[int, dict] = {}

def _get_ms_kb(game: dict, reveal_all=False) -> InlineKeyboardMarkup:
    # 5x5
    rows = []
    for i in range(0, 25, 5):
        row = []
        for j in range(5):
            idx = i + j
            if reveal_all or idx in game["revealed"]:
                if idx in game["mines"]:
                    text = "💣"
                else:
                    # Count adjacent
                    adj = 0
                    c_r, c_c = idx // 5, idx % 5
                    for dr in [-1,0,1]:
                        for dc in [-1,0,1]:
                            nr, nc = c_r+dr, c_c+dc
                            if 0<=nr<5 and 0<=nc<5 and (nr*5+nc) in game["mines"]:
                                adj += 1
                    text = str(adj) if adj > 0 else "⬜"
            else:
                text = "⬛"
            row.append(InlineKeyboardButton(text, callback_data=f"ms_{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def cmd_buscaminas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    mines = random.sample(range(25), 5) # 5 minas
    _ms_games[tg_user.id] = {"mines": mines, "revealed": []}
    
    await update.message.reply_text(
        "💣 <b>Buscaminas (5x5)</b>\n\nEncuentra las 20 casillas seguras. Hay 5 minas.",
        parse_mode="HTML",
        reply_markup=_get_ms_kb(_ms_games[tg_user.id])
    )

async def cb_buscaminas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    await query.answer()

    game = _ms_games.get(tg_user.id)
    if not game:
        await query.edit_message_text("⚠️ Partida expirada. Usa /buscaminas.")
        return

    idx = int(query.data.split("_")[1])
    if idx in game["revealed"]:
        return

    if idx in game["mines"]:
        del _ms_games[tg_user.id]
        await query.edit_message_text(
            "💥 <b>¡BOOM!</b> Pisaste una mina.\nHas perdido.",
            parse_mode="HTML",
            reply_markup=_get_ms_kb(game, reveal_all=True)
        )
        return

    game["revealed"].append(idx)
    if len(game["revealed"]) >= 20: # 25 - 5 minas
        del _ms_games[tg_user.id]
        await query.edit_message_text(
            "🎉 <b>¡Victoria!</b> Despejaste todo el campo seguro.",
            parse_mode="HTML",
            reply_markup=_get_ms_kb(game, reveal_all=True)
        )
        return

    await query.edit_message_reply_markup(reply_markup=_get_ms_kb(game))


# ─────────────────────────────────────────────────────────────────────────────
# /pptls — Piedra, Papel, Tijera, Lagarto, Spock
# ─────────────────────────────────────────────────────────────────────────────

PPT_CHOICES = {
    "piedra": "🪨 Piedra", "papel": "📄 Papel", "tijera": "✂️ Tijera",
    "lagarto": "🦎 Lagarto", "spock": "🖖 Spock"
}
# Qué vence a qué
PPT_RULES = {
    "piedra": ["tijera", "lagarto"],
    "papel": ["piedra", "spock"],
    "tijera": ["papel", "lagarto"],
    "lagarto": ["spock", "papel"],
    "spock": ["tijera", "piedra"]
}

async def cmd_pptls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪨", callback_data="ppt_piedra"),
         InlineKeyboardButton("📄", callback_data="ppt_papel"),
         InlineKeyboardButton("✂️", callback_data="ppt_tijera")],
        [InlineKeyboardButton("🦎", callback_data="ppt_lagarto"),
         InlineKeyboardButton("🖖", callback_data="ppt_spock")]
    ])
    await update.message.reply_text("Elige tu jugada:", reply_markup=kb)

async def cb_pptls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_ch = query.data.split("_")[1]
    bot_ch = random.choice(list(PPT_CHOICES.keys()))

    text = f"Tú elegiste **{PPT_CHOICES[user_ch]}**.\nYo elegí **{PPT_CHOICES[bot_ch]}**.\n\n"
    if user_ch == bot_ch:
        text += "🤝 **¡Empate!**"
    elif bot_ch in PPT_RULES[user_ch]:
        text += "🎉 **¡Ganaste!**"
    else:
        text += "💀 **¡Gané yo!**"

    await query.edit_message_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# /ruleta — Ruleta Rusa para grupos
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_ruleta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Minijuego de suerte. 1/6 probabilidad de "morir"."""
    bullet = random.randint(1, 6)
    if bullet == 1:
        await update.message.reply_text("🔫 *BANG!* 💀\nHas perdido a la Ruleta Rusa.", parse_mode="Markdown")
        # Optional: Si el bot es admin y estamos en grupo, mutear usuario por 1 min
    else:
        await update.message.reply_text("🔫 *Click.* 😌\nTe has salvado... esta vez.", parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# /adivina — Adivina el Número (Frío/Caliente)
# ─────────────────────────────────────────────────────────────────────────────

_adivina_games: dict[int, dict] = {}

async def cmd_adivina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /adivina [numero_intento]. Si no hay número, inicia."""
    tg_user = update.effective_user
    args = context.args

    if not args:
        num = random.randint(1, 1000)
        _adivina_games[tg_user.id] = {"target": num, "tries": 0}
        await update.message.reply_text(
            "🔢 <b>Adivina el Número</b>\n\n"
            "He pensado un número del <b>1 al 1000</b>.\n"
            "Usa <code>/adivina &lt;numero&gt;</code> para intentar adivinarlo.\n(Ej: /adivina 500)",
            parse_mode="HTML"
        )
        return

    game = _adivina_games.get(tg_user.id)
    if not game:
        await update.message.reply_text("No tienes ninguna partida activa. Usa /adivina para empezar.")
        return

    try:
        guess = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Debes introducir un número válido.")
        return

    game["tries"] += 1
    target = game["target"]
    tries = game["tries"]

    if guess == target:
        del _adivina_games[tg_user.id]
        await update.message.reply_text(
            f"🎉 <b>¡ACERTASTE!</b>\n\n"
            f"El número era <b>{target}</b>.\n"
            f"Lo has adivinado en <b>{tries}</b> intentos.",
            parse_mode="HTML"
        )
    elif guess < target:
        await update.message.reply_text(f"📈 El número es <b>MAYOR</b> que {guess}. (Intento {tries})", parse_mode="HTML")
    else:
        await update.message.reply_text(f"📉 El número es <b>MENOR</b> que {guess}. (Intento {tries})", parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# Registro
# ─────────────────────────────────────────────────────────────────────────────

def register(app):
    app.add_handler(CommandHandler("tictactoe",  cmd_tictactoe))
    app.add_handler(CommandHandler("buscaminas", cmd_buscaminas))
    app.add_handler(CommandHandler("pptls",      cmd_pptls))
    app.add_handler(CommandHandler("ruleta",     cmd_ruleta))
    app.add_handler(CommandHandler("adivina",    cmd_adivina))
    
    app.add_handler(CallbackQueryHandler(cb_tictactoe, pattern=r"^ttt_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_buscaminas, pattern=r"^ms_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_pptls, pattern=r"^ppt_.*$"))
    logger.info("✅ features/games: /tictactoe /buscaminas /pptls /ruleta /adivina")
