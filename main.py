# -*- coding: utf-8 -*-
# MatchQuiz_bot — тест совпадений (финальная версия для Render)

import os
import csv
import re
import random
import asyncio
from typing import List, Dict, Any
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ---------- Конфигурация ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/webhook"
QUESTIONS_CSV = os.getenv("QUESTIONS_CSV", "questions.csv")

# ---------- FSM ----------
class Flow(StatesGroup):
    name = State()
    code = State()
    quiz = State()
    role = State()


# ---------- Загрузка вопросов ----------
def load_questions_from_csv(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    questions = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        next(reader, None)  # пропускаем заголовок
        for row in reader:
            q = row.get("question", "").strip()
            opts = row.get("options", "").strip()
            if not q or not opts:
                continue
            options = [o.strip() for o in re.split(r"[;,]", opts) if o.strip()]
            questions.append({"question": q, "options": options})
    return questions


# ---------- Эмодзи для категорий ----------
EMOJI_TOPICS = {
    "утром": "🌅",
    "работа": "💼",
    "юмор": "😂",
    "музыка": "🎵",
    "ценности": "💎",
    "отношения": "💞",
}

def add_emoji_to_question(text: str) -> str:
    for word, emoji in EMOJI_TOPICS.items():
        if word.lower() in text.lower():
            return f"{emoji} {text}"
    return f"✨ {text}"


# ---------- Хранилище сессий ----------
sessions: Dict[str, Dict[str, Any]] = {}
router = Router()


# ---------- Старт ----------
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Пройти как первый", callback_data="start_first")
    kb.button(text="💞 Пройти как второй", callback_data="start_second")
    kb.adjust(2)
    await message.answer(
        "Привет! 🥰 Это тест совпадений. Один из вас проходит его первым, а второй потом вводит код. Кто ты?",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("start_"))
async def start_role(cb: types.CallbackQuery, state: FSMContext):
    role = cb.data.split("_")[1]
    if role == "first":
        code = str(random.randint(1000, 9999))
        sessions[code] = {"a": {}, "b": {}, "questions": load_questions_from_csv(QUESTIONS_CSV)}
        await state.update_data(role="first", code=code, role_key="a")
        await cb.message.answer(f"💬 Введи своё имя (код для второго участника: {code})")
        await state.set_state(Flow.name)
    else:
        await state.update_data(role="second", role_key="b")
        await cb.message.answer("💬 Введи код пары (4 цифры)")
        await state.set_state(Flow.code)
    await cb.answer()


# ---------- Ввод кода ----------
@router.message(Flow.code)
async def input_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if code not in sessions:
        await message.answer("⚠️ Код не найден, попробуй снова 🙂")
        return
    await state.update_data(code=code)
    await message.answer("Как тебя зовут? 😊")
    await state.set_state(Flow.name)


# ---------- Имя ----------
@router.message(Flow.name)
async def input_name(message: types.Message, state: FSMContext):
    name = message.text.strip() or "Без имени"
    data = await state.get_data()
    role = data.get("role", "first")
    role_key = data.get("role_key", "a")
    code = data.get("code")

    session = sessions.get(code)
    if not session:
        session = {"a": {}, "b": {}, "questions": load_questions_from_csv(QUESTIONS_CSV)}
        sessions[code] = session

    session[role_key]["name"] = name
    session[role_key]["answers"] = []
    await message.answer(f"Отлично, {name}! 🚀 Поехали!")

    await ask_question(message, state, session, code, role_key, 0)


# ---------- Вопросы ----------
async def ask_question(message, state, session, code, role_key, index):
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
        f"🌀 Вопрос {index+1}/{len(questions)}:
<b>{question_text}</b>",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )


# ---------- Ответ ----------
@router.callback_query(F.data.startswith("answer:"))
async def process_answer(cb: types.CallbackQuery, state: FSMContext):
    _, idx, opt = cb.data.split(":", 2)
    idx = int(idx)
    data = await state.get_data()
    code = data.get("code")
    role_key = data.get("role_key", "a")
    session = sessions.get(code)
    if not session:
        await cb.answer("Ошибка! Сессия не найдена.")
        return
    session[role_key]["answers"].append(opt)
    await cb.answer("✅")
    await cb.message.delete()
    await ask_question(cb.message, state, session, code, role_key, idx + 1)


# ---------- Завершение ----------
async def finish_quiz(message, state, session, code, role_key):
    role_other = "b" if role_key == "a" else "a"
    if not session.get(role_other):
        await message.answer("Ожидаем второго участника... 💫")
        return

    a, b = session["a"], session["b"]
    qa = a.get("answers", [])
    qb = b.get("answers", [])
    qs = session["questions"]

    total = len(qs)
    same = sum(1 for i in range(min(len(qa), len(qb))) if qa[i] == qb[i])
    percent = int(same / total * 100)

    if percent > 80:
        text = "💞 Уровень совпадений высокий! Между вами сильная эмоциональная близость ❤️"
    elif percent > 50:
        text = "😊 У вас много общего, но есть и различия — как в хороших отношениях 🌈"
    else:
        text = "🌀 Вы противоположности, но именно это делает вас интересными 💫"

    result = [f"<b>Результаты теста совпадений:</b>
Совпадений: {same} из {total} ({percent}%)", text, "
<b>Подробности:</b>"]

    for i, q in enumerate(qs):
        result.append(f"
<b>{i+1}. {q['question']}</b>
— {a['name']}: {qa[i] if i < len(qa) else '—'}
— {b['name']}: {qb[i] if i < len(qb) else '—'}")

    await message.answer("
".join(result), parse_mode="HTML")


# ---------- Запуск ----------
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    async def on_startup(bot: Bot):
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        print(f"Webhook set to {WEBHOOK_URL}")

    async def on_shutdown(bot: Bot):
        await bot.delete_webhook()

    app = web.Application()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)

    await on_startup(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print("✅ Bot is running...")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
