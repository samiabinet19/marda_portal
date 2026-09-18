from datetime import datetime
import html
import logging
import os
import threading
import time

# Flask እና Telegram ቤተ-መጻሕፍት
from flask import Flask
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================================
# 1. RENDER HEALTH CHECK SERVER (Flask Keep-Alive)
# ============================================================
app = Flask('')


@app.route('/')
def home():
  return 'Bot is alive and running!'


def run_flask():
  # Render የሚሰጠውን PORT በራሱ ይወስዳል፤ ከሌለ 8080 ይጠቀማል
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)


def keep_alive():
  t = threading.Thread(target=run_flask)
  t.daemon = True  # ቦቱ ሲቆም ዌብ ሰርቨሩ አብሮ እንዲዘጋ ያደርጋል
  t.start()


# Render UptimeRobot እንዲያገኘው ዌብ ሰርቨሩን ማስነሳት
keep_alive()

# ============================================================
# 2. YOUR TELEGRAM BOT CODE CONTINUES HERE...
# ============================================================

# እዚህ ጋር የቀረውን የቦትህን logic/handlers ቀጥል...
