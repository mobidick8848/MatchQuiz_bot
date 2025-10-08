import csv
import os
import random
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiohttp import web
import json
from aiogram.utils.keyboard import InlineKeyboardBuilder

builder = InlineKeyboardBuilder()
for opt in question["options"]:
    builder.button(text=opt, callback_data=opt)

# 👇 Кнопки будут идти строго по одной в ряд
builder.adjust(1)

await message.answer(
    text=question["question"],
    reply_markup=builder.as_markup()
)



BOT_TOKEN = os.getenv("BOT_TOKEN")

from aiogram.client.default import DefaultBotProperties

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

QUESTIONS_PATH = "questions.csv"
SESSIONS_PATH = "sessions.json"

def load_questions_from_csv(path: str):
    import csv, re, os, random

    if not os.path.exists(path):
        return []

    # автоопределение кодировки
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                rows = list(reader)
                break
        except Exception:
            continue
    else:
        return []

    out = []
    for row in rows:
        q = (row.get("question") or row.get("вопрос") or "").strip()
        opts_field = (row.get("options") or row.get("варианты ответов") or "").strip()
        qtype = (row.get("type") or row.get("тип") or "single").strip().lower()
        if not q or not opts_field or "question" in q.lower():
            # пропускаем шапку
            continue
        # делим варианты по | ; ,
        options = [o.strip() for o in re.split(r"[|;,]", opts_field) if o.strip()]
        out.append({"type": qtype, "question": q, "options": options})
    return out

    # ---- эмодзи-подбор, как раньше ----
    emoji_groups = {
        "отнош": "💕", "люб": random.choice(["❤️", "💌", "💐", "🌹", "💞"]),
        "чувств": random.choice(["💖", "💘", "💗"]), "поцел": "😘", "друз": "👫", "объят": "🤗",
        "работ": "💼", "офис": "🏢", "началь": "👔", "деньги": "💰", "проект": "📊",
        "еда": "🍽️", "куш": "🍲", "завтр": "☕", "обед": "🥗", "ужин": "🍝", "коф": "☕", "чай": "🍵",
        "спорт": "🏃", "бег": "🏃‍♀️", "трен": "💪", "фитнес": "🏋️", "вело": "🚴",
        "отдых": "🌙", "сон": "😴", "релакс": "🧘",
        "путеше": "🧳", "поезд": "🚆", "отпуск": "🏖️", "море": "🌊",
        "живот": "🐶", "кот": "🐱", "собак": "🐕", "питом": "🐾",
        "хобби": "🎨", "увлеч": "🎯", "музык": "🎵", "фильм": "🎬", "книг": "📚",
        "юмор": "😂", "шут": "🤣", "смех": "😄"
    }
    neutral_emojis = ["💭", "🌟", "🎈", "🎉", "💬", "🎵", "💫", "🌈", "✨"]

    def pick_emoji(text: str) -> str:
        t_lower = text.lower()
        for key, emo in emoji_groups.items():
            if re.search(key, t_lower):
                return emo
        return random.choice(neutral_emojis)

    # ---- читаем с авто-детектом кодировки и разделителя ----
    rows = None
    dialect = None
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                sample = f.read(2048)
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

    # ---- нормализуем заголовки ----
    header = [h.strip().lower().lstrip("\ufeff") for h in rows[0]]

    def find_idx(candidates):
        for name in candidates:
            if name in header:
                return header.index(name)
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
        if not q or not opts_field:
            continue

        # делим варианты по | ; или , (что бы ни прислала Excel/Numbers/Google)
        options_raw = [o.strip() for o in re.split(r"\s*\|\s*|\s*;\s*|\s*,\s*", opts_field) if o.strip()]

        # добавляем эмодзи к вопросу и вариантам
        q_emoji = pick_emoji(q)
        options = []
        for o in options_raw:
            emo = pick_emoji(o)
            if emo not in o:
                o = f"{emo} {o}"
            options.append(o)

        out.append({"type": qtype, "question": f"{q_emoji} {q}", "options": options})

    return out
questions = load_questions_from_csv(QUESTIONS_PATH)

class Quiz(StatesGroup):
    role = State()
    code = State()
    name_a = State()
    name_b = State()
    q = State()

def load_sessions():
    if os.path.exists(SESSIONS_PATH):
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_sessions(s):
    with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def equal(q, a, b):
    return a == b

def opt_text(q, ans):
    if ans is None:
        return "—"
    opts = q["options"]
    try:
        idx = int(ans)
        return opts[idx]
    except:
        return str(ans)

@dp.message(CommandStart())
async def start_cmd(msg: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Пройти как первый участник", callback_data="first")
    kb.button(text="💞 Пройти как второй участник", callback_data="second")
    await msg.answer("Привет! 🥰 Это тест совпадений."
                     "Один из вас проходит его первым, а второй потом вводит код."
                     "Кто ты?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.in_({"first","second"}))
async def pick_role(call: types.CallbackQuery, state: FSMContext):
    role = call.data
    await state.update_data(role=role)
    if role == "first":
        code = str(random.randint(1000, 9999))
        await state.update_data(code=code)
        await call.message.answer(f"💬 Введи своё имя (код для второго участника: <code>{code}</code>)")
    else:
        await call.message.answer("💬 Введи код от первого участника:")

@dp.message(F.text.regexp(r"^\d{4}$"))
async def got_code(msg: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("role") != "second":
        return
    code = msg.text.strip()
    await state.update_data(code=code)
    await msg.answer("💬 А теперь введи своё имя:")

@dp.message(F.text)
async def got_name(msg: Message, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    if not role:
        return
    if role == "first":
        await state.update_data(name_a=msg.text.strip(), answers={})
    else:
        await state.update_data(name_b=msg.text.strip(), answers={})
    await ask_question(msg, state, 0)

async def ask_question(msg: Message, state: FSMContext, idx: int):
    if idx >= len(questions):
        await finish(msg, state)
        return
    q = questions[idx]
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(q["options"]):
        kb.button(text=opt, callback_data=f"ans_{idx}_{i}")
    await msg.answer(f"<b>{q['question']}</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("ans_"))
async def answer(call: types.CallbackQuery, state: FSMContext):
    _, idx, ans = call.data.split("_")
    idx, ans = int(idx), int(ans)
    data = await state.get_data()
    answers = data.get("answers", {})
    answers[str(idx)] = ans
    await state.update_data(answers=answers)
    await call.message.edit_reply_markup()
    await ask_question(call.message, state, idx + 1)

async def finish(msg: Message, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    code = data.get("code")
    answers = data.get("answers", {})
    sessions = load_sessions()

    if role == "first":
        s = sessions.get(code, {})
        s["answers_a"] = answers
        s["name_a"] = data.get("name_a", "")
        sessions[code] = s
        save_sessions(sessions)
        await msg.answer(f"💌 Готово! Отправь этот код второй половинке: <code>{code}</code>")
    else:
        s = sessions.get(code)
        if not s:
            await msg.answer("⚠️ Сессия не найдена.")
            await state.clear()
            return
        s["answers_b"] = answers
        s["name_b"] = data.get("name_b", "")
        sessions[code] = s
        save_sessions(sessions)

        hits = 0
        total = len(questions)
        body_lines = []

        for i, q in enumerate(questions):
            a = s["answers_a"].get(str(i))
            b = answers.get(str(i))
            ok = equal(q, a, b)
            hits += 1 if ok else 0
            mark = "❤️ Совпало!" if ok else "💔 По-разному"
            prefix = "💭" if ok else "🤔"
            body_lines.append(
                f"{prefix} <b>{q['question']}</b>\n"
                f"— 💕 {s.get('name_a', 'Первый')}: {opt_text(q, a)}\n"
                f"— 💙 {s.get('name_b', 'Второй')}: {opt_text(q, b)}\n"
                f"{mark}\n"
            )

        percent = int((hits / total) * 100) if total > 0 else 0

        if percent >= 85:
            emotional = "🌹 Ваша эмоциональная близость почти идеальна — вы чувствуете друг друга без слов 💞"
        elif percent >= 60:
            emotional = "💖 Между вами хорошая эмоциональная связь — вы понимаете друг друга даже в тишине 😊"
        elif percent >= 40:
            emotional = "💫 Есть отклик, но вы ещё изучаете внутренний мир друг друга 🌙"
        else:
            emotional = "💔 Пока близость хрупкая, но искренность может всё изменить 🌱"

        if percent >= 85:
            summary_line = f"💞 Совместимость: <b>{percent}%</b> — вы просто созданы друг для друга! 🌹"
        elif percent >= 60:
            summary_line = f"💖 Совместимость: <b>{percent}%</b> — отличная пара, различия только украшают 😄"
        elif percent >= 40:
            summary_line = f"💫 Совместимость: <b>{percent}%</b> — неплохо, но вы разные — и это интересно 😉"
        else:
            summary_line = f"💔 Совместимость: <b>{percent}%</b> — противоположности притягиваются 😅"

        header = "🥰 <b>Вот что получилось!</b>\nПосмотрим, как совпадают ваши ответы и насколько вы на одной волне 💫\n\n"
        summary = f"{summary_line}\n{emotional}\n\n❤️ Совпадений: <b>{hits}</b> из <b>{total}</b>\n\n"
        text = header + summary + "\n".join(body_lines)

        for chunk in [text[i:i+3500] for i in range(0, len(text), 3500)]:
            await msg.answer(chunk, parse_mode="HTML")

    await state.clear()

async def handle(request):
    body = await request.json()
    await dp.feed_update(bot, types.Update(**body))
    return web.Response()

app = web.Application()
app.router.add_post("/webhook", handle)

async def on_startup(_):
    await bot.set_webhook(os.getenv("RENDER_EXTERNAL_URL") + "/webhook")

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
