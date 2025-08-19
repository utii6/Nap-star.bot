import json
import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# تحميل الإعدادات من config.json
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

BOT_TOKEN = CONFIG["BOT_TOKEN"]
KD1S_API_KEY = CONFIG["KD1S_API_KEY"]
ADMIN_ID = int(CONFIG["ADMIN_ID"])
CHANNEL_USERNAME = CONFIG["CHANNEL_USERNAME"]
CHANNEL_LINK = CONFIG["CHANNEL_LINK"]
SERVICES = CONFIG["SERVICES"]

# نقاط المستخدمين
user_points = {}

# تفعيل اللوج
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- التحقق من الاشتراك ---
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("📢 اشترك هنا", url=CHANNEL_LINK)]]
        await update.message.reply_text(
            "⚠️ للمتابعة يجب أن تشترك في القناة أولاً:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # إعطاء 10 نقاط ترحيبية
    if user_id not in user_points:
        user_points[user_id] = 10
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"👤😂 مستخدم جديد دخل البوت: {update.effective_user.mention_html()}",
            parse_mode="HTML",
        )

    keyboard = [
        [InlineKeyboardButton("💠 😎الخدمات", callback_data="services")],
        [InlineKeyboardButton("👤 😂حسابي", callback_data="account")],
        [InlineKeyboardButton("🎁💁 رابط الدعوة", callback_data="invite")],
    ]
    await update.message.reply_text(
        "👋 😂أهلاً بك في بوت ناب ستار!\nيمكنك طلب الخدمات بالنقاط.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# --- عرض الخدمات ---
async def services_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton(service["name"], callback_data=f"buy_{sid}")]
        for sid, service in SERVICES.items()
    ]
    await query.message.reply_text("⭐️اختر الخدمة:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- حساب المستخدم ---
async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    points = user_points.get(user_id, 0)
    await query.message.reply_text(f"💳 😂رصيدك الحالي: {points} نقطة")

# --- رابط الدعوة ---
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    invite_link = f"https://t.me/{context.bot.username}?start={query.from_user.id}"
    await query.message.reply_text(
        f"🎁😂 شارك الرابط مع أصدقائك لتحصل على نقاط:\n{invite_link}"
    )

# --- تنفيذ الطلب ---
async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    sid = query.data.split("_")[1]

    if user_points.get(user_id, 0) < 5:  # مؤقت: كل خدمة = 5 نقاط
        await query.message.reply_text("❌😎 ليس لديك نقاط كافية لشراء هذه الخدمة.")
        return

    user_points[user_id] -= 5

    await query.message.reply_text(
        f"✅ تم تنفيذ طلبك للخدمة: {SERVICES[sid]['name']} (خصم 5 نقاط)."
    )

    # إشعار المدير
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📦 المستخدم {query.from_user.mention_html()} طلب خدمة {SERVICES[sid]['name']}",
        parse_mode="HTML",
    )

# --- لوحة التحكم ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("➕ 😎إضافة نقاط", callback_data="admin_add_points")],
        [InlineKeyboardButton("⛔️ 😂حظر مستخدم", callback_data="admin_ban")],
    ]
    await update.message.reply_text("لوحة التحكم:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- كولباك ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "services":
        await services_menu(update, context)
    elif query.data == "account":
        await account(update, context)
    elif query.data == "invite":
        await invite(update, context)
    elif query.data.startswith("buy_"):
        await handle_buy(update, context)

# --- تشغيل البوت ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.run_polling()

if __name__ == "__main__":
    main()
