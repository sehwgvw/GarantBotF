
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# =========================================================
# ЛОГИ
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot_log.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("GARANT_BOT")

# =========================================================
# КОНФИГ ИЗ ВАШЕГО ФАЙЛА
# =========================================================
API_TOKEN = "8928212368:AAE7sKeL47TGOcFCfQk-hDOO2SqJKt0EXow"
ADMIN_ID = 8807653458

TEST_LIST = [8807653458, 123456789]

STARS_RECEIVER = "@GetGarantSupport"
TON_ADDRESS = "UQBOHmUuiMAM0co8xWrfd8AcmbJj_qgSeHHjJYguy4Qmad8t"
USDT_ADDRESS_TRC20 = "TVfg9k1ZofN3eZR3WfYeS81BxJvMkadtdR"
NFT_BLOCKCHAIN_ADDRESS = "UQCR16wYV7uSvVNbGXvjC2THlK8a5nV65X6eVfwGFbkdATg-"

BOT_NAME = "GetGarant bot"
MANAGER_USERNAME = "@GetGarantSupport"

DATA_FILE = "bot_state.json"

# =========================================================
# ПАМЯТЬ / СОСТОЯНИЕ
# =========================================================
users_db: Dict[int, Dict[str, Any]] = {}
deals_db: Dict[str, Dict[str, Any]] = {}
logs_db: list[str] = []
bot_treasury = {"stars": 0.0, "ton": 0.0, "usdt": 0.0}
blocked_users: Set[int] = set()
tester_users: Set[int] = set(TEST_LIST)

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
BOT_USERNAME: Optional[str] = None


# =========================================================
# FSM
# =========================================================
class DealCreation(StatesGroup):
    waiting_for_partner_method = State()
    waiting_for_partner_id = State()
    waiting_for_role = State()
    waiting_for_item_type = State()
    waiting_for_item_details = State()
    waiting_for_currency = State()
    waiting_for_amount = State()
    waiting_for_partner_join = State()


class WithdrawState(StatesGroup):
    waiting_for_wallet = State()
    waiting_for_amount = State()


class AdminState(StatesGroup):
    waiting_for_balance_user_id = State()
    waiting_for_balance_currency = State()
    waiting_for_balance_amount = State()

    waiting_for_tester_user_id = State()
    waiting_for_block_user_id = State()
    waiting_for_unblock_user_id = State()


# =========================================================
# УТИЛИТЫ
# =========================================================
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_currency(value: str) -> str:
    value = (value or "").strip().lower()
    mapping = {
        "stars": "stars",
        "⭐": "stars",
        "star": "stars",
        "ton": "ton",
        "💎": "ton",
        "usdt": "usdt",
        "usd": "usdt",
        "💵": "usdt",
    }
    return mapping.get(value, value)


def human_currency(currency: str) -> str:
    return {"stars": "Stars", "ton": "TON", "usdt": "USDT"}.get(currency, currency.upper())


def currency_emoji(currency: str) -> str:
    return {"stars": "⭐", "ton": "💎", "usdt": "💵"}.get(currency, "🪙")


def safe_username(username: Optional[str]) -> str:
    return f"@{username}" if username and not username.startswith("@") else (username or "—")


def log_event(text: str) -> None:
    logger.info(text)
    logs_db.append(f"[{now_utc()}] {text}")
    if len(logs_db) > 5000:
        del logs_db[:1000]


def log_deal_event(deal_id: str, text: str) -> None:
    log_event(f"Deal #{deal_id}: {text}")
    deal = deals_db.get(deal_id)
    if deal is not None:
        deal.setdefault("history", []).append(f"[{now_utc()}] {text}")
        if len(deal["history"]) > 200:
            deal["history"] = deal["history"][-200:]


def ensure_user(user_id: int, username: Optional[str] = None) -> Dict[str, Any]:
    user = users_db.get(user_id)
    if user is None:
        user = {
            "username": username or "",
            "balances": {"stars": 0.0, "ton": 0.0, "usdt": 0.0},
            "blocked": False,
        }
        users_db[user_id] = user
    else:
        if username:
            user["username"] = username
        user.setdefault("balances", {"stars": 0.0, "ton": 0.0, "usdt": 0.0})
        user.setdefault("blocked", False)
    return user


def user_balance(user_id: int, currency: str) -> float:
    user = ensure_user(user_id)
    return float(user["balances"].get(currency, 0.0))


def add_user_balance(user_id: int, currency: str, amount: float) -> float:
    user = ensure_user(user_id)
    user["balances"][currency] = round(float(user["balances"].get(currency, 0.0)) + amount, 8)
    save_state()
    return user["balances"][currency]


def subtract_user_balance(user_id: int, currency: str, amount: float) -> bool:
    user = ensure_user(user_id)
    current = float(user["balances"].get(currency, 0.0))
    if current < amount:
        return False
    user["balances"][currency] = round(current - amount, 8)
    save_state()
    return True


def treasury_balance(currency: str) -> float:
    return float(bot_treasury.get(currency, 0.0))


def add_treasury(currency: str, amount: float) -> float:
    bot_treasury[currency] = round(float(bot_treasury.get(currency, 0.0)) + amount, 8)
    save_state()
    return bot_treasury[currency]


def subtract_treasury(currency: str, amount: float) -> bool:
    current = float(bot_treasury.get(currency, 0.0))
    if current < amount:
        return False
    bot_treasury[currency] = round(current - amount, 8)
    save_state()
    return True


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_tester(user_id: int) -> bool:
    return user_id in tester_users


def is_blocked(user_id: int) -> bool:
    user = users_db.get(user_id)
    return bool(user and user.get("blocked")) or user_id in blocked_users


def format_amount(value: float) -> str:
    if abs(value - int(value)) < 1e-9:
        return str(int(value))
    return f"{value:.8f}".rstrip("0").rstrip(".")


def build_cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")]
        ]
    )


def build_back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_flow")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def main_menu_keyboard(user_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🤝 Создать сделку"), KeyboardButton(text="👤 Мой кабинет")],
        [KeyboardButton(text="💎 Пополнить"), KeyboardButton(text="📤 Вывести TON")],
        [KeyboardButton(text="ℹ️ Помощь и поддержка")],
    ]
    if user_id is not None and is_admin(user_id):
        rows.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def deal_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 NFT Gift", callback_data="item_type:nft_gift")],
            [InlineKeyboardButton(text="🪪 NFT Username", callback_data="item_type:nft_username")],
            [InlineKeyboardButton(text="🔢 Anonymous Number", callback_data="item_type:anonymous_number")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Stars", callback_data="currency:stars"),
                InlineKeyboardButton(text="💎 TON", callback_data="currency:ton"),
                InlineKeyboardButton(text="💵 USDT", callback_data="currency:usdt"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Я покупатель", callback_data="role:buyer"),
                InlineKeyboardButton(text="📦 Я продавец", callback_data="role:seller"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def partner_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆔 По ID пользователя", callback_data="partner_method:id")],
            [InlineKeyboardButton(text="🔗 По приглашению", callback_data="partner_method:link")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Логи бота", callback_data="admin:logs_bot")],
            [InlineKeyboardButton(text="📦 Логи сделок", callback_data="admin:logs_deals")],
            [InlineKeyboardButton(text="🧪 Добавить в тест-лист", callback_data="admin:add_tester")],
            [InlineKeyboardButton(text="💸 Выдать баланс", callback_data="admin:give_balance")],
            [InlineKeyboardButton(text="⛔ Заблокировать пользователя", callback_data="admin:block_user")],
            [InlineKeyboardButton(text="✅ Разблокировать пользователя", callback_data="admin:unblock_user")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")],
        ]
    )


def admin_currency_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Stars", callback_data=f"{prefix}:currency:stars"),
                InlineKeyboardButton(text="💎 TON", callback_data=f"{prefix}:currency:ton"),
                InlineKeyboardButton(text="💵 USDT", callback_data=f"{prefix}:currency:usdt"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_flow")],
        ]
    )


def deal_status_text(status: str) -> str:
    return {
        "waiting_for_partner": "ожидает партнера",
        "waiting_for_payment": "ожидает оплаты",
        "paid": "оплачена",
        "item_sent": "товар передан",
        "completed": "завершена",
        "cancelled": "отменена",
    }.get(status, status)


def deal_item_text(item_type: str) -> str:
    return {
        "nft_gift": "🎁 NFT Gift",
        "nft_username": "🪪 NFT Username",
        "anonymous_number": "🔢 Anonymous Number",
    }.get(item_type, item_type)


def deal_role_for_user(deal: Dict[str, Any], user_id: int) -> str:
    if deal.get("buyer_id") == user_id:
        return "buyer"
    if deal.get("seller_id") == user_id:
        return "seller"
    return "observer"


def deal_link(deal_id: str) -> str:
    bot_username = BOT_USERNAME or "GetGarantEbot"
    return f"https://t.me/{bot_username}?start=deal_{deal_id}"


def deal_card(deal: Dict[str, Any], user_id: int) -> str:
    role = deal_role_for_user(deal, user_id)
    buyer = deal.get("buyer_id")
    seller = deal.get("seller_id")
    buyer_name = safe_username(ensure_user(buyer).get("username")) if buyer else "—"
    seller_name = safe_username(ensure_user(seller).get("username")) if seller else "—"

    lines = [
        f"✨ <b>Сделка #{deal['id']}</b>",
        "",
        f"👤 <b>Ваша роль:</b> {('Покупатель' if role == 'buyer' else 'Продавец' if role == 'seller' else 'Наблюдатель')}",
        f"📦 <b>Тип товара:</b> {deal_item_text(deal.get('item_type', '—'))}",
        f"📝 <b>Детали:</b> {deal.get('item_details', '—')}",
        f"💠 <b>Валюта:</b> {currency_emoji(deal.get('currency', ''))} {human_currency(deal.get('currency', ''))}",
        f"💰 <b>Сумма:</b> {format_amount(float(deal.get('amount', 0.0)))} {human_currency(deal.get('currency', ''))}",
        f"📊 <b>Статус:</b> {deal_status_text(deal.get('status', ''))}",
        "",
        f"🧾 <b>Покупатель:</b> {buyer_name}",
        f"🧾 <b>Продавец:</b> {seller_name}",
        "",
        f"🛡 <b>{BOT_NAME}</b> работает только через менеджера <b>{MANAGER_USERNAME}</b>.",
    ]
    if deal.get("status") == "waiting_for_partner" and deal.get("partner_link"):
        lines.extend(
            [
                "",
                "🔗 <b>Ссылка-приглашение:</b>",
                f"<code>{deal['partner_link']}</code>",
            ]
        )
    return "\n".join(lines)


def get_deal_keyboard(deal: Dict[str, Any], user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    role = deal_role_for_user(deal, user_id)
    is_tester_user = is_tester(user_id)
    status = deal.get("status")

    if status == "waiting_for_payment":
        if role == "buyer":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"💸 Оплатить {format_amount(float(deal['amount']))} {human_currency(deal['currency'])}",
                        callback_data=f"deal:pay:{deal['id']}",
                    )
                ]
            )
            if is_tester_user:
                buttons.append(
                    [InlineKeyboardButton(text="🧪 Тест-оплата", callback_data=f"deal:test_pay:{deal['id']}")]
                )
            buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"deal:cancel:{deal['id']}")])
        elif role == "seller":
            if is_tester_user:
                buttons.append(
                    [InlineKeyboardButton(text="🧪 Тест-передача NFT менеджеру", callback_data=f"deal:test_send:{deal['id']}")]
                )
            buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"deal:cancel:{deal['id']}")])

    elif status == "paid":
        if role == "seller":
            if is_tester_user:
                buttons.append(
                    [InlineKeyboardButton(text="🧪 Передать NFT менеджеру", callback_data=f"deal:test_send:{deal['id']}")]
                )
            else:
                buttons.append(
                    [InlineKeyboardButton(text="📦 Подтвердить отправку", callback_data=f"deal:send:{deal['id']}")]
                )
            buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"deal:cancel:{deal['id']}")])

    elif status == "item_sent":
        if role == "buyer":
            buttons.append(
                [InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"deal:confirm:{deal['id']}")]
            )

    elif status in {"completed", "cancelled"}:
        pass

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else InlineKeyboardMarkup(inline_keyboard=[])


def make_log_file(lines: list[str], filename: str) -> BufferedInputFile:
    data = "\n".join(lines).encode("utf-8")
    return BufferedInputFile(data, filename=filename)


def chunk_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            cut = text.rfind("\n", start, end)
            if cut > start:
                end = cut
        parts.append(text[start:end])
        start = end
    return [p for p in parts if p.strip()]


def save_state() -> None:
    payload = {
        "users_db": {str(k): v for k, v in users_db.items()},
        "deals_db": {str(k): v for k, v in deals_db.items()},
        "logs_db": logs_db[-5000:],
        "bot_treasury": bot_treasury,
        "blocked_users": list(blocked_users),
        "tester_users": list(tester_users),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_state() -> None:
    global users_db, deals_db, logs_db, bot_treasury, blocked_users, tester_users
    if not os.path.exists(DATA_FILE):
        tester_users.update(TEST_LIST)
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        users_db = {int(k): v for k, v in payload.get("users_db", {}).items()}
        deals_db = {str(k): v for k, v in payload.get("deals_db", {}).items()}
        logs_db = list(payload.get("logs_db", []))
        bot_treasury.update(payload.get("bot_treasury", {}))
        blocked_users = {int(x) for x in payload.get("blocked_users", [])}
        tester_users = {int(x) for x in payload.get("tester_users", TEST_LIST)}
        tester_users.update(TEST_LIST)
    except Exception as e:
        logger.error(f"Failed to load state: {e}")
        tester_users.update(TEST_LIST)


def append_admin_note(text: str) -> None:
    log_event(f"ADMIN: {text}")
    save_state()


async def delete_message_safe(message: Optional[types.Message]) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def delete_by_id_safe(chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def replace_step_message(
    *,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> types.Message:
    data = await state.get_data()
    old_prompt_id = data.get("prompt_message_id")
    if old_prompt_id:
        await delete_by_id_safe(chat_id, old_prompt_id)

    sent = await bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )
    await state.update_data(prompt_message_id=sent.message_id)
    return sent


async def clear_flow_visuals(message: Optional[types.Message], state: FSMContext, remove_prompt: bool = True) -> None:
    data = await state.get_data()
    if remove_prompt:
        prompt_id = data.get("prompt_message_id")
        if prompt_id:
            await delete_by_id_safe(message.chat.id if message else 0, prompt_id)
    if message:
        await delete_message_safe(message)


def deal_exists(deal_id: str) -> bool:
    return deal_id in deals_db


def create_deal_base(creator_id: int, creator_role: str, partner_method: str) -> Dict[str, Any]:
    deal_id = str(uuid.uuid4())[:8]
    deal = {
        "id": deal_id,
        "creator_id": creator_id,
        "creator_role": creator_role,
        "partner_method": partner_method,
        "buyer_id": creator_id if creator_role == "buyer" else None,
        "seller_id": creator_id if creator_role == "seller" else None,
        "partner_id": None,
        "item_type": None,
        "item_details": None,
        "currency": None,
        "amount": 0.0,
        "status": "waiting_for_partner" if partner_method == "link" else "draft",
        "partner_link": None,
        "history": [],
        "created_at": now_utc(),
    }
    deals_db[deal_id] = deal
    save_state()
    return deal


def finalize_deal(deal: Dict[str, Any], partner_id: Optional[int]) -> None:
    if partner_id:
        deal["partner_id"] = partner_id

    if deal["creator_role"] == "buyer":
        deal["buyer_id"] = deal["creator_id"]
        deal["seller_id"] = partner_id
    else:
        deal["seller_id"] = deal["creator_id"]
        deal["buyer_id"] = partner_id

    deal["status"] = "waiting_for_payment"
    save_state()


def build_payment_notice(deal: Dict[str, Any]) -> str:
    currency = deal["currency"]
    amount = float(deal["amount"])
    reserve = treasury_balance(currency)
    return (
        f"💳 <b>Оплата по сделке #{deal['id']}</b>\n\n"
        f"К оплате: <b>{format_amount(amount)} {human_currency(currency)}</b>\n"
        f"Резерв бота: <b>{format_amount(reserve)} {human_currency(currency)}</b>\n\n"
        f"Платёж будет проведён только если на внутреннем балансе бота достаточно средств.\n"
        f"Если резерва не хватает — оплату подтвердить нельзя."
    )


async def notify_participants(deal: Dict[str, Any], text: str) -> None:
    for uid in [deal.get("buyer_id"), deal.get("seller_id")]:
        if uid:
            try:
                await bot.send_message(uid, text, reply_markup=get_deal_keyboard(deal, uid))
            except Exception:
                pass


async def send_deal_card_to_user(deal: Dict[str, Any], user_id: int, extra_text: str = "") -> None:
    text = deal_card(deal, user_id)
    if extra_text:
        text = extra_text + "\n\n" + text
    await bot.send_message(
        user_id,
        text,
        reply_markup=get_deal_keyboard(deal, user_id),
        disable_web_page_preview=True,
    )


def deal_manager_text() -> str:
    return (
        f"🛡 <b>{BOT_NAME}</b>\n"
        f"👮 Менеджер: <b>{MANAGER_USERNAME}</b>\n\n"
        f"— Сделки создаются через меню.\n"
        f"— Устаревшие шаги удаляются автоматически.\n"
        f"— Для тестов доступны отдельные кнопки.\n"
        f"— Оплата подтверждается только при наличии внутреннего резерва бота.\n"
    )


def prepare_support_text() -> str:
    return (
        f"🧩 <b>Поддержка {BOT_NAME}</b>\n\n"
        f"Менеджер: {MANAGER_USERNAME}\n\n"
        f"Реквизиты для пополнения внутреннего резерва:\n"
        f"• TON: <code>{TON_ADDRESS}</code>\n"
        f"• USDT TRC20: <code>{USDT_ADDRESS_TRC20}</code>\n"
        f"• NFT wallet: <code>{NFT_BLOCKCHAIN_ADDRESS}</code>\n"
        f"• Stars: <code>{STARS_RECEIVER}</code>\n\n"
        f"После пополнения резерва бот сможет подтверждать оплаты по сделкам."
    )


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID and is_blocked(user.id):
            if isinstance(event, types.Message):
                await event.answer("⛔ Вы заблокированы и не можете пользоваться ботом.")
            elif isinstance(event, types.CallbackQuery):
                await event.answer("⛔ Вы заблокированы.", show_alert=True)
            return
        return await handler(event, data)


dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())


# =========================================================
# / START / МЕНЮ
# =========================================================
@dp.message(F.text == "ℹ️ Помощь и поддержка")
async def help_handler(message: types.Message):
    await message.answer(prepare_support_text(), reply_markup=main_menu_keyboard(message.from_user.id))


@dp.message(F.text == "👤 Мой кабинет")
async def profile_handler(message: types.Message):
    user = ensure_user(message.from_user.id, message.from_user.username)
    text = (
        f"👤 <b>Ваш кабинет</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Username: {safe_username(user.get('username'))}\n"
        f"⭐ Stars: <b>{format_amount(user_balance(message.from_user.id, 'stars'))}</b>\n"
        f"💎 TON: <b>{format_amount(user_balance(message.from_user.id, 'ton'))}</b>\n"
        f"💵 USDT: <b>{format_amount(user_balance(message.from_user.id, 'usdt'))}</b>\n"
        f"🧪 Tester: <b>{'да' if is_tester(message.from_user.id) else 'нет'}</b>\n"
        f"⛔ Blocked: <b>{'да' if is_blocked(message.from_user.id) else 'нет'}</b>"
    )
    await message.answer(text, reply_markup=main_menu_keyboard(message.from_user.id))


@dp.message(F.text == "💎 Пополнить")
async def topup_info(message: types.Message):
    await message.answer(prepare_support_text(), reply_markup=main_menu_keyboard(message.from_user.id))


@dp.message(F.text == "🛠 Админ-панель")
async def admin_panel_open(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Только для администратора.", reply_markup=main_menu_keyboard(message.from_user.id))
    await message.answer("🛠 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_panel_keyboard())


# =========================================================
# ОЧИСТКА / ОТМЕНА
# =========================================================
@dp.callback_query(F.data == "cancel_flow")
async def cancel_flow(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("✨ Действие отменено.", reply_markup=main_menu_keyboard(callback.from_user.id))


@dp.callback_query(F.data == "back_flow")
async def back_flow(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    prev = data.get("prev_step")
    if prev == "partner_method":
        await state.set_state(DealCreation.waiting_for_partner_method)
        await replace_step_message(
            chat_id=callback.message.chat.id,
            state=state,
            text="🤝 <b>Как добавить партнёра?</b>",
            reply_markup=partner_method_keyboard(),
        )
    else:
        await state.clear()
        await callback.message.answer("Главное меню.", reply_markup=main_menu_keyboard(callback.from_user.id))
    try:
        await callback.message.delete()
    except Exception:
        pass


@dp.message(F.text == "❌ Отмена", StateFilter("*"))
async def cancel_text(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✨ Действие отменено.", reply_markup=main_menu_keyboard(message.from_user.id))


# =========================================================
# СОЗДАНИЕ СДЕЛКИ
# =========================================================
@dp.message(F.text == "🤝 Создать сделку")
async def create_deal_entry(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(DealCreation.waiting_for_partner_method)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text=(
            f"🤝 <b>Создание сделки</b>\n\n"
            f"Выберите, как хотите добавить партнёра:\n"
            f"• по ID пользователя\n"
            f"• по приглашению\n\n"
            f"Шаги будут выводиться красиво и без мусора."
        ),
        reply_markup=partner_method_keyboard(),
    )


@dp.callback_query(F.data.startswith("partner_method:"))
async def partner_method_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    method = callback.data.split(":", 1)[1]
    await state.update_data(partner_method=method, prev_step="partner_method")
    try:
        await callback.message.delete()
    except Exception:
        pass

    if method == "id":
        await state.set_state(DealCreation.waiting_for_partner_id)
        await replace_step_message(
            chat_id=callback.message.chat.id,
            state=state,
            text="🆔 <b>Введите ID партнёра</b>\n\nМожно отправить только числом.",
            reply_markup=build_cancel_inline(),
        )
    else:
        await state.set_state(DealCreation.waiting_for_role)
        await replace_step_message(
            chat_id=callback.message.chat.id,
            state=state,
            text="🎭 <b>Выберите свою роль в сделке</b>",
            reply_markup=role_keyboard(),
        )


@dp.message(StateFilter(DealCreation.waiting_for_partner_id))
async def partner_id_entered(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        return await message.answer("❌ ID должен быть числом. Попробуйте ещё раз.")
    partner_id = int(text)
    if partner_id == message.from_user.id:
        return await message.answer("❌ Нельзя указать самого себя.")
    ensure_user(partner_id)
    await state.update_data(partner_id=partner_id)
    await state.set_state(DealCreation.waiting_for_role)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text="🎭 <b>Выберите свою роль в сделке</b>",
        reply_markup=role_keyboard(),
    )
    await delete_message_safe(message)


@dp.callback_query(F.data.startswith("role:"))
async def role_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    role = callback.data.split(":", 1)[1]
    await state.update_data(my_role=role)
    await state.set_state(DealCreation.waiting_for_item_type)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text=(
            f"📦 <b>Что будет товаром в вашей сделке?</b>\n\n"
            f"Выберите тип:\n"
            f"— NFT Gift\n"
            f"— NFT Username\n"
            f"— Anonymous Number"
        ),
        reply_markup=deal_type_keyboard(),
    )


@dp.callback_query(F.data.startswith("item_type:"))
async def item_type_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    item_type = callback.data.split(":", 1)[1]
    await state.update_data(item_type=item_type)
    await state.set_state(DealCreation.waiting_for_item_details)
    try:
        await callback.message.delete()
    except Exception:
        pass

    prompts = {
        "nft_gift": "🎁 <b>Введите детали NFT Gift</b>\n\nНапример: название, ссылка, номер подарка, описание.",
        "nft_username": "🪪 <b>Введите детали NFT Username</b>\n\nНапример: @username или ссылка.",
        "anonymous_number": "🔢 <b>Введите детали Anonymous Number</b>\n\nНапример: номер, формат, комментарий.",
    }
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text=prompts.get(item_type, "📝 <b>Введите детали товара</b>"),
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(DealCreation.waiting_for_item_details))
async def item_details_entered(message: types.Message, state: FSMContext):
    details = (message.text or "").strip()
    if not details:
        return await message.answer("❌ Пустое сообщение. Опишите товар ещё раз.")

    await state.update_data(item_details=details)
    await state.set_state(DealCreation.waiting_for_currency)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text=(
            f"💱 <b>Выберите валюту оплаты</b>\n\n"
            f"Оплата будет подтверждаться только если внутренний резерв бота достаточен."
        ),
        reply_markup=currency_keyboard(),
    )
    await delete_message_safe(message)


@dp.callback_query(F.data.startswith("currency:"))
async def currency_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    currency = callback.data.split(":", 1)[1]
    await state.update_data(currency=currency)
    await state.set_state(DealCreation.waiting_for_amount)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text=(
            f"💰 <b>Введите сумму сделки</b>\n\n"
            f"Можно целое число или дробь."
        ),
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(DealCreation.waiting_for_amount))
async def amount_entered(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ Введите корректную сумму больше нуля.")

    data = await state.get_data()
    partner_method = data.get("partner_method")
    my_role = data.get("my_role")
    item_type = data.get("item_type")
    item_details = data.get("item_details")
    currency = data.get("currency")
    partner_id = data.get("partner_id")

    deal = create_deal_base(message.from_user.id, my_role, partner_method)
    deal["item_type"] = item_type
    deal["item_details"] = item_details
    deal["currency"] = currency
    deal["amount"] = amount

    if partner_method == "id":
        finalize_deal(deal, partner_id)
        log_deal_event(deal["id"], f"Deal created by {message.from_user.id} via ID method as {my_role}")
    else:
        deal["status"] = "waiting_for_partner"
        deal["partner_link"] = deal_link(deal["id"])
        save_state()
        log_deal_event(deal["id"], f"Deal created by {message.from_user.id} via link as {my_role}")

    await state.clear()
    await delete_message_safe(message)

    if partner_method == "id":
        creator_text = (
            f"✅ <b>Сделка #{deal['id']} создана</b>\n\n"
            f"{deal_card(deal, message.from_user.id)}"
        )
        await message.answer(creator_text, reply_markup=main_menu_keyboard(message.from_user.id))
        if partner_id:
            await send_deal_card_to_user(
                deal,
                partner_id,
                extra_text=f"✨ <b>Вам пришло приглашение в сделку #{deal['id']}</b>",
            )
        return

    invite_link = deal["partner_link"]
    creator_text = (
        f"✅ <b>Сделка #{deal['id']} создана</b>\n\n"
        f"Пригласительная ссылка для второго участника:\n<code>{invite_link}</code>\n\n"
        f"Передайте её партнёру, после входа сделка активируется автоматически."
    )
    await message.answer(creator_text, reply_markup=main_menu_keyboard(message.from_user.id))


# =========================================================
# ВСТУПЛЕНИЕ ПО ССЫЛКЕ
# =========================================================
async def activate_deal_if_needed(deal: Dict[str, Any], joiner_id: int) -> None:
    if deal.get("status") != "waiting_for_partner":
        return
    creator_role = deal.get("creator_role")
    if creator_role == "buyer" and deal.get("seller_id") is None:
        deal["seller_id"] = joiner_id
    elif creator_role == "seller" and deal.get("buyer_id") is None:
        deal["buyer_id"] = joiner_id
    deal["partner_id"] = joiner_id
    deal["status"] = "waiting_for_payment"
    save_state()
    log_deal_event(deal["id"], f"Deal activated by {joiner_id}")


@dp.message(CommandStart())
async def start_command(message: types.Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("deal_"):
        deal_id = parts[1].replace("deal_", "").strip()
        deal = deals_db.get(deal_id)
        if deal:
            # Если в сделке есть место для партнёра — присоединим автоматически
            if deal.get("status") == "waiting_for_partner":
                if deal.get("creator_role") == "buyer" and message.from_user.id != deal.get("creator_id"):
                    deal["seller_id"] = message.from_user.id
                    deal["partner_id"] = message.from_user.id
                    deal["status"] = "waiting_for_payment"
                    save_state()
                    log_deal_event(deal_id, f"User {message.from_user.id} joined as seller")
                elif deal.get("creator_role") == "seller" and message.from_user.id != deal.get("creator_id"):
                    deal["buyer_id"] = message.from_user.id
                    deal["partner_id"] = message.from_user.id
                    deal["status"] = "waiting_for_payment"
                    save_state()
                    log_deal_event(deal_id, f"User {message.from_user.id} joined as buyer")

                await message.answer(
                    f"🎉 Вы присоединились к сделке #{deal_id}.\n\n{deal_card(deal, message.from_user.id)}",
                    reply_markup=main_menu_keyboard(message.from_user.id),
                    disable_web_page_preview=True,
                )
                await notify_participants(
                    deal,
                    f"✨ <b>Сделка #{deal_id} активирована</b>\n\n{deal_card(deal, message.from_user.id)}",
                )
                return

            await message.answer(
                f"ℹ️ <b>Сделка #{deal_id}</b>\n\n{deal_card(deal, message.from_user.id)}",
                reply_markup=main_menu_keyboard(message.from_user.id),
                disable_web_page_preview=True,
            )
            return

    await message.answer(
        f"👑 <b>Добро пожаловать в {BOT_NAME}</b>\n\n{deal_manager_text()}",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# =========================================================
# СДЕЛКИ: ОПЛАТА / ПЕРЕДАЧА / ПОДТВЕРЖДЕНИЯ / ОТМЕНА
# =========================================================
@dp.callback_query(F.data.startswith("deal:pay:"))
async def deal_pay(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if deal.get("buyer_id") != callback.from_user.id:
        return await callback.answer("⛔ Это не ваша сделка.", show_alert=True)
    if deal.get("status") != "waiting_for_payment":
        return await callback.answer("ℹ️ Оплата уже обработана или сделка неактивна.", show_alert=True)

    currency = deal["currency"]
    amount = float(deal["amount"])
    reserve = treasury_balance(currency)
    if reserve < amount:
        return await callback.answer(
            f"❌ Недостаточно резерва бота: {format_amount(reserve)} {human_currency(currency)}",
            show_alert=True,
        )

    subtract_treasury(currency, amount)
    deal["status"] = "paid"
    save_state()
    log_deal_event(deal_id, f"Payment confirmed by {callback.from_user.id}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await notify_participants(
        deal,
        f"✅ <b>Оплата по сделке #{deal_id} подтверждена</b>\n\n{deal_card(deal, callback.from_user.id)}",
    )


@dp.callback_query(F.data.startswith("deal:test_pay:"))
async def deal_test_pay(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if deal.get("buyer_id") != callback.from_user.id and not is_tester(callback.from_user.id):
        return await callback.answer("⛔ Только тестировщики.", show_alert=True)
    if deal.get("status") != "waiting_for_payment":
        return await callback.answer("ℹ️ Сделка не ждёт оплату.", show_alert=True)

    deal["status"] = "paid"
    save_state()
    log_deal_event(deal_id, f"Test payment confirmed by {callback.from_user.id}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await notify_participants(
        deal,
        f"🧪 <b>Тест-оплата по сделке #{deal_id}</b>\n\nПользователь нажал тестовую оплату.",
    )


@dp.callback_query(F.data.startswith("deal:send:"))
async def deal_send(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if deal.get("seller_id") != callback.from_user.id:
        return await callback.answer("⛔ Это не ваша сделка.", show_alert=True)
    if deal.get("status") != "paid":
        return await callback.answer("ℹ️ Сначала нужна оплата.", show_alert=True)

    deal["status"] = "item_sent"
    save_state()
    log_deal_event(deal_id, f"Item sent by {callback.from_user.id}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await notify_participants(
        deal,
        f"📦 <b>Товар по сделке #{deal_id} передан</b>\n\n{deal_card(deal, callback.from_user.id)}",
    )


@dp.callback_query(F.data.startswith("deal:test_send:"))
async def deal_test_send(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if deal.get("seller_id") != callback.from_user.id and not is_tester(callback.from_user.id):
        return await callback.answer("⛔ Только тестировщики.", show_alert=True)
    if deal.get("status") not in {"waiting_for_payment", "paid"}:
        return await callback.answer("ℹ️ Сейчас нельзя выполнить тестовую передачу.", show_alert=True)

    deal["status"] = "item_sent"
    save_state()
    log_deal_event(deal_id, f"Test item transfer by {callback.from_user.id}")

    try:
        await callback.message.delete()
    except Exception:
        pass

    await notify_participants(
        deal,
        f"🧪 <b>Тестовая передача NFT менеджеру по сделке #{deal_id}</b>\n\n"
        f"NFT считается переданным менеджеру <b>{MANAGER_USERNAME}</b>.\n"
        f"Покупателю отображается, что передача выполнена через менеджера.",
    )


@dp.callback_query(F.data.startswith("deal:confirm:"))
async def deal_confirm(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if deal.get("buyer_id") != callback.from_user.id:
        return await callback.answer("⛔ Это не ваша сделка.", show_alert=True)
    if deal.get("status") != "item_sent":
        return await callback.answer("ℹ️ Подтверждать пока нечего.", show_alert=True)

    deal["status"] = "completed"
    save_state()
    log_deal_event(deal_id, f"Deal completed by {callback.from_user.id}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await notify_participants(
        deal,
        f"✅ <b>Сделка #{deal_id} завершена</b>\n\nОбе стороны подтвердили завершение.",
    )


@dp.callback_query(F.data.startswith("deal:cancel:"))
async def deal_cancel(callback: types.CallbackQuery):
    await callback.answer()
    deal_id = callback.data.split(":")[-1]
    deal = deals_db.get(deal_id)
    if not deal:
        return await callback.message.answer("❌ Сделка не найдена.")
    if callback.from_user.id not in {deal.get("buyer_id"), deal.get("seller_id"), ADMIN_ID}:
        return await callback.answer("⛔ Нельзя отменить чужую сделку.", show_alert=True)

    if deal.get("status") == "completed":
        return await callback.answer("ℹ️ Завершённую сделку отменить нельзя.", show_alert=True)

    deal["status"] = "cancelled"
    save_state()
    log_deal_event(deal_id, f"Deal cancelled by {callback.from_user.id}")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await notify_participants(
        deal,
        f"⛔ <b>Сделка #{deal_id} отменена</b>",
    )


# =========================================================
# ВЫВОД TON
# =========================================================
@dp.message(F.text == "📤 Вывести TON")
async def withdraw_start(message: types.Message, state: FSMContext):
    bal = user_balance(message.from_user.id, "ton")
    if bal <= 0:
        return await message.answer("❌ На вашем балансе TON нет средств.", reply_markup=main_menu_keyboard(message.from_user.id))
    await state.set_state(WithdrawState.waiting_for_wallet)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text="🏦 <b>Введите адрес TON-кошелька</b>",
        reply_markup=build_cancel_inline(),
    )
    await delete_message_safe(message)


@dp.message(StateFilter(WithdrawState.waiting_for_wallet))
async def withdraw_wallet(message: types.Message, state: FSMContext):
    wallet = (message.text or "").strip()
    if len(wallet) < 10:
        return await message.answer("❌ Адрес слишком короткий.")
    await state.update_data(wallet=wallet)
    await state.set_state(WithdrawState.waiting_for_amount)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text="💰 <b>Введите сумму вывода</b>",
        reply_markup=build_cancel_inline(),
    )
    await delete_message_safe(message)


@dp.message(StateFilter(WithdrawState.waiting_for_amount))
async def withdraw_amount(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ Введите корректную сумму.")

    data = await state.get_data()
    wallet = data.get("wallet", "")
    if user_balance(message.from_user.id, "ton") < amount:
        return await message.answer("❌ Сумма превышает ваш баланс TON.")

    subtract_user_balance(message.from_user.id, "ton", amount)
    await state.clear()
    await delete_message_safe(message)
    await message.answer(
        f"✅ Заявка на вывод <b>{format_amount(amount)} TON</b> на адрес:\n<code>{wallet}</code>\n\n"
        f"Принята в обработку.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


# =========================================================
# АДМИНКА
# =========================================================
@dp.callback_query(F.data == "admin:back")
async def admin_back(callback: types.CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_panel_keyboard())


@dp.callback_query(F.data == "admin:logs_bot")
async def admin_logs_bot(callback: types.CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    try:
        with open("bot_log.log", "r", encoding="utf-8") as f:
            lines = f.readlines()[-400:]
        if not lines:
            return await callback.message.answer("Логов пока нет.")
        file = make_log_file(lines, "bot_logs.txt")
        await callback.message.answer_document(file, caption="📄 Последние строки логов бота")
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось получить логи: {e}")


@dp.callback_query(F.data == "admin:logs_deals")
async def admin_logs_deals(callback: types.CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    if not logs_db:
        return await callback.message.answer("Логи сделок пустые.")
    file = make_log_file(logs_db[-1000:], "deal_logs.txt")
    await callback.message.answer_document(file, caption="📦 Логи сделок")


@dp.callback_query(F.data == "admin:add_tester")
async def admin_add_tester(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_for_tester_user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text="🧪 <b>Введите ID пользователя для тест-листа</b>",
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(AdminState.waiting_for_tester_user_id))
async def admin_add_tester_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        return await message.answer("❌ ID должен быть числом.")
    uid = int(text)
    tester_users.add(uid)
    ensure_user(uid)
    save_state()
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{uid}</code> добавлен в тест-лист.", reply_markup=admin_panel_keyboard())


@dp.callback_query(F.data == "admin:give_balance")
async def admin_give_balance(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_for_balance_user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text="💸 <b>Введите ID пользователя, которому выдать баланс</b>",
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(AdminState.waiting_for_balance_user_id))
async def admin_balance_user_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        return await message.answer("❌ ID должен быть числом.")
    uid = int(text)
    ensure_user(uid)
    await state.update_data(balance_user_id=uid)
    await state.set_state(AdminState.waiting_for_balance_currency)
    await replace_step_message(
        chat_id=message.chat.id,
        state=state,
        text="💠 <b>Выберите валюту</b>",
        reply_markup=admin_currency_keyboard("admin_balance"),
    )
    await delete_message_safe(message)


@dp.callback_query(F.data.startswith("admin_balance:currency:"))
async def admin_balance_currency(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    currency = callback.data.split(":")[-1]
    await state.update_data(balance_currency=currency)
    await state.set_state(AdminState.waiting_for_balance_amount)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text="💸 <b>Введите сумму</b>\n\nМожно с точкой.",
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(AdminState.waiting_for_balance_amount))
async def admin_balance_amount(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
        if amount == 0:
            raise ValueError
    except Exception:
        return await message.answer("❌ Введите корректную сумму.")

    data = await state.get_data()
    uid = int(data["balance_user_id"])
    currency = data["balance_currency"]
    add_user_balance(uid, currency, amount)
    append_admin_note(f"Added {amount} {currency} to user {uid}")
    await state.clear()
    await delete_message_safe(message)
    await message.answer(
        f"✅ Пользователю <code>{uid}</code> выдано <b>{format_amount(amount)} {human_currency(currency)}</b>.",
        reply_markup=admin_panel_keyboard(),
    )


@dp.callback_query(F.data == "admin:block_user")
async def admin_block_user(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_for_block_user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text="⛔ <b>Введите ID пользователя для блокировки</b>",
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(AdminState.waiting_for_block_user_id))
async def admin_block_user_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        return await message.answer("❌ ID должен быть числом.")
    uid = int(text)
    ensure_user(uid)
    users_db[uid]["blocked"] = True
    blocked_users.add(uid)
    save_state()
    append_admin_note(f"Blocked user {uid}")
    await state.clear()
    await delete_message_safe(message)
    await message.answer(f"⛔ Пользователь <code>{uid}</code> заблокирован.", reply_markup=admin_panel_keyboard())


@dp.callback_query(F.data == "admin:unblock_user")
async def admin_unblock_user(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_for_unblock_user_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await replace_step_message(
        chat_id=callback.message.chat.id,
        state=state,
        text="✅ <b>Введите ID пользователя для разблокировки</b>",
        reply_markup=build_cancel_inline(),
    )


@dp.message(StateFilter(AdminState.waiting_for_unblock_user_id))
async def admin_unblock_user_id(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit():
        return await message.answer("❌ ID должен быть числом.")
    uid = int(text)
    ensure_user(uid)
    users_db[uid]["blocked"] = False
    blocked_users.discard(uid)
    save_state()
    append_admin_note(f"Unblocked user {uid}")
    await state.clear()
    await delete_message_safe(message)
    await message.answer(f"✅ Пользователь <code>{uid}</code> разблокирован.", reply_markup=admin_panel_keyboard())


# =========================================================
# СТАРТ
# =========================================================
async def main():
    load_state()
    ensure_user(ADMIN_ID)
    tester_users.update(TEST_LIST)
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    log_event(f"Starting {BOT_NAME}")
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as e:
        logger.error(f"Telegram network error: {e}")
        raise
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
