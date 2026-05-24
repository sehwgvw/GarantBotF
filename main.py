import logging
import asyncio
import uuid
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError

# Включаем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('GARANT_BOT')

# --- КОНФИГУРАЦИЯ БОТА ---
API_TOKEN = '8928212368:AAE7sKeL47TGOcFCfQk-hDOO2SqJKt0EXow'
ADMIN_ID = 8807653458  # Ваш Telegram ID как админа для управления балансом, выводами и арбитражем

# Список тестировщиков (добавьте сюда ID пользователей, которым будет доступна тестовая передача)
TEST_LIST = [8807653458, 123456789] 

# Реквизиты для приема платежей
STARS_RECEIVER = "отправьте звёзды на @GetGarantSupport"
TON_ADDRESS = "UQBOHmUuiMAM0co8xWrfd8AcmbJj_qgSeHHjJYguy4Qmad8t"
USDT_ADDRESS_TRC20 = "TVfg9k1ZofN3eZR3WfYeS81BxJvMkadtdR"

# Процент комиссии гаранта (например, 1% или 0 для тестов)
FEE_PERCENT = 0.0

# Внутриигровая база данных (в оперативной памяти)
users_db = {} 
deals_db = {}  # Хранилище сделок { deal_id: { ... } }

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ (FSM) ---
class DealCreation(StatesGroup):
    waiting_for_partner_id = State()
    waiting_for_role = State()
    waiting_for_item_type = State()
    waiting_for_item_details = State()  # Описание юзернейма, номера или NFT
    waiting_for_currency = State()
    waiting_for_amount = State()

class WithdrawState(StatesGroup):
    waiting_for_wallet = State()
    waiting_for_amount = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_balance(user_id: int, username: str = "DealsPartner") -> dict:
    if user_id not in users_db:
        users_db[user_id] = {
            "stars": 0.0,
            "ton": 0.0,
            "usdt": 0.0,
            "username": username
        }
    else:
        if username and username != "DealsPartner":
            users_db[user_id]["username"] = username
    return users_db[user_id]

# Вспомогательные клавиатуры для продавца (динамически добавляют тестовую кнопку)
def get_initial_seller_keyboard(deal_id: str, seller_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal_cancel_{deal_id}")]
    ]
    if seller_id in TEST_LIST:
        buttons.insert(0, [InlineKeyboardButton(text="⚙️ Тестовая передача", callback_data=f"test_transfer_{deal_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_active_seller_keyboard(deal_id: str, seller_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📦 Подтвердить отправку товара", callback_data=f"dealsent_confirm_{deal_id}")],
        [InlineKeyboardButton(text="❌ Открыть спор / Арбитраж", callback_data=f"deal_dispute_{deal_id}")]
    ]
    if seller_id in TEST_LIST:
        buttons.insert(0, [InlineKeyboardButton(text="⚙️ Тестовая передача", callback_data=f"test_transfer_{deal_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤝 Создать сделку"), KeyboardButton(text="👤 Мой кабинет")],
            [KeyboardButton(text="💎 Пополнить баланс"), KeyboardButton(text="📤 Вывести TON")],
            [KeyboardButton(text="ℹ️ Помощь и поддержка")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

# --- УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ОТМЕНЫ (ДЛЯ ВСЕХ СОСТОЯНИЙ) ---
@dp.message(F.text == "❌ Отмена", StateFilter("*"))
async def process_cancel_state_global(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
    await message.answer("Действие успешно отменено.", reply_markup=get_main_keyboard())

# --- ОБРАБОТЧИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    get_user_balance(user_id, username)
        
    welcome_text = (
        f"👋 Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        f"Я автоматический бот-автогарант безопасных сделок. Со мной вы защищены от мошенничества.\n"
        f"Доступные валюты: <b>Telegram Stars ⭐, TON 💎, USDT 💵</b>\n"
        f"Специализация: <b>NFT, Юзернеймы Telegram, Анонимные Номера (+888) и цифровые товары</b>.\n\n"
        f"Используйте нижнее меню для управления функциями."
    )
    
    chat_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Наш чат сделок", url="https://t.me/+9DB_Esznk2U3OGEy")]
    ])
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())
    await message.answer("Рекомендуем вступить в наш официальный чат:", reply_markup=chat_kb)

# --- АДМИН-КОМАНДА ИЗМЕНЕНИЯ БАЛАНСА ---
@dp.message(Command("salary"))
async def admin_set_salary(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⚠️ У вас нет прав для использования этой команды.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 4:
            await message.answer("ℹ️ Формат команды:\n<code>/salary [ID пользователя] [stars/ton/usdt] [сумма]</code>")
            return
        
        target_id = int(args[1])
        currency = args[2].lower()
        amount = float(args[3])
        
        if currency not in ["stars", "ton", "usdt"]:
            await message.answer("❌ Неверная валюта. Доступны: <code>stars</code>, <code>ton</code>, <code>usdt</code>")
            return
            
        user_data = get_user_balance(target_id)
        user_data[currency] = amount
        
        await message.answer(
            f"✅ Успешно изменен баланс пользователя <code>{target_id}</code> (@{user_data['username']}):\n"
            f"💰 Новый баланс <b>{currency.upper()}</b>: <code>{amount}</code>"
        )
        
        try:
            await bot.send_message(
                target_id,
                f"⚙️ Администратор обновил ваш баланс!\n"
                f"💰 Ваш текущий баланс <b>{currency.upper()}</b>: <code>{amount}</code>"
            )
        except Exception:
            await message.answer("⚠️ Пользователь заблокировал бота, но изменения применились.")
            
    except ValueError:
        await message.answer("❌ Ошибка: ID пользователя и сумма должны быть числами!")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка: {str(e)}")

# --- МОЙ КАБИНЕТ И ПОПОЛНЕНИЕ ---

@dp.message(F.text == "👤 Мой кабинет")
async def show_cabinet(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    bal = get_user_balance(user_id, username)
    
    cabinet_text = (
        f"👤 <b>Личный кабинет</b>\n"
        f"🆔 Ваш ID: <code>{user_id}</code>\n"
        f"🔗 Логин: @{bal['username']}\n\n"
        f"💰 <b>Ваши балансы в системе:</b>\n"
        f"⭐ Telegram Stars: <b>{bal['stars']}</b>\n"
        f"💎 TON: <b>{bal['ton']} TON</b>\n"
        f"💵 USDT (TRC-20): <b>{bal['usdt']} USDT</b>\n\n"
        f"<i>Вы можете пополнить баланс или создать безопасную сделку.</i>"
    )
    await message.answer(cabinet_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "💎 Пополнить баланс")
async def deposit_info(message: types.Message):
    kb_builder = InlineKeyboardBuilder()
    kb_builder.button(text="⭐ Stars", callback_data="dep_stars")
    kb_builder.button(text="💎 TON", callback_data="dep_ton")
    kb_builder.button(text="💵 USDT (TRC20)", callback_data="dep_usdt")
    kb_builder.adjust(3)
    
    await message.answer("Выберите валюту для пополнения счета:", reply_markup=kb_builder.as_markup())

@dp.callback_query(F.data.startswith("dep_"))
async def process_deposit_choice(callback: types.CallbackQuery):
    currency = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    if currency == "stars":
        text = (
            f"⭐ <b>Пополнение через Telegram Stars</b>\n\n"
            f"Отправьте необходимую сумму звёзд нашему официальному менеджеру:\n"
            f"<b>{STARS_RECEIVER}</b>\n\n"
            f"В комментариях к переводу обязательно укажите ваш ID: <code>{user_id}</code>\n\n"
            f"После отправки свяжитесь с поддержкой для моментального зачисления."
        )
    elif currency == "ton":
        text = (
            f"💎 <b>Пополнение баланса TON</b>\n\n"
            f"Переведите желаемую сумму TON на адрес:\n"
            f"<code>{TON_ADDRESS}</code>\n\n"
            f"⚠️ В комментарии к транзакции ОБЯЗАТЕЛЬНО укажите ваш ID:\n"
            f"<code>{user_id}</code>\n\n"
            f"После отправки нажмите кнопку «Проверить платеж» ниже."
        )
    else: # usdt
        text = (
            f"💵 <b>Пополнение баланса USDT (TRC-20)</b>\n\n"
            f"Переведите USDT на адрес сети TRON (TRC20):\n"
            f"<code>{USDT_ADDRESS_TRC20}</code>\n\n"
            f"В комментарии или при подтверждении транзакции укажите ваш ID:\n"
            f"<code>{user_id}</code>\n\n"
            f"После отправки наши операторы сверят хэш транзакции."
        )
        
    check_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Я оплатил", callback_data=f"check_pay_{currency}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_deposits")]
    ])
    
    await callback.message.edit_text(text, reply_markup=check_kb)
    await callback.answer()

@dp.callback_query(F.data == "back_to_deposits")
async def back_to_dep(callback: types.CallbackQuery):
    kb_builder = InlineKeyboardBuilder()
    kb_builder.button(text="⭐ Stars", callback_data="dep_stars")
    kb_builder.button(text="💎 TON", callback_data="dep_ton")
    kb_builder.button(text="💵 USDT (TRC20)", callback_data="dep_usdt")
    kb_builder.adjust(3)
    await callback.message.edit_text("Выберите валюту для пополнения счета:", reply_markup=kb_builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("check_pay_"))
async def check_payment(callback: types.CallbackQuery):
    await callback.message.answer(
        "⏰ Ожидайте, ваш платеж находится в обработке или сверяется в блокчейне.\n"
        "➖➖➖➖➖➖\n"
        "🎛 Если баланс не обновился в течение 10 минут, пожалуйста, обратитесь в техническую поддержку."
    )
    await callback.answer()

# --- ВЫВОД СРЕДСТВ (ТОЛЬКО TON, С ПОДТВЕРЖДЕНИЕМ АДМИНИСТРАТОРА) ---

@dp.message(F.text == "📤 Вывести TON")
async def withdraw_ton_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    bal = get_user_balance(user_id)
    
    if bal["ton"] <= 0:
        await message.answer("❌ У вас недостаточно TON на балансе для совершения вывода.")
        return
        
    await state.set_state(WithdrawState.waiting_for_wallet)
    await message.answer(
        f"💵 Ваша сумма на вывод: {bal['ton']} TON.\n"
        f"Введите адрес вашего TON кошелька (например, Tonkeeper, MyTonWallet):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(StateFilter(WithdrawState.waiting_for_wallet), F.text)
async def withdraw_wallet_received(message: types.Message, state: FSMContext):
    await state.update_data(wallet=message.text)
    await state.set_state(WithdrawState.waiting_for_amount)
    
    user_id = message.from_user.id
    bal = get_user_balance(user_id)
    await message.answer(
        f"Ваш баланс: {bal['ton']} TON.\n"
        f"Укажите сумму TON для вывода (используйте точку для дробей):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(StateFilter(WithdrawState.waiting_for_amount), F.text)
async def withdraw_amount_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        user_id = message.from_user.id
        bal = get_user_balance(user_id)
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0. Попробуйте еще раз:")
            return
            
        if amount > bal["ton"]:
            await message.answer(f"❌ Недостаточно средств. Ваш баланс: {bal['ton']} TON. Попробуйте еще раз:")
            return
            
        data = await state.get_data()
        wallet = data["wallet"]
        
        await state.clear()
        
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"wd_approve_{user_id}_{amount}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd_reject_{user_id}_{amount}")
            ]
        ])
        
        await bot.send_message(
            ADMIN_ID,
            f"🔔 <b>Новая заявка на вывод средств!</b>\n\n"
            f"👤 Пользователь: {message.from_user.first_name} (ID: <code>{user_id}</code>, @{bal['username']})\n"
            f"💰 Сумма: <b>{amount} TON</b>\n"
            f"👛 Кошелек: <code>{wallet}</code>",
            reply_markup=admin_kb
        )
        
        await message.answer(
            f"✅ Заявка на вывод <b>{amount} TON</b> успешно отправлена на проверку администратору.\n"
            f"Вы получите уведомление сразу после одобрения транзакции.",
            reply_markup=get_main_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Введите корректное число. Пример: 2.5")

@dp.callback_query(F.data.startswith("wd_"))
async def process_withdraw_decision(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⚠️ Вы не администратор!", show_alert=True)
        return
        
    parts = callback.data.split("_")
    action = parts[1]
    user_id = int(parts[2])
    amount = float(parts[3])
    
    bal = get_user_balance(user_id)
    
    if action == "approve":
        if bal["ton"] >= amount:
            bal["ton"] -= amount
            await callback.message.edit_text(
                callback.message.text + f"\n\n✅ <b>ОДОБРЕНО АДМИНИСТРАТОРОМ</b>\nСчет пользователя успешно списан."
            )
            try:
                await bot.send_message(
                    user_id,
                    f"🎉 <b>Ваша заявка на вывод одобрена!</b>\n\n"
                    f"💰 Сумма <b>{amount} TON</b> успешно отправлена на ваш кошелек.\n"
                    f"Баланс в боте обновлен."
                )
            except Exception:
                pass
        else:
            await callback.message.edit_text(
                callback.message.text + f"\n\n❌ <b>Ошибка:</b> У пользователя недостаточно баланса на момент подтверждения!"
            )
    else: # reject
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ <b>ОТКЛОНЕНО АДМИНИСТРАТОРОМ</b>"
        )
        try:
            await bot.send_message(
                user_id,
                f"❌ Ваша заявка на вывод <b>{amount} TON</b> была отклонена администратором.\n"
                f"Для уточнения причин обратитесь в поддержку."
            )
        except Exception:
            pass
            
    await callback.answer()

# --- ПОЛНОЦЕННЫЙ ПРОЦЕСС СОЗДАНИЯ СДЕЛКИ С ВЫБОРОМ ТОВАРА ---

@dp.message(F.text == "🤝 Создать сделку")
async def start_deal_creation(message: types.Message, state: FSMContext):
    await state.set_state(DealCreation.waiting_for_partner_id)
    await message.answer(
        "🤝 <b>Создание безопасной сделки через автогарант</b>\n\n"
        "Пожалуйста, укажите <b>Telegram ID второго участника</b> сделки.\n"
        "Узнать ID партнера можно с помощью специальных ботов (например, @getmyid_bot).",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(StateFilter(DealCreation.waiting_for_partner_id), F.text)
async def process_partner_id(message: types.Message, state: FSMContext):
    try:
        partner_id = int(message.text)
        if partner_id == message.from_user.id:
            await message.answer("❌ Вы не можете совершить сделку с самим собой. Введите корректный ID партнера:")
            return
            
        await state.update_data(partner_id=partner_id)
        await state.set_state(DealCreation.waiting_for_role)
        
        role_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Я Покупатель", callback_data="role_buyer"),
                InlineKeyboardButton(text="📦 Я Продавец", callback_data="role_seller")
            ]
        ])
        await message.answer("Выберите вашу роль в этой сделке:", reply_markup=role_kb)
        
    except ValueError:
        await message.answer("❌ ID должен быть числом! Пожалуйста, проверьте и введите ID повторно:")

@dp.callback_query(F.data.startswith("role_"))
async def process_role_choice(callback: types.CallbackQuery, state: FSMContext):
    role = callback.data.split("_")[1]
    await state.update_data(role=role)
    await state.set_state(DealCreation.waiting_for_item_type)
    
    # Клавиатура выбора типа товара
    item_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 NFT", callback_data="itemtype_NFT")],
        [InlineKeyboardButton(text="🔗 Telegram Username", callback_data="itemtype_Username")],
        [InlineKeyboardButton(text="📞 Anonymous Number (+888)", callback_data="itemtype_AnonymousNumber")],
        [InlineKeyboardButton(text="📦 Другой цифровой товар", callback_data="itemtype_Other")]
    ])
    await callback.message.edit_text("Выберите предмет сделки (тип товара):", reply_markup=item_kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("itemtype_"))
async def process_item_type_choice(callback: types.CallbackQuery, state: FSMContext):
    item_type = callback.data.split("_")[1]
    await state.update_data(item_type=item_type)
    await state.set_state(DealCreation.waiting_for_item_details)
    
    prompt_text = "Введите детали товара"
    if item_type == "NFT":
        prompt_text = "Укажите название NFT, коллекцию или ссылку на нее на Getgems/Fragment:"
    elif item_type == "Username":
        prompt_text = "Введите продаваемый юзернейм (например, @username):"
    elif item_type == "AnonymousNumber":
        prompt_text = "Укажите продаваемый анонимный номер Fragment (например, +888 1234 5678):"
    else:
        prompt_text = "Опишите товар или услугу, которая будет передана в сделке:"
        
    await callback.message.delete()
    await callback.message.answer(
        f"ℹ️ {prompt_text}",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@dp.message(StateFilter(DealCreation.waiting_for_item_details), F.text)
async def process_item_details(message: types.Message, state: FSMContext):
    await state.update_data(item_details=message.text)
    await state.set_state(DealCreation.waiting_for_currency)
    
    cur_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Stars", callback_data="dealcur_stars"),
            InlineKeyboardButton(text="💎 TON", callback_data="dealcur_ton"),
            InlineKeyboardButton(text="💵 USDT", callback_data="dealcur_usdt")
        ]
    ])
    await message.answer("Выберите валюту, в которой будет производиться оплата сделки:", reply_markup=cur_kb)

@dp.callback_query(F.data.startswith("dealcur_"))
async def process_currency_choice(callback: types.CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]
    await state.update_data(currency=currency)
    await state.set_state(DealCreation.waiting_for_amount)
    
    await callback.message.delete()
    await callback.message.answer(
        f"Укажите сумму сделки в <b>{currency.upper()}</b>:\n"
        f"<i>(Дробные числа пишите через точку, например 10.5)</i>",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()

@dp.message(StateFilter(DealCreation.waiting_for_amount), F.text)
async def process_deal_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.answer("❌ Сумма сделки должна быть больше нуля. Введите сумму повторно:")
            return
            
        data = await state.get_data()
        partner_id = data["partner_id"]
        role = data["role"]
        item_type = data["item_type"]
        item_details = data["item_details"]
        currency = data["currency"]
        
        await state.clear()
        
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        get_user_balance(user_id, username)
        
        # Генерация уникального ID для этой сделки
        deal_id = str(uuid.uuid4())[:8]
        
        # Определяем покупателя и продавца на основе роли создателя
        buyer_id = user_id if role == "buyer" else partner_id
        seller_id = partner_id if role == "buyer" else user_id
        
        # Сохраняем в БД
        deals_db[deal_id] = {
            "id": deal_id,
            "buyer_id": buyer_id,
            "seller_id": seller_id,
            "item_type": item_type,
            "item_details": item_details,
            "currency": currency,
            "amount": amount,
            "status": "waiting_for_payment",  # waiting_for_payment -> paid -> item_sent -> completed (арбитраж: arbitration)
            "creator_id": user_id
        }
        
        role_ru = "Покупатель" if role == "buyer" else "Продавец"
        
        await message.answer(
            f"✅ <b>Сделка #{deal_id} успешно зарегистрирована!</b>\n\n"
            f"👤 Ваша роль: <b>{role_ru}</b>\n"
            f"📦 Предмет сделки ({item_type}): <code>{item_details}</code>\n"
            f"👥 Партнер (ID): <code>{partner_id}</code>\n"
            f"💰 Сумма: <b>{amount} {currency.upper()}</b>\n\n"
            f"Инструкция по оплате сгенерирована ниже:",
            reply_markup=get_main_keyboard()
        )
        
        # Отправка инструкций сторонам
        await send_deal_instructions(deal_id)
                
    except ValueError:
        await message.answer("❌ Сумма должна быть числом! Используйте точку для разделения копеек. Попробуйте еще раз:")

# --- ВЫВОД ИНСТРУКЦИЙ И ОБНОВЛЕНИЕ КНОПОК СДЕЛКИ ---

async def send_deal_instructions(deal_id: str):
    deal = deals_db.get(deal_id)
    if not deal:
        return
        
    buyer_id = deal["buyer_id"]
    seller_id = deal["seller_id"]
    amount = deal["amount"]
    currency = deal["currency"]
    item_type = deal["item_type"]
    item_details = deal["item_details"]
    
    # 1. Отправляем инструкцию покупателю
    buyer_bal = get_user_balance(buyer_id).get(currency, 0.0)
    pay_instructions = (
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🛍 <b>ОПЛАТА СДЕЛКИ #{deal_id} (Покупатель)</b>\n\n"
        f"Для безопасной сделки вам необходимо оплатить <b>{amount} {currency.upper()}</b> гаранту.\n"
        f"📦 Товар: [<b>{item_type}</b>] <code>{item_details}</code>\n\n"
        f"💼 Ваши реквизиты для перевода:\n"
    )
    
    if currency == "stars":
        pay_instructions += f"⭐ Направьте Stars менеджеру {STARS_RECEIVER} с комментарием <code>{buyer_id}_deal_{deal_id}</code>"
    elif currency == "ton":
        pay_instructions += f"💎 Адрес TON:\n<code>{TON_ADDRESS}</code>\n💬 Комментарий к платежу: <code>{buyer_id}_deal_{deal_id}</code>"
    else: # usdt
        pay_instructions += f"💵 Адрес USDT (TRC-20):\n<code>{USDT_ADDRESS_TRC20}</code>\n💬 Примечание: <code>{buyer_id}_deal_{deal_id}</code>"
        
    pay_instructions += f"\n\nВаш текущий баланс в боте: <b>{buyer_bal} {currency.upper()}</b>."
    
    inline_buttons = []
    
    # Кнопка быстрой оплаты с баланса бота (если на балансе хватает денег)
    inline_buttons.append([
        InlineKeyboardButton(
            text=f"👛 Оплатить с баланса ({buyer_bal} {currency.upper()})", 
            callback_data=f"dealpay_bal_{deal_id}"
        )
    ])
    
    inline_buttons.append([
        InlineKeyboardButton(text="💎 Я сделал прямой перевод", callback_data=f"dealpay_direct_{deal_id}")
    ])
    
    inline_buttons.append([
        InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal_cancel_{deal_id}")
    ])
    
    try:
        await bot.send_message(buyer_id, pay_instructions, reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_buttons))
    except Exception:
        pass
        
    # 2. Отправляем инструкцию продавцу
    seller_text = (
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📦 <b>СДЕЛКА #{deal_id} (Продавец)</b>\n\n"
        f"Вы выступаете в роли <b>Продавца</b>.\n"
        f"📦 Товар: [<b>{item_type}</b>] <code>{item_details}</code>\n"
        f"💰 Ожидаемая сумма: <b>{amount} {currency.upper()}</b>\n\n"
        f"⏳ Ожидайте, пока покупатель произведет оплату на счет гаранта.\n"
        f"Вы получите автоматическое уведомление, когда можно будет безопасно отправлять товар."
    )
    
    try:
        await bot.send_message(
            seller_id, 
            seller_text, 
            reply_markup=get_initial_seller_keyboard(deal_id, seller_id)
        )
    except Exception:
        pass

# --- КНОПКИ УПРАВЛЕНИЯ СДЕЛКОЙ: ОПЛАТА, ОТПРАВКА, ПОЛУЧЕНИЕ, СПОР ---

@dp.callback_query(F.data.startswith("dealpay_bal_"))
async def deal_pay_from_balance(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    buyer_id = deal["buyer_id"]
    seller_id = deal["seller_id"]
    currency = deal["currency"]
    amount = deal["amount"]
    
    if callback.from_user.id != buyer_id:
        await callback.answer("⚠️ Вы не являетесь покупателем по этой сделке!", show_alert=True)
        return
        
    bal = get_user_balance(buyer_id)
    if bal[currency] >= amount:
        # Списываем средства в боте
        bal[currency] -= amount
        deal["status"] = "paid"
        
        await callback.message.edit_text(
            f"🎉 <b>Сделка #{deal_id} успешно оплачена!</b>\n"
            f"С вашего баланса списано: <b>{amount} {currency.upper()}</b>.\n\n"
            f"Ожидайте, пока продавец отправит вам товар. Как только вы получите его, "
            f"не забудьте подтвердить получение товара по кнопке ниже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Открыть спор / Арбитраж", callback_data=f"deal_dispute_{deal_id}")]
            ])
        )
        
        # Уведомляем продавца и даем ему кнопку ПОДТВЕРЖДЕНИЯ ВЫДАЧИ ТОВАРА
        try:
            await bot.send_message(
                seller_id,
                f"🔔 <b>Сделка #{deal_id} Оплачена Покупателем!</b>\n\n"
                f"💰 Сумма: <b>{amount} {currency.upper()}</b> на балансе гаранта.\n"
                f"📦 Выдайте покупателю товар:\n"
                f"👉 [<b>{deal['item_type']}</b>] <code>{deal['item_details']}</code>\n\n"
                f"После отправки товара покупателю ОБЯЗАТЕЛЬНО нажмите кнопку ниже, чтобы зафиксировать отправку.",
                reply_markup=get_active_seller_keyboard(deal_id, seller_id)
            )
        except Exception:
            pass
            
    else:
        await callback.answer(
            f"❌ Недостаточно средств на балансе! У вас {bal[currency]} {currency.upper()}, а требуется {amount}.",
            show_alert=True
        )

@dp.callback_query(F.data.startswith("dealpay_direct_"))
async def deal_pay_direct(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    buyer_id = deal["buyer_id"]
    seller_id = deal["seller_id"]
    
    if callback.from_user.id != buyer_id:
        await callback.answer("⚠️ Вы не покупатель!", show_alert=True)
        return
        
    # Имитируем ручной платеж (отправляем на проверку оператору)
    deal["status"] = "paid" # Для простоты теста автоматически делаем её оплаченной.
    
    await callback.message.edit_text(
        f"⏳ Платеж по сделке #{deal_id} отправлен на проверку гаранту...\n"
        f"Продавцу отправлено временное уведомление.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Открыть спор / Арбитраж", callback_data=f"deal_dispute_{deal_id}")]
        ])
    )
    
    # Уведомляем продавца
    try:
        await bot.send_message(
            seller_id,
            f"🔔 <b>Сделка #{deal_id} оплачена (прямой перевод)!</b>\n\n"
            f"Покупатель сообщил о совершении прямого перевода гаранту.\n"
            f"Вы можете передать товар покупателю:\n"
            f"👉 [<b>{deal['item_type']}</b>] <code>{deal['item_details']}</code>\n\n"
            f"После выдачи товара нажмите кнопку ниже.",
            reply_markup=get_active_seller_keyboard(deal_id, seller_id)
        )
    except Exception:
        pass
    await callback.answer()

# КНОПКА 1: Продавец подтверждает, что отправил товар
@dp.callback_query(F.data.startswith("dealsent_confirm_"))
async def process_deal_sent_confirm(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    seller_id = deal["seller_id"]
    buyer_id = deal["buyer_id"]
    
    if callback.from_user.id != seller_id:
        await callback.answer("⚠️ Вы не продавец в этой сделке!", show_alert=True)
        return
        
    deal["status"] = "item_sent"
    
    await callback.message.edit_text(
        f"✅ <b>Вы подтвердили отправку товара по сделке #{deal_id}!</b>\n"
        f"Ожидаем подтверждения получения от Покупателя. Деньги поступят на ваш "
        f"баланс, как только покупатель подтвердит сделку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Открыть спор / Арбитраж", callback_data=f"deal_dispute_{deal_id}")]
        ])
    )
    
    # Уведомляем покупателя и даем ему КНОПКУ ПОДТВЕРЖДЕНИЯ ПОЛУЧЕНИЯ ТОВАРА
    buyer_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить получение товара", callback_data=f"dealreceived_confirm_{deal_id}")],
        [InlineKeyboardButton(text="❌ Открыть спор / Арбитраж", callback_data=f"deal_dispute_{deal_id}")]
    ])
    
    try:
        await bot.send_message(
            buyer_id,
            f"🔔 <b>Продавец подтвердил отправку товара по сделке #{deal_id}!</b>\n\n"
            f"Проверьте наличие товара:\n"
            f"👉 [<b>{deal['item_type']}</b>] <code>{deal['item_details']}</code>\n\n"
            f"Если все в порядке и вы получили товар, нажмите кнопку <b>«Подтвердить получение товара»</b>. "
            f"После этого средства будут автоматически отправлены продавцу.",
            reply_markup=buyer_keyboard
        )
    except Exception:
        pass
    await callback.answer()

# --- СКРЫТАЯ КНОПКА ТЕСТОВОЙ ПЕРЕДАЧИ ДЛЯ ТЕСТЕРОВ ---
@dp.callback_query(F.data.startswith("test_transfer_"))
async def process_test_transfer(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    seller_id = deal["seller_id"]
    buyer_id = deal["buyer_id"]
    
    # Проверка прав: должен быть продавцом по сделке и входить в тест-лист
    if callback.from_user.id != seller_id:
        await callback.answer("⚠️ Вы не продавец в этой сделке!", show_alert=True)
        return
        
    if seller_id not in TEST_LIST:
        await callback.answer("⚠️ Ошибка прав.", show_alert=True)
        return
        
    try:
        # Отправляем сообщение покупателю
        await bot.send_message(buyer_id, "Продавец передал товар боту")
        await callback.answer("⚙️ Тестовая передача выполнена! Уведомление отправлено покупателю.", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Ошибка при передаче: {str(e)}", show_alert=True)

# КНОПКА 2: Покупатель подтверждает получение товара -> ЗАВЕРШЕНИЕ СДЕЛКИ
@dp.callback_query(F.data.startswith("dealreceived_confirm_"))
async def process_deal_received_confirm(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    buyer_id = deal["buyer_id"]
    seller_id = deal["seller_id"]
    currency = deal["currency"]
    amount = deal["amount"]
    
    if callback.from_user.id != buyer_id:
        await callback.answer("⚠️ Вы не покупатель в этой сделке!", show_alert=True)
        return
        
    if deal["status"] == "completed":
        await callback.answer("ℹ️ Эта сделка уже была успешно завершена.", show_alert=True)
        return
        
    # Рассчитываем сумму к зачислению продавцу (за вычетом комиссии)
    fee = amount * FEE_PERCENT
    payout_amount = amount - fee
    
    # Зачисляем продавцу на баланс бота
    seller_bal = get_user_balance(seller_id)
    seller_bal[currency] += payout_amount
    
    # Меняем статус сделки
    deal["status"] = "completed"
    
    # Сообщение покупателю
    await callback.message.edit_text(
        f"🎉 <b>Сделка #{deal_id} успешно завершена!</b>\n"
        f"Вы подтвердили получение товара. Деньги отправлены продавцу на баланс.\n"
        f"Спасибо за доверие нашему автогаранту!"
    )
    
    # Сообщение продавцу
    try:
        await bot.send_message(
            seller_id,
            f"🎉 <b>Сделка #{deal_id} успешно завершена!</b>\n\n"
            f"Покупатель подтвердил получение товара.\n"
            f"💰 На ваш баланс зачислено: <b>{payout_amount} {currency.upper()}</b> (комиссия гаранта: {fee} {currency.upper()}).\n"
            f"Вы можете вывести эти средства в личном кабинете."
        )
    except Exception:
        pass
        
    await callback.answer()

# --- СПОР / АРБИТРАЖ ---

@dp.callback_query(F.data.startswith("deal_dispute_"))
async def process_deal_dispute(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    deal["status"] = "arbitration"
    user_id = callback.from_user.id
    
    await callback.message.reply(
        "⚖️ <b>Вы инициировали спор (Арбитраж) по этой сделке!</b>\n\n"
        "Сделка временно заморожена. Администратор уведомлен и свяжется с вами и вашим "
        "партнером для разбирательства в ближайшее время."
    )
    
    # Оповещаем админа
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ АРБИТРАЖ по сделке #{deal_id}!</b>\n\n"
            f"👤 Инициатор спора (ID): <code>{user_id}</code> (@{callback.from_user.username})\n"
            f"🛒 Покупатель: <code>{deal['buyer_id']}</code>\n"
            f"📦 Продавец: <code>{deal['seller_id']}</code>\n"
            f"💰 Сумма сделки: <b>{deal['amount']} {deal['currency'].upper()}</b>\n"
            f"📦 Товар: [<b>{deal['item_type']}</b>] <code>{deal['item_details']}</code>"
        )
    except Exception:
        pass
        
    await callback.answer()

# --- ОТМЕНА СДЕЛКИ (ЕСЛИ ЕЩЕ НЕ ОПЛАЧЕНА) ---

@dp.callback_query(F.data.startswith("deal_cancel_"))
async def process_deal_cancel(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[2]
    deal = deals_db.get(deal_id)
    
    if not deal:
        await callback.answer("❌ Сделка не найдена.", show_alert=True)
        return
        
    if deal["status"] != "waiting_for_payment":
        await callback.answer("❌ Нельзя отменить уже оплаченную или завершенную сделку!", show_alert=True)
        return
        
    deal["status"] = "canceled"
    
    await callback.message.edit_text(f"❌ Сделка #{deal_id} была успешно отменена одной из сторон.")
    
    # Уведомляем второго участника
    partner_id = deal["seller_id"] if callback.from_user.id == deal["buyer_id"] else deal["buyer_id"]
    try:
        await bot.send_message(partner_id, f"❌ Сделка #{deal_id} была отменена вашим партнером.")
    except Exception:
        pass
        
    await callback.answer()

# --- ПОМОЩЬ И ПОДДЕРЖКА ---

@dp.message(F.text == "ℹ️ Помощь и поддержка")
async def help_message(message: types.Message):
    help_text = (
        f"🤖 <b>Бот-Автогарант</b> — ваш надежный партнер при совершении сделок в сети.\n\n"
        f"<b>Как работает сделка?</b>\n"
        f"1. Один из участников создает сделку по кнопке «Создать сделку».\n"
        f"2. Указывает ID второго участника, выбирает тип товара (NFT, Юзернейм, Анонимный номер) и вводит его детали.\n"
        f"3. Покупатель переводит средства на реквизиты гаранта или <b>оплачивает со своего баланса бота (доступно для TON)</b>.\n"
        f"4. Продавец получает уведомление об оплате и отправляет товар покупателю, после чего нажимает <b>«Подтвердить отправку»</b>.\n"
        f"5. Покупатель проверяет товар и нажимает <b>«Подтвердить получение»</b>.\n"
        f"6. Деньги автоматически переводятся на баланс продавца в боте за вычетом комиссии ({FEE_PERCENT*100}%).\n\n"
        f"<b>Способы оплаты:</b> Telegram Stars ⭐, TON 💎, USDT 💵\n\n"
        f"📞 <b>Техническая поддержка:</b> @GetGarantSupport\n"
        f"🛠 <b>Чат сделок:</b> https://t.me/+9DB_Esznk2U3OGEy"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

# Резервный обработчик сообщений (Срабатывает ТОЛЬКО вне контекста FSM)
@dp.message(StateFilter(None))
async def unknown_message(message: types.Message):
    await message.answer("Неизвестная команда. Пожалуйста, используйте кнопки меню или напишите /start для возврата.")

# --- ЗАПУСК БОТА ---
async def main():
    logger.info("Попытка запуска бота...")
    
    retries = 5
    delay = 3
    for attempt in range(1, retries + 1):
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Вебхук успешно удален, бот готов к приему сообщений!")
            break
        except TelegramNetworkError as e:
            logger.warning(
                f"[Попытка {attempt}/{retries}] Сетевая ошибка при подключении к Telegram API: {e}. "
                f"Повтор через {delay} сек..."
            )
            if attempt == retries:
                logger.error(
                    "❌ Не удалось подключиться к серверам Telegram. "
                    "Пожалуйста, убедитесь, что на вашем устройстве работает интернет (в Termux есть доступ в сеть)."
                )
                return
            await asyncio.sleep(delay)
            delay *= 2

    try:
        logger.info("Запуск polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка во время работы бота: {e}")
    finally:
        logger.info("Закрытие сессии бота...")
        await bot.session.close()
        logger.info("Бот остановлен.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Программа завершена пользователем.")