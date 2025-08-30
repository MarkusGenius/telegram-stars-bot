import asyncio
from datetime import datetime, timedelta
import sqlite3

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup

API_TOKEN = "8309652807:AAGm9d0lWcUcqonxFOgXruXpHDxE2ClUwfI"
MTS_CARD = "2203830201305241"  # сюда ставишь карту
STAR_PRICE = 1.19
SUBSCRIPTION_PRICE = 200
REFERRAL_DISCOUNT = 50  # фиксированная скидка по рефералке

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- DATABASE SETUP ---
conn = sqlite3.connect("users.db")
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    subscription_until TEXT,
    referrer_id INTEGER
)
''')
conn.commit()

# --- HELPERS ---
def get_user(user_id):
    cursor.execute("SELECT user_id, username, subscription_until, referrer_id FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def add_or_update_user(user_id, username, referrer_id=None):
    now = datetime.now()
    sub_until = now.strftime("%Y-%m-%d %H:%M:%S")
    if get_user(user_id):
        cursor.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    else:
        cursor.execute("INSERT INTO users (user_id, username, subscription_until, referrer_id) VALUES (?, ?, ?, ?)",
                       (user_id, username, sub_until, referrer_id))
    conn.commit()

def extend_subscription(user_id, days=30):
    user = get_user(user_id)
    now = datetime.now()
    if user:
        if user[2]:
            current_end = datetime.strptime(user[2], "%Y-%m-%d %H:%M:%S")
            new_end = max(now, current_end) + timedelta(days=days)
        else:
            new_end = now + timedelta(days=days)
        cursor.execute("UPDATE users SET subscription_until=? WHERE user_id=?", (new_end.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        conn.commit()

def subscription_active(user_id):
    user = get_user(user_id)
    if not user or not user[2]:
        return False
    return datetime.strptime(user[2], "%Y-%m-%d %H:%M:%S") > datetime.now()

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    args = message.get_args()
    referrer_id = None
    if args.isdigit():
        referrer_id = int(args)
    add_or_update_user(message.from_user.id, message.from_user.username or "", referrer_id)

    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Для себя"), KeyboardButton("Для друга")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "Добро пожаловать! Здесь вы можете приобрести звёзды по курсу 1.19 руб/звезда.\nВыберите вариант:",
        reply_markup=markup
    )

# --- HANDLE CHOICE ---
@dp.message(Text(text=["Для себя", "Для друга"]))
async def choose_target(message: types.Message):
    user = get_user(message.from_user.id)
    if not user[1]:
        await message.answer("Пожалуйста, установите username в Telegram, чтобы продолжить.")
        return

    if message.text == "Для себя":
        await message.answer("Введите количество звёзд (целое, минимум 50):")
        dp.current_state(user=message.from_user.id).set_state("buy_self")
    else:
        await message.answer("Введите в формате <юзернейм> <количество звёзд> (минимум 50):")
        dp.current_state(user=message.from_user.id).set_state("buy_friend")

# --- BUY HANDLERS ---
@dp.message(lambda message: True)
async def handle_buy(message: types.Message):
    state = dp.current_state(user=message.from_user.id)
    current_state = await state.get_state()
    user_id = message.from_user.id

    if current_state == "buy_self":
        try:
            stars = int(message.text)
            if stars < 50:
                await message.answer("Минимальное количество: 50")
                return
        except:
            await message.answer("Введите целое число")
            return

        price = stars * STAR_PRICE + SUBSCRIPTION_PRICE
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton("Оплатить МТС карту", url=f"tel:{MTS_CARD}")]]
        )
        await message.answer(f"Сумма к оплате: {price} руб\nНомер карты: {MTS_CARD}", reply_markup=markup)
        await state.clear()

    elif current_state == "buy_friend":
        try:
            parts = message.text.split()
            if len(parts) != 2:
                raise ValueError
            username = parts[0].lstrip("@")
            stars = int(parts[1])
            if stars < 50:
                await message.answer("Минимальное количество: 50")
                return
        except:
            await message.answer("Неверный формат. Пример: user123 50")
            return

        price = stars * STAR_PRICE + SUBSCRIPTION_PRICE
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton("Оплатить МТС карту", url=f"tel:{MTS_CARD}")]]
        )
        await message.answer(f"Вы отправляете {stars} звёзд пользователю @{username}\nСумма к оплате: {price} руб\nНомер карты: {MTS_CARD}", reply_markup=markup)
        await state.clear()

# --- BUY COMMAND FOR EXISTING USERS ---
@dp.message(Command("buy"))
async def buy_command(message: types.Message):
    if subscription_active(message.from_user.id):
        await message.answer("Введите /start, чтобы начать покупку звёзд")
    else:
        await message.answer("Ваша подписка истекла. Оплатите снова подписку, чтобы продолжить.\nНомер карты для оплаты: " + MTS_CARD)

# --- BACKGROUND TASK TO CHECK EXPIRING SUBSCRIPTIONS ---
async def subscription_reminder():
    while True:
        cursor.execute("SELECT user_id, subscription_until FROM users")
        for user_id, sub_until in cursor.fetchall():
            if sub_until:
                dt = datetime.strptime(sub_until, "%Y-%m-%d %H:%M:%S")
                if 0 <= (dt - datetime.now()).days <= 1:
                    try:
                        await bot.send_message(user_id, "Ваша подписка скоро истечёт! Пожалуйста, оплатите снова.")
                    except:
                        pass
        await asyncio.sleep(3600)  # проверяем каждый час

# --- MAIN ---
async def main():
    asyncio.create_task(subscription_reminder())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
