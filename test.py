# test.py
import os
import asyncio
import logging
import re
import sqlite3
from pathlib import Path
import pandas as pd
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# ---------------- CONFIG (env orqali) ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN muhit o'zgaruvchisi aniqlanmadi. Railway > Variables ga qo'ying.")

# optional: teacher IDs as comma-separated env variable, yoki fallback ro'yxat
TEACHER_IDS = []
tids = os.environ.get("TEACHER_IDS")
if tids:
    try:
        TEACHER_IDS = [int(x.strip()) for x in tids.split(",") if x.strip()]
    except Exception:
        TEACHER_IDS = []

DB_PATH = os.environ.get("DB_PATH", "data/math_bot.db")  # railwayda yozish uchun data/ papka yarating

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE HELPERS ----------------
def ensure_db_dir(path_str: str):
    p = Path(path_str).resolve()
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db_conn():
    ensure_db_dir(DB_PATH)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)  # aiogram async bilan ishlaganda bu yaxshi
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS students (
        tg_id INTEGER PRIMARY KEY,
        name TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        file_id TEXT,
        file_type TEXT,
        correct_answers TEXT,
        created_by INTEGER
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER,
        student_tg_id INTEGER,
        raw_answers TEXT,
        correct_count INTEGER,
        wrong_count INTEGER,
        percent REAL,
        grade TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

# initialize DB on startup
init_db()

# ---------------- STATES ----------------
class Register(StatesGroup):
    name = State()

class UploadTest(StatesGroup):
    title = State()
    file = State()
    answers = State()

class SubmitAnswers(StatesGroup):
    test_id = State()
    answers = State()

# ---------------- UTILITIES ----------------
def is_teacher(user_id: int) -> bool:
    # agar TEACHER_IDS bo'sh bo'lsa, fallback sifatida bot yaratuvchisini ishlatish mumkin emas —
    # shuning uchun adminlar listini Railway Variables ga qo'yish tavsiya etiladi
    return user_id in TEACHER_IDS

def main_menu(is_teacher=False):
    kb = ReplyKeyboardBuilder()
    if is_teacher:
        kb.button(text="📤 Test yuklash")
        kb.button(text="📨 Test yuborish")
        kb.button(text="📊 Natijalar (Excel)")
        kb.button(text="🧹 Clean baza")
    else:
        kb.button(text="👤 Profilim")
        kb.button(text="🧮 Test olish")
        kb.button(text="📈 Natijalarim")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def parse_answers_string(s: str):
    s = s.replace(' ', '').lower()
    pattern = re.compile(r'(\d+)([a-z])')
    matches = pattern.findall(s)
    if not matches:
        return None
    return {int(num): ans for num, ans in matches}

def compare_answers(correct, submitted):
    total = len(correct) or 1
    correct_count = sum(1 for q, a in correct.items() if submitted.get(q) == a)
    wrong = total - correct_count
    percent = round((correct_count / total) * 100, 2)
    if percent >= 86:
        grade = "5 (A’lo)"
    elif percent >= 71:
        grade = "4 (Yaxshi)"
    elif percent >= 51:
        grade = "3 (Qoniqarli)"
    else:
        grade = "2 (Qoniqarsiz)"
    return correct_count, wrong, percent, grade

# ---------------- HANDLERS ----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE tg_id=?", (message.from_user.id,))
    student = cur.fetchone()
    conn.close()

    if student or is_teacher(message.from_user.id):
        await message.answer("👋 Xush kelibsiz!", reply_markup=main_menu(is_teacher(message.from_user.id)))
    else:
        await message.answer("Ismingizni kiriting:")
        await state.set_state(Register.name)

@dp.message(Register.name)
async def reg_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    conn = get_db_conn()
    cur = conn.cursor()
    # explicit columns to avoid mismatch if table changes
    cur.execute("INSERT OR REPLACE INTO students (tg_id, name) VALUES (?, ?)", (message.from_user.id, name))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Ro‘yxatdan o‘tish muvaffaqiyatli!", reply_markup=main_menu(False))

@dp.message(F.text == "👤 Profilim")
async def show_profile(message: types.Message):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM students WHERE tg_id=?", (message.from_user.id,))
    s = cur.fetchone()
    conn.close()
    if not s:
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    await message.answer(f"👤 {s['name']}")

@dp.message(F.text == "🧮 Test olish")
async def get_latest_test(message: types.Message):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests ORDER BY id DESC LIMIT 1")
    test = cur.fetchone()
    conn.close()
    if not test:
        await message.answer("📄 Hozircha testlar mavjud emas.")
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Javob yuborish", callback_data=f"answer_{test['id']}")
    kb.adjust(1)
    caption = f"🧮 {test['title']}\n\nQuyidagi tugma orqali javob yuboring 👇"
    if test["file_type"] == "photo":
        await bot.send_photo(message.chat.id, test["file_id"], caption=caption, reply_markup=kb.as_markup())
    else:
        await bot.send_document(message.chat.id, test["file_id"], caption=caption, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("answer_"))
async def start_answer(callback: types.CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[1])
    await state.update_data(test_id=test_id)
    await state.set_state(SubmitAnswers.answers)
    await callback.message.answer("✍️ Javoblaringizni kiriting (masalan: 1a2b3c4d):")
    await callback.answer()

@dp.message(SubmitAnswers.answers)
async def receive_answers(message: types.Message, state: FSMContext):
    data = await state.get_data()
    answers = parse_answers_string(message.text)
    if not answers:
        return await message.answer("❌ Noto‘g‘ri format! Masalan: 1a2b3c4d")
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT correct_answers FROM tests WHERE id=?", (data["test_id"],))
    test = cur.fetchone()
    if not test:
        conn.close()
        return await message.answer("❌ Test topilmadi.")
    correct_answers = eval(test["correct_answers"])
    correct, wrong, percent, grade = compare_answers(correct_answers, answers)
    cur.execute("""
        INSERT INTO results (test_id, student_tg_id, raw_answers, correct_count, wrong_count, percent, grade)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (data["test_id"], message.from_user.id, str(answers), correct, wrong, percent, grade))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        f"✅ Javoblaringiz qabul qilindi!\n\n📊 Natija:\n✅ {correct} ta to‘g‘ri\n❌ {wrong} ta noto‘g‘ri\n📈 {percent}%\n🏅 {grade}",
        reply_markup=main_menu(False)
    )

@dp.message(F.text == "📈 Natijalarim")
async def my_results(message: types.Message):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT test_id, correct_count, wrong_count, percent, grade, timestamp 
        FROM results WHERE student_tg_id=? ORDER BY id DESC LIMIT 5
    """, (message.from_user.id,))
    results = cur.fetchall()
    conn.close()
    if not results:
        await message.answer("📊 Sizda hali natijalar yo‘q.")
        return
    text = "📈 So‘nggi natijalar:\n\n"
    for r in results:
        text += (f"🧮 Test ID: {r['test_id']}\n✅ {r['correct_count']} ta to‘g‘ri\n"
                 f"❌ {r['wrong_count']} ta noto‘g‘ri\n📊 {r['percent']}%\n🏅 {r['grade']}\n🕓 {r['timestamp']}\n\n")
    await message.answer(text)

# Teacher panel (upload, send, excel, clean) — unchanged logic but safe DB calls
# ... (re-use your existing teacher handlers, ensure SQL columns are explicit like above)

@dp.message(F.text == "📊 Natijalar (Excel)")
async def teacher_results(message: types.Message):
    if not is_teacher(message.from_user.id):
        return await message.answer("❌ Siz o‘qituvchi emassiz.")
    conn = get_db_conn()
    df = pd.read_sql_query("""
        SELECT s.name, r.test_id, r.raw_answers, r.correct_count, r.wrong_count, r.percent, r.grade, r.timestamp
        FROM results r
        JOIN students s ON r.student_tg_id = s.tg_id
        ORDER BY r.timestamp DESC
    """, conn)
    conn.close()
    if df.empty:
        return await message.answer("📊 Hozircha natijalar yo‘q.")
    file_path = "results.xlsx"
    df.to_excel(file_path, index=False)
    await message.answer_document(FSInputFile(file_path), caption="📊 Natijalar Excel faylida")

# clean handlers (same as your code) ...
@dp.message(F.text == "🧹 Clean baza")
async def clean_database(message: types.Message):
    if not is_teacher(message.from_user.id):
        return await message.answer("❌ Siz o‘qituvchi emassiz.")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, tozalash", callback_data="confirm_clean")
    kb.button(text="❌ Yo‘q", callback_data="cancel_clean")
    kb.adjust(2)
    await message.answer("⚠️ Siz haqiqatdan ham *tests* va *results* jadvallarini tozalamoqchimisiz?",
                         parse_mode="Markdown", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "confirm_clean")
async def confirm_clean(callback: types.CallbackQuery):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tests")
        cur.execute("DELETE FROM results")
        conn.commit()
        conn.close()
        await callback.message.edit_text("✅ Baza tozalandi! (tests, results)")
    except Exception as e:
        logger.exception("Clean DB xatolik")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(F.data == "cancel_clean")
async def cancel_clean(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Tozalash bekor qilindi.")

# ---------------- RUN ----------------
async def main():
    logger.info("Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("Botda xatolik yuz berdi")
