import asyncio, json, time, urllib.parse, aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ⚙️ AYARLAR
BOT_TOKEN = "9236599500:AAHPb9C7IEcW5yWFRUUm62-gpM-EooS-dvA"
CHANNEL_ID = "-1004466199191"  
SUPER_ADMIN_ID = 8531801779             
SALES_ADMIN = "servis_istanbul"        

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

async def init_db():
    async with aiosqlite.connect("shop.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, title TEXT, price TEXT, ch_msg_id INTEGER, btn_msg_id INTEGER)")
        await db.commit()

class PostState(StatesGroup):
    photos, desc, price, preview = State(), State(), State(), State()

class SoldState(StatesGroup):
    p_id = State()

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi post chiqarish", callback_data="new_post")],
        [InlineKeyboardButton(text="❌ Tovar holatini 'SOTILDI' qilish", callback_data="mark_sold")]
    ])

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if m.from_user.id == SUPER_ADMIN_ID:
        await m.answer("👋 <b>Admin panel:</b>", parse_mode="HTML", reply_markup=main_kb())
    else:
        await m.answer(f"Assalomu alaykum! Savollar uchun: @{SALES_ADMIN}")

# --- YENİ POST OLUŞTURMA ---
@dp.callback_query(F.data == "new_post")
async def start_post(c: CallbackQuery, state: FSMContext):
    await state.update_data(photos=[])
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Rasmlar tayyor")]], resize_keyboard=True)
    await c.message.answer("Rasmlarni yuboring (1-10 ta). Tugatgach pastdagi tugmani bosing:", reply_markup=kb)
    await state.set_state(PostState.photos)

@dp.message(PostState.photos, F.photo)
async def get_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) < 10:
        photos.append(m.photo[-1].file_id)
        await state.update_data(photos=photos)
        await m.answer(f"📸 {len(photos)}-rasm olindi.")

@dp.message(PostState.photos, F.text == "✅ Rasmlar tayyor")
async def finish_photos(m: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos"):
        return await m.answer("Kamida 1 ta rasm yuboring!")
    await m.answer("Mahsulot nomi va tavsifini yozing:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(PostState.desc)

@dp.message(PostState.desc)
async def get_desc(m: Message, state: FSMContext):
    await state.update_data(desc=m.text)
    await m.answer("Narxini kiriting (Masalan: 250 000 yoki: 300 000 | 250 000):")
    await state.set_state(PostState.price)

@dp.message(PostState.price)
async def get_price(m: Message, state: FSMContext):
    d = await state.get_data()
    p_raw = m.text.strip()
    price = f"<s>{p_raw.split('|')[0].strip()}</s> {p_raw.split('|')[1].strip()}" if "|" in p_raw else p_raw
    p_id = str(int(time.time()))[-6:]
    caption = f"✨ <b>{d['desc']}</b>\n\n💰 Narxi: <b>{price}</b>\n🆔 Kod: #{p_id}\n\n🚚 Yetkazib berish mavjud!"
    await state.update_data(caption=caption, price=price, p_id=p_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Kanalga chiqarish", callback_data="send_post")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel")]
    ])
    media = [InputMediaPhoto(media=pid, caption=caption if i==0 else None, parse_mode="HTML" if i==0 else None) for i, pid in enumerate(d["photos"])]
    await m.answer_media_group(media=media)
    await m.answer("Kanalga joylansinmi?", reply_markup=kb)
    await state.set_state(PostState.preview)

@dp.callback_query(PostState.preview, F.data == "send_post")
async def publish(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    msg_txt = urllib.parse.quote(f"Assalomu alaykum! Men tovar olmoqchiman:\n📦 {d['desc']}\n💰 {d['price']}\n🆔 #{d['p_id']}")
    btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛍 Sotib olish", url=f"https://t.me/{SALES_ADMIN}?text={msg_txt}")]])
    
    media = [InputMediaPhoto(media=pid, caption=d["caption"] if i==0 else None, parse_mode="HTML" if i==0 else None) for i, pid in enumerate(d["photos"])]
    sent = await bot.send_media_group(CHANNEL_ID, media)
    btn_msg = await bot.send_message(CHANNEL_ID, f"👆 <b>#{d['p_id']}</b> uchun buyurtma bering:", parse_mode="HTML", reply_markup=btn)
    
    async with aiosqlite.connect("shop.db") as db:
        await db.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?)", (d['p_id'], d['desc'], d['price'], sent[0].message_id, btn_msg.message_id))
        await db.commit()
    
    await c.message.answer(f"🎉 #{d['p_id']} kanalga chiqdi!", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "cancel")
async def cancel_act(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Bekor qilindi.", reply_markup=main_kb())

# --- SOTILDI QILISH ---
@dp.callback_query(F.data == "mark_sold")
async def mark_sold_start(c: CallbackQuery, state: FSMContext):
    await c.message.answer("Sotilgan tovarning 6 xonali <b>ID kodini</b> yuboring (Masalan: 123456):", parse_mode="HTML")
    await state.set_state(SoldState.p_id)

@dp.message(SoldState.p_id)
async def process_sold(m: Message, state: FSMContext):
    p_id = m.text.strip().replace("#", "")
    async with aiosqlite.connect("shop.db") as db:
        async with db.execute("SELECT ch_msg_id, btn_msg_id, title FROM products WHERE id = ?", (p_id,)) as cur:
            prod = await cur.fetchone()
    if not prod:
        return await m.answer("❌ Tovar topilmadi. Qayta kiriting:")
    
    ch_id, btn_id, title = prod
    sold_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚫 MAHSULOT SOTILDI", callback_data="none")]])
    try:
        await bot.edit_message_caption(CHANNEL_ID, ch_id, caption=f"🔴 <b>[SOTILDI ❌]</b>\n\n✨ <b>{title}</b>", parse_mode="HTML")
        await bot.edit_message_text("🔴 <b>Bu mahsulot sotildi!</b>", CHANNEL_ID, btn_id, parse_mode="HTML", reply_markup=sold_btn)
    except Exception as e:
        pass
    
    await m.answer(f"✅ #{p_id} kanalda [SOTILDI] deb belgilandi!", reply_markup=main_kb())
    await state.clear()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
