# coding: utf-8
# MatchQuiz_bot — стабильная версия с Render, эмодзи и анализом совпадений

import os
import csv
import random
import asyncio
from typing import Dict, Any, List
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters.command import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/webhook"
QUESTIONS_CSV = os.getenv("QUESTIONS_CSV", "questions.csv")

def load_questions_from_csv(path: str) -> List[Dict[str, Any]]:
    questions = []
    if not os.path.exists(path):
        return questions
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        next(reader, None)
        for row in reader:
            q = row.get("question", "").strip()
            opts = [o.strip() for o in row.get("options", "").split(";") if o.strip()]
            if q and opts:
                questions.append({"question": q, "options": opts, "type": row.get("type", "single")})
    return questions

questions = load_questions_from_csv(QUESTIONS_CSV)

class Flow(StatesGroup):
    role = State()
    name = State()
    code = State()
    quiz = State()

sessions = {}

def add_emoji_to_question(text: str) -> str:
    mapping = {
        "утром": "☀️", "кофе": "☕", "работа": "💼", "стресс": "😬",
        "отдых": "🌴", "смех": "😂", "подарок": "🎁", "друзья": "👫",
        "любовь": "💖", "отношен": "💞", "сон": "💤", "еда": "🍽️",
        "музыка": "🎧", "путешеств": "✈️", "вечер": "🌙", "эмоции": "💫",
    }
    for k, v in mapping.items():
        if k.lower() in text.lower():
            return f"{v} {text}"
    return f"✨ {text}"

def get_keyboard(options: List[str]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for opt in options:
        kb.button(text=opt, callback_data=f"answer:{opt}")
    kb.adjust(1)
    return kb.as_markup()

async def send_question(message: types.Message, index: int, state: FSMContext):
    data = await state.get_data()
    code = data["code"]
    role_key = data["role_key"]
    session = sessions[code]
    questions = session["questions"]
    if index >= len(questions):
        await finish_quiz(message, state, session, code, role_key)
        return

    q = questions[index]
    question_text = add_emoji_to_question(q["question"])
    kb = InlineKeyboardBuilder()
    for opt in q["options"]:
        kb.button(text=opt, callback_data=f"answer:{index}:{opt}")
    kb.adjust(1)
    await message.answer(
        f"📘 Вопрос {index + 1}/{len(questions)}:<b>{question_text}</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )

async def finish_quiz(message: types.Message, state: FSMContext, session, code, role_key):
    session[role_key]["done"] = True
    if not all(session[k]["done"] for k in ["a", "b"]):
        await message.answer("💌 Ответы сохранены! Когда второй участник закончит, я покажу результаты.")
        return

    qa = session["a"]["answers"]
    qb = session["b"]["answers"]
    qs = session["questions"]
    same = sum(1 for i in range(min(len(qa), len(qb))) if qa[i] == qb[i])
    total = len(qs)
    percent = int(same / total * 100)

    if percent > 80:
        text = "❤️ Уровень совпадений высокий! Между вами сильная эмоциональная близость."
    elif percent > 50:
        text = "💞 У вас много общего, но есть и различия — как в хороших отношениях."
    else:
        text = "🌈 Вы противоположности, но именно это делает вас интересными."

    result = [
        "<b>✨ Результаты теста совпадений:</b>",
        f"Совпадений: {same} из {total} ({percent}%)",
        text,
        "<b>📋 Подробности:</b>",
    ]
    for i, q in enumerate(qs):
        result.append(
            f"<b>{i + 1}. {q['question']}</b>"
            f"— 💬 {session['a']['name']}: {qa[i] if i < len(qa) else '—'}"
            f"— 💬 {session['b']['name']}: {qb[i] if i < len(qb) else '—'}"
        )

    await message.answer("\n".join(result), parse_mode="HTML")

# ---------- Обработка ролей ----------

@dp.callback_query(F.data.startswith("role:"))
async def role_handler(callback: types.CallbackQuery, state: FSMContext):
    role = callback.data.split(":")[1]
    await callback.message.edit_reply_markup(reply_markup=None)

    if role == "first":
        await state.update_data(role="first", role_key="a")
        await callback.message.answer("💬 Введи своё имя (код для второго участника появится позже)")
        await state.set_state(Flow.name)
    else:
        await state.update_data(role="second", role_key="b")
        await callback.message.answer("💞 Введи код пары (4 цифры):")
        await state.set_state(Flow.code)

# ---------- Старт ----------

async def start_handler(message: types.Message, state: FSMContext):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🎯 Пройти как первый", callback_data="role:first"),
        types.InlineKeyboardButton(text="💞 Пройти как второй", callback_data="role:second")
    ]])
    await message.answer(
        "Привет! 🥰 Это тест совпадений. Один из вас проходит его первым, "
        "а второй потом вводит код. Кто ты?",
        reply_markup=kb
    )
    await state.set_state(Flow.role)

async def on_startup(bot: Bot):
    try:
        info = await bot.get_webhook_info()
        if info.url != WEBHOOK_URL:
            await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
            print(f"[webhook] set to {WEBHOOK_URL}")
        else:
            print(f"[webhook] already set: {info.url}")
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    except (TelegramBadRequest, Exception) as e:
        print(f"[webhook] skipped: {e}")

async def on_shutdown(bot: Bot):
    try:
        await bot.session.close()
    except Exception:
        pass

def build_app() -> web.Application:
    app = web.Application()
    dp = Dispatcher(storage=MemoryStorage())
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp.message.register(start_handler, CommandStart())  # реакция на /start
    dp.callback_query.register(role_handler, F.data.startswith("role:"))  # обработка кнопок

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    return app

if __name__ == "__main__":
    app = build_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
