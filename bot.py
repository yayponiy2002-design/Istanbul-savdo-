import asyncio
import json
import time
import urllib.parse
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
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

# =========================================================================
# ⚙️ BU ALANI KENDİ BİLGİLERİNİZLE DOLDURUN:
# =========================================================================
BOT_TOKEN = "9236599500:AAHPb9C7IEcW5yWFRUUm62-gpM-EooS-dvA"
CHANNEL_ID = "-1004466199191"
SUPER_ADMIN_ID = "8531801779"
DEFAULT_SALES_ADMIN = "servis_istanbul"
# =========================================================================

DB_NAME = "shop_bot.db"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# --- VERİTABANI İŞLEMLERİ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                title TEXT,
                category TEXT,
                price TEXT,
                photos_json TEXT,
                caption TEXT,
                channel_msg_id INTEGER,
                button_msg_id INTEGER,
                is_sold INTEGER DEFAULT 0,
                posted_by_admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS content_admins (
                admin_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('sales_admin', ?)",
            (DEFAULT_SALES_ADMIN.replace("@", ""),)
        )
        await db.commit()


async def check_is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM content_admins WHERE admin_id = ?", (user_id,)) as cursor:
            return (await cursor.fetchone()) is not None


async def get_current_sales_admin() -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'sales_admin'") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else DEFAULT_SALES_ADMIN


async def set_sales_admin(new_username: str):
    clean_username = new_username.replace("@", "").strip()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('sales_admin', ?)",
            (clean_username,)
        )
        await db.commit()
    return clean_username


def make_post_link(chat_id: str | int, msg_id: int) -> str:
    if str(chat_id).startswith("@"):
        channel_user = str(chat_id).replace("@", "")
        return f"https://t.me/{channel_user}/{msg_id}"
    else:
        clean_id = str(chat_id).replace("-100", "").replace("-", "")
        return f"https://t.me/c/{clean_id}/{msg_id}"


def build_promo_text(title: str, price_display: str, p_id: str, custom_note: str = None) -> str:
    text = (
        f"⚡️ <b>DIQQAT, KATTA CHEGIRMA!</b>\n\n"
        f"📦 <b>Mahsulot:</b> {title}\n"
        f"💰 <b>Yangi narx:</b> {price_display} 🔥\n"
        f"🆔 Mahsulot kodi: #{p_id}\n\n"
    )
    if custom_note:
        text += f"💬 <b>Qo‘shimcha ma’lumot:</b>\n<i>{custom_note}</i>\n\n"
    text += "<i>Shoshiling, mahsulot soni cheklangan! Rasmlar va tavsifni ko‘rish uchun pastdagi tugmani bosing:</i>"
    return text


# --- FSM DURUMLARI ---
class ProductPost(StatesGroup):
    photos = State()
    category = State()
    title_desc = State()
    price = State()
    preview = State()


class SettingsState(StatesGroup):
    waiting_for_sales_username = State()
    waiting_for_new_admin_id = State()
    waiting_for_sold_product_id = State()
    waiting_for_edit_price_id = State()
    waiting_for_edit_new_price = State()
    waiting_for_forward_new_price = State()
    waiting_for_custom_promo_note = State()
    waiting_for_promo_confirm = State()


def get_admin_menu(user_id: int):
    buttons = [
        [InlineKeyboardButton(text="➕ Yangi post yaratish", callback_data="admin_new_post")],
        [InlineKeyboardButton(text="🏷 Narxni yangilash (Chegirma)", callback_data="admin_edit_price")],
        [InlineKeyboardButton(text="❌ Tovar holatini 'SOTILDI' qilish", callback_data="admin_mark_sold")],
        [InlineKeyboardButton(text="📊 Umumiy statistika", callback_data="admin_stats")],
    ]
    if user_id == SUPER_ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="⚙️ Savdo adminini o‘zgartirish", callback_data="admin_change_sales")])
        buttons.append([InlineKeyboardButton(text="👥 Adminlarni boshqarish", callback_data="admin_manage_team")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================================================================
# ANA YÖNETİCİ MENÜSÜ
# =========================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    is_admin = await check_is_admin(message.from_user.id)
    if is_admin:
        role = "Bosh Admin" if message.from_user.id == SUPER_ADMIN_ID else "Kontent Admin"
        await message.answer(
            f"👋 <b>Assalomu alaykum!</b>\nSizning rolingiz: <b>{role}</b>\n\nKerakli amalni tanlang:",
            parse_mode="HTML",
            reply_markup=get_admin_menu(message.from_user.id)
        )
    else:
        sales_admin = await get_current_sales_admin()
        await message.answer(
            f"Assalomu alaykum! Mahsulotlarimiz bilan kanalimiz orqali tanishishingiz mumkin.\n\n"
            f"Buyurtma va savollar uchun: @{sales_admin}"
        )


@dp.callback_query(F.data == "admin_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    if not await check_is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q!", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text(
        "👋 <b>Admin panel:</b>\nKerakli amalni tanlang:",
        parse_mode="HTML",
        reply_markup=get_admin_menu(call.from_user.id)
    )


@dp.callback_query(F.data == "admin_stats")
async def show_stats(call: CallbackQuery):
    if not await check_is_admin(call.from_user.id):
        return
    sales_admin = await get_current_sales_admin()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM products") as cursor:
            total_products = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM products WHERE is_sold = 1") as cursor:
            sold_products = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM products WHERE posted_by_admin_id = ?", (call.from_user.id,)) as cursor:
            my_products = (await cursor.fetchone())[0]

    stats_text = (
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"📦 <b>Jami mahsulotlar:</b> {total_products} ta\n"
        f"✅ <b>Sotilgan tovarlar:</b> {sold_products} ta\n"
        f"✍️ <b>Siz chiqargan tovarlar:</b> {my_products} ta\n"
        f"👤 <b>Hozirgi savdo admini:</b> @{sales_admin}"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_menu")]])
    await call.message.edit_text(stats_text, parse_mode="HTML", reply_markup=back_kb)


# =========================================================================
# YENİ ÜRÜN PAYLAŞIMI (ALBÜM MODU)
# =========================================================================

@dp.callback_query(F.data == "admin_new_post")
async def new_post_start(call: CallbackQuery, state: FSMContext):
    if not await check_is_admin(call.from_user.id):
        return
    await state.update_data(photos=[])
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Rasmlarni yuklab bo‘ldim")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await call.message.answer(
        "Mahsulot rasmlarini yuboring (1 tadan 10 tagacha).\n"
        "Yakunlash uchun pastdagi <b>'✅ Rasmlarni yuklab bo‘ldim'</b> tugmasini bosing:",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(ProductPost.photos)


@dp.message(ProductPost.photos, F.photo)
async def process_photos(message: Message, state: FSMContext):
    if not await check_is_admin(message.from_user.id):
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 10:
        await message.answer("Maksimal 10 ta rasm yuklash mumkin.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"📸 {len(photos)}-rasm qabul qilindi.")


@dp.message(ProductPost.photos, F.text == "✅ Rasmlarni yuklab bo‘ldim")
async def finish_photos(message: Message, state: FSMContext):
    if not await check_is_admin(message.from_user.id):
        return
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer("Kamida bitta rasm yuklang!")
        return
    await message.answer("Rasmlar qabul qilindi.", reply_markup=ReplyKeyboardRemove())
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👗 Ayollar", callback_data="cat_#ayollar")],
            [InlineKeyboardButton(text="👔 Erkaklar", callback_data="cat_#erkaklar")],
            [InlineKeyboardButton(text="🏠 Uy jihozlari", callback_data="cat_#uy_jihozlari")],
        ]
    )
    await message.answer("Mahsulot bo‘limini tanlang:", reply_markup=kb)
    await state.set_state(ProductPost.category)


@dp.callback_query(ProductPost.category, F.data.startswith("cat_"))
async def process_category(call: CallbackQuery, state: FSMContext):
    if not await check_is_admin(call.from_user.id):
        return
    await state.update_data(category=call.data.replace("cat_", ""))
    await call.message.edit_reply_markup()
    await call.message.answer("Mahsulot nomi va tavsifini yozing:")
    await state.set_state(ProductPost.title_desc)


@dp.message(ProductPost.title_desc, F.text)
async def process_title(message: Message, state: FSMContext):
    if not await check_is_admin(message.from_user.id):
        return
    await state.update_data(title_desc=message.text)
    await message.answer("Mahsulot narxini kiriting (Masalan: 250 000 yoki chegirma bilan: 300 000 | 250 000):")
    await state.set_state(ProductPost.price)


@dp.message(ProductPost.price, F.text)
async def process_price(message: Message, state: FSMContext):
    if not await check_is_admin(message.from_user.id):
        return
    data = await state.get_data()
    price_raw = message.text.strip()
    if "|" in price_raw:
        old_p, new_p = price_raw.split("|")
        price_text = f"<s>{old_p.strip()}</s> {new_p.strip()}"
    else:
        price_text = price_raw

    product_id = str(int(time.time()))[-6:]
    caption = (
        f"✨ <b>{data['title_desc']}</b>\n\n"
        f"📌 Bo‘lim: {data['category']}\n"
        f"💰 Narxi: <b>{price_text}</b>\n"
        f"🆔 Mahsulot kodi: #{product_id}\n\n"
        f"🚚 Yetkazib berish mavjud!"
    )
    await state.update_data(
        final_caption=caption,
        price_display=price_text,
        product_id=product_id
    )
    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Kanalga chiqarish", callback_data="post_publish")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="post_cancel")],
        ]
    )
    await message.answer("Post ko‘rinishi (Preview):")
    media_group = [
        InputMediaPhoto(media=pid, caption=caption if i == 0 else None, parse_mode="HTML" if i == 0 else None)
        for i, pid in enumerate(data["photos"])
    ]
    await message.answer_media_group(media=media_group)
    await message.answer("Yuqoridagi post kanalga joylansinmi?", reply_markup=confirm_kb)
    await state.set_state(ProductPost.preview)


@dp.callback_query(ProductPost.preview, F.data == "post_publish")
async def publish_post(call: CallbackQuery, state: FSMContext):
    if not await check_is_admin(call.from_user.id):
        return
    data = await state.get_data()
    p_id = data["product_id"]
    photos = data["photos"]
    sales_admin = await get_current_sales_admin()

    auto_message = (
        f"Assalomu alaykum! Men kanaldagi mahsulotni sotib olmoqchiman:\n"
        f"📦 Mahsulot: {data['title_desc']}\n"
        f"💰 Narxi: {data['price_display']}\n"
        f"🆔 Kod: #{p_id}"
    )
    encoded_message = urllib.parse.quote(auto_message)
    sales_admin_link = f"https://t.me/{sales_admin}?text={encoded_message}"
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛍 Sotib olish (Menejerga yozish)", url=sales_admin_link)]]
    )
    media_group = [
        InputMediaPhoto(media=pid, caption=data["final_caption"] if i == 0 else None, parse_mode="HTML" if i == 0 else None)
        for i, pid in enumerate(photos)
    ]
    sent_album = await bot.send_media_group(chat_id=CHANNEL_ID, media=media_group)
    channel_msg_id = sent_album[0].message_id
    sent_button_msg = await bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"👆 <b>#{p_id}</b> raqamli mahsulotga buyurtma berish uchun quyidagi tugmani bosing:",
        parse_mode="HTML",
        reply_markup=channel_kb
    )
    button_msg_id = sent_button_msg.message_id

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO products (id, title, category, price, photos_json, caption, channel_msg_id, button_msg_id, posted_by_admin_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p_id, data["title_desc"], data["category"], data["price_display"],
            json.dumps(photos), data["final_caption"], channel_msg_id, button_msg_id, call.from_user.id
        ))
        await db.commit()

    await call.message.edit_reply_markup()
    await call.message.answer(
        f"🎉 Mahsulot #{p_id} raqami bilan kanalga joylandi!",
        reply_markup=get_admin_menu(call.from_user.id)
    )
    await state.clear()


@dp.callback_query(ProductPost.preview, F.data == "post_cancel")
async def cancel_post(call: CallbackQuery, state: F
