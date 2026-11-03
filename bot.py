"""
Telegram e-commerce bot (aiogram 3.x + aiosqlite).

Render.com:
    Build Command : pip install -r requirements.txt
    Start Command : python bot.py

Bot long polling bilan ishlaydi. Render Web Service (Free) uchun PORT
o'zgaruvchisi bo'lsa, kichik health-check server ham ishga tushadi.
"""

import asyncio
import logging
import os
import time
from html import escape
from typing import Any, Awaitable, List, Optional, Tuple
from urllib.parse import quote

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiohttp import web

# ============================================================================
# CONFIG
# Render'da bu qiymatlarni Environment Variables orqali bering
# (tokenni GitHub'ga yozmaslik uchun). Bo'lmasa, pastdagi default ishlatiladi.
# ============================================================================
BOT_TOKEN = "9236599500:AAHPb9C7IEcW5yWFRUUm62-gpM-EooS-dvA"
CHANNEL_ID ="-1004410450381"
SUPER_ADMIN_ID = "8531801779"
SALES_ADMIN = "servis_istanbul"  
DB_PATH = os.getenv("DB_PATH", "shop.db")

MAX_PHOTOS = 10
MAX_TITLE_LEN = 800
MAX_PRICE_LEN = 40

BTN_DONE = "✅ Rasmlar tayyor"
BTN_CANCEL = "❌ Bekor qilish"

SALES_USERNAME = SALES_ADMIN.strip().lstrip("@")
_channel = CHANNEL_ID.strip()
if _channel.lstrip("-").isdigit():
    CHANNEL_CHAT = int(_channel)
elif _channel.startswith("@"):
    CHANNEL_CHAT = _channel
else:
    CHANNEL_CHAT = "@" + _channel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("shop_bot")

admin_router = Router(name="admin")
admin_router.message.filter(F.from_user.id == SUPER_ADMIN_ID, F.chat.type == "private")
admin_router.callback_query.filter(F.from_user.id == SUPER_ADMIN_ID)

public_router = Router(name="public")
public_router.message.filter(F.chat.type == "private")

photo_lock = asyncio.Lock()
publish_lock = asyncio.Lock()


# ============================================================================
# FSM
# ============================================================================
class NewPost(StatesGroup):
    photos = State()
    title = State()
    price = State()
    preview = State()


class SoldFlow(StatesGroup):
    code = State()


# ============================================================================
# DATABASE
# ============================================================================
async def init_db() -> None:
    folder = os.path.dirname(DB_PATH) or "."
    os.makedirs(folder, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                ch_msg_id INTEGER,
                btn_msg_id INTEGER,
                is_sold INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()


async def product_exists(code: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM products WHERE id = ?", (code,)) as cur:
            return await cur.fetchone() is not None


async def get_product(code: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE id = ?", (code,)) as cur:
            return await cur.fetchone()


async def save_product(
    code: int, title: str, price: str, ch_msg_id: int, btn_msg_id: int
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO products (id, title, price, ch_msg_id, btn_msg_id, is_sold) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (code, title, price, ch_msg_id, btn_msg_id),
        )
        await db.commit()


async def set_sold(code: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (code,))
        await db.commit()


async def generate_code() -> int:
    """Vaqt belgisiga asoslangan 6 xonali noyob kod."""
    while True:
        code = int(time.time() * 1000) % 900000 + 100000
        if not await product_exists(code):
            return code
        await asyncio.sleep(0.01)


# ============================================================================
# HELPERS: narx, matn, tugmalar
# ============================================================================
def parse_price(raw: str) -> Optional[Tuple[Optional[str], str]]:
    """
    "250 000"            -> (None, "250 000")
    "300 000 | 250 000"  -> ("300 000", "250 000")
    Noto'g'ri format bo'lsa None qaytaradi.
    """
    if "\n" in raw:
        return None
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) > 2 or any(not p for p in parts):
        return None
    for p in parts:
        if len(p) > MAX_PRICE_LEN or not any(ch.isdigit() for ch in p):
            return None
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def format_price_html(stored: str) -> str:
    parsed = parse_price(stored)
    if parsed is None:
        return escape(stored, quote=False)
    old, new = parsed
    if old:
        return "<s>" + escape(old, quote=False) + "</s> " + escape(new, quote=False)
    return escape(new, quote=False)


def current_price(stored: str) -> str:
    parsed = parse_price(stored)
    if parsed is None:
        return stored
    return parsed[1]


def build_caption(title: str, price: str, code: int, sold: bool = False) -> str:
    parts: List[str] = []
    if sold:
        parts.append("<b>🔴 [SOTILDI ❌]</b>")
    parts.append(escape(title, quote=False))
    parts.append("💰 Narxi: " + format_price_html(price))
    parts.append("🆔 Kod: <code>" + str(code) + "</code>")
    return "\n\n".join(parts)


def build_button_text(code: int, sold: bool = False) -> str:
    if sold:
        return "🆔 Kod: <code>" + str(code) + "</code>\n🔴 <b>[SOTILDI ❌]</b>"
    return (
        "🆔 Kod: <code>" + str(code) + "</code>\n"
        "👇 Buyurtma berish uchun tugmani bosing"
    )


def build_order_url(title: str, price: str, code: int) -> str:
    lines = [ln.strip() for ln in title.splitlines() if ln.strip()]
    first_line = lines[0] if lines else title.strip()
    if len(first_line) > 60:
        first_line = first_line[:59] + "…"
    text = (
        "Assalomu alaykum! Men quyidagi mahsulotni buyurtma qilmoqchiman:\n\n"
        "📦 Mahsulot: " + first_line + "\n"
        "🆔 Kod: " + str(code) + "\n"
        "💰 Narxi: " + current_price(price)
    )
    return "https://t.me/" + SALES_USERNAME + "?text=" + quote(text, safe="")


def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi post chiqarish", callback_data="new_post")],
            [
                InlineKeyboardButton(
                    text="❌ Tovar holatini 'SOTILDI' qilish", callback_data="mark_sold"
                )
            ],
        ]
    )


def photos_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_DONE)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Kanalga chiqarish", callback_data="publish"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_post"),
            ]
        ]
    )


def order_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛒 Buyurtma berish", url=url)]]
    )


def sold_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 MAHSULOT SOTILDI", callback_data="sold_noop")]
        ]
    )


async def ack(callback: CallbackQuery, text: Optional[str] = None, alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=alert)
    except TelegramAPIError as exc:
        logger.warning("callback.answer xatosi: %s", exc)


async def send_menu(bot: Bot, chat_id: int, text: str = "📋 <b>Admin menyusi</b>") -> None:
    await bot.send_message(chat_id, text, reply_markup=menu_kb())


async def finish(bot: Bot, chat_id: int, text: str) -> None:
    """Reply klaviaturani olib tashlab, natijani yuboradi va menyuni ko'rsatadi."""
    await bot.send_message(chat_id, text, reply_markup=ReplyKeyboardRemove())
    await send_menu(bot, chat_id)


async def send_album(
    bot: Bot, chat_id: Any, photos: List[str], caption: str
) -> List[int]:
    """
    1 ta rasm bo'lsa send_photo, 2-10 ta bo'lsa send_media_group
    (Telegram media group uchun kamida 2 ta element talab qiladi).
    Yuborilgan xabarlarning ID ro'yxatini qaytaradi (birinchisi - caption egasi).
    """
    if len(photos) == 1:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=photos[0],
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return [msg.message_id]
    media = [
        InputMediaPhoto(
            media=file_id,
            caption=caption if index == 0 else None,
            parse_mode=ParseMode.HTML if index == 0 else None,
        )
        for index, file_id in enumerate(photos)
    ]
    messages = await bot.send_media_group(chat_id=chat_id, media=media)
    return [m.message_id for m in messages]


async def try_edit(call: Awaitable[Any]) -> bool:
    """Kanaldagi xabarni tahrirlash; xato bo'lsa bot yiqilmaydi."""
    try:
        await call
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        logger.warning("Xabarni tahrirlab bo'lmadi: %s", exc)
        return False
    except TelegramAPIError as exc:
        logger.warning("Telegram API xatosi: %s", exc)
        return False


async def mark_product_sold(bot: Bot, product: aiosqlite.Row) -> Tuple[bool, bool]:
    """Kanal postini va tugmani yangilaydi, bazada is_sold=1 qiladi."""
    code = int(product["id"])
    caption_ok = False
    button_ok = False

    if product["ch_msg_id"]:
        caption_ok = await try_edit(
            bot.edit_message_caption(
                chat_id=CHANNEL_CHAT,
                message_id=int(product["ch_msg_id"]),
                caption=build_caption(product["title"], product["price"], code, sold=True),
                parse_mode=ParseMode.HTML,
            )
        )

    if product["btn_msg_id"]:
        button_ok = await try_edit(
            bot.edit_message_text(
                chat_id=CHANNEL_CHAT,
                message_id=int(product["btn_msg_id"]),
                text=build_button_text(code, sold=True),
                parse_mode=ParseMode.HTML,
                reply_markup=sold_kb(),
            )
        )

    await set_sold(code)
    return caption_ok, button_ok


PRICE_PROMPT = (
    "💰 <b>Mahsulot narxini yuboring.</b>\n\n"
    "Oddiy narx: <code>250 000</code>\n"
    "Chegirmali narx: <code>300 000 | 250 000</code>\n"
    "(eski narx | yangi narx)"
)


# ============================================================================
# ADMIN: /start, bekor qilish
# ============================================================================
@admin_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    had_state = await state.get_state() is not None
    await state.clear()
    if had_state:
        await message.answer("↩️ Oldingi jarayon to'xtatildi.", reply_markup=ReplyKeyboardRemove())
    await message.answer("👋 Salom, admin!\nQuyidagi menyudan tanlang:", reply_markup=menu_kb())


@admin_router.message(Command("cancel"))
@admin_router.message(F.text == BTN_CANCEL)
async def cancel_any(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await finish(bot, message.chat.id, "❌ Bekor qilindi.")


# ============================================================================
# ADMIN: yangi post (FSM)
# ============================================================================
@admin_router.callback_query(F.data == "new_post")
async def new_post_start(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await ack(callback)
    await state.clear()
    await state.set_state(NewPost.photos)
    await state.update_data(photos=[], last_group=None)
    await bot.send_message(
        callback.from_user.id,
        "📸 <b>1 tadan 10 tagacha rasm yuboring.</b>\n"
        "Tugagach «" + BTN_DONE + "» tugmasini bosing.",
        reply_markup=photos_kb(),
    )


@admin_router.message(NewPost.photos, F.photo)
async def photos_add(message: Message, state: FSMContext) -> None:
    async with photo_lock:
        data = await state.get_data()
        photos = list(data.get("photos", []))
        last_group = data.get("last_group")
        same_album = (
            message.media_group_id is not None and message.media_group_id == last_group
        )
        full = len(photos) >= MAX_PHOTOS
        if not full:
            photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos, last_group=message.media_group_id)
        count = len(photos)

    if same_album:
        return
    if full:
        text = "⚠️ Maksimum " + str(MAX_PHOTOS) + " ta rasm. Ortiqchasi qabul qilinmadi."
    elif message.media_group_id is not None:
        text = "📸 Rasmlar qabul qilinmoqda. Tugagach «" + BTN_DONE + "» tugmasini bosing."
    else:
        text = "📸 Rasm qabul qilindi: " + str(count) + "/" + str(MAX_PHOTOS)
    await message.answer(text)


@admin_router.message(NewPost.photos, F.text == BTN_DONE)
async def photos_done(message: Message, state: FSMContext) -> None:
    async with photo_lock:
        data = await state.get_data()
        count = len(data.get("photos", []))
    if count == 0:
        await message.answer("⚠️ Avval kamida 1 ta rasm yuboring.")
        return
    await state.set_state(NewPost.title)
    await message.answer(
        "✅ " + str(count) + " ta rasm qabul qilindi.\n\n"
        "✍️ Endi mahsulotning <b>nomi va tavsifini</b> yuboring.",
        reply_markup=cancel_kb(),
    )


@admin_router.message(NewPost.photos)
async def photos_invalid(message: Message) -> None:
    await message.answer(
        "⚠️ Iltimos, rasm yuboring (fayl emas, oddiy rasm) yoki «" + BTN_DONE + "» tugmasini bosing."
    )


@admin_router.message(NewPost.title, F.text)
async def get_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if title == BTN_DONE:
        await message.answer("ℹ️ Rasmlar allaqachon qabul qilingan. Mahsulot nomi va tavsifini yozing.")
        return
    if not title:
        await message.answer("⚠️ Matn bo'sh bo'lmasligi kerak.")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(
            "⚠️ Matn juda uzun (" + str(len(title)) + " belgi). "
            "Maksimum " + str(MAX_TITLE_LEN) + " belgi. Qisqartirib qayta yuboring."
        )
        return
    await state.update_data(title=title)
    await state.set_state(NewPost.price)
    await message.answer(PRICE_PROMPT, reply_markup=cancel_kb())


@admin_router.message(NewPost.title)
async def title_invalid(message: Message) -> None:
    await message.answer("⚠️ Iltimos, matn yuboring.")


@admin_router.message(NewPost.price, F.text)
async def get_price(message: Message, state: FSMContext, bot: Bot) -> None:
    parsed = parse_price(message.text or "")
    if parsed is None:
        await message.answer(
            "⚠️ Narx noto'g'ri. Raqam bo'lishi kerak.\n\n"
            "Oddiy: <code>250 000</code>\n"
            "Chegirmali: <code>300 000 | 250 000</code>"
        )
        return
    old, new = parsed
    price = (old + " | " + new) if old else new

    data = await state.get_data()
    title = data.get("title", "")
    photos = data.get("photos", [])
    code = await generate_code()

    try:
        await send_album(bot, message.chat.id, photos, build_caption(title, price, code))
    except TelegramAPIError as exc:
        logger.exception("Preview yuborishda xatolik")
        await state.clear()
        await finish(
            bot,
            message.chat.id,
            "❌ Oldindan ko'rishda xatolik:\n<code>" + escape(str(exc)) + "</code>",
        )
        return

    await state.update_data(price=price, code=code)
    await state.set_state(NewPost.preview)
    await message.answer(
        "👆 Post kanalda shunday ko'rinadi.\n"
        "🆔 Kod: <code>" + str(code) + "</code>\n\n"
        "Kanalga chiqaramizmi?",
        reply_markup=confirm_kb(),
    )


@admin_router.message(NewPost.price)
async def price_invalid(message: Message) -> None:
    await message.answer("⚠️ Iltimos, narxni matn ko'rinishida yuboring.")


@admin_router.message(NewPost.preview)
async def preview_text(message: Message) -> None:
    await message.answer("👆 Yuqoridagi tugmalardan birini tanlang: «🚀 Kanalga chiqarish» yoki «" + BTN_CANCEL + "».")


@admin_router.callback_query(NewPost.preview, F.data == "publish")
async def publish_post(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await ack(callback, "Yuborilmoqda…")
    chat_id = callback.from_user.id

    async with publish_lock:
        if await state.get_state() != NewPost.preview.state:
            return

        if callback.message is not None:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=callback.message.message_id,
                    reply_markup=None,
                )
            except TelegramAPIError as exc:
                logger.warning("Tugmalarni olib tashlab bo'lmadi: %s", exc)

        data = await state.get_data()
        title = data["title"]
        price = data["price"]
        code = int(data["code"])
        photos = data["photos"]

        sent_ids: List[int] = []
        try:
            album_ids = await send_album(
                bot, CHANNEL_CHAT, photos, build_caption(title, price, code)
            )
            sent_ids.extend(album_ids)
            button_msg = await bot.send_message(
                chat_id=CHANNEL_CHAT,
                text=build_button_text(code),
                reply_markup=order_kb(build_order_url(title, price, code)),
            )
            sent_ids.append(button_msg.message_id)
            await save_product(code, title, price, album_ids[0], button_msg.message_id)
        except Exception as exc:  # noqa: BLE001 - bot yiqilmasligi kerak
            logger.exception("Kanalga chiqarishda xatolik")
            for message_id in sent_ids:
                try:
                    await bot.delete_message(chat_id=CHANNEL_CHAT, message_id=message_id)
                except TelegramAPIError as del_exc:
                    logger.warning("Xabarni o'chirib bo'lmadi: %s", del_exc)
            await bot.send_message(
                chat_id,
                "❌ Kanalga chiqarib bo'lmadi:\n<code>" + escape(str(exc)) + "</code>\n\n"
                "Bot kanalda admin ekanini (post yuborish va tahrirlash huquqi bilan) "
                "hamda CHANNEL_ID to'g'riligini tekshiring.\n"
                "Qayta urinish uchun «🚀 Kanalga chiqarish» tugmasini bosing.",
                reply_markup=confirm_kb(),
            )
            return

        await state.clear()

    await finish(
        bot,
        chat_id,
        "✅ <b>Post kanalga chiqarildi!</b>\n🆔 Mahsulot kodi: <code>" + str(code) + "</code>",
    )


@admin_router.callback_query(NewPost.preview, F.data == "cancel_post")
async def cancel_post(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await ack(callback)
    await state.clear()
    if callback.message is not None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=callback.from_user.id,
                message_id=callback.message.message_id,
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            logger.warning("Tugmalarni olib tashlab bo'lmadi: %s", exc)
    await finish(bot, callback.from_user.id, "❌ Post bekor qilindi.")


@admin_router.callback_query(F.data.in_({"publish", "cancel_post"}))
async def stale_buttons(callback: CallbackQuery) -> None:
    await ack(callback, "Bu tugma eskirgan. /start ni bosing.", alert=True)


# ============================================================================
# ADMIN: SOTILDI
# ============================================================================
@admin_router.callback_query(F.data == "mark_sold")
async def sold_start(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await ack(callback)
    await state.clear()
    await state.set_state(SoldFlow.code)
    await bot.send_message(
        callback.from_user.id,
        "🔢 <b>SOTILDI</b> deb belgilanadigan mahsulotning 6 xonali kodini yuboring:",
        reply_markup=cancel_kb(),
    )


@admin_router.message(SoldFlow.code, F.text)
async def sold_code(message: Message, state: FSMContext, bot: Bot) -> None:
    raw = (message.text or "").strip()
    if not (raw.isascii() and raw.isdigit() and len(raw) == 6):
        await message.answer("⚠️ Kod 6 ta raqamdan iborat bo'lishi kerak. Qaytadan yuboring:")
        return

    product = await get_product(int(raw))
    if product is None:
        await message.answer("❌ Bunday kodli mahsulot topilmadi. Kodni tekshirib, qaytadan yuboring:")
        return

    was_sold = bool(product["is_sold"])
    caption_ok, button_ok = await mark_product_sold(bot, product)
    await state.clear()

    lines = ["✅ Mahsulot <code>" + raw + "</code> <b>SOTILDI</b> deb belgilandi."]
    if was_sold:
        lines.append("ℹ️ Bu mahsulot avval ham SOTILDI deb belgilangan edi.")
    if not caption_ok:
        lines.append("⚠️ Kanaldagi post matnini yangilab bo'lmadi.")
    if not button_ok:
        lines.append("⚠️ Buyurtma tugmasini yangilab bo'lmadi.")
    if not (caption_ok and button_ok):
        lines.append("Post kanaldan o'chirilgan bo'lishi yoki botda huquq yetishmasligi mumkin.")
    await finish(bot, message.chat.id, "\n".join(lines))


@admin_router.message(SoldFlow.code)
async def sold_invalid(message: Message) -> None:
    await message.answer("⚠️ Iltimos, mahsulot kodini matn ko'rinishida yuboring.")


# ============================================================================
# PUBLIC: oddiy foydalanuvchilar va kanaldagi nofaol tugma
# ============================================================================
@public_router.callback_query(F.data == "sold_noop")
async def sold_noop(callback: CallbackQuery) -> None:
    await ack(callback, "🚫 Bu mahsulot sotilgan.", alert=True)


@public_router.message()
async def fallback(message: Message) -> None:
    if message.from_user is not None and message.from_user.id == SUPER_ADMIN_ID:
        await message.answer("Menyu uchun /start buyrug'ini yuboring.")
    else:
        await message.answer("🔒 Bu bot faqat administrator uchun.")


# ============================================================================
# ERROR HANDLER va WEB SERVER (Render health-check)
# ============================================================================
async def on_error(event: ErrorEvent) -> bool:
    logger.error("Kutilmagan xatolik: %s", event.exception, exc_info=event.exception)
    return True


async def start_web_server() -> Optional[web.AppRunner]:
    """Render Web Service port kutadi. PORT yo'q bo'lsa (Worker) ishga tushmaydi."""
    port = os.getenv("PORT")
    if not port:
        return None

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=int(port))
    await site.start()
    logger.info("Health server %s portda ishga tushdi", port)
    return runner


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("BU_YERGA"):
        raise SystemExit("BOT_TOKEN ko'rsatilmagan. Uni Environment Variables yoki config'da kiriting.")

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(public_router)
    dp.errors.register(on_error)

    runner = await start_web_server()
    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except TelegramAPIError as exc:
            logger.warning("Webhook o'chirilmadi: %s", exc)
        logger.info("Bot polling rejimida ishga tushdi")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
