import logging
import psycopg2
import asyncio
import os
import threading
import html
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, filters, ContextTypes

class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is alive!')

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logging.info(f'Health server running on port {port}')
    server.serve_forever()
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise RuntimeError('BOT_TOKEN environment variable is missing. Add BOT_TOKEN in Render Environment Variables.')
ADMIN_IDS = [7857140781, 7619940687]
VIP_LINK = 'https://t.me/+YourVIPPrivateChannelLinkHere'
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
DATA_DIR = '/var/data' if os.path.exists('/var/data') else '.'
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL environment variable is missing. Add DATABASE_URL in Render Environment Variables.')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)
POLLING_LOCK_KEY = 'samad_telegram_getupdates_singleton'

def acquire_polling_lock():
    lock_conn = get_db_connection()
    lock_conn.autocommit = True
    while True:
        with lock_conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtext(%s));', (POLLING_LOCK_KEY,))
            locked = cur.fetchone()[0]
        if locked:
            logging.info('Telegram polling lock acquired. This instance may run getUpdates.')
            return lock_conn
        logging.warning('Another bot instance is currently polling Telegram. Waiting 5 seconds for the polling lock...')
        time.sleep(5)
BASE_BATCH_DIR = os.path.join(DATA_DIR, 'batch_folders')

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def init_batch_folders():
    """
    Automatically creates Batch_15 through Batch_50.
    """
    os.makedirs(BASE_BATCH_DIR, exist_ok=True)
    for batch_number in range(15, 51):
        folder_path = os.path.join(BASE_BATCH_DIR, f'Batch_{batch_number}')
        os.makedirs(folder_path, exist_ok=True)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("\n        CREATE TABLE IF NOT EXISTS users (\n            user_id BIGINT PRIMARY KEY,\n            name TEXT DEFAULT 'አልተመዘገበም',\n            phone TEXT DEFAULT 'አልተመዘገበም',\n            batch TEXT DEFAULT 'ያልተመረጠ',\n            payment_status TEXT DEFAULT 'አልተከፈለም',\n            balance REAL DEFAULT 0.0,\n            is_banned INTEGER DEFAULT 0,\n            payment_date TEXT DEFAULT ''\n        )\n        ")
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS payment_history (\n            id BIGSERIAL PRIMARY KEY,\n            user_id BIGINT,\n            photo_id TEXT,\n            amount REAL DEFAULT 100.0,\n            payment_date TEXT,\n            status TEXT\n        )\n        ')
    cursor.execute('ALTER TABLE users ALTER COLUMN user_id TYPE BIGINT')
    cursor.execute('ALTER TABLE payment_history ALTER COLUMN user_id TYPE BIGINT')
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('\n        SELECT\n            user_id,\n            name,\n            phone,\n            batch,\n            payment_status,\n            balance,\n            is_banned,\n            payment_date\n        FROM users\n        WHERE user_id = %s\n        ', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {'user_id': row[0], 'name': row[1], 'phone': row[2], 'batch': row[3], 'payment_status': row[4], 'balance': row[5], 'is_banned': row[6], 'payment_date': row[7]}

def add_user_if_not_exists(user_id: int):
    user = get_user(user_id)
    if user:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('\n        INSERT INTO users (user_id)\n        VALUES (%s)\n        ', (user_id,))
    conn.commit()
    conn.close()

def update_user(user_id: int, **kwargs):
    if not kwargs:
        return
    allowed_columns = {'name', 'phone', 'batch', 'payment_status', 'balance', 'is_banned', 'payment_date'}
    conn = get_db_connection()
    cursor = conn.cursor()
    for key, value in kwargs.items():
        if key not in allowed_columns:
            continue
        cursor.execute(f'\n            UPDATE users\n            SET {key} = %s\n            WHERE user_id = %s\n            ', (value, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('\n        SELECT\n            user_id,\n            name,\n            phone,\n            batch,\n            payment_status,\n            balance,\n            is_banned,\n            payment_date\n        FROM users\n        ')
    rows = cursor.fetchall()
    conn.close()
    users = []
    for row in rows:
        users.append({'user_id': row[0], 'name': row[1], 'phone': row[2], 'batch': row[3], 'payment_status': row[4], 'balance': row[5], 'is_banned': row[6], 'payment_date': row[7]})
    return users

def get_paid_users_only():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("\n        SELECT\n            user_id,\n            name,\n            phone,\n            batch,\n            payment_date,\n            payment_status\n        FROM users\n        WHERE payment_status = 'ፅድቋል (Approved)'\n        ")
    rows = cursor.fetchall()
    conn.close()
    paid_users = []
    for row in rows:
        paid_users.append({'user_id': row[0], 'name': row[1], 'phone': row[2], 'batch': row[3], 'payment_date': row[4], 'payment_status': row[5]})
    return paid_users

def get_users_by_batch(batch_name: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('\n        SELECT\n            user_id,\n            name,\n            phone,\n            payment_status,\n            payment_date,\n            is_banned\n        FROM users\n        WHERE batch = %s\n        ', (batch_name,))
    rows = cursor.fetchall()
    conn.close()
    users = []
    for row in rows:
        users.append({'user_id': row[0], 'name': row[1], 'phone': row[2], 'payment_status': row[3], 'payment_date': row[4], 'is_banned': row[5]})
    return users

def record_payment_history(user_id: int, photo_id: str, status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    today_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    cursor.execute('\n        INSERT INTO payment_history\n        (\n            user_id,\n            photo_id,\n            payment_date,\n            status\n        )\n        VALUES (%s, %s, %s, %s)\n        ', (user_id, photo_id, today_str, status))
    conn.commit()
    conn.close()

async def check_expired_payments_logic(bot):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('\n        SELECT\n            user_id,\n            name,\n            payment_date\n        FROM users\n        WHERE payment_status = %s\n        ', ('ፅድቋል (Approved)',))
    approved_users = cursor.fetchall()
    conn.close()
    today = datetime.now()
    for uid, name, p_date_str in approved_users:
        if not p_date_str:
            continue
        try:
            clean_date_str = p_date_str.split(' ')[0]
            p_date = datetime.strptime(clean_date_str, '%Y-%m-%d')
            days_passed = (today - p_date).days
            safe_name = html.escape(str(name))
            if days_passed == 28:
                try:
                    await bot.send_message(chat_id=uid, text=f'⚠️ <b>የክፍያ ማስታወሻ!</b>\n\nሰላም <b>{safe_name}</b>፣ ክፍያዎ ለማለቅ <b>2 ቀን ብቻ</b> ቀርቶታል። አገልግሎቱ እንዳይቋረጥብዎ ያድሱ።', parse_mode=ParseMode.HTML)
                except Exception as e:
                    logging.error(f'Failed reminder for {uid}: {e}')
            elif days_passed >= 30:
                await asyncio.to_thread(update_user, uid, payment_status='ጊዜው ያለፈበት (Expired)')
                try:
                    await bot.send_message(chat_id=uid, text=f'🔔 <b>የክፍያ ጊዜዎ አብቅቷል!</b>\n\nሰላም <b>{safe_name}</b>፣ 30 ቀናት ስለሞሉ የወሩ ክፍያ ጊዜዎ አብቅቷል።\n\nበ <b>💳 ክፍያ ፈፅም</b> በኩል ድጋሚ ይክፈሉ።', parse_mode=ParseMode.HTML)
                except Exception as e:
                    logging.error(f'Failed expiration message for {uid}: {e}')
        except Exception as e:
            logging.error(f'Error checking user {uid}: {e}')

async def background_payment_checker(app):
    while True:
        try:
            await check_expired_payments_logic(app.bot)
        except Exception as e:
            logging.error(f'Background checker error: {e}')
        await asyncio.sleep(43200)
REG_NAME, REG_PHONE, REG_BATCH = range(3)
PAY_RECEIPT = 3
BROADCAST_STATE = 4
BATCH_MSG_STATE, BATCH_PDF_STATE = range(5, 7)

def main_menu(user_id: int):
    user = get_user(user_id)
    keyboard = [[InlineKeyboardButton('🏫 ስለ ትምህርት ቤቱ (School Info)', callback_data='school_info')], [InlineKeyboardButton('📝 አዲስ ምዝገባ (Register)', callback_data='register')], [InlineKeyboardButton('💳 ክፍያ ፈፅም (Pay)', callback_data='pay')], [InlineKeyboardButton('👤 የመገለጫ መረጃ (Profile)', callback_data='profile')], [InlineKeyboardButton('💰 የሂሳብ ባላንስ (Wallet)', callback_data='wallet')]]
    if user and user['payment_status'] == 'ፅድቋል (Approved)':
        keyboard.append([InlineKeyboardButton('🌟 VIP ቻናል መግቢያ', url=VIP_LINK)])
    keyboard.append([InlineKeyboardButton('📞 ግንኙነት (Contact)', callback_data='contact')])
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton('⚙️ የአድሚን ገጽ (Admin)', callback_data='admin_panel')])
    return InlineKeyboardMarkup(keyboard)

def get_batches_keyboard():
    keyboard = []
    row = []
    for b in range(15, 51):
        b_name = f'{b}ኛ ባች'
        row.append(InlineKeyboardButton(f'{b}ኛ', callback_data=f'reg_batch_{b_name}'))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ ዋና ማውጫ ተመለስ', callback_data='main')]])

async def is_banned(update: Update) -> bool:
    user_id = update.effective_user.id
    user = await asyncio.to_thread(get_user, user_id)
    if user and user['is_banned'] == 1:
        if update.message:
            await update.message.reply_text('❌ <b>እርስዎ ከዚህ ቦት ታግደዋል!</b>', parse_mode=ParseMode.HTML)
        elif update.callback_query:
            try:
                await update.callback_query.answer('❌ እርስዎ ከዚህ ቦት ታግደዋል!', show_alert=True)
            except Exception:
                pass
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return
    user_id = update.effective_user.id
    await asyncio.to_thread(add_user_if_not_exists, user_id)
    text = 'እንኳን ወደ ፖርታሉ በሰላም መጡ! 👋\nእባክዎን የሚፈልጉትን አገልግሎት ይምረጡ፡'
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu(user_id))
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu(user_id))

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.edit_message_text('📝 <b>የምዝገባ ፎርም</b>\n\nእባክዎን <b>ሙሉ ስምዎን</b> ይፃፉልን፡\n\nለማቋረጥ /cancel ይበሉ', parse_mode=ParseMode.HTML)
    return REG_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    await asyncio.to_thread(add_user_if_not_exists, user_id)
    await asyncio.to_thread(update_user, user_id, name=update.message.text)
    await update.message.reply_text('በጣም ጥሩ! አሁን <b>የስልክ ቁጥርዎን</b> ያስገቡ፡', parse_mode=ParseMode.HTML)
    return REG_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    await asyncio.to_thread(update_user, user_id, phone=update.message.text)
    await update.message.reply_text('አሁን ደግሞ <b>የተመደቡበትን ባች (ከባች 15 - ባች 50)</b> ይምረጡ፡', reply_markup=get_batches_keyboard(), parse_mode=ParseMode.HTML)
    return REG_BATCH

async def get_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    selected_batch = query.data.replace('reg_batch_', '')
    await asyncio.to_thread(update_user, user_id, batch=selected_batch)
    user = await asyncio.to_thread(get_user, user_id)
    await query.edit_message_text(f"✅ <b>ምዝገባዎ ተጠናቋል!</b>\n\n👤 <b>ስም:</b> {html.escape(str(user['name']))}\n📞 <b>ስልክ:</b> {html.escape(str(user['phone']))}\n🎓 <b>ባች:</b> {html.escape(str(user['batch']))}\n\nአሁን <b>💳 ክፍያ ፈፅም</b> የሚለውን በመጫን ደረሰኝ ማስገባት ይችላሉ።", reply_markup=main_menu(user_id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def start_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    query = update.callback_query
    payment_info = '💳 <b>የክፍያ መረጃ</b>\n\nእባክዎን የቦቱን አገልግሎት ለማግኘት ክፍያውን በታች ባሉት አካውንቶች ይላኩ፡\n\n• <b>CBE (ንግድ ባንክ):</b> <code>1000579602264</code>\n• <b>Telebirr:</b> <code>0966089190</code>\n\nከከፈሉ በኋላ የከፈሉበትን <b>ደረሰኝ (Photo/Screenshot)</b> እዚህ ይላኩልን።'
    await query.edit_message_text(payment_info, parse_mode=ParseMode.HTML, reply_markup=back_menu())
    return PAY_RECEIPT

async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update):
        return ConversationHandler.END
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    photo_file_id = photo.file_id
    await asyncio.to_thread(add_user_if_not_exists, user_id)
    await asyncio.to_thread(update_user, user_id, payment_status='በማረጋገጥ ላይ (Pending)')
    user = await asyncio.to_thread(get_user, user_id)
    user_batch = user.get('batch', '')
    if user_batch and 'ባች' in user_batch:
        batch_num = ''.join(filter(str.isdigit, user_batch))
        if batch_num:
            target_folder = os.path.join(BASE_BATCH_DIR, f'Batch_{batch_num}')
            os.makedirs(target_folder, exist_ok=True)
            try:
                receipt_file = await context.bot.get_file(photo_file_id)
                time_stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                file_name = f'user_{user_id}_{time_stamp}.jpg'
                file_path = os.path.join(target_folder, file_name)
                await receipt_file.download_to_drive(file_path)
            except Exception as e:
                logging.error(f'Failed to save receipt: {e}')
    record_payment_history(user_id, photo_file_id, 'በማረጋገጥ ላይ')
    await update.message.reply_text('✅ <b>ደረሰኝዎ ደርሶናል!</b>\nአድሚኑ አረጋግጦ እስኪያፀድቀው ድረስ እባክዎን ትንሽ ይታገሱ።', reply_markup=main_menu(user_id), parse_mode=ParseMode.HTML)
    admin_markup = InlineKeyboardMarkup([[InlineKeyboardButton('✅ አፅድቅ', callback_data=f'approve_{user_id}'), InlineKeyboardButton('❌ ውደቅ አድርግ', callback_data=f'reject_{user_id}')]])
    caption = f"📩 <b>አዲስ የክፍያ ደረሰኝ ደርሷል!</b>\n\n👤 <b>ስም:</b> {html.escape(str(user['name']))}\n📞 <b>ስልክ:</b> {html.escape(str(user['phone']))}\n🎓 <b>ባች:</b> {html.escape(str(user['batch']))}\n🆔 <b>User ID:</b> <code>{user_id}</code>"
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=photo_file_id, caption=caption, reply_markup=admin_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f'Failed to send receipt to admin {admin_id}: {e}')
    return ConversationHandler.END

async def invalid_receipt_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('⚠️ <b>እባክዎን የደረሰኝ ፎቶ (Photo/Screenshot) ብቻ ይላኩ!</b>\n\nለማቋረጥ /cancel ይበሉ።', parse_mode=ParseMode.HTML)
    return PAY_RECEIPT

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('❌ የአድሚን ፈቃድ የሎትም!', show_alert=True)
        return
    await query.answer()
    data = query.data.split('_')
    action = data[0]
    try:
        target_user_id = int(data[1])
    except (ValueError, IndexError):
        await query.answer('Invalid user ID', show_alert=True)
        return
    if action == 'approve':
        today_str = datetime.now().strftime('%Y-%m-%d')
        await asyncio.to_thread(update_user, target_user_id, payment_status='ፅድቋል (Approved)', balance=100.0, payment_date=today_str)
        try:
            await query.edit_message_caption(caption=f'{query.message.caption}\n\n✅ <b>ሁኔታ:</b> ክፍያው ፅድቋል!', reply_markup=None, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f'Failed editing admin receipt: {e}')
        vip_markup = InlineKeyboardMarkup([[InlineKeyboardButton('🌟 ወደ VIP ቻናል ይግቡ', url=VIP_LINK)]])
        try:
            await context.bot.send_message(chat_id=target_user_id, text='🎉 <b>እንኳን ደስ አለዎት!</b>\n\nክፍያዎ በአድሚኑ ተረጋግጦ ፅድቋል።\n\nአሁን VIP ቻናላችንን መቀላቀል ይችላሉ!', reply_markup=vip_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f'Failed approval message: {e}')
    elif action == 'reject':
        await asyncio.to_thread(update_user, target_user_id, payment_status='ተሰርዟል (Rejected)')
        try:
            await query.edit_message_caption(caption=f'{query.message.caption}\n\n❌ <b>ሁኔታ:</b> ክፍያው ውድቅ ተደርጓል!', reply_markup=None, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f'Failed editing rejected receipt: {e}')
        try:
            await context.bot.send_message(chat_id=target_user_id, text='❌ <b>ክፍያዎ አልፀደቀም።</b>\n\nእባክዎን ትክክለኛ ደረሰኝ መላክዎን ያረጋግጡ።', parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f'Failed rejection message: {e}')

async def show_admin_panel(query, context):
    all_users = await asyncio.to_thread(get_all_users)
    paid_users = get_paid_users_only()
    report = f'⚙️ <b>የአድሚን መቆጣጠሪያ Dashboard</b>\n\n👥 <b>ጠቅላላ ተጠቃሚዎች:</b> {len(all_users)}\n✅ <b>ከፍለው ደረሰኝ ያፀደቁ:</b> {len(paid_users)} ተጠቃሚዎች\n\nከታች ያሉትን ቁልፎች በመጠቀም ዝርዝር ማየት እና ማስታወቂያ መላክ ይችላሉ፦'
    admin_buttons = InlineKeyboardMarkup([[InlineKeyboardButton('✅ ከፍለው ደረሰኝ የላኩ ብቻ (Paid)', callback_data='show_paid_only')], [InlineKeyboardButton('📁 በባች አስተዳድር/ማስታወቂያ ላክ', callback_data='show_batch_menu')], [InlineKeyboardButton('📢 ለአጠቃላይ ተጠቃሚ መልእክት', callback_data='start_broadcast')], [InlineKeyboardButton('🔄 ክፍያዎችን በግድ ፈትሽ', callback_data='force_check_payments')], [InlineKeyboardButton('⬅️ ወደ ዋና ማውጫ', callback_data='main')]])
    await query.edit_message_text(report, reply_markup=admin_buttons, parse_mode=ParseMode.HTML)

async def show_batch_selector_admin(query, context):
    keyboard = []
    row = []
    for b in range(15, 51):
        b_name = f'{b}ኛ ባች'
        row.append(InlineKeyboardButton(f'{b}ኛ', callback_data=f'adm_batch_{b_name}'))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton('⬅️ ወደ አድሚን ገጽ', callback_data='admin_panel')])
    await query.edit_message_text('📁 <b>የትኛውን ባች ማስተዳደር/ማስታወቂያ መላክ ትፈልጋለህ?</b>\n\nማየት የሚፈልጉትን ባች ይምረጡ፦', reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def show_batch_options_menu(query, context, batch_name):
    users = await asyncio.to_thread(get_users_by_batch, batch_name)
    text = f'📂 <b>የ{batch_name} መቆጣጠሪያ</b>\n\nበዚህ ባች ውስጥ የተመዘገቡ ተጠቃሚዎች ብዛት፦ <b>{len(users)}</b>\n\nእባክዎን ማድረግ የሚፈልጉትን ይምረጡ፦'
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton('💬 የጽሑፍ ማስታወቂያ ላክ', callback_data=f'btn_send_txt_{batch_name}')], [InlineKeyboardButton('📄 PDF/ሰነድ ማስታወቂያ ላክ', callback_data=f'btn_send_pdf_{batch_name}')], [InlineKeyboardButton('📋 አባላቱን ዝርዝር እይ', callback_data=f'btn_list_users_{batch_name}')], [InlineKeyboardButton('⬅️ ወደ ባች መረጣ ተመለስ', callback_data='show_batch_menu')]])
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def display_specific_batch_users(query, context, batch_name):
    users = await asyncio.to_thread(get_users_by_batch, batch_name)
    if not users:
        text = f'📂 <b>{batch_name}</b> ውስጥ እስካሁን የተመዘገበ ተጠቃሚ የለም።'
    else:
        text = f'📂 <b>የ{batch_name} አባላት ዝርዝር ({len(users)})፦</b>\n\n'
        for idx, u in enumerate(users, 1):
            text += f"{idx}. <b>ስም:</b> {html.escape(str(u['name']))}\n   <b>ስልክ:</b> {html.escape(str(u['phone']))}\n   <b>ክፍያ:</b> {html.escape(str(u['payment_status']))}\n   <b>ID:</b> <code>{u['user_id']}</code>\n   -------------------\n"
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ ባች አማራጭ', callback_data=f'adm_batch_{batch_name}')], [InlineKeyboardButton('⬅️ ወደ አድሚን ገጽ', callback_data='admin_panel')]])
    if len(text) > 4000:
        text = text[:3900] + '\n\n⚠️ ዝርዝሩ ረጅም ስለሆነ ከፊሉ ብቻ ታይቷል።'
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def prompt_batch_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    batch_name = query.data.replace('btn_send_txt_', '')
    context.user_data['target_batch'] = batch_name
    await query.edit_message_text(f'💬 <b>ለ{batch_name} የሚላክ የጽሑፍ ማስታወቂያ</b>\n\nለ <b>{batch_name}</b> ተማሪዎች ብቻ እንዲላክ የሚፈልጉትን መልእክት ይፃፉ፦\n\nለማቋረጥ /cancel ይበሉ', parse_mode=ParseMode.HTML)
    return BATCH_MSG_STATE

async def send_batch_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    batch_name = context.user_data.get('target_batch')
    if not batch_name:
        await update.message.reply_text('❌ ባች አልተመረጠም።')
        return ConversationHandler.END
    msg_text = update.message.text
    users = await asyncio.to_thread(get_users_by_batch, batch_name)
    if not users:
        await update.message.reply_text(f'❌ በ{batch_name} ውስጥ የተመዘገበ ምንም ተጠቃሚ አልተገኘም።')
        return ConversationHandler.END
    success = 0
    failed = 0
    await update.message.reply_text(f'⏳ መልእክቱ ለ{batch_name} ተማሪዎች እየተላከ ነው...')
    safe_message = html.escape(msg_text)
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f'📢 <b>የ{html.escape(str(batch_name))} ማስታወቂያ፦</b>\n\n{safe_message}', parse_mode=ParseMode.HTML)
                success += 1
            except Exception as e:
                logging.error(f"Batch message failed for {u['user_id']}: {e}")
                failed += 1
    await update.message.reply_text(f'✅ <b>የ{html.escape(str(batch_name))} ማስታወቂያ ተላከ!</b>\n\n• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}', reply_markup=main_menu(update.effective_user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def prompt_batch_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    batch_name = query.data.replace('btn_send_pdf_', '')
    context.user_data['target_batch'] = batch_name
    await query.edit_message_text(f'📄 <b>ለ{batch_name} የሚላክ PDF / ሰነድ ማስታወቂያ</b>\n\nእባክዎን ለ <b>{batch_name}</b> የሚላከውን PDF ፋይል እዚህ ያያይዙልን (Upload)።\n\n<i>ፋይሉ በራስ-ሰር በ{batch_name} ፎልደር ውስጥ ይቀመጣል!</i>\n\nለማቋረጥ /cancel ይበሉ', parse_mode=ParseMode.HTML)
    return BATCH_PDF_STATE

async def send_batch_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    batch_name = context.user_data.get('target_batch')
    if not batch_name:
        await update.message.reply_text('❌ ባች አልተመረጠም።')
        return ConversationHandler.END
    doc = update.message.document
    caption = update.message.caption or f'📄 የ{batch_name} ማስታወቂያ PDF'
    batch_num = ''.join(filter(str.isdigit, batch_name))
    target_folder = os.path.join(BASE_BATCH_DIR, f'Batch_{batch_num}')
    os.makedirs(target_folder, exist_ok=True)
    original_name = doc.file_name or 'announcement_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.pdf'
    safe_file_name = os.path.basename(original_name)
    file_path = os.path.join(target_folder, safe_file_name)
    await update.message.reply_text(f'⏳ ፋይሉ በ{batch_name} ፎልደር ውስጥ እየተቀመጠ እና ለተማሪዎች እየተላከ ነው...')
    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(file_path)
    except Exception as e:
        logging.error(f'Failed to save PDF: {e}')
        await update.message.reply_text('❌ PDF ፋይሉን ማስቀመጥ አልተቻለም።')
        return ConversationHandler.END
    users = await asyncio.to_thread(get_users_by_batch, batch_name)
    success = 0
    failed = 0
    safe_caption = html.escape(caption)
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_document(chat_id=u['user_id'], document=doc.file_id, caption=f'📢 <b>የ{html.escape(str(batch_name))} ማስታወቂያ PDF፦</b>\n\n{safe_caption}', parse_mode=ParseMode.HTML)
                success += 1
            except Exception as e:
                logging.error(f"PDF failed for {u['user_id']}: {e}")
                failed += 1
    await update.message.reply_text(f'✅ <b>የ{html.escape(str(batch_name))} PDF ማስታወቂያ በተሳካ ሁኔታ ተላከ!</b>\n\n📂 <b>የተቀመጠበት:</b> <code>{html.escape(file_path)}</code>\n\n• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}', reply_markup=main_menu(update.effective_user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def show_paid_users_list(query, context):
    paid_users = get_paid_users_only()
    if not paid_users:
        text = '❌ <b>እስካሁን ደረሰኝ ልከው ክፍያቸው የጸደቀላቸው ተጠቃሚዎች የሉም።</b>'
    else:
        text = f'✅ <b>ከፍለው ደረሰኝ የላኩ ተጠቃሚዎች ዝርዝር ({len(paid_users)})</b>\n\n'
        for idx, u in enumerate(paid_users, 1):
            text += f"{idx}. <b>ስም:</b> {html.escape(str(u['name']))}\n   <b>ስልክ:</b> {html.escape(str(u['phone']))}\n   <b>ባች:</b> {html.escape(str(u['batch']))}\n   <b>የተከፈለበት ቀን:</b> {html.escape(str(u['payment_date']))}\n   <b>ID:</b> <code>{u['user_id']}</code>\n   -------------------\n"
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ አድሚን ገጽ', callback_data='admin_panel')]])
    if len(text) > 4000:
        text = text[:3900] + '\n\n⚠️ ዝርዝሩ ረጅም ስለሆነ ከፊሉ ብቻ ታይቷል።'
    await query.edit_message_text(text, reply_markup=buttons, parse_mode=ParseMode.HTML)

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text('📢 <b>አጠቃላይ የብሮድካስት መልእክት</b>\n\nለሁሉም ተጠቃሚዎች እንዲላክ የሚፈልጉትን መልእክት ይጻፉልኝ፡\n\nለማቋረጥ /cancel ይበሉ', parse_mode=ParseMode.HTML)
    return BROADCAST_STATE

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    broadcast_msg = update.message.text
    users = await asyncio.to_thread(get_all_users)
    success = 0
    failed = 0
    await update.message.reply_text('⏳ መልእክቱ እየተላከ ነው...')
    safe_message = html.escape(broadcast_msg)
    for u in users:
        if u['is_banned'] == 0:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f'📢 <b>ማስታወቂያ ከፖርታሉ:</b>\n\n{safe_message}', parse_mode=ParseMode.HTML)
                success += 1
            except Exception as e:
                logging.error(f"Broadcast failed for {u['user_id']}: {e}")
                failed += 1
    await update.message.reply_text(f'✅ <b>ብሮድካስት ተጠናቋል!</b>\n\n• በተሳካ ሁኔታ የደረሳቸው: {success}\n• ያልደረሳቸው: {failed}', reply_markup=main_menu(update.effective_user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def revoke_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text('⚠️ <b>የተጠቃሚውን ID አብረው ይጻፉ!</b>\n\n<b>ምሳሌ፦</b> <code>/revoke 8711072926</code>', parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(context.args[0])
        user = await asyncio.to_thread(get_user, target_id)
        if not user:
            await update.message.reply_text('❌ ይህ ተጠቃሚ በዳታቤዝ ውስጥ አልተገኘም።')
            return
        await asyncio.to_thread(update_user, target_id, payment_status='ተሰርዟል (Rejected)', balance=0.0, batch='ያልተመረጠ')
        user_name = user.get('name', 'ተጠቃሚ')
        await update.message.reply_text(f'🔄 ተጠቃሚ <b>{html.escape(str(user_name))}</b> (ID: <code>{target_id}</code>) ክፍያው ተሰርዞ ከባቹ ተወግዷል!', parse_mode=ParseMode.HTML)
        try:
            await context.bot.send_message(chat_id=target_id, text='⚠️ <b>ማሳሰቢያ፦</b>\n\nየክፍያ ማረጋገጫዎ ተሰርዟል!\nእባክዎን እንደገና ትክክለኛ ደረሰኝ ያስገቡ።', parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text('⚠️ እባክዎን ትክክለኛ የቁጥር ID ያስገቡ!')

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text('⚠️ <b>የተጠቃሚውን ID አብረው ይጻፉ!</b>\n\n<b>ምሳሌ፦</b> <code>/ban 8711072926</code>', parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(context.args[0])
        user = await asyncio.to_thread(get_user, target_id)
        if not user:
            await update.message.reply_text('❌ ይህ ተጠቃሚ በዳታቤዝ ውስጥ አልተገኘም።')
            return
        await asyncio.to_thread(update_user, target_id, is_banned=1)
        await update.message.reply_text(f"🚫 ተጠቃሚ <b>{html.escape(str(user['name']))}</b> (ID: <code>{target_id}</code>) በተሳካ ሁኔታ ታግዷል!", parse_mode=ParseMode.HTML)
        try:
            await context.bot.send_message(chat_id=target_id, text='❌ <b>እርስዎ ከዚህ ቦት በአድሚኑ ታግደዋል!</b>', parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text('⚠️ እባክዎን ትክክለኛ የቁጥር ID ያስገቡ!')

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text('⚠️ <b>የተጠቃሚውን ID አብረው ይጻፉ!</b>\n\n<b>ምሳሌ፦</b> <code>/unban 8711072926</code>', parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(context.args[0])
        user = await asyncio.to_thread(get_user, target_id)
        if not user:
            await update.message.reply_text('❌ ይህ ተጠቃሚ በዳታቤዝ ውስጥ አልተገኘም።')
            return
        await asyncio.to_thread(update_user, target_id, is_banned=0)
        await update.message.reply_text(f"✅ ተጠቃሚ <b>{html.escape(str(user['name']))}</b> (ID: <code>{target_id}</code>) ከእገዳ ነፃ ወጥቷል!", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text('⚠️ እባክዎን ትክክለኛ የቁጥር ID ያስገቡ!')

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    # Acknowledge the Telegram button immediately. This removes the
    # loading spinner before any database work happens.
    try:
        await query.answer()
    except Exception:
        pass

    if await is_banned(update):
        return
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
        await query.edit_message_text('⏳ ክፍያዎች እየተፈተሹ ነው...')
        await check_expired_payments_logic(context.bot)
        await query.edit_message_text('✅ የክፍያ ማስታወሻዎች በተሳካ ሁኔታ ተላኩ!', reply_markup=back_menu())
    elif query.data == 'school_info':
        school_menu = InlineKeyboardMarkup([[InlineKeyboardButton('ℹ️ ስለ ትምህርት ቤቱ', callback_data='about_school')], [InlineKeyboardButton('📚 የሚሰጡ ትምህርቶች', callback_data='school_courses')], [InlineKeyboardButton('📢 ወቅታዊ ማስታወቂያዎች', callback_data='school_news')], [InlineKeyboardButton('⬅️ ወደ ዋና ማውጫ', callback_data='main')]])
        await query.edit_message_text('🏫 <b>የትምህርት ቤቱ መረጃ እና ማስታወቂያዎች</b>\n\nእባክዎን ማወቅ የሚፈልጉትን መረጃ ከታች ይምረጡ፡', reply_markup=school_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'about_school':
        text = 'ℹ️ <b>ስለ ትምህርት ቤታችን</b>\n\nትምህርት ቤታችን በዘመናዊ የትምህርት አሰጣጥ እና በቴክኖሎጂ የተደገፈ ጥራት ያለው ትምህርት ለመስጠት የተቋቋመ ነው፡\n\n🎯 <b>ራዕይ:</b> በደንብ የዳበረ እና ከልምድ ወጥቶ በትምህርት የታገዘ የሳውንድ እውቀት ያለው ባለሙያ መፍጠር።\n⭐ <b>ተልዕኮ:</b> ጥራት ያለውና ተመጣጣኝ ትምህርት ለሁሉም ማዳረስ።'
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ ትምህርት ቤት ማውጫ', callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'school_courses':
        text = '📚 <b>የሚሰጡ ትምህርቶች እና ኮርሶች</b>\n\n1. 💻 <b>Audio Fundamentals & Sound Physics:</b>\nየድምፅ ሞገድ ባህሪያት (Frequency, Amplitude, Phase)፣ የሰው ልጅ የመስማት ሂደት (Psychoacoustics) እና የክፍል አካውስቲክስ (Room Acoustics) መሠረታዊ ሕጎችን ይሸፍናል።\n\n2. 🇬🇧 <b>Digital Audio Workstations (DAW) & Signal Flow:</b>\nእንደ Pro Tools, Logic Pro ወይም Ableton ያሉ ሶፍትዌሮችን አጠቃቀም፣ የማይክሮፎን አይነቶችንና አቀማመጥ፣ እንዲሁም የኦዲዮ ሲግናል ፍሰት (Signal Routing) ያስተምራል።\n\n3. 📐 <b>Mixing & Mastering Engineering:</b>\nየተለያዩ የተቀረፁ የድምፅ መስመሮች (Multi-track audio) አዋህዶ ሚዛናዊ ማድረግ (Mixing) እና ለመጨረሻው ዲጂታል ስርጭት ጥራቱን ጠብቆ ማዘጋጀትን (Mastering) ያተኩራል።'
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ ትምህርት ቤት ማውጫ', callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'school_news':
        text = "📢 <b>ወቅታዊ ማስታወቂያዎች</b>\n\n📌 <b>ለአዲሱ መንፈቅ ዓመት የምዝገባ ጥሪ!</b>\nየአዲሱ ትምህርት ዘመን ምዝገባ ተጀምሯል። ቦታዎች ሳይሞሉ በፍጥነት ይመዝገቡ።\n\n📅 <b>የክፍል መጀመሪያ ቀን:</b> የፊታችን ሰኞ\n\n💡 ለተጨማሪ መረጃ የ 'Contact' ቁልፍን በመጫን ያግኙን።"
        sub_menu = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ ወደ ትምህርት ቤት ማውጫ', callback_data='school_info')]])
        await query.edit_message_text(text, reply_markup=sub_menu, parse_mode=ParseMode.HTML)
    elif query.data == 'profile':
        user = await asyncio.to_thread(get_user, user_id)
        if not user:
            await asyncio.to_thread(add_user_if_not_exists, user_id)
            user = await asyncio.to_thread(get_user, user_id)
        p_date = user['payment_date'] if user['payment_date'] else 'ያልተመዘገበ'
        profile_text = f"📋 <b>የመገለጫ መረጃ</b>\n\n• <b>ስም:</b> {html.escape(str(user['name']))}\n• <b>ስልክ:</b> {html.escape(str(user['phone']))}\n• <b>ባች:</b> {html.escape(str(user['batch']))}\n• <b>የክፍያ ሁኔታ:</b> {html.escape(str(user['payment_status']))}\n• <b>መጨረሻ የተከፈለው:</b> {html.escape(str(p_date))}\n• <b>የአካውንት ባላንስ:</b> {user['balance']} ETB"
        await query.edit_message_text(profile_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'wallet':
        user = await asyncio.to_thread(get_user, user_id)
        wallet_text = f"💰 <b>የእርስዎ የሂሳብ ባላንስ (Wallet)</b>\n\n• <b>ያለዎት ባላንስ:</b> <code>{user['balance']} ETB</code>\n\n💡 ባላንስዎን ለመጨመር በ '💳 ክፍያ ፈፅም' በኩል ደረሰኝ ያስገቡ።"
        await query.edit_message_text(wallet_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'contact':
        contact_text = '📞 <b>እኛን ለማግኘት:</b>\n\n• <b>ስልክ:</b> +251966089190\n• <b>ኢሜይል:</b> samiabinet19@gmail.com\n• <b>አድራሻ:</b> አዲስ አበባ ልዩ ስሙ መገናኛ ከዘፍነሽ ሞል ፊት ለፊት ያለው አቢስንያ ባንክ የሚገኝበት ላይ ሁለተኛ ፎቅ፣ ኢትዮጵያ'
        await query.edit_message_text(contact_text, reply_markup=back_menu(), parse_mode=ParseMode.HTML)
    elif query.data == 'admin_panel':
        if is_admin(user_id):
            await show_admin_panel(query, context)
        else:
            await query.edit_message_text('❌ ይህንን ገጽ ለማየት ፈቃድ የሎትም።', reply_markup=back_menu())

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.pop('target_batch', None)
    if update.message:
        await update.message.reply_text('❌ ሂደቱ ተቋርጧል።', reply_markup=main_menu(user_id))
    elif update.callback_query:
        await update.callback_query.edit_message_text('❌ ሂደቱ ተቋርጧል።', reply_markup=main_menu(user_id))
    return ConversationHandler.END

async def post_init(app):
    asyncio.create_task(background_payment_checker(app))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_db()
    init_batch_folders()
    polling_lock_conn = acquire_polling_lock()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    reg_handler = ConversationHandler(entry_points=[CallbackQueryHandler(start_registration, pattern='^register$')], states={REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)], REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)], REG_BATCH: [CallbackQueryHandler(get_batch, pattern='^reg_batch_')]}, fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^main$')], allow_reentry=True)
    pay_handler = ConversationHandler(entry_points=[CallbackQueryHandler(start_payment, pattern='^pay$')], states={PAY_RECEIPT: [MessageHandler(filters.PHOTO, receive_receipt), MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_receipt_format)]}, fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^main$')], allow_reentry=True)
    broadcast_handler = ConversationHandler(entry_points=[CallbackQueryHandler(start_broadcast, pattern='^start_broadcast$')], states={BROADCAST_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)]}, fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^main$')], allow_reentry=True)
    batch_announcement_handler = ConversationHandler(entry_points=[CallbackQueryHandler(prompt_batch_msg, pattern='^btn_send_txt_'), CallbackQueryHandler(prompt_batch_pdf, pattern='^btn_send_pdf_')], states={BATCH_MSG_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_batch_text_message)], BATCH_PDF_STATE: [MessageHandler(filters.Document.ALL, send_batch_pdf_document)]}, fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^main$')], allow_reentry=True)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('ban', ban_user))
    app.add_handler(CommandHandler('unban', unban_user))
    app.add_handler(CommandHandler('revoke', revoke_user))
    app.add_handler(reg_handler)
    app.add_handler(pay_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(batch_announcement_handler)
    app.add_handler(CallbackQueryHandler(handle_admin_action, pattern='^(approve|reject)_'))
    app.add_handler(CallbackQueryHandler(handle_buttons))
    logging.info('🚀 Bot started successfully!')
    print('🚀 ቦቱ Render ላይ በተሳካ ሁኔታ ስራ ጀምሯል...')
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        try:
            polling_lock_conn.close()
            logging.info('Telegram polling lock released.')
        except Exception:
            logging.exception('Failed to close Telegram polling lock connection.')
if __name__ == '__main__':
    main()
