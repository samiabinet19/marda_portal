import logging
import sqlite3
import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

------------------ Render Port Health Check Server ------------------

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

------------------ 1. መቼቶች (Configuration) ------------------

TOKEN = "8647816372:AAGG43oY-pndgRXT6V_E_REyW1zTHQ0-jrs"

# አድሚኖች
ADMIN_IDS = [7857140781, 7619940687]

VIP_LINK = "https://t.me/+YourVIPPrivateChannelLinkHere"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Render Persistent Storage - ዳታ እንዳይጠፋ ማስተካከያ
DATA_DIR = "/var/data" if os.path.exists("/var/data") else "."
DB_NAME = os.path.join(DATA_DIR, "bot_database.db")
BASE_BATCH_DIR = os.path.join(DATA_DIR, "batch_folders")

def is_admin(user_id: int) -> bool:
    """ ተጠቃሚው አድሚን መሆኑን ያረጋግጣል """
    return user_id in ADMIN_IDS

------------------ 2. ፎልደር እና SQLite Database ዝግጅት ------------------

def init_batch_folders():
    """ ከባች 15 እስከ ባች 50 ያሉ ፎልደሮችን በራሱ ይፈጥራል """
    os.makedirs(BASE_BATCH_DIR, exist_ok=True)
    for b in range(15, 51):
        folder_path = os.path.join(BASE_BATCH_DIR, f"Batch_{b}")
        os.makedirs(folder_path, exist_ok=True)

def init_db():
    """ ዳታቤዝ ይፈጥራል፤ WAL mode በማብራት ላትና ላግ ያስወግዳል """
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()

    cursor.execute('PRAGMA journal_mode=WAL;')
    cursor.execute('PRAGMA synchronous=NORMAL;')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT DEFAULT 'አልተመዘገበም',
            phone TEXT DEFAULT 'አልተመዘገበም',
            batch TEXT DEFAULT 'ያልተመረጠ',
            payment_status TEXT DEFAULT 'አልተከፈለም',
            balance REAL DEFAULT 0.0,
            is_banned INTEGER DEFAULT 0,
            payment_date TEXT DEFAULT ''
        )
    ''')
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN batch TEXT DEFAULT "ያልተመረጠ"')
    except Exception:
        pass
        
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            photo_id TEXT,
            amount REAL DEFAULT 100.0,
            payment_date TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, phone, batch, payment_status, balance, is_banned, payment_date FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'name': row[1],
            'phone': row[2],
            'batch': row[3],
            'payment_status': row[4],
            'balance': row[5],
            'is_banned': row[6],
            'payment_date': row[7]
        }
    return None

def add_user_if_not_exists(user_id: int):
    user = get_user(user_id)
    if not user:
        conn = sqlite3.connect(DB_NAME, timeout=30.0)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

def update_user(user_id: int, **kwargs):
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = ? WHERE user_id = ?', (value, user_id))
    conn.commit()
    conn.close()

def get_paid_users_only():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, name, phone, batch, payment_date, payment_status
        FROM users
        WHERE payment_status = 'ፅድቋል (Approved)'
    ''')
    rows = cursor.fetchall()
    conn.close()

    paid_users = []
    for row in rows:
        paid_users.append({
            'user_id': row[0],
            'name': row[1],
            'phone': row[2],
            'batch': row[3],
            'payment_date': row[4],
            'payment_status': row[5]
        })
    return paid_users

def get_users_by_batch(batch_name: str):
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, name, phone, payment_status, payment_date, is_banned
        FROM users
        WHERE batch = ?
    ''', (batch_name,))
    rows = cursor.fetchall()
    conn.close()

    users = []
    for row in rows:
        users.append({
            'user_id': row[0],
            'name': row[1],
            'phone': row[2],
            'payment_status': row[3],
            'payment_date': row[4],
            'is_banned': row[5]
        })
    return users

def record_payment_history(user_id: int, photo_id: str, status: str):
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute('''
        INSERT INTO payment_history (user_id, photo_id, payment_date, status)
        VALUES (?, ?, ?, ?)
    ''', (user_id, photo_id, today_str, status))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, phone, batch, payment_status, balance, is_banned, payment_date FROM users')
    rows = cursor.fetchall()
    conn.close()
    users = []
    for row in rows:
        users.append({
            'user_id': row[0],
            'name': row[1],
            'phone': row[2],
            'batch': row[3],
            'payment_status': row[4],
            'balance': row[5],
            'is_banned': row[6],
            'payment_date': row[7]
        })
    return users

------------------ 3. የወርሃዊ ክፍያ ማስታወሻ ------------------

async def check_expired_payments_logic(bot):
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, payment_date FROM users WHERE payment_status = ?', ('ፅድቋል (Approved)',))
    approved_users = cursor.fetchall()
    conn.close()

    today = datetime.now()
    for uid, name, p_date_str in approved_users:
        if p_date_str:
            try:
                clean_date_str = p_date_str.split(' ')[0]
                p_date = datetime.strptime(clean_date_str, "%Y-%m-%d")
                days_passed = (today - p_date).days
                if days_passed == 28:
                    await bot.send_message(
                        chat_id=uid,
                        text=f"⚠️ <b>የክፍያ ማስታወሻ!</b>\n\nሰላም <b>{name}</b>፣ ክፍያዎ ለማለቅ <b>2 ቀን ብቻ</b> ቀርቶታል። አገልግሎቱ እንዳይቋረጥብዎ ያድሱ።",
                        parse_mode=ParseMode.HTML
                    )
                elif days_passed >= 30:
                    update_user(uid, payment_status='ጊዜው ያለፈበት (Expired)')
                    await bot.send_message(
                        chat_id=uid,
                        text=f"🔔 <b>የክፍያ ጊዜዎ አብቅቷል!</b>\n\nሰላም <b>{name}</b>፣ 30 ቀናት ስለሞሉ የወሩ ክፍያ ጊዜዎ አብቅቷል። በ <b>'💳 ክፍያ ፈፅም'</b> በኩል ድጋሚ ይክፈሉ።",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logging.error(f"Error checking user {uid}: {e}")

async def background_payment_checker(app):
    while True:
        try:
            await check_expired_payments_logic(app.bot)
        except Exception as e:
            logging.error(f"Background checker error: {e}")
        await asyncio.sleep(43200)

------------------ 4. Keyboards & States ------------------

REG_NAME, REG_PHONE, REG_BATCH = range(3)
PAY_RECEIPT = 3
BROADCAST_STATE = 4
BATCH_MSG_STATE, BATCH_PDF_STATE = range(5, 7)

def main_menu(user_id: int) -> InlineKeyboardMarkup:
    user = get_user(user_id)
    keyboard = [
        [InlineKeyboardButton("🏫 ስለ ትምህርት ቤቱ (School Info)", callback_data='school_info')],
        [InlineKeyboardButton("📝 አዲስ ምዝገባ (Register)", callback_data='register')],
        [InlineKeyboardButton("💳 ክፍያ ፈፅም (Pay)", callback_data='pay')],
        [InlineKeyboardButton("👤 የመገለጫ መረጃ (Profile)", callback_data='profile')],
        [InlineKeyboardButton("💰 የሂሳብ ባላንስ (Wallet)", callback_data='wallet')]
    ]

    if user and user['payment_status'] == 'ፅድቋል (Approved)':
        keyboard.append([InlineKeyboardButton("🌟 VIP ቻናል መግቢያ", url=VIP_LINK)])
    keyboard.append([InlineKeyboardButton("📞 ግንኙነት (Contact)", callback_data='contact')])
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ የአድሚን ገጽ (Admin)", callback_data='admin_panel')])
    return InlineKeyboardMarkup(keyboard)

def get_batches_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for b in range(15, 51):
        b_name = f"{b}ኛ ባች"
        row.append(InlineKeyboardButton(f"{b}ኛ", callback_data=f"reg_batch_{b_name}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ወደ ዋና ማውጫ ተመለስ", callback_data='main')]])

async def is_banned(update: Update) -> bool:
    user_id = update.effective_user.id
    user = get_user(user_id)
    if user and user['is_banned'] == 1:
        if update.message:
            await update.message.reply_text("❌ እርስዎ ከዚህ ቦት ታግደዋል!", parse_mode=ParseMode.HTML)
        elif update.callback_query:
            try:
                await update.callback_query.answer("❌ እርስዎ ከዚህ ቦት ታግደዋል!", show_alert=True)
            except Exception:
                pass
        return True
    return False

------------------ 5. User Registration & Payment Handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return
    user_id = update.effective_user.id
    add_user_if_not_exists(user_id)

    text = "እንኳን ወደ ፖርታሉ በሰላም መጡ! 👋\nእባክዎን የሚፈልጉትን አገልግሎት ይምረጡ፡"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu(user_id))
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu(user_id))

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.edit_message_text("📝 የምዝገባ ፎርም\n\nእባክዎን ሙሉ ስምዎን ይፃፉልን፡\n\n(ለማቋረጥ /cancel ይበሉ)", parse_mode=ParseMode.HTML)
    return REG_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    add_user_if_not_exists(user_id)
    update_user(user_id, name=update.message.text)
    await update.message.reply_text("በጣም ጥሩ! አሁን የስልክ ቁጥርዎን ያስገቡ፡", parse_mode=ParseMode.HTML)
    return REG_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    update_user(user_id, phone=update.message.text)

    await update.message.reply_text(
        "አሁን ደግሞ <b>የተመደቡበትን ባች (ከባች 15 - ባች 50)</b> ይምረጡ፡",
        reply_markup=get_batches_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return REG_BATCH

async def get_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    selected_batch = query.data.replace('reg_batch_', '')
    update_user(user_id, batch=selected_batch)
    user = get_user(user_id)
    await query.edit_message_text(
        f"✅ <b>ምዝገባዎ ተጠናቋል!</b>\n\n"
        f"👤 <b>ስም:</b> {user['name']}\n"
        f"📞 <b>ስልክ:</b> {user['phone']}\n"
        f"🎓 <b>ባች:</b> {user['batch']}\n\n"
        f"አሁን <b>'💳 ክፍያ ፈፅም'</b> የሚለውን በመጫን ደረሰኝ ማስገባት ይችላሉ።",
        reply_markup=main_menu(user_id),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    query = update.callback_query

    payment_info = (
        "💳 <b>የክፍያ መረጃ</b>\n\n"
        "እባክዎን የቦቱን አገልግሎት ለማግኘት ክፍያውን በታች ባሉት አካውንቶች ይላኩ፡\n\n"
        "• <b>CBE (ንግድ ባንክ):</b> <code>1000579602264</code>\n"
        "• <b>Telebirr:</b> <code>0966089190</code>\n\n"
        "ከከፈሉ በኋላ የከፈሉበትን <b>ደረሰኝ (የደረሰኝ ፎቶ/Screenshot)</b> እዚህ ይላኩሊን፡"
    )
    await query.edit_message_text(payment_info, parse_mode=ParseMode.HTML, reply_markup=back_menu())
    return PAY_RECEIPT

async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    photo_file_id = photo.file_id

    add_user_if_not_exists(user_id)
    update_user(user_id, payment_status='በማረጋገጥ ላይ (Pending)')
    user = get_user(user_id)
    user_batch = user.get('batch', '')
    if user_batch and "ባች" in user_batch:
        batch_num = ''.join(filter(str.isdigit, user_batch))
        if batch_num:
            target_folder = os.path.join(BASE_BATCH_DIR, f"Batch_{batch_num}")
            os.makedirs(target_folder, exist_ok=True)
            try:
                receipt_file = await context.bot.get_file(photo_file_id)
                time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"user_{user_id}_{time_stamp}.jpg"
                file_path = os.path.join(target_folder, file_name)
                await receipt_file.download_to_drive(file_path)
            except Exception as e:
                logging.error(f"Failed to save image in batch folder: {e}")

    record_payment_history(user_id, photo_file_id, "በማረጋገጥ ላይ")
    await update.message.reply_text(
        "✅ <b>ደረሰኝዎ ደርሶናል!</b>\nአድሚኑ አረጋግጦ እስኪያፀድቀው ድረስ እባክዎን ትንሽ ይታገሱ።",
        reply_markup=main_menu(user_id),
        parse_mode=ParseMode.HTML
    )

    admin_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ አፅድቅ", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ ውደቅ አድርግ", callback_data=f"reject_{user_id}")
        ]
    ])
    caption = (
        f"📩 <b>አዲስ የክፍያ ደረሰኝ ደርሷል!</b>\n\n"
        f"👤 <b>ስም:</b> {user['name']}\n"
        f"📞 <b>ስልክ:</b> {user['phone']}\n"
        f"🎓 <b>ባች:</b> {user['batch']}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=caption,
                reply_markup=admin_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Failed to send to admin {admin_id}: {e}")
    return ConversationHandler.END

async def invalid_receipt_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ እባክዎን የደረሰኝ ፎቶ (Photo/Screenshot) ብቻ ይላኩ!\n\nሂደቱን ለማቋረጥ ከፈለጉ /cancel የሚለውን ይጫኑ።",
        parse_mode=ParseMode.HTML
    )
    return PAY_RECEIPT

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        return

    data = query.data.split('_')
    action = data[0]
    target_user_id = int(data[1])

    if action == "approve":
        today_str = datetime.now().strftime("%Y-%m-%d")
        update_user(target_user_id, payment_status='ፅድቋል (Approved)', balance=100.0, payment_date=today_str)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ <b>ሁኔታ:</b> ክፍያው ፅድቋል!",
            reply_markup=None,
            parse_mode=ParseMode.HTML
        )
        vip_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🌟 ወደ VIP ቻናል ይግቡ", url=VIP_LINK)]])
        await context.bot.send_message(
            chat_id=target_user_id,
            text="🎉 <b>እንኳን ደስ አለዎት!</b> ክፍያዎ በአድሚኑ ተረጋግጦ ፅድቋል። አሁን VIP ቻናላችንን መቀላቀል ይችላሉ!",
            reply_markup=vip_markup,
            parse_mode=ParseMode.HTML
        )
    elif action == "reject":
        update_user(target_user_id, payment_status='ተሰርዟል (Rejected)')
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n❌ <b>ሁኔታ:</b> ክፍያው ውድቅ ተደርጓል!",
            reply_markup=None,
            parse_mode=ParseMode.HTML
        )
        await context.bot.send_message(
            chat_id=target_user_id,
            text="❌ <b>ክፍያዎ አልፀደቀም።</b> እባክዎን ትክክለኛ ደረሰኝ መላክዎን ያረጋግጡ።",
            parse_mode=ParseMode.HTML
        )

------------------ 6. የአድሚን ክፍሎች (Admin Dashboard & Batch Announcements) ------------------

async def show_admin_panel(query, context):
    all_users = get_all_users()
    paid_users = get_paid_users_only()

    report = (
        f"⚙️ <b>የአድሚን መቆጣጠሪያ Dashboard</b>\n\n"
        f"👥 <b>ጠቅላላ ተጠቃሚዎች:</b> {len(all_users)}\n"
        f"✅ <b>ከፍለው ደረሰኝ ያፀደቁ:</b> {len(paid_users)} ተጠቃሚዎች\n\n"
        f"ከታች ያሉትን ቁልፎች በመጠቀም ዝርዝር ማየት እና ማስታወቂያ መላክ ይችላሉ፦"
    )
    admin_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ከፍለው ደረሰኝ የላኩ ብቻ (Paid)", callback_data='show_paid_only')],
        [InlineKeyboardButton("📁 በባች አስተዳድር/ማስታወቂያ ላክ (Manage Batch)", callback_data='show_batch_menu')],
        [InlineKeyboardButton("📢 ለአጠቃላይ ተጠቃሚ መልእክት (General Broadcast)", callback_data='start_broadcast')],
        [InlineKeyboardButton("🔄 ክፍያዎችን በግድ ፈትሽ", callback_data='force_check_payments')],
        [InlineKeyboardButton("⬅️ ወደ ዋና ማውጫ", callback_data='main')]
    ])
    await query.edit_message_text(report, reply_markup=admin_buttons, parse_mode=ParseMode.HTML)

async def show_batch_selector_admin(query, context):
    keyboard = []
    row = []
    for b in range(15, 51):
        b_name = f"{b}ኛ ባች"
        row.append(InlineKeyboardButton(f"{b}ኛ", callback_data=f"adm_batch_{b_name}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⬅️ ወደ አድሚን ገጽ", callback_data='admin_panel')])
    await query.edit_message_text(
        "📁 <b>የትኛውን ባች ማስተዳደር/ማስታወቂያ መላክ ትፈልጋለህ?</b>\n\nማየት የሚፈልጉትን ባች ይምረጡ፦",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def show_batch_options_menu(query, context, batch_name):
    users = get_users_by_batch(batch_name)
    text = f"📂 የ {batch_name} መቆጣጠሪያ\n\nበዚህ ባች ውስጥ የተመዘገቡ ተጠቃሚዎች ብዛት፦ {len(users)}\n\nእባክዎን ማድረግ የሚፈልጉትን ይምረጡ፦"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 የጽሑፍ ማስታወቂያ ላክ", callback_data=f"btn_send_txt_{batch_name}")],
        [InlineKeyboardButton("📄 PDF/ሰነድ ማስታወቂያ ላክ", callback_data=f"btn_send_pdf_{batch_name}")],
        [InlineKeyboardButton("📋 አባላቱን ዝርዝር እይ", callback_data=f"btn_list_users_{batch_name}")],
        [InlineKeyboardButton("⬅️ ወደ ባች መረጣ ተመለስ", callback_data='show_batch_menu')]
    ])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def display_specific_batch_users(query, context, batch_name):
    users = get_users_by_batch(batch_name)

    if not users:
        text = f"📂 <b>{batch_name}</b> ውስጥ እስካሁን የተመዘገበ ተጠቃሚ የለም።"
    else:
        text = f"📂 <b>የ{batch_name} አባላት ዝርዝር ({len(users)})፦</b>\n\n"
        for idx, u in enumerate(users, 1):
            text += (
                f"{idx}. <b>ስም:</b> {u['name']}\n"
                f"   <b>ስልክ:</b> {u['phone']}\n"
                f"   <b>ክፍያ:</b> {u['payment_status']}\n"
                f"   <b>ID:</b> <code>{u['user_id']}</code>\n"
                f"   -------------------\n"
            )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ ወደ ባች አማራጭ ተመለስ", callback_data=f"adm_batch_{batch_name}")],
        [InlineKeyboardButton("⬅️ ወደ አድሚን ገጽ", callback_data='admin_panel')]
    ])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def prompt_batch_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    batch_name = query.data.replace('btn_send_txt_', '')
    context.user_data['target_batch'] = batch_name

    await query.edit_message_text(
        f"💬 <b>ለ{batch_name} የሚላክ የጽሑፍ ማስታወቂያ</b>\n\n"
        f"ለ <b>{batch_name}</b> ተማሪዎች ብቻ እንዲላክ የሚፈልጉትን መልእክት ይፃፉ፦\n\n"
        f"(ለማቋረጥ /cancel ይበሉ)",
        parse_mode=ParseMode.HTML
    )
    return BATCH_MSG_STATE

async def send_batch_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    batch_name = context.user_data.get('target_batch')
    msg_text = update.message.text
    users = get_users_by_batch(batch_name)
    if not users:
        await update.message.reply_text(f"❌ በ{batch_name} ውስጥ የተመዘገበ ምንም ተጠቃሚ አልተገኘም።")
        return ConversationHandler.END

    success, failed = 0, 0
    await update.message.reply_text(f"⏳ መልእክቱ ለ {batch_name} ተማሪዎች እየተላከ ነው...")
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'],
                    text=f"📢 <b>የ{batch_name} ማስታወቂያ፦</b>\n\n{msg_text}",
                    parse_mode=ParseMode.HTML
                )
                success += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"✅ <b>የ{batch_name} ማስታወቂያ ተላከ!</b>\n\n• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def prompt_batch_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    batch_name = query.data.replace('btn_send_pdf_', '')
    context.user_data['target_batch'] = batch_name

    await query.edit_message_text(
        f"📄 <b>ለ{batch_name} የሚላክ PDF / ሰነድ ማስታወቂያ</b>\n\n"
        f"እባክዎን ለ <b>{batch_name}</b> የሚላከውን PDF ፋይል እዚህ ያያይዙልን (Upload)።\n"
        f"<i>(ፋይሉ በራስ-ሰር በ {batch_name} ፎልደር ውስጥ ይቀመጣል!)</i>\n\n"
        f"(ለማቋረጥ /cancel ይበሉ)",
        parse_mode=ParseMode.HTML
    )
    return BATCH_PDF_STATE

async def send_batch_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    batch_name = context.user_data.get('target_batch')
    doc = update.message.document
    caption = update.message.caption or f"📄 የ{batch_name} ማስታወቂያ PDF"
    
    batch_num = ''.join(filter(str.isdigit, batch_name))
    target_folder = os.path.join(BASE_BATCH_DIR, f"Batch_{batch_num}")
    os.makedirs(target_folder, exist_ok=True)
    
    file_path = os.path.join(target_folder, doc.file_name or f"announcement_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    await update.message.reply_text(f"⏳ ፋይሉ በ {batch_name} ፎልደር ውስጥ እየተቀመጠ እና ለተማሪዎች እየተላከ ነው...")
    
    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(file_path)
    except Exception as e:
        logging.error(f"Failed to save PDF in batch folder: {e}")

    users = get_users_by_batch(batch_name)
    success, failed = 0, 0
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_document(
                    chat_id=u['user_id'],
                    document=doc.file_id,
                    caption=f"📢 <b>የ{batch_name} ማስታወቂያ PDF፦</b>\n\n{caption}",
                    parse_mode=ParseMode.HTML
                )
                success += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"✅ <b>የ{batch_name} PDF ማስታወቂያ በተሳካ ሁኔታ ተላከ!</b>\n\n"
        f"📂 <b>የተቀመጠበት ፎልደር:</b> <code>{file_path}</code>\n"
        f"• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

------------------ 7. General Admin Broadcast & Ban Handlers ------------------

async def show_paid_users_list(query, context):
    paid_users = get_paid_users_only()

    if not paid_users:
        text = "❌ <b>እስካሁን ደረሰኝ ልከው ክፍያቸው የጸደቀላቸው ተጠቃሚዎች የሉም።</b>"
    else:
        text = f"✅ <b>ከፍለው ደረሰኝ የላኩ ተጠቃሚዎች ዝርዝር ({len(paid_users)})</b>\n\n"
        for idx, u in enumerate(paid_users, 1):
            text += (
                f"{idx}. <b>ስም:</b> {u['name']}\n"
                f"   <b>ስልክ:</b> {u['phone']}\n"
                f"   <b>ባች:</b> {u['batch']}\n"
                f"   <b>የተከፈለበት ቀን:</b> {u['payment_date']}\n"
                f"   <b>ID:</b> <code>{u['user_id']}</code>\n"
                f"   -------------------\n"
            )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ወደ አድሚን ገጽ", callback_data='admin_panel')]])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "📢 አጠቃላይ የብሮድካስት መልእክት\n\nለሁሉም ተጠቃሚዎች እንዲላክ የሚፈልጉትን መልእክት ይጻፉልኝ፡\n\n(ለማቋረጥ /cancel ይበሉ)",
        parse_mode=ParseMode.HTML
    )
    return BROADCAST_STATE

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    broadcast_msg = update.message.text
    users = get_all_users()
    success, failed = 0, 0
    await update.message.reply_text("⏳ መልእክቱ እየተላከ ነው...")
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'],
                    text=f"📢 <b>ማስታወቂያ ከፖርታሉ:</b>\n\n{broadcast_msg}",
                    parse_mode=ParseMode.HTML
                )
                success += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"✅ <b>ብሮድካስት ተጠናቋል!</b>\n\n• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>የተጠቃሚውን ID አብረው ይጻፉ!</b>\n\n<b>ምሳሌ፦</b> <code>/ban 8711072926</code>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(context.args[0])
        user = get_user(target_id)
        if not user:
            await update.message.reply_text("❌ ይህ ተጠቃሚ በዳታቤዝ ውስጥ አልተገኘም።")
            return
        update_user(target_id, is_banned=1)
        await update.message.reply_text(
            f"🚫 ተጠቃሚ <b>{user['name']}</b> (ID: <code>{target_id}</code>) በተሳካ ሁኔታ ታግዷል!",
            parse_mode=ParseMode.HTML
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="❌ <b>እርስዎ ከዚህ ቦት በአድሚኑ ታግደዋል!</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("⚠️ እባክዎን ትክክለኛ የቁጥር ID ያስገቡ!")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>የተጠቃሚውን ID አብረው ይጻፉ!</b>\n\n<b>ምሳሌ፦</b> <code>/unban 8711072926</code>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        target_id = int(context.args[0])
        user = get_user(target_id)
        if not user:
            await update.message.reply_text("❌ ይህ ተጠቃሚ በዳታቤዝ ውስጥ አልተገኘም።")
            return
        update_user(target_id, is_banned=0)
        await update.message.reply_text(
            f"✅ ተጠቃሚ <b>{user['name']}</b> (ID: <code>{target_id}</code>) ከእገዳ ነፃ ወጥቷል!",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("⚠️ እባክዎን ትክክለኛ የቁጥር ID ያስገቡ!")

------------------ 8. Other Button Handlers ------------------

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return
    query = update.callback_query
    user_id = update.effective_user.id

    try:
        await query.answer()
    except Exception:
        pass

    if query.data == 'main':
        await start(update, context)
    elif query.data == 'show_paid_only' and is_admin(user_id):
        await show_paid_users_list(query, context)
    elif query.data == 'show_batch_menu' and is_admin(user_id):
        await show_batch_selector_admin(query, context)
    elif query.data.startswith('adm_batch_') and is_admin(user_id):
        batch_name = query.data.replace('adm_batch_', '')
        await show_batch_options_menu(query, context, batch_name)
    elif query.data.startswith('btn_list_users_') and is_admin(user_id):
        batch_name = query.data.replace('btn_list_users_', '')
        await display_specific_batch_users(query, context, batch_name)
    elif query.data == 'force_check_payments' and is_admin(user_id):
        await query.edit_message_text("⏳ ክፍያዎች እየተፈተሹ ነው...")
        await check_expired_payments_logic(context.bot)
        await query.edit_message_text("✅ የክፍያ ማስታወሻዎች በተሳካ ሁኔታ ተላኩ!", reply_markup=back_menu())
    elif query.data == 'school_info':
        school_menu = InlineKeyboardMarkup([
            [InlineKeyboardButton("ℹ️ ስለ ትምህርት ቤቱ", callback_data='about_school')],
            [InlineKeyboardButton("📚 የሚሰጡ ትምህርቶች", callback_data='school_courses')],
            [InlineKeyboardButton("📢 ወቅታዊ ማስታወቂያዎች", callback_data='school_news')],
            [InlineKeyboardButton("⬅️ ወደ ዋና ማውጫ", callback_data='main')]
        ])
        await query.edit_message_text(
            "🏫 <b>የትምህርት ቤቱ መረጃ እና ማስታወቂያዎች</b>\n\nእባክዎን ማወቅ የሚፈልጉትን መረጃ ከታች ይምረጡ፡",
            reply_markup=school_menu,
            parse_mode=ParseMode.HTML
        )
    elif query.data == 'about_school':
        text = (
            "ℹ️ <b>ስለ ትምህርት ቤታችን</b>\n\n"
            "ትምህርት ቤታችን በዘመናዊ የትምህርት አሰጣጥ እና በቴክኖሎጂ የተደገፈ ጥራት ያለው ትምህርት ለመስጠት የተቋቋመ ነው፡\n\n"
            "🎯 <b>ራዕይ:</b> በደንብ የዳበረ እና ከልምድ ወጥቶ በትምህርት የታገዘ የሳውንድ እውቀት ያለው ባለሙያ መፍጠር።\n"
            "⭐ <b>ተልዕኮ:</b> ጥራት ያለውና ተመጣጣኝ ትምህርት ለሁሉም ማዳረስ。"
        )
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ወደ ትምህርት ቤት ማውጫ", callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'school_courses':
        text = (
            "📚 <b>የሚሰጡ ትምህርቶች እና ኮርሶች</b>\n\n"
            "1. 💻 <b>Audio Fundamentals & Sound Physics:</b> የድምፅ ሞገድ ባህሪያት (Frequency, Amplitude, Phase)፣ የሰው ልጅ የመስማት ሂደት (Psychoacoustics) እና የክፍል አካውስቲክስ (Room Acoustics) መሠረታዊ ሕጎችን ይሸፍናል\n"
            "2. 🇬🇧 <b>የDigital Audio Workstations (DAW) & Signal Flow:</b> እንደ Pro Tools, Logic Pro ወይም Ableton ያሉ ሶፍትዌሮችን አጠቃቀም፣ የማይክሮፎን አይነቶችንና አቀማመጥ፣ እንዲሁም የኦዲዮ ሲግናል ፍሰትን (Signal Routing) ያስተምራል።\n"
            "3. 📐 <b>የMixing & Mastering Engineering:</b> የተለያዩ የተቀረፁ የድምፅ መስመሮችን (Multi-track audio) አዋህዶ ሚዛናዊ ማድረግ (Mixing) እና ለመጨረሻው ዲጂታል ስርጭት ጥራቱን ጠብቆ ማዘጋጀትን (Mastering) ያተኩራል"
        )
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ወደ ትምህርት ቤት ማውጫ", callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'school_news':
        text = (
            "📢 <b>ወቅታዊ ማስታወቂያዎች</b>\n\n"
            "📌 <b>ለአዲሱ መንፈቅ ዓመት የምዝገባ ጥሪ!</b>\n"
            "የአዲሱ ትምህርት ዘመን ምዝገባ ተጀምሯል። ቦታዎች ሳይሞሉ በፍጥነት ይመዝገቡ።\n\n"
            "📅 <b>የክፍል መጀመሪያ ቀን:</b> የፊታችን ሰኞ\n"
            "💡 ለተጨማሪ መረጃ የ 'Contact' ቁልፍን በመጫን ያግኙን。"
        )
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ ወደ ትምህርት ቤት ማውጫ", callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'profile':
        user = get_user(user_id)
        p_date = user['payment_date'] if user['payment_date'] else "ያልተመዘገበ"
        profile_text = (
            f"📋 <b>የመገለጫ መረጃ</b>\n\n"
            f"• <b>ስም:</b> {user['name']}\n"
            f"• <b>ስልክ:</b> {user['phone']}\n"
            f"• <b>ባች:</b> {user['batch']}\n"
            f"• <b>የክፍያ ሁኔታ:</b> {user['payment_status']}\n"
            f"• <b>መጨረሻ የተከፈለው:</b> {p_date}\n"
            f"• <b>የአካውንት ባላንስ:</b> {user['balance']} ETB"
        )
        await query.edit_message_text(profile_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'wallet':
        user = get_user(user_id)
        wallet_text = (
            f"💰 <b>የእርስዎ የሂሳብ ባላንስ (Wallet)</b>\n\n"
            f"• <b>ያለዎት ባላንስ:</b> <code>{user['balance']} ETB</code>\n\n"
            f"💡 ባላንስዎን ለመጨመር በ '💳 ክፍያ ፈፅም' በኩል ደረሰኝ ያስገቡ።"
        )
        await query.edit_message_text(wallet_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'contact':
        contact_text = (
            "📞 <b>እኛን ለማግኘት:</b>\n\n"
            "• <b>ስልክ:</b> +251966089190\n"
            "• <b>ኢሜይል:</b> samiabinet19@gmail.com\n"
            "• <b>አድራሻ:</b> አዲስ አበባ ልዩ ስሙ መገናኛ ከዘፍነሽ ሞል ፊት ለፊት ያለው አቢስንያ ባንክ የሚገኝበት ላይ ሁለተኛ ፍቅ፣ ኢትዮጵያ"
        )
        await query.edit_message_text(contact_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'admin_panel':
        if is_admin(user_id):
            await show_admin_panel(query, context)
        else:
            await query.edit_message_text("❌ ይህንን ገጽ ለማየት ፈቃድ የሎትም።", reply_markup=back_menu())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message:
        await update.message.reply_text("❌ ሂደቱ ተቋርጧል።", reply_markup=main_menu(user_id))
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text("❌ ሂደቱ ተቋርጧል።", reply_markup=main_menu(user_id))
        except Exception:
            await context.bot.send_message(chat_id=user_id, text="❌ ሂደቱ ተቋርጧል።", reply_markup=main_menu(user_id))
    return ConversationHandler.END

------------------ 9. ዋና ማስኪያጃ (Main Execution) ------------------

async def post_init(app):
    asyncio.create_task(background_payment_checker(app))

if __name__ == '__main__':
    threading.Thread(target=run_health_server, daemon=True).start()

    init_db()
    init_batch_folders()
    
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    reg_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_registration, pattern='^register$')],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            REG_BATCH: [
                CallbackQueryHandler(get_batch, pattern='^reg_batch_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text(
                    "⚠️ እባክዎን ከላይ ካሉት የባች ቁልፎች ውስጥ አንዱን ይምረጡ!", parse_mode=ParseMode.HTML
                ))
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('start', cancel),
            CallbackQueryHandler(cancel, pattern='^main$')
        ],
        allow_reentry=True
    )

    pay_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_payment, pattern='^pay$')],
        states={
            PAY_RECEIPT: [
                MessageHandler(filters.PHOTO, receive_receipt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_receipt_format)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('start', cancel),
            CallbackQueryHandler(cancel, pattern='^main$')
        ],
        allow_reentry=True
    )

    broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_broadcast, pattern='^start_broadcast$')],
        states={
            BROADCAST_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('start', cancel),
            CallbackQueryHandler(cancel, pattern='^main$')
        ],
        allow_reentry=True
    )

    batch_announcement_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(prompt_batch_msg, pattern='^btn_send_txt_'),
            CallbackQueryHandler(prompt_batch_pdf, pattern='^btn_send_pdf_')
        ],
        states={
            BATCH_MSG_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_batch_text_message)],
            BATCH_PDF_STATE: [MessageHandler(filters.Document.ALL, send_batch_pdf_document)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('start', cancel),
            CallbackQueryHandler(cancel, pattern='^main$')
        ],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(reg_handler)
    app.add_handler(pay_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(batch_announcement_handler)
    app.add_handler(CallbackQueryHandler(handle_admin_action, pattern='^(approve|reject)_'))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    print("🚀 ቦቱ Render ላይ በተሳካ ሁኔታ ስራ ጀምሯል...")
    app.run_polling()
