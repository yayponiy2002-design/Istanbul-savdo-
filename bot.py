import asyncio
import html
import logging
import os
import random
import sys
import urllib.parse
from typing import List

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ==========================================
# KONFIGURATSIYA (SOZLAMALAR)
# ==========================================
# Ushbu qiymatlarni o'zingizning ma'lumotlaringizga moslang
BOT_TOKEN = "9236599500:AAHcuooe0K3CSuQCb7ZvSucLZNYSjbetpHQ"
CHANNEL_ID = "@Istanbul_savdo" 
SUPER_ADMIN_ID = 8531801779
SALES_ADMIN = "@servis_istanbul" 
DB_NAME = "shop_bot.db"

# ==========================================
# FSM HOLATLARI (STATES)
# ==========================================
class PostCreation(StatesGroup):
    waiting_for_photos = State()
    waiting_for_details = State()
    waiting_for_price = State()
    waiting_for_confirm = State()


class MarkSold(StatesGroup):
    waiting_for_product_id = State()


# ==========================================
# MA'LUMOTLAR BAZASI FUNKSIYALARI (aiosqlite)
# ==========================================
async def init_db() -> None:
    """Ma'lumotlar bazasi va mahsulotlar jadvalini yaratish."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                clean_price TEXT NOT NULL,
                caption TEXT NOT NULL,
                ch_msg_id INTEGER NOT NULL,
                btn_msg_id INTEGER NOT NULL,
                is_sold INTEGER DEFAULT 0
            )
            """
        )
        await db.commit()


async def add_product(
    prod_id: str,
    title: str,
    price: str,
    clean_price: str,
    caption: str,
    ch_msg_id: int,
    btn_msg_id: int,
) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO products (id, title, price, clean_price, caption, ch_msg_id, btn_msg_id, is_sold)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (prod_id, title, price, clean_price, caption, ch_msg_id, btn_msg_id),
        )
        await db.commit()


async def get_product(prod_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, title, price, clean_price, caption, ch_msg_id, btn_msg_id, is_sold FROM products WHERE id = ?",
            (prod_id,),
        ) as cursor:
            return await cursor.fetchone()


async def set_product_sold(prod_id: str) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE products SET is_sold = 1 WHERE id = ?",
            (prod_id,),
        )
        await db.commit()


async def is_id_unique(prod_id: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM products WHERE id = ?", (prod_id,)) as cursor:
            res = await cursor.fetchone()
            return res is None


async def generate_unique_product_id() -> str:
    """6 xonali takrorlanmas mahsulot ID raqami yaratish."""
    while True:
        candidate_id = f"{random.randint(100000, 999999)}"
        if await is_id_unique(candidate_id):
            return candidate_id


# ==========================================
# KLAVIATURALAR (KEYBOARDS)
# ==========================================
def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi post chiqarish", callback_data="btn_add_post")],
            [InlineKeyboardButton(text="❌ Tovar holatini 'SOTILDI' qilish", callback_data="btn_mark_sold")],
        ]
    )


def get_photo_done_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Rasmlar tayyor")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Kanalga chiqarish", callback_data="btn_publish")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="btn_cancel")],
        ]
    )


def get_cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ==========================================
# BOT VA DISPATCHER YARATISH
# ==========================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())


# ==========================================
# HANDLERLAR (ADMIN PANELI VA AKISHLAR)
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    await state.clear()
    await message.answer(
        "👋 <b>Assalomu alaykum, Admin!</b>\n\nQuyidagi menyu orqali kanalga yangi post chiqarishingiz yoki tovar holatini o'zgartirishingiz mumkin:",
        reply_markup=get_admin_menu_keyboard(),
    )


@dp.callback_query(F.data == "btn_cancel")
async def cb_cancel_inline(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Ruxsat berilmagan!", show_alert=True)
        return

    await state.clear()
    await callback.message.delete()
    await callback.message.answer(
        "Amal bekor qilindi. Bosh menyu:",
        reply_markup=get_admin_menu_keyboard(),
    )
    await callback.answer()


@dp.message(F.text == "❌ Bekor qilish")
async def msg_cancel(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    await state.clear()
    await message.answer(
        "Amal bekor qilindi. Bosh menyu:",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Tanlang:",
        reply_markup=get_admin_menu_keyboard(),
    )


# ------------------------------------------
# 1. YANGI MAHSULOT POSTINI YARATISH BOSQICHLARI
# ------------------------------------------
@dp.callback_query(F.data == "btn_add_post")
async def cb_start_add_post(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Ruxsat berilmagan!", show_alert=True)
        return

    await state.clear()
    await state.set_state(PostCreation.waiting_for_photos)
    await state.update_data(photos=[])

    await callback.message.answer(
        "📸 Mahsulot uchun 1 tadan 10 tagacha rasm yuboring.\n"
        "Yuklab bo'lgach, quyidagi <b>'✅ Rasmlar tayyor'</b> tugmasini bosing.",
        reply_markup=get_photo_done_keyboard(),
    )
    await callback.answer()


@dp.message(PostCreation.waiting_for_photos, F.photo)
async def process_photos(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    data = await state.get_data()
    photos: List[str] = data.get("photos", [])

    if len(photos) >= 10:
        await message.answer("⚠️ Maksimal 10 ta rasm qabul qilinadi. '✅ Rasmlar tayyor' tugmasini bosing.")
        return

    photo_id = message.photo[-1].file_id
    photos.append(photo_id)
    await state.update_data(photos=photos)

    await message.answer(f"Rasm qabul qilindi ({len(photos)}/10).")


@dp.message(PostCreation.waiting_for_photos, F.text == "✅ Rasmlar tayyor")
async def process_photos_done(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    data = await state.get_data()
    photos = data.get("photos", [])

    if not photos:
        await message.answer("⚠️ Hech bo'lmaganda 1 ta rasm yuklashingiz kerak!")
        return

    await state.set_state(PostCreation.waiting_for_details)
    await message.answer(
        "📝 Endi mahsulot <b>nomi va tavsifini</b> (matnini) yuboring:",
        reply_markup=get_cancel_reply_keyboard(),
    )


@dp.message(PostCreation.waiting_for_details, F.text)
async def process_details(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    await state.update_data(details=message.text.strip())
    await state.set_state(PostCreation.waiting_for_price)

    await message.answer(
        "💰 Mahsulot narxini kiriting.\n\n"
        "• Oddiy narx namunasi: <code>250 000</code>\n"
        "• Chegirmali narx namunasi: <code>300 000 | 250 000</code>",
        reply_markup=get_cancel_reply_keyboard(),
    )


@dp.message(PostCreation.waiting_for_price, F.text)
async def process_price(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    raw_price = message.text.strip()
    clean_price = raw_price

    if "|" in raw_price:
        parts = [p.strip() for p in raw_price.split("|", 1)]
        old_price_esc = html.escape(parts[0])
        new_price_esc = html.escape(parts[1])
        formatted_price = f"<s>{old_price_esc}</s> {new_price_esc}"
        clean_price = parts[1]
    else:
        formatted_price = html.escape(raw_price)

    prod_id = await generate_unique_product_id()

    data = await state.get_data()
    photos = data.get("photos", [])
    details = data.get("details", "")

    # Kanaldagi post matni
    caption = (
        f"{details}\n\n"
        f"💰 <b>Narxi:</b> {formatted_price} so'm\n"
        f"🆔 <b>Mahsulot kodi:</b> #{prod_id}"
    )

    await state.update_data(
        prod_id=prod_id,
        price=formatted_price,
        clean_price=clean_price,
        caption=caption,
    )
    await state.set_state(PostCreation.waiting_for_confirm)

    await message.answer("Ko'rib chiqish (Preview) tayyorlanmoqda...", reply_markup=ReplyKeyboardRemove())

    # Admin uchun preview yuborish
    if len(photos) == 1:
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photos[0],
            caption=f"👁 <b>PREVIEW (Ko'rib chiqish):</b>\n\n{caption}",
            reply_markup=get_preview_keyboard(),
        )
    else:
        media_group = []
        for i, photo_id in enumerate(photos):
            if i == 0:
                media_group.append(
                    InputMediaPhoto(
                        media=photo_id,
                        caption=f"👁 <b>PREVIEW (Ko'rib chiqish):</b>\n\n{caption}",
                    )
                )
            else:
                media_group.append(InputMediaPhoto(media=photo_id))

        await bot.send_media_group(chat_id=message.chat.id, media=media_group)
        await message.answer(
            "Yuqoridagi postni tasdiqlaysizmi?",
            reply_markup=get_preview_keyboard(),
        )


@dp.callback_query(PostCreation.waiting_for_confirm, F.data == "btn_publish")
async def cb_publish_post(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Ruxsat berilmagan!", show_alert=True)
        return

    data = await state.get_data()
    photos = data["photos"]
    caption = data["caption"]
    prod_id = data["prod_id"]
    price = data["price"]
    clean_price = data["clean_price"]
    title = data["details"].split("\n")[0][:60]  # Birinchi qator yoki qisqa sarlavha

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Post kanalga yuborilmoqda...")

    try:
        # 1. Rasmlarni kanalga yuborish
        if len(photos) == 1:
            channel_msg = await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photos[0],
                caption=caption,
            )
            ch_msg_id = channel_msg.message_id
        else:
            media_group = [
                InputMediaPhoto(media=photo_id, caption=caption if i == 0 else None)
                for i, photo_id in enumerate(photos)
            ]
            channel_msgs = await bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)
            ch_msg_id = channel_msgs[0].message_id

        # 2. Xarid qilish tugmasini yaratish
        order_msg_text = (
            f"Assalomu alaykum! Men ushbu mahsulotni sotib olmoqchiman:\n"
            f"📦 Mahsulot: {title}\n"
            f"🆔 Kod: #{prod_id}\n"
            f"💰 Narx: {clean_price} so'm"
        )
        encoded_text = urllib.parse.quote(order_msg_text)
        order_url = f"https://t.me/{SALES_ADMIN}?text={encoded_text}"

        order_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Buyurtma berish", url=order_url)]
            ]
        )

        # 3. Post ostiga buyurtma tugmali xabar yuborish
        btn_msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"👆 <b>#{prod_id}</b> raqamli mahsulotga buyurtma berish uchun quyidagi tugmani bosing:",
            reply_markup=order_keyboard,
        )
        btn_msg_id = btn_msg.message_id

        # 4. Ma'lumotlar bazasiga saqlash
        await add_product(
            prod_id=prod_id,
            title=title,
            price=price,
            clean_price=clean_price,
            caption=caption,
            ch_msg_id=ch_msg_id,
            btn_msg_id=btn_msg_id,
        )

        await state.clear()
        await callback.message.answer(
            f"✅ <b>Post muvaffaqiyatli kanalga chiqarildi!</b>\n"
            f"Mahsulot kodi: <code>{prod_id}</code>",
            reply_markup=get_admin_menu_keyboard(),
        )

    except TelegramAPIError as e:
        logging.error(f"Telegram API Xatosi: {e}")
        await callback.message.answer(
            f"❌ Kanalga yuborishda xatolik yuz berdi:\n<code>{html.escape(str(e))}</code>\n\n"
            "Bot kanalga administrator qilinganligini va xabar yuborish huquqi borligini tekshiring.",
            reply_markup=get_admin_menu_keyboard(),
        )
        await state.clear()

    await callback.answer()


# ------------------------------------------
# 2. TOVARNI 'SOTILDI' QILISH BOSQICHI
# ------------------------------------------
@dp.callback_query(F.data == "btn_mark_sold")
async def cb_start_mark_sold(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Ruxsat berilmagan!", show_alert=True)
        return

    await state.clear()
    await state.set_state(MarkSold.waiting_for_product_id)
    await callback.message.answer(
        "🆔 'SOTILDI' holatiga o'tkazmoqchi bo'lgan tovarning 6 xonali ID kodini kiriting:",
        reply_markup=get_cancel_reply_keyboard(),
    )
    await callback.answer()


@dp.message(MarkSold.waiting_for_product_id, F.text)
async def process_mark_sold(message: Message, state: FSMContext) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    prod_id = message.text.replace("#", "").strip()
    product = await get_product(prod_id)

    if not product:
        await message.answer(
            f"❌ <b>#{prod_id}</b> raqamli mahsulot topilmadi. Qayta tekshirib, ID ni kiriting:",
            reply_markup=get_cancel_reply_keyboard(),
        )
        return

    _, title, price, clean_price, original_caption, ch_msg_id, btn_msg_id, is_sold = product

    if is_sold == 1:
        await state.clear()
        await message.answer(
            f"ℹ️ <b>#{prod_id}</b> raqamli mahsulot allaqachon 'SOTILDI' qilingan.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("Bosh menyu:", reply_markup=get_admin_menu_keyboard())
        return

    sold_caption = f"🔴 [SOTILDI ❌]\n\n{original_caption}"
    sold_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 MAHSULOT SOTILDI", callback_data="btn_sold_inactive")]
        ]
    )

    edit_errors = []

    # 1. Kanaldagi rasm sarlavhasini tahrirlash
    try:
        await bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=ch_msg_id,
            caption=sold_caption,
        )
    except TelegramBadRequest as e:
        logging.warning(f"Rasm sarlavhasi tahrirlanmadi (ID: {ch_msg_id}): {e}")
        edit_errors.append("Asosiy post sarlavhasini o'zgartirib bo'lmadi (ehtimol xabar o'chirilgan).")

    # 2. Kanaldagi buyurtma tugmasini nofaol tugmaga o'zgartirish
    try:
        await bot.edit_message_text(
            chat_id=CHANNEL_ID,
            message_id=btn_msg_id,
            text=f"🔴 <b>#{prod_id}</b> raqamli tovar sotildi va sotuvdan olindi.",
            reply_markup=sold_keyboard,
        )
    except TelegramBadRequest:
        try:
            await bot.edit_message_reply_markup(
                chat_id=CHANNEL_ID,
                message_id=btn_msg_id,
                reply_markup=sold_keyboard,
            )
        except TelegramBadRequest as e2:
            logging.warning(f"Tugma xabari tahrirlanmadi (ID: {btn_msg_id}): {e2}")
            edit_errors.append("Buyurtma tugmasini tahrirlab bo'lmadi.")

    # 3. Bazada tovar holatini yangilash
    await set_product_sold(prod_id)
    await state.clear()

    result_text = f"✅ <b>#{prod_id}</b> raqamli mahsulot muvaffaqiyatli <b>SOTILDI</b> holatiga o'tkazildi!"
    if edit_errors:
        result_text += "\n\n⚠️ <i>Eslatma:</i>\n" + "\n".join(f"• {err}" for err in edit_errors)

    await message.answer(result_text, reply_markup=ReplyKeyboardRemove())
    await message.answer("Bosh menyu:", reply_markup=get_admin_menu_keyboard())


@dp.callback_query(F.data == "btn_sold_inactive")
async def cb_sold_inactive(callback: CallbackQuery) -> None:
    await callback.answer("Kechirasiz, ushbu tovar sotib bo'lingan!", show_alert=True)


# ==========================================
# ASOSIY ISHGA TUSHIRISH (MAIN)
# ==========================================
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logging.info("Ma'lumotlar bazasi initsializatsiya qilinmoqda...")
    await init_db()

    logging.info("Bot polling rejimida ishga tushirilmoqda...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot to'xtatildi.")
