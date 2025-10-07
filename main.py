#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, asyncio, random, string, csv
from typing import List, Dict, Any, Set
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/webhook"
PORT = int(os.getenv("PORT", "10000"))
QUESTIONS_CSV = os.getenv("QUESTIONS_CSV", "questions.csv")
QUESTIONS_JSON = os.getenv("QUESTIONS_JSON", "questions.json")
SESSIONS_FILE = os.getenv("SESSIONS_FILE", "sessions.json")

def load_questions_from_csv(path: str):
    import io, re, csv, os

    if not os.path.exists(path):
        return []

    # 1) читаем байты и пробуем кодировки
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        # в крайнем случае заменим битые байты
        text = raw.decode("utf-8", errors="replace")

    # 2) определяем разделитель по образцу
    import csv as _csv
    sample = "\n".join(text.splitlines()[:5])
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=";,")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";" if ";" in sample else ","

    # 3) читаем словарём
    f = io.StringIO(text)
    reader = csv.DictReader(f, delimiter=delimiter)

    out = []
    for row in reader:
        # поддерживаем и русские, и английские заголовки
        q = (row.get("Вопрос") or row.get("question") or "").strip()
        opts_raw = (
            row.get("Варианты ответов")
            or row.get("Ответы")
            or row.get("options")
            or ""
        ).strip()
        qtype = (row.get("Тип") or row.get("type") or "single").strip().lower()
        if not q or not opts_raw:
            continue

        # варианты ответа внутри ячейки: ожидаем ; (если вдруг запятые — тоже поймаем)
        parts = [p.strip() for p in re.split(r"[;]", opts_raw) if p.strip()]
        if len(parts) <= 1:
            parts = [p.strip() for p in re.split(r"[;,]", opts_raw) if p.strip()]
        if qtype not in ("single", "multi"):
            qtype = "single"

        out.append({"type": qtype, "question": q, "options": parts})

    return out

def load_questions_from_json(path: str):
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def load_questions():
    qs = load_questions_from_csv(QUESTIONS_CSV)
    if qs: return qs
    qs = load_questions_from_json(QUESTIONS_JSON)
    if qs: return qs
    raise RuntimeError("Не найдены вопросы. Залей CSV с колонками: Вопрос; Варианты ответов; Тип (single/multi)")

def load_sessions():
    if not os.path.exists(SESSIONS_FILE): return {}
    with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {}

def save_sessions(data: Dict[str, Any]):
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def gen_code(n=6): return ''.join(random.choice('0123456789') for _ in range(n))

questions = load_questions()
router = Router()

class St(StatesGroup):
    name_a = State()
    name_b = State()
    code_wait = State()

def kb_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Я отвечаю первым", callback_data="role:first")],
        [InlineKeyboardButton(text="🧑‍🤝‍🧑 Дима отвечает по коду", callback_data="role:second")],
        [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="about")]
    ])

def kb_single(opts, qid, role):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=o, callback_data=f"s:{role}:{qid}:{i}")]
        for i, o in enumerate(opts)
    ])

def kb_multi(opts, qid, role, sel: Set[int]):
    rows = []
    for i, o in enumerate(opts):
        mark = "✅ " if i in sel else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{o}", callback_data=f"m:{role}:{qid}:{i}")])
    rows.append([InlineKeyboardButton(text="➡️ Готово", callback_data=f"mdone:{role}:{qid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "Привет! Это бот-сопоставлятор ответов.\n"
        "1) Первый проходит вопросы и получает код.\n"
        "2) Дима вводит код и проходит те же вопросы.\n"
        "3) На финише — совпадения по каждому вопросу и общий счёт.",
        reply_markup=kb_menu()
    )

@router.callback_query(F.data == "about")
async def about(cb: CallbackQuery):
    await cb.message.answer(
        "Как пользоваться:\n"
        "• Нажми «Я отвечаю первым», пройди вопросы — код придёт в конце.\n"
        "• Отправь код Диме.\n"
        "• Дима выбирает «Дима отвечает по коду», вводит код и отвечает.\n"
        "• Бот покажет, где совпало, а где — нет."
    )
    await cb.answer()

@router.callback_query(F.data.startswith("role:"))
async def role_pick(cb: CallbackQuery, state: FSMContext):
    role = cb.data.split(":")[1]
    await state.update_data(role=role, qid=0, answers={}, sel=set())
    if role == "first":
        await cb.message.answer("Как тебя зовут?")
        await state.set_state(St.name_a)
    else:
        await cb.message.answer("Введи 6-значный код, который получил первый участник.")
        await state.set_state(St.code_wait)
    await cb.answer()

@router.message(St.code_wait)
async def enter_code(msg: Message, state: FSMContext):
    code = msg.text.strip()
    sessions = load_sessions()
    sess = sessions.get(code)
    if not sess:
        await msg.answer("Код не найден. Проверь и пришли ещё раз.")
        return
    await state.update_data(code=code, role="second", qid=0, answers={}, name_a=sess.get("name_a"))
    await msg.answer("Как тебя зовут?")
    await state.set_state(St.name_b)

@router.message(St.name_a)
async def set_name_a(msg: Message, state: FSMContext):
    name = msg.text.strip()
    code = gen_code()
    sessions = load_sessions()
    while code in sessions: code = gen_code()
    sessions[code] = {"name_a": name, "answers_a": {}, "questions_len": len(questions)}
    save_sessions(sessions)
    await state.update_data(code=code, name_a=name, role="first", qid=0, answers={})
    await msg.answer("Отлично! Поехали. Код пришлю по завершении.")
    await ask_q(msg, state)

@router.message(St.name_b)
async def set_name_b(msg: Message, state: FSMContext):
    await state.update_data(name_b=msg.text.strip())
    await msg.answer("Поехали!")
    await ask_q(msg, state)

async def ask_q(msg: Message, state: FSMContext):
    data = await state.get_data()
    qid = data.get("qid", 0); role = data.get("role")
    if qid >= len(questions):
        await finish(msg, state); return
    q = questions[qid]
    if q["type"] == "multi":
        await state.update_data(sel=set())
        await msg.answer(q["question"], reply_markup=kb_multi(q["options"], qid, role, set()))
    else:
        await msg.answer(q["question"], reply_markup=kb_single(q["options"], qid, role))

@router.callback_query(F.data.startswith("s:"))
async def pick_single(cb: CallbackQuery, state: FSMContext):
    _, role, qid, idx = cb.data.split(":"); qid=int(qid); idx=int(idx)
    data = await state.get_data()
    answers = data.get("answers", {}); answers[str(qid)] = idx
    await state.update_data(answers=answers, qid=qid+1)
    await cb.message.edit_reply_markup(reply_markup=None)
    await ask_q(cb.message, state); await cb.answer()

@router.callback_query(F.data.startswith("m:"))
async def multi_toggle(cb: CallbackQuery, state: FSMContext):
    _, role, qid, idx = cb.data.split(":"); qid=int(qid); idx=int(idx)
    data = await state.get_data()
    sel = set(data.get("sel", set()))
    if idx in sel: sel.remove(idx)
    else: sel.add(idx)
    await state.update_data(sel=sel)
    q = questions[qid]
    await cb.message.edit_reply_markup(reply_markup=kb_multi(q["options"], qid, role, sel))
    await cb.answer()

@router.callback_query(F.data.startswith("mdone:"))
async def multi_done(cb: CallbackQuery, state: FSMContext):
    _, role, qid = cb.data.split(":"); qid=int(qid)
    data = await state.get_data()
    sel = list(sorted(set(data.get("sel", set()))))
    answers = data.get("answers", {}); answers[str(qid)] = sel
    await state.update_data(answers=answers, qid=qid+1, sel=set())
    await cb.message.edit_reply_markup(reply_markup=None)
    await ask_q(cb.message, state); await cb.answer()

def opt_text(q, v):
    if isinstance(v, list):
        return ", ".join(q["options"][i] for i in v if 0 <= i < len(q["options"]))
    if isinstance(v, int) and 0 <= v < len(q["options"]):
        return q["options"][v]
    return "—"

def equal(q, a, b):
    return (set(a or []) == set(b or [])) if q["type"] == "multi" else (a == b)

async def finish(msg: Message, state: FSMContext):
    data = await state.get_data()
    role = data.get("role"); code = data.get("code"); answers = data.get("answers", {})
    sessions = load_sessions()

    if role == "first":
        s = sessions.get(code, {}); s["answers_a"] = answers; s["name_a"] = data.get("name_a", "")
        sessions[code] = s; save_sessions(sessions)
        await msg.answer(f"Готово! Код для Димы: <code>{code}</code>")
    else:
        s = sessions.get(code)
        if not s:
            await msg.answer("Сессия не найдена."); await state.clear(); return
        s["answers_b"] = answers; s["name_b"] = data.get("name_b", "")
        sessions[code] = s; save_sessions(sessions)

        hits = 0; total = len(questions)
        header = "Совпадений: {}/{}\n\n"
        body_lines = []
        for i, q in enumerate(questions):
            a = s["answers_a"].get(str(i)); b = answers.get(str(i))
            ok = equal(q, a, b); hits += 1 if ok else 0
            mark = "✅ Совпало" if ok else "❌ По-разному"
            body_lines.append(f"{i+1}. {q['question']}\n— {s.get('name_a','Первый')}: {opt_text(q,a)}\n— {s.get('name_b','Второй')}: {opt_text(q,b)}\n{mark}\n")

        # разбиение на части
        chunks = []
        buf = header.format(hits, total)
        for ln in body_lines:
            if len(buf) + len(ln) > 3800:
                chunks.append(buf); buf = ""
            buf += ln
        if buf: chunks.append(buf)
        for ch in chunks: await msg.answer(ch)
    await state.clear()

async def on_startup(bot: Bot):
    if WEBHOOK_URL and BOT_TOKEN: await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage()); dp.include_router(router)
    app = web.Application(); app["bot"] = bot
    dp.startup.register(lambda: on_startup(bot))
    dp.shutdown.register(lambda: on_shutdown(bot))
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT); await site.start()
    print(f"🚀 MatchQuiz running on port {PORT}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
