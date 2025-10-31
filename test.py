import asyncio
import logging
import re
import sqlite3
import pandas as pd
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# ---------------- CONFIG ----------------
BOT_TOKEN = "8370449540:AAHnDCJe-xBhYhwTrZfLGQnustiDd4_7m24"
TEACHER_IDS = [8309413647,7057220878]
DB_PATH = "math_bot.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE ----------------
def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
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
    return user_id in TEACHER_IDS

def main_menu(is_teacher=False):
    kb = ReplyKeyboardBuilder()
    if is_teacher:
        kb.button(text="📤 Test yuklash")
        kb.button(text="📨 Test yuborish")
        kb.button(text="📊 Natijalar (Excel)")
        kb.button(text="🧹 Clean baza")  # ✅ yangi tugma
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
    total = len(correct)
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

# ---------------- /START ----------------
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

# ---------------- RO‘YXATDAN O‘TISH ----------------
@dp.message(Register.name)
async def reg_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO students VALUES (?, ?)", (message.from_user.id, name))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Ro‘yxatdan o‘tish muvaffaqiyatli!", reply_markup=main_menu(False))

# ---------------- O‘QUVCHI PANELI ----------------
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
        f"✅ Javoblaringiz qabul qilindi!\n\n"
        f"📊 Natija:\n✅ {correct} ta to‘g‘ri\n❌ {wrong} ta noto‘g‘ri\n"
        f"📈 {percent}%\n🏅 {grade}",
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

# ---------------- O‘QITUVCHI PANELI ----------------
@dp.message(F.text == "📤 Test yuklash")
async def upload_test(message: types.Message, state: FSMContext):
    if not is_teacher(message.from_user.id):
        return await message.answer("❌ Siz o‘qituvchi emassiz.")
    await message.answer("📘 Test nomini kiriting:")
    await state.set_state(UploadTest.title)

@dp.message(UploadTest.title)
async def upload_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📎 Endi test faylini yuboring (PDF yoki rasm).")
    await state.set_state(UploadTest.file)

@dp.message(UploadTest.file, F.photo | F.document)
async def upload_file(message: types.Message, state: FSMContext):
    if message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        file_id = message.photo[-1].file_id
        file_type = "photo"

    await state.update_data(file_id=file_id, file_type=file_type)
    await message.answer("✍️ To‘g‘ri javoblarni kiriting (masalan: 1a2b3c4d):")
    await state.set_state(UploadTest.answers)

@dp.message(UploadTest.answers)
async def upload_answers(message: types.Message, state: FSMContext):
    data = await state.get_data()
    answers = parse_answers_string(message.text)
    if not answers:
        return await message.answer("❌ Javob formati noto‘g‘ri! Masalan: 1a2b3c4d")

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tests (title, file_id, file_type, correct_answers, created_by)
        VALUES (?, ?, ?, ?, ?)
    """, (data["title"], data["file_id"], data["file_type"], str(answers), message.from_user.id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Test yuklandi!", reply_markup=main_menu(True))

@dp.message(F.text == "📨 Test yuborish")
async def send_test_list(message: types.Message):
    if not is_teacher(message.from_user.id):
        return await message.answer("❌ Siz o‘qituvchi emassiz.")
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM tests ORDER BY id DESC LIMIT 5")
    tests = cur.fetchall()
    conn.close()

    if not tests:
        return await message.answer("📭 Hech qanday test mavjud emas.")

    kb = InlineKeyboardBuilder()
    for t in tests:
        kb.button(text=t["title"], callback_data=f"send_{t['id']}")
    kb.adjust(1)
    await message.answer("✉️ Yuboriladigan testni tanlang:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("send_"))
async def send_selected_test(callback: types.CallbackQuery):
    test_id = int(callback.data.split("_")[1])
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id=?", (test_id,))
    test = cur.fetchone()
    cur.execute("SELECT tg_id FROM students")
    students = cur.fetchall()
    conn.close()

    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Javob yuborish", callback_data=f"answer_{test_id}")
    kb.adjust(1)

    sent = 0
    for s in students:
        try:
            caption = f"🧮 {test['title']}\n\nQuyidagi tugma orqali javob yuboring 👇"
            if test["file_type"] == "photo":
                await bot.send_photo(s["tg_id"], test["file_id"], caption=caption, reply_markup=kb.as_markup())
            else:
                await bot.send_document(s["tg_id"], test["file_id"], caption=caption, reply_markup=kb.as_markup())
            sent += 1
        except Exception as e:
            print(f"❌ Yuborishda xato: {e}")

    await callback.message.edit_text(f"✅ {sent} ta o‘quvchiga yuborildi.")

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

# ---------------- 🧹 CLEAN DATABASE ----------------
@dp.message(F.text == "🧹 Clean baza")
async def clean_database(message: types.Message):
    if not is_teacher(message.from_user.id):
        return await message.answer("❌ Siz o‘qituvchi emassiz.")

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, tozalash", callback_data="confirm_clean")
    kb.button(text="❌ Yo‘q", callback_data="cancel_clean")
    kb.adjust(2)

    await message.answer(
        "⚠️ Siz haqiqatdan ham *tests* va *results* jadvallarini tozalamoqchimisiz?",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )

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
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(F.data == "cancel_clean")
async def cancel_clean(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Tozalash bekor qilindi.")

# ---------------- RUN ----------------
async def main():
    print("✅ Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
