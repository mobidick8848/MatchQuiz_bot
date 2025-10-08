# -*- coding: utf-8 -*-
# MatchQuiz bot – финальная версия для questions.csv
# aiogram 3.x webhook mode for Render

import os
import csv
import re
import random
from typing import Dict, Any, List, Optional
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters.command import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ---------- Конфигурация ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/webhook"
QUESTIONS_CSV = os.getenv("QUESTIONS_CSV", "questions.csv")

# ---------- Загрузка вопросов ----------
def load_questions_from_csv(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []

    rows = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,")
                except csv.Error:
                    class Dialect(csv.excel):
                        delimiter = ";"
                    dialect = Dialect()
                reader = csv.reader(f, dialect)
                rows = list(reader)
                break
        except UnicodeDecodeError:
            continue

    if not rows:
        return []

    header = [h.strip().lower().lstrip("\ufeff") for h in rows[0]]

    def find_idx(cands: List[str]) -> Optional[int]:
        for c in cands:
            if c in header:
                return header.index(c)
        return None

    qi = find_idx(["question", "вопрос"])
    oi = find_idx(["options", "варианты", "варианты ответов"])
    ti = find_idx(["type", "тип"])

    out = []
    for r in rows[1:]:
        if not r:
            continue
        q = (r[qi] if qi is not None and qi < len(r) else "").strip()
        opts_field = (r[oi] if oi is not None and oi < len(r) else "").strip()
        qtype = (r[ti] if ti is not None and ti < len(r) else "single").strip().lower()

        if not q or q.lower() in ("question", "вопрос"):
            continue

        options_raw = [o.strip() for o in re.split(r"\s*\|\s*|\s*;\s*|\s*,\s*", opts_field) if o.strip()]
        if not options_raw:
            continue

        out.append({"type": qtype, "question": q, "options": options_raw})
    return out

questions = load_questions_from_csv(QUESTIONS_CSV)

# ---------- Эмодзи ----------
EMOJIS = ["🌞","☕","🍀","💫","🎯","❤️","💭","🌸","🔥","🎵","✨","🌈","📚","🎁","🌹","🌙","🍷","🤍","💬","🌻"]
def emojify(text: str, idx: int) -> str:
    return f"{EMOJIS[idx % len(EMOJIS)]} {text}"

# ---------- Память ----------
sessions: Dict[str, Dict[str, Any]] = {}

# ---------- Состояния ----------
class Flow(StatesGroup):
    role = State()
    name = State()
    code = State()
    idx = State()
    role_key = State()

# ---------- Бот ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ---------- Клавиатуры ----------
def kb_start():
    b = InlineKeyboardBuilder()
    b.button(text="🎯 Пройти как первый", callback_data="role:first")
    b.button(text="💞 Пройти как второй", callback_data="role:second")
    b.adjust(2)
    return b.as_markup()

def kb_answers(qidx: int, options: List[str], code: str, role_key: str):
    b = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        b.button(text=opt, callback_data=f"ans:{code}:{role_key}:{qidx}:{i}")
    b.adjust(1)  # по одной кнопке в ряд
    return b.as_markup()

# ---------- Утилиты ----------
def gen_code() -> str:
    return f"{random.randint(1000, 9999)}"

def ensure_session(code: str):
    if code not in sessions:
        sessions[code] = {"a": {"name": "", "answers": []},
                          "b": {"name": "", "answers": []}}

def calc_result(code: str):
    s = sessions.get(code, {})
    a = s.get("a", {})
    b = s.get("b", {})
    qa, qb = a.get("answers", []), b.get("answers", [])
    matches, pairs = 0, []
    for i in range(min(len(qa), len(qb))):
        ok = qa[i] == qb[i]
        if ok:
            matches += 1
        pairs.append({
            "q": questions[i]["question"],
            "a_opt": questions[i]["options"][qa[i]] if qa[i] < len(questions[i]["options"]) else "",
            "b_opt": questions[i]["options"][qb[i]] if qb[i] < len(questions[i]["options"]) else "",
            "ok": ok
        })
    pct = int(round(100 * matches / max(1, len(pairs))))
    return {"a": a.get("name", ""), "b": b.get("name", ""), "matches": matches, "total": len(pairs), "pct": pct, "pairs": pairs}

async def send_question(chat_id: int, code: str, role_key: str, idx: int):
    if idx >= len(questions):
        # Всё — тест закончен
        res = calc_result(code)
        a_done = len(sessions[code]["a"]["answers"]) >= len(questions)
        b_done = len(sessions[code]["b"]["answers"]) >= len(questions)

        if a_done and b_done:
            pct = res["pct"]

            # 💖 Эмоциональная интерпретация по проценту совпадений
            if pct >= 90:
                summary = "💞 У вас редкое совпадение! Похоже, между вами настоящая эмоциональная близость — вы чувствуете друг друга с полуслова и на одной волне 🌈"
            elif pct >= 70:
                summary = "💫 Между вами очень тёплая связь. Вы хорошо понимаете друг друга, просто иногда смотрите на вещи с разных сторон ❤️"
            elif pct >= 50:
                summary = "🌷 Есть основа для близости — у вас много общего, но и пространство для роста. Немного внимания, и вы сможете стать настоящей командой 🤝"
            elif pct >= 30:
                summary = "🌧 Похоже, вы по-разному воспринимаете эмоции и ситуации. Но это не плохо — просто вам важно чаще говорить о своих чувствах 💬"
            else:
                summary = "💔 Совпадений немного, но, возможно, вы просто разные — и в этом ваша сила. Иногда контрасты создают самую яркую химию ⚡"

            # 🪞 Текст результата
            lines = [
                f"💞 <b>{res['a']}</b> + <b>{res['b']}</b>",
                f"Совпадений: <b>{res['matches']}</b> из {res['total']} — <b>{res['pct']}%</b>","",
                summary
            ]

            bad = [p for p in res["pairs"] if not p["ok"]]
            if bad:
                lines.append("\n🔍 Где не совпало:")
                for p in bad[:5]:
                    lines.append(f"• <b>{p['q']}</b>\n  — {res['a']}: {p['a_opt']}\n  — {res['b']}: {p['b_opt']}")

            await bot.send_message(chat_id, "\n".join(lines))
        else:
            await bot.send_message(chat_id, "👌 Готово! Ждём второго участника…")
        return

    q = questions[idx]
    await bot.send_message(chat_id, f"<b>{emojify(q['question'], idx)}</b>", reply_markup=kb_answers(idx, q["options"], code, role_key))

# ---------- Хендлеры ----------
@dp.message(CommandStart())
async def on_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! 🥰 Это тест совпадений. Один из вас проходит первым, другой — вторым. Кто ты?", reply_markup=kb_start())
    await state.set_state(Flow.role)

@dp.callback_query(F.data.startswith("role:"))
async def choose_role(cb: types.CallbackQuery, state: FSMContext):
    role = cb.data.split(":")[1]
    await cb.message.edit_reply_markup(reply_markup=None)
    if role == "first":
        await state.update_data(role="first", role_key="a")
        await cb.message.answer("💬 Введи своё имя:")
        await state.set_state(Flow.name)
    else:
        await state.update_data(role="second", role_key="b")
        await cb.message.answer("🔢 Введи код пары (4 цифры):")
        await state.set_state(Flow.code)
    await cb.answer()

@dp.message(Flow.code)
async def input_code(message: types.Message, state: FSMContext):
    code = re.sub(r"\D+", "", message.text or "")
    if len(code) != 4:
        await message.answer("Код должен состоять из 4 цифр. Попробуй снова 🙂")
        return
    ensure_session(code)
    await state.update_data(code=code)
    await message.answer("💬 Как тебя зовут?")
    await state.set_state(Flow.name)

@dp.message(Flow.name)
async def input_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip() or "Без имени"
    data = await state.get_data()
    role = data.get("role", "first")
    role_key = data.get("role_key", "a")

    code = data.get("code")
    if role == "first":
        code = gen_code()
        await state.update_data(code=code)

    ensure_session(code)
    sessions[code][role_key]["name"] = name

    if role == "first":
        await message.answer(f"🔐 Твой код: <b>{code}</b>\nПередай его второму участнику 💌")
    else:
        await message.answer(f"✅ Код принят: <b>{code}</b>")

    await send_question(message.chat.id, code, role_key, 0)
    await state.set_state(Flow.idx)

@dp.callback_query(F.data.startswith("ans:"))
async def on_answer(cb: types.CallbackQuery, state: FSMContext):
    try:
        _, code, role_key, qidx, opt_idx = cb.data.split(":")
        qidx, opt_idx = int(qidx), int(opt_idx)
    except Exception:
        await cb.answer()
        return

    ensure_session(code)
    answers = sessions[code][role_key]["answers"]
    if len(answers) == qidx:
        answers.append(opt_idx)
    elif len(answers) > qidx:
        answers[qidx] = opt_idx

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅")

    await send_question(cb.message.chat.id, code, role_key, qidx + 1)

# ---------- Webhook ----------
async def on_startup(bot: Bot):
    try:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    except Exception:
        pass

async def on_shutdown(bot: Bot):
    try:
        await bot.delete_webhook()
    except Exception:
        pass

def build_app() -> web.Application:
    app = web.Application()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    return app

if __name__ == "__main__":
    app = build_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
