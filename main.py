‏import os
‏import json
‏import math
‏import asyncio
‏import logging
‏import sqlite3
‏from datetime import datetime, timezone
‏from typing import Optional

‏import requests
‏from fastapi import FastAPI, Request, HTTPException
‏from fastapi.responses import JSONResponse
‏from pydantic import BaseModel
‏from telegram import (
‏    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
‏from telegram.constants import ParseMode, ChatMemberStatus
‏from telegram.ext import (
‏    Application, ApplicationBuilder, AIORateLimiter,
‏    ContextTypes, CommandHandler, CallbackQueryHandler,
‏    MessageHandler, filters, ConversationHandler
)

# ------------------------
# إعدادات عامة + لوجينغ
# ------------------------
‏logging.basicConfig(
‏    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
‏log = logging.getLogger("teleboost")

# ------------------------
# تحميل الإعدادات
# ------------------------
‏CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")
‏if not os.path.exists(CONFIG_PATH):
‏    raise RuntimeError("config.json مفقود!")

‏with open(CONFIG_PATH, "r", encoding="utf-8") as f:
‏    CFG = json.load(f)

‏BOT_TOKEN: str = CFG[“6663550850:AAGGxmXCBQcmsqowNNRh8hIJLooWSutrguc"]                     # ضع توكن البوت
‏ADMIN_ID: int = int(CFG["5581457665"])                  # معرّف المدير
‏CHANNEL_USERNAME: str = CFG["@qd3qd"]       # مثل @qd3qd
‏CHANNEL_LINK: str = CFG["https://t.me/qd3qd"]               # رابط القناة
‏KD1S_API_KEY: str = CFG["fd226cc94ad4730494305d2ec2364778"]               # API Key
‏BASE_URL: str = CFG["BASE_URL"].rstrip("/")           # عنوان موقعك على Render
‏REF_POINTS: int = int(CFG.get("REF_POINTS", 100))     # نقاط الإحالة
‏POINTS_PER_USD: int = int(CFG.get("POINTS_PER_USD", 1000))
‏SERVICE_MARKUP: float = float(CFG.get("SERVICE_MARKUP", 0.0))

# خريطة الخدمات: حدّث الأسعار بالدولار لاحقًا وفق kd1s
‏SERVICES = CFG.get("SERVICES", {
    # مثال: id على kd1s + السعر لكل 1000 بالـ USD (يمكن تركه لحين التحديث)
‏    "13021": {"name": "مشاهدات تيك توك رخيصه 😎", "usd_per_1000": None},
‏    "13400": {"name": "مشاهدات انستا رخيصه 🅰️", "usd_per_1000": None},
‏    "14527": {"name": "مشاهدات تلي ✅",          "usd_per_1000": None},
‏    "15007": {"name": "لايكات تيك توك 💎",       "usd_per_1000": None},
‏    "14676": {"name": "لايكات انستا سريعة 😎👍",  "usd_per_1000": None},
})

# ------------------------
# قاعدة البيانات (SQLite)
# ------------------------
‏DB_PATH = os.environ.get("DB_PATH", "db.sqlite3")

‏def db_conn():
‏    con = sqlite3.connect(DB_PATH)
‏    con.row_factory = sqlite3.Row
‏    return con

‏def db_init():
‏    con = db_conn()
‏    cur = con.cursor()
‏    cur.execute("""
‏        CREATE TABLE IF NOT EXISTS users(
‏            id INTEGER PRIMARY KEY,
‏            username TEXT,
‏            points INTEGER DEFAULT 0,
‏            referred_by INTEGER,
‏            banned INTEGER DEFAULT 0,
‏            joined_at TEXT
        )
    """)
‏    cur.execute("""
‏        CREATE TABLE IF NOT EXISTS orders(
‏            id INTEGER PRIMARY KEY AUTOINCREMENT,
‏            user_id INTEGER,
‏            service_id TEXT,
‏            link TEXT,
‏            quantity INTEGER,
‏            points_spent INTEGER,
‏            panel_order_id TEXT,
‏            status TEXT,
‏            created_at TEXT
        )
    """)
‏    cur.execute("""
‏        CREATE TABLE IF NOT EXISTS meta(
‏            k TEXT PRIMARY KEY,
‏            v TEXT
        )
    """)
‏    con.commit()
‏    con.close()

‏db_init()

# ------------------------
# أدوات قاعدة البيانات
# ------------------------
‏def get_user(uid: int) -> Optional[sqlite3.Row]:
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
‏    row = cur.fetchone()
‏    con.close()
‏    return row

‏def create_user(uid: int, username: Optional[str], referred_by: Optional[int] = None):
‏    con = db_conn(); cur = con.cursor()
‏    now = datetime.now(timezone.utc).isoformat()
‏    cur.execute(
‏        "INSERT OR IGNORE INTO users(id, username, points, referred_by, banned, joined_at) VALUES(?, ?, 0, ?, 0, ?)",
‏        (uid, username, referred_by, now)
    )
‏    con.commit(); con.close()

‏def set_username(uid: int, username: Optional[str]):
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
‏    con.commit(); con.close()

‏def add_points(uid: int, pts: int):
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("UPDATE users SET points = COALESCE(points,0) + ? WHERE id=?", (pts, uid))
‏    con.commit(); con.close()

‏def subtract_points(uid: int, pts: int) -> bool:
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("SELECT points FROM users WHERE id=?", (uid,))
‏    row = cur.fetchone()
‏    if not row: 
‏        con.close()
‏        return False
‏    if row["points"] < pts:
‏        con.close()
‏        return False
‏    cur.execute("UPDATE users SET points = points - ? WHERE id=?", (pts, uid))
‏    con.commit(); con.close()
‏    return True

‏def set_ban(uid: int, banned: int):
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("UPDATE users SET banned=? WHERE id=?", (banned, uid))
‏    con.commit(); con.close()

‏def stats():
‏    con = db_conn(); cur = con.cursor()
‏    cur.execute("SELECT COUNT(*) AS c FROM users"); users = cur.fetchone()["c"]
‏    cur.execute("SELECT COUNT(*) AS c FROM orders"); orders = cur.fetchone()["c"]
‏    cur.execute("SELECT COALESCE(SUM(points),0) AS s FROM users"); points = cur.fetchone()["s"]
‏    con.close()
‏    return users, orders, points

‏def create_order(user_id: int, service_id: str, link: str, quantity: int,
‏                 points_spent: int, panel_order_id: str, status: str):
‏    con = db_conn(); cur = con.cursor()
‏    now = datetime.now(timezone.utc).isoformat()
‏    cur.execute("""
‏        INSERT INTO orders(user_id, service_id, link, quantity, points_spent, panel_order_id, status, created_at)
‏        VALUES(?,?,?,?,?,?,?,?)
‏    """, (user_id, service_id, link, quantity, points_spent, panel_order_id, status, now))
‏    con.commit(); con.close()

# ------------------------
# أدوات تسعير النقاط
# ------------------------
‏def service_points_required(service_id: str, quantity: int) -> Optional[int]:
‏    s = SERVICES.get(service_id)
‏    if not s or s.get("usd_per_1000") in (None, 0):
‏        return None
‏    price_usd = float(s["usd_per_1000"]) * (1.0 + SERVICE_MARKUP)
‏    need_per_1000_pts = price_usd * POINTS_PER_USD
‏    units = quantity / 1000.0
‏    pts = math.ceil(units * need_per_1000_pts)
‏    return int(pts)

# ------------------------
# تكامل kd1s
# ------------------------
‏def kd1s_place_order(service_id: str, link: str, quantity: int) -> dict:
‏    url = "https://kd1s.com/api/v2"
‏    data = {
‏        "key": KD1S_API_KEY,
‏        "action": "add",
‏        "service": service_id,
‏        "link": link,
‏        "quantity": quantity
    }
‏    r = requests.post(url, data=data, timeout=30)
‏    r.raise_for_status()
‏    return r.json()

# ------------------------
# اشتراك إجباري
# ------------------------
‏async def ensure_channel_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
‏    user = update.effective_user
‏    if not user:
‏        return False
‏    try:
‏        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user.id)
‏        if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
‏            return True
‏        else:
‏            await prompt_join(update, context)
‏            return False
‏    except Exception as e:
‏        log.warning(f"get_chat_member failed: {e}")
        # كحل مؤقت لو في مشكلة صلاحيات، اسمح بالدخول
‏        return True

‏async def prompt_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    kb = InlineKeyboardMarkup([
‏        [InlineKeyboardButton("🔗مَـداار", url=CHANNEL_LINK)],
‏        [InlineKeyboardButton("✅ تم الاشتراك", callback_data="check_sub")]
    ])
‏    text = "⚠️اسف حبيبي، اشترك وارسل /start:\n" + CHANNEL_LINK
‏    if update.callback_query:
‏        await update.callback_query.edit_message_text(text, reply_markup=kb)
‏    else:
‏        await update.effective_message.reply_text(text, reply_markup=kb)

# ------------------------
# واجهة المستخدم
# ------------------------
‏def main_menu_kb(is_admin=False):
‏    rows = [
‏        [InlineKeyboardButton("🎁 رصيدي", callback_data="balance"),
‏         InlineKeyboardButton("🛒 اطلب خدمة", callback_data="order")],
‏        [InlineKeyboardButton("🔗 رابط الدعوة", callback_data="ref")]
    ]
‏    if is_admin:
‏        rows.append([InlineKeyboardButton("🛠️ لوحة التحكم", callback_data="admin_menu")])
‏    return InlineKeyboardMarkup(rows)

‏def services_menu_kb(page: int = 0, per_page: int = 5):
‏    keys = list(SERVICES.keys())
‏    start = page * per_page
‏    items = keys[start:start+per_page]
‏    rows = [[InlineKeyboardButton(f"{SERVICES[k]['name']}", callback_data=f"svc_{k}")]
‏            for k in items]
‏    nav = []
‏    if start > 0: nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"svc_page_{page-1}"))
‏    if start + per_page < len(keys): nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"svc_page_{page+1}"))
‏    if nav: rows.append(nav)
‏    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="home")])
‏    return InlineKeyboardMarkup(rows)

‏def admin_menu_kb():
‏    return InlineKeyboardMarkup([
‏        [InlineKeyboardButton("➕ إضافة نقاط", callback_data="adm_add_points"),
‏         InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban")],
‏        [InlineKeyboardButton("✅ إلغاء الحظر", callback_data="adm_unban"),
‏         InlineKeyboardButton("📊 إحصائيات", callback_data="adm_stats")],
‏        [InlineKeyboardButton("⬅️ رجوع", callback_data="home")]
    ])

# ------------------------
# ستارت + إحالة
# ------------------------
‏BOT_USERNAME = None  # سيتم تعيينه عند الإقلاع

‏async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    user = update.effective_user
‏    if not user:
‏        return
    # اشتراك إجباري
‏    if not await ensure_channel_join(update, context):
‏        return

    # إنشاء المستخدم إن لم يوجد
‏    ref_by = None
‏    if context.args and len(context.args) > 0 and context.args[0].startswith("ref_"):
‏        try:
‏            ref_by = int(context.args[0].split("_", 1)[1])
‏        except:  # noqa
‏            ref_by = None

‏    existed = get_user(user.id)
‏    if not existed:
‏        create_user(user.id, user.username, ref_by)
        # مكافأة إحالة
‏        if ref_by and ref_by != user.id:
‏            add_points(ref_by, REF_POINTS)
‏            try:
‏                await context.bot.send_message(chat_id=ref_by,
‏                    text=f"🎉 😂صديقك انضم من رابطك! حصلت على {REF_POINTS} نقطة.")
‏            except:  # noqa
‏                pass
        # إشعار المدير
‏        try:
‏            await context.bot.send_message(chat_id=ADMIN_ID,
‏                text=f"📩 😂مستخدم جديد دخل للبوت: @{user.username or 'بدون_اسم'} (ID: {user.id})")
‏        except:  # noqa
‏            pass
‏    else:
        # تحديث اليوزرنيم لو تغيّر
‏        if existed["username"] != (user.username or None):
‏            set_username(user.id, user.username)

‏    is_admin = (user.id == ADMIN_ID)
‏    await update.effective_message.reply_text(
        "أهلاً بك في بوت ناب ستار 😂✨\nاختر من القوائم:",
‏        reply_markup=main_menu_kb(is_admin)
    )

# زر “تم الاشتراك😂”
‏async def check_sub_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query
‏    await q.answer()
‏    if await ensure_channel_join(update, context):
‏        await start_cmd(update, context)

# ------------------------
# أزرار رئيسية
# ------------------------
‏ASK_LINK, ASK_QTY, ADM_ADD_UID, ADM_ADD_PTS, ADM_BAN_UID, ADM_UNBAN_UID = range(6)

‏async def home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    user = update.effective_user
‏    is_admin = (user.id == ADMIN_ID)
‏    await update.callback_query.edit_message_text(
        "القائمة الرئيسية:", reply_markup=main_menu_kb(is_admin)
    )

‏async def balance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    uid = update.effective_user.id
‏    if not await ensure_channel_join(update, context): return
‏    u = get_user(uid)
‏    if not u: create_user(uid, update.effective_user.username); u = get_user(uid)
‏    if u["banned"]:
‏        await q.edit_message_text("🚫 حسابك محظور😂.")
‏        return
‏    await q.edit_message_text(f"🎁 😎رصيدك الحالي: {u['points']} نقطة", reply_markup=main_menu_kb(uid==ADMIN_ID))

‏async def ref_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    uid = update.effective_user.id
‏    if not await ensure_channel_join(update, context): return
‏    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
‏    await q.edit_message_text(
‏        f"🔗 رابط إحالتك:\n{link}\n\nكل صديق ينضم يعطيك {REF_POINTS} نقطة 😂🎉",
‏        reply_markup=main_menu_kb(uid==ADMIN_ID)
    )

# ------------------------
# عملية الطلب
# ------------------------
‏async def order_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    uid = update.effective_user.id
‏    if not await ensure_channel_join(update, context): return
‏    u = get_user(uid)
‏    if u and u["banned"]:
‏        await q.edit_message_text("🚫 حسابك محظور.")
‏        return
‏    await q.edit_message_text("اختر الخدمة:", reply_markup=services_menu_kb())

‏async def svc_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
‏    q = update.callback_query; await q.answer()
‏    await q.edit_message_text("اختر الخدمة:", reply_markup=services_menu_kb(page))

‏async def svc_pick_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, service_id: str):
‏    q = update.callback_query; await q.answer()
‏    context.user_data["service_id"] = service_id
‏    await q.edit_message_text("أرسل الرابط المطلوب (URL) للخدمة:")
‏    return ASK_LINK

‏async def ask_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    link = update.message.text.strip()
‏    context.user_data["link"] = link
‏    await update.message.reply_text("جميل! أرسل الكمية المطلوبة😂 (عدد صحيح):")
‏    return ASK_QTY

‏async def ask_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    uid = update.effective_user.id
‏    qty_txt = update.message.text.strip()
‏    if not qty_txt.isdigit():
‏        await update.message.reply_text("الكمية يجب أن تكون رقماً صحيحاً. أعد الإرسال💔:")
‏        return ASK_QTY
‏    qty = int(qty_txt)
‏    service_id = context.user_data.get("service_id")
‏    link = context.user_data.get("link")

‏    need_pts = service_points_required(service_id, qty)
‏    if need_pts is None:
‏        await update.message.reply_text(
            "السعر غير محدد لهذه الخدمة بعد. سيُحدّث قريباً. اختر خدمة أخرى أو تواصل مع الدعم💔."
        )
‏        return ConversationHandler.END

‏    u = get_user(uid)
‏    if not u:
‏        create_user(uid, update.effective_user.username)
‏        u = get_user(uid)

‏    if u["banned"]:
‏        await update.message.reply_text("🚫 حسابك محظور.")
‏        return ConversationHandler.END

‏    if u["points"] < need_pts:
‏        await update.message.reply_text(
‏            f"نقاطك غير كافية.\nمطلوب: {need_pts} نقطة\nرصيدك: {u['points']} نقطة."
        )
‏        return ConversationHandler.END

    # خصم النقاط أولاً
‏    if not subtract_points(uid, need_pts):
‏        await update.message.reply_text("حدث تعارض بالرصيد، حاول مرة أخرى.")
‏        return ConversationHandler.END

‏    try:
‏        resp = kd1s_place_order(service_id, link, qty)
‏        panel_order_id = str(resp.get("order") or resp)
‏        status = "placed"
‏        create_order(uid, service_id, link, qty, need_pts, panel_order_id, status)
‏        await update.message.reply_text(
‏            f"✅ تم تنفيذ الطلب!\n"
‏            f"الخدمة: {SERVICES[service_id]['name']}\n"
‏            f"الكمية: {qty}\n"
‏            f"المطلوب: {need_pts} نقطة\n"
‏            f"رقم الطلب: {panel_order_id}"
        )
        # إشعار المدير
‏        try:
‏            await context.bot.send_message(
‏                chat_id=ADMIN_ID,
‏                text=f"🧾 طلب جديد من @{update.effective_user.username or 'بدون_اسم'}\n"
‏                     f"ID: {uid}\nخدمة: {service_id}\nكمية: {qty}\nنقاط: {need_pts}\nرقم اللوحة: {panel_order_id}"
            )
‏        except:  # noqa
‏            pass
‏    except Exception as e:
        # فشل الطلب: إعادة النقاط
‏        add_points(uid, need_pts)
‏        log.exception("KD1S order error")
‏        await update.message.reply_text("❌ فشل تنفيذ الطلب عند المزود. تم رد النقاط لحسابك.")
‏    return ConversationHandler.END

# ------------------------
# لوحة الإدارة
# ------------------------
‏async def admin_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    if update.effective_user.id != ADMIN_ID:
‏        await q.edit_message_text("غير مصرح.")
‏        return
‏    await q.edit_message_text("لوحة التحكم:", reply_markup=admin_menu_kb())

# إضافة نقاط
‏async def adm_add_points_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    if update.effective_user.id != ADMIN_ID:
‏        return
‏    await q.edit_message_text("أرسل ID المستخدم الذي تريد إضافة نقاط له:")
‏    return ADM_ADD_UID

‏async def adm_add_uid_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    if update.effective_user.id != ADMIN_ID:
‏        return ConversationHandler.END
‏    try:
‏        target = int(update.message.text.strip())
‏        context.user_data["target_uid"] = target
‏        await update.message.reply_text("كم نقطة تريد إضافتها؟ (عدد صحيح)")
‏        return ADM_ADD_PTS
‏    except:
‏        await update.message.reply_text("ID غير صحيح. أعد الإرسال:")
‏        return ADM_ADD_UID

‏async def adm_add_pts_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    if update.effective_user.id != ADMIN_ID:
‏        return ConversationHandler.END
‏    try:
‏        pts = int(update.message.text.strip())
‏        target = context.user_data.get("target_uid")
‏        create_user(target, None)
‏        add_points(target, pts)
‏        await update.message.reply_text(f"تمت إضافة {pts} نقطة للمستخدم {target}.")
‏    except:
‏        await update.message.reply_text("قيمة غير صحيحة.")
‏    return ConversationHandler.END

# حظر/إلغاء حظر
‏async def adm_ban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    if update.effective_user.id != ADMIN_ID: return
‏    await q.edit_message_text("أرسل ID المستخدم لحظره:")
‏    return ADM_BAN_UID

‏async def adm_ban_uid_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    if update.effective_user.id != ADMIN_ID:
‏        return ConversationHandler.END
‏    try:
‏        uid = int(update.message.text.strip())
‏        create_user(uid, None)
‏        set_ban(uid, 1)
‏        await update.message.reply_text(f"🚫 تم حظر المستخدم {uid}.")
‏    except:
‏        await update.message.reply_text("ID غير صحيح.")
‏    return ConversationHandler.END

‏async def adm_unban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    if update.effective_user.id != ADMIN_ID: return
‏    await q.edit_message_text("أرسل ID المستخدم لإلغاء الحظر:")
‏    return ADM_UNBAN_UID

‏async def adm_unban_uid_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    if update.effective_user.id != ADMIN_ID:
‏        return ConversationHandler.END
‏    try:
‏        uid = int(update.message.text.strip())
‏        create_user(uid, None)
‏        set_ban(uid, 0)
‏        await update.message.reply_text(f"✅ تم إلغاء الحظر عن {uid}.")
‏    except:
‏        await update.message.reply_text("ID غير صحيح.")
‏    return ConversationHandler.END

‏async def adm_stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
‏    q = update.callback_query; await q.answer()
‏    if update.effective_user.id != ADMIN_ID: return
‏    u, o, p = stats()
‏    await q.edit_message_text(f"📊 الإحصائيات:\nالمستخدمون: {u}\nالطلبات: {o}\nإجمالي النقاط المملوكة: {p}",
‏                              reply_markup=admin_menu_kb())

# ------------------------
# ربط الويب هوك مع FastAPI
# ------------------------
‏class TelegramUpdate(BaseModel):
‏    update_id: int

‏app = FastAPI()

‏application: Application = ApplicationBuilder()\
‏    .token(BOT_TOKEN)\
‏    .rate_limiter(AIORateLimiter())\
‏    .build()

‏# Handlers
‏application.add_handler(CommandHandler("start", start_cmd))
‏application.add_handler(CallbackQueryHandler(home_cb, pattern="^home$"))
‏application.add_handler(CallbackQueryHandler(balance_cb, pattern="^balance$"))
‏application.add_handler(CallbackQueryHandler(ref_cb, pattern="^ref$"))
‏application.add_handler(CallbackQueryHandler(order_cb, pattern="^order$"))
‏application.add_handler(CallbackQueryHandler(check_sub_cb, pattern="^check_sub$"))
‏application.add_handler(CallbackQueryHandler(admin_menu_cb, pattern="^admin_menu$"))
‏application.add_handler(CallbackQueryHandler(adm_add_points_cb, pattern="^adm_add_points$"))
‏application.add_handler(CallbackQueryHandler(adm_ban_cb, pattern="^adm_ban$"))
‏application.add_handler(CallbackQueryHandler(adm_unban_cb, pattern="^adm_unban$"))
‏application.add_handler(CallbackQueryHandler(adm_stats_cb, pattern="^adm_stats$"))
‏application.add_handler(CallbackQueryHandler(lambda u, c: svc_page_cb(u, c, int(u.callback_query.data.split("_")[-1])), pattern="^svc_page_\\d+$"))
‏application.add_handler(CallbackQueryHandler(lambda u, c: svc_pick_cb(u, c, u.callback_query.data.split("_",1)[1]), pattern="^svc_\\d+$"))

‏# Conversations
‏order_conv = ConversationHandler(
‏    entry_points=[],
‏    states={
‏        ASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_link)],
‏        ASK_QTY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_qty)],
    },
‏    fallbacks=[],
‏    map_to_parent={}
)
‏application.add_handler(order_conv)

‏admin_conv = ConversationHandler(
‏    entry_points=[],
‏    states={
‏        ADM_ADD_UID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_uid_msg)],
‏        ADM_ADD_PTS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_pts_msg)],
‏        ADM_BAN_UID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_ban_uid_msg)],
‏        ADM_UNBAN_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_unban_uid_msg)],
    },
‏    fallbacks=[],
‏    map_to_parent={}
)
‏application.add_handler(admin_conv)

‏@app.on_event("startup")
‏async def on_startup():
‏    global BOT_USERNAME
‏    me = await application.bot.get_me()
‏    BOT_USERNAME = me.username
    # تعيين الويب هوك
‏    webhook_url = f"{BASE_URL}/webhook/{BOT_TOKEN}"
‏    await application.bot.set_webhook(url=webhook_url, allowed_updates=["message","callback_query"])
‏    log.info(f"Webhook set to: {webhook_url}")

‏@app.post("/webhook/{token}")
‏async def telegram_webhook(token: str, request: Request):
‏    if token != BOT_TOKEN:
‏        raise HTTPException(status_code=403, detail="Invalid token in path.")
‏    data = await request.json()
‏    update = Update.de_json(data, application.bot)
‏    await application.process_update(update)
‏    return JSONResponse({"ok": True})

‏@app.get("/health")
‏async def health():
‏    return {"status": "ok"}

# لتشغيل محلياً: uvicorn main:app --port 10000
