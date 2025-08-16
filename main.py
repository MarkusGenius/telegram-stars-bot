import asyncio
import hashlib
import logging
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from flask import Flask, request, jsonify
import threading
import requests
import re
from urllib.parse import urlencode

# Настройки из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# Настройки Free-Kassa
MERCHANT_ID = os.getenv("MERCHANT_ID", "YOUR_MERCHANT_ID")
SECRET_WORD_1 = os.getenv("SECRET_WORD_1", "YOUR_SECRET_WORD_1")
SECRET_WORD_2 = os.getenv("SECRET_WORD_2", "YOUR_SECRET_WORD_2")

# Курс звезды к рублю
STAR_TO_RUB_RATE = float(os.getenv("STAR_TO_RUB_RATE", "4.0"))

# Порт для Flask
PORT = int(os.getenv("PORT", 5000))

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Flask приложение для webhook
app = Flask(__name__)

# Состояния FSM
class OrderStates(StatesGroup):
    waiting_for_recipient = State()
    waiting_for_payment = State()

# База данных заказов (в продакшене использовать настоящую БД)
orders_db = {}
pending_payments = {}

def generate_payment_link(amount, order_id, user_id):
    """Генерирует ссылку для оплаты через Free-Kassa"""
    params = {
        'm': MERCHANT_ID,
        'oa': amount,
        'o': order_id,
        's': hashlib.md5(f"{MERCHANT_ID}:{amount}:{SECRET_WORD_1}:{order_id}".encode()).hexdigest(),
        'us_user_id': user_id
    }
    return f"https://pay.freekassa.ru/?{urlencode(params)}"

def verify_payment_signature(data):
    """Проверяет подпись платежа от Free-Kassa"""
    try:
        merchant_id = data.get('MERCHANT_ID')
        amount = data.get('AMOUNT')
        merchant_order_id = data.get('MERCHANT_ORDER_ID')
        sign = data.get('SIGN')
        
        expected_sign = hashlib.md5(
            f"{merchant_id}:{amount}:{SECRET_WORD_2}:{merchant_order_id}".encode()
        ).hexdigest()
        
        return sign.upper() == expected_sign.upper()
    except Exception as e:
        logging.error(f"Ошибка проверки подписи: {e}")
        return False

def check_username_exists(username):
    """Проверяет существование пользователя в Telegram"""
    try:
        clean_username = username.replace('@', '')
        # Проверка формата username
        if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', clean_username):
            return True
        return False
    except:
        return False

def calculate_cost(stars_count):
    """Рассчитывает стоимость звезд в рублях"""
    return int(stars_count * STAR_TO_RUB_RATE)

# Flask routes
@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка уведомлений о платежах от Free-Kassa"""
    try:
        data = request.form.to_dict()
        logging.info(f"Получено уведомление: {data}")
        
        if not verify_payment_signature(data):
            logging.error("Неверная подпись платежа")
            return "ERROR", 400
        
        order_id = data.get('MERCHANT_ORDER_ID')
        amount = float(data.get('AMOUNT', 0))
        user_id = int(data.get('us_user_id', 0))
        
        if order_id in orders_db:
            order = orders_db[order_id]
            
            # Проверяем сумму
            if amount >= order['cost']:
                order['status'] = 'paid'
                order['paid_at'] = datetime.now().isoformat()
                
                # Запускаем асинхронную функцию уведомления
                asyncio.run_coroutine_threadsafe(
                    notify_payment_success(user_id, order_id),
                    asyncio.get_event_loop()
                )
        
        return "YES"
    
    except Exception as e:
        logging.error(f"Ошибка в webhook: {e}")
        return "ERROR", 500

@app.route('/success.html')
def success_page():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="3;url=https://t.me/{bot_username}?start=success">
        <title>Оплата успешна</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 50px; }
            .success { color: green; font-size: 24px; }
        </style>
    </head>
    <body>
        <div class="success">✅ Оплата прошла успешно!</div>
        <p>Перенаправление в бота через 3 секунды...</p>
        <a href="https://t.me/{bot_username}?start=success">Перейти в бота сейчас</a>
    </body>
    </html>
    '''.replace('{bot_username}', 'YOUR_BOT_USERNAME')

@app.route('/failed.html')
def failed_page():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="3;url=https://t.me/{bot_username}?start=failed">
        <title>Ошибка оплаты</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 50px; }
            .error { color: red; font-size: 24px; }
        </style>
    </head>
    <body>
        <div class="error">❌ Ошибка оплаты</div>
        <p>Попробуйте еще раз или обратитесь в поддержку</p>
        <p>Перенаправление в бота через 3 секунды...</p>
        <a href="https://t.me/{bot_username}?start=failed">Перейти в бота сейчас</a>
    </body>
    </html>
    '''.replace('{bot_username}', 'YOUR_BOT_USERNAME')

@app.route('/')
def index():
    return "Telegram Stars Bot is running!"

# Telegram bot handlers
async def notify_payment_success(user_id, order_id):
    """Уведомляет о успешной оплате"""
    try:
        order = orders_db.get(order_id)
        if not order:
            return
        
        # Уведомляем пользователя
        await bot.send_message(
            user_id,
            "✅ Оплата прошла! Ожидайте в течение 5-10 минут!\n"
            "Ваш заказ передан в обработку."
        )
        
        # Создаем кнопки для админа
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "✅ Обработать заказ", 
            callback_data=f"process_{order_id}"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "❌ Отменить заказ", 
            callback_data=f"cancel_admin_{order_id}"
        ))
        
        # Уведомляем админа
        fragment_url = f"https://fragment.com/stars/buy?recipient={order['recipient']}&quantity={order['stars_count']}"
        
        await bot.send_message(
            ADMIN_ID,
            f"🔔 Новый оплаченный заказ!\n\n"
            f"📋 ID заказа: <code>{order_id}</code>\n"
            f"👤 Заказчик: @{order['username']}\n"
            f"🎯 Получатель: @{order['recipient']}\n"
            f"⭐ Количество звёзд: {order['stars_count']}\n"
            f"💰 Оплачено: {order['cost']} руб.\n"
            f"📅 Время оплаты: {order.get('paid_at', 'N/A')}\n\n"
            f"🔗 Ссылка для покупки на Fragment:\n"
            f"<code>{fragment_url}</code>\n\n"
            f"👆 Скопируйте ссылку, перейдите на Fragment, купите звёзды и нажмите 'Обработать заказ'",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при уведомлении об оплате: {e}")

@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message, state: FSMContext):
    args = message.get_args()
    
    if args == 'success':
        await message.answer("✅ Оплата прошла успешно! Ожидайте обработку заказа администратором.")
        return
    elif args == 'failed':
        await message.answer("❌ Оплата не прошла. Попробуйте еще раз или обратитесь в поддержку.")
        return
    
    # Проверяем наличие username у пользователя
    if not message.from_user.username:
        await message.answer(
            "❌ У вас отсутствует username в Telegram!\n\n"
            "Пожалуйста, установите его в настройках и попробуйте снова:\n\n"
            "📱 <b>Как установить username:</b>\n"
            "1️⃣ Откройте настройки Telegram\n" 
            "2️⃣ Нажмите на свое имя\n"
            "3️⃣ Введите username в поле 'Имя пользователя'\n"
            "4️⃣ Вернитесь в бота и нажмите /start",
            parse_mode='HTML'
        )
        return
    
    await state.finish()
    
    welcome_text = (
        "🌟 <b>Добро пожаловать в нашего бота!</b>\n\n"
        "Здесь вы можете приобрести звёзды по чистому курсу "
        "или подарить их своему другу!\n\n"
        f"💱 <b>Текущий курс:</b> 1 звезда = {STAR_TO_RUB_RATE} руб.\n"
        f"⚡ <b>Комиссия:</b> 0% (чистый курс)\n"
        f"🚀 <b>Скорость:</b> 5-10 минут"
    )
    
    await message.answer(welcome_text, parse_mode='HTML')
    
    await OrderStates.waiting_for_recipient.set()
    await message.answer(
        "📝 <b>Напишите username получателя и количество звёзд:</b>\n\n"
        "🔸 Формат: <code>@username количество_звёзд</code>\n"
        "🔸 Пример: <code>@durov 50</code>\n\n"
        "⚠️ Username должен существовать в Telegram!",
        parse_mode='HTML'
    )

@dp.message_handler(state=OrderStates.waiting_for_recipient)
async def process_recipient(message: types.Message, state: FSMContext):
    try:
        # Парсим сообщение
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer(
                "❌ <b>Неверный формат!</b>\n\n"
                "Используйте: <code>@username количество_звёзд</code>\n"
                "Пример: <code>@durov 50</code>",
                parse_mode='HTML'
            )
            return
        
        recipient_username = parts[0].replace('@', '')
        stars_count = int(parts[1])
        
        # Проверки
        if stars_count <= 0:
            await message.answer("❌ Количество звёзд должно быть больше 0!")
            return
            
        if stars_count > 10000:
            await message.answer("❌ Максимальное количество звёзд за раз: 10,000")
            return
            
        if not check_username_exists(recipient_username):
            await message.answer(
                "❌ <b>Некорректный username!</b>\n\n"
                "Проверьте правильность написания.\n"
                "Username должен:\n"
                "• Начинаться с буквы\n"
                "• Содержать 5-32 символа\n"
                "• Состоять из букв, цифр и подчеркиваний",
                parse_mode='HTML'
            )
            return
        
        # Рассчитываем стоимость
        cost = calculate_cost(stars_count)
        
        # Создаем уникальный ID заказа
        order_id = f"order_{message.from_user.id}_{int(datetime.now().timestamp())}"
        
        order_data = {
            'user_id': message.from_user.id,
            'username': message.from_user.username,
            'recipient': recipient_username,
            'stars_count': stars_count,
            'cost': cost,
            'order_id': order_id,
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }
        
        orders_db[order_id] = order_data
        pending_payments[message.from_user.id] = order_data
        
        # Генерируем ссылку для оплаты
        payment_link = generate_payment_link(cost, order_id, message.from_user.id)
        
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("💳 Оплатить", url=payment_link))
        keyboard.add(types.InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{order_id}"))
        
        order_text = (
            f"✅ <b>Заказ создан!</b>\n\n"
            f"📋 <b>Детали заказа:</b>\n"
            f"🆔 ID: <code>{order_id}</code>\n"
            f"👤 Получатель: @{recipient_username}\n"
            f"⭐ Количество звёзд: {stars_count:,}\n"
            f"💰 К оплате: <b>{cost} рублей</b>\n\n"
            f"💳 Нажмите кнопку ниже для оплаты:\n"
            f"🔸 Принимаем все банковские карты\n"
            f"🔸 СБП (Система быстрых платежей)\n"
            f"🔸 Электронные кошельки"
        )
        
        await message.answer(order_text, reply_markup=keyboard, parse_mode='HTML')
        await OrderStates.waiting_for_payment.set()
        
    except ValueError:
        await message.answer("❌ Количество звёзд должно быть целым числом!")
    except Exception as e:
        await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")
        logging.error(f"Ошибка при обработке заказа: {e}")

@dp.callback_query_handler(lambda c: c.data.startswith('cancel_'))
async def cancel_order(callback_query: types.CallbackQuery, state: FSMContext):
    order_id = callback_query.data.replace('cancel_', '')
    
    if order_id in orders_db:
        del orders_db[order_id]
    
    if callback_query.from_user.id in pending_payments:
        del pending_payments[callback_query.from_user.id]
    
    await state.finish()
    await callback_query.message.edit_text("❌ Заказ отменен. Для создания нового заказа нажмите /start")
    await callback_query.answer("Заказ отменен")

@dp.callback_query_handler(lambda c: c.data.startswith('process_'))
async def process_order(callback_query: types.CallbackQuery):
    """Обработка заказа админом после покупки на Fragment"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    order_id = callback_query.data.replace('process_', '')
    
    if order_id not in orders_db:
        await callback_query.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order = orders_db[order_id]
    order['status'] = 'completed'
    order['completed_at'] = datetime.now().isoformat()
    
    # Уведомляем пользователя о завершении
    try:
        success_text = (
            f"🎉 <b>Спасибо за покупку!</b>\n\n"
            f"⭐ <b>{order['stars_count']:,} звёзд</b> успешно отправлены "
            f"пользователю @{order['recipient']}!\n\n"
            f"✨ Звёзды уже доступны получателю\n"
            f"🕒 Время обработки: {datetime.now().strftime('%H:%M')}\n\n"
            f"🌟 <b>Будем ждать вас снова!</b>"
        )
        
        await bot.send_message(order['user_id'], success_text, parse_mode='HTML')
        
    except Exception as e:
        logging.error(f"Ошибка при уведомлении пользователя: {e}")
    
    # Обновляем сообщение админа
    completion_text = (
        f"✅ <b>Заказ {order_id} обработан!</b>\n\n"
        f"👤 Заказчик уведомлен о завершении\n"
        f"⏰ Время завершения: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    await callback_query.message.edit_text(completion_text, parse_mode='HTML')
    await callback_query.answer("✅ Заказ успешно обработан!")

@dp.callback_query_handler(lambda c: c.data.startswith('cancel_admin_'))
async def cancel_order_admin(callback_query: types.CallbackQuery):
    """Отмена заказа админом"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    order_id = callback_query.data.replace('cancel_admin_', '')
    
    if order_id not in orders_db:
        await callback_query.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order = orders_db[order_id]
    
    # Уведомляем пользователя об отмене
    try:
        await bot.send_message(
            order['user_id'],
            f"😔 Извините, заказ #{order_id} был отменен по техническим причинам.\n\n"
            f"💰 Средства будут возвращены в течение 1-3 рабочих дней.\n"
            f"📞 По вопросам обращайтесь в поддержку: @support"
        )
    except:
        pass
    
    # Удаляем заказ
    del orders_db[order_id]
    
    await callback_query.message.edit_text(f"❌ Заказ {order_id} отменен админом")
    await callback_query.answer("Заказ отменен")

@dp.message_handler(commands=['orders'])
async def show_orders(message: types.Message):
    """Показать активные заказы (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        return
    
    if not orders_db:
        await message.answer("📭 Активных заказов нет")
        return
    
    orders_text = "📋 <b>Активные заказы:</b>\n\n"
    
    for order_id, order in orders_db.items():
        status_emoji = {
            'pending': '⏳',
            'paid': '💰', 
            'completed': '✅'
        }.get(order['status'], '❓')
        
        orders_text += (
            f"{status_emoji} <code>{order_id}</code>\n"
            f"👤 @{order['username']} → @{order['recipient']}\n"
            f"⭐ {order['stars_count']:,} звёзд | 💰 {order['cost']} руб.\n"
            f"📅 {order.get('created_at', 'N/A')[:16]}\n\n"
        )
    
    await message.answer(orders_text, parse_mode='HTML')

@dp.message_handler(commands=['stats'])
async def show_stats(message: types.Message):
    """Статистика (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        return
    
    total_orders = len(orders_db)
    paid_orders = len([o for o in orders_db.values() if o['status'] in ['paid', 'completed']])
    completed_orders = len([o for o in orders_db.values() if o['status'] == 'completed'])
    total_revenue = sum([o['cost'] for o in orders_db.values() if o['status'] in ['paid', 'completed']])
    
    stats_text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"📋 Всего заказов: {total_orders}\n"
        f"💰 Оплаченных: {paid_orders}\n"
        f"✅ Выполненных: {completed_orders}\n"
        f"💵 Общая выручка: {total_revenue} руб.\n\n"
        f"💎 Текущий курс: {STAR_TO_RUB_RATE} руб./звезда"
    )
    
    await message.answer(stats_text, parse_mode='HTML')

@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "👋 Привет! Используйте команду /start для начала работы с ботом.\n\n"
        "🌟 Здесь вы можете купить Telegram Stars по лучшему курсу!"
    )

def run_flask():
    """Запуск Flask в отдельном потоке"""
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logging.info("🚀 Telegram Stars Bot запущен!")
    
    # Запускаем Telegram бота
    executor.start_polling(dp, skip_updates=True)
