import logging
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
import sqlite3
from datetime import datetime
import asyncio
from threading import Lock
from typing import Optional

# .env faylni yuklash
load_dotenv()

# Logging sozlash
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database thread-safe wrapper
class DatabaseManager:
    def __init__(self, db_path='bot.db'):
        self.db_path = db_path
        self.lock = Lock()
        self._initialize_db()
    
    def _initialize_db(self):
        """Database jadvallarini yaratish"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            wallet TEXT DEFAULT NULL,
            referrals INTEGER DEFAULT 0,
            invited_by INTEGER DEFAULT NULL,
            joined_at TIMESTAMP,
            is_admin BOOLEAN DEFAULT FALSE
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_progress (
            user_id INTEGER,
            category TEXT,
            question_number INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            completed BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (user_id, category)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            wallet TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP,
            processed_at TIMESTAMP DEFAULT NULL,
            admin_id INTEGER DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mandatory_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT,
            entity_name TEXT,
            entity_username TEXT,
            entity_type TEXT CHECK(entity_type IN ('channel', 'bot', 'group')),
            added_at TIMESTAMP,
            added_by INTEGER,
            invite_link TEXT
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            message TEXT,
            sent_at TIMESTAMP,
            receivers_count INTEGER DEFAULT 0
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            user_id INTEGER,
            details TEXT,
            created_at TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
    
    def execute(self, query, params=None):
        """Thread-safe execute"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                conn.commit()
                result = cursor.fetchall()
                return result
            finally:
                conn.close()
    
    def fetchone(self, query, params=None):
        """Thread-safe fetchone"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                result = cursor.fetchone()
                return result
            finally:
                conn.close()
    
    def fetchall(self, query, params=None):
        """Thread-safe fetchall"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                result = cursor.fetchall()
                return result
            finally:
                conn.close()
    
    def insert_and_get_id(self, query, params=None):
        """Thread-safe insert va lastrowid qaytarish"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()

# Database manager
db = DatabaseManager()

# Ma'lumotlarni .env dan o'qish
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []
PAYMENT_CHANNEL = os.getenv('PAYMENT_CHANNEL', '@payment_channel')

# Boshlang'ich adminlarni qo'shish
for admin_id in ADMIN_IDS:
    db.execute('INSERT OR IGNORE INTO users (user_id, is_admin, joined_at) VALUES (?, ?, ?)',
               (admin_id, True, datetime.now()))

# Conversation states
(SETTING_WALLET, WITHDRAW_AMOUNT, ADMIN_ADD_SUBSCRIPTION, 
 ADMIN_ADD_ADMIN, ADMIN_BROADCAST, ADMIN_WAITING_SUB_TYPE) = range(6)

# Quiz savollari
QUIZ_QUESTIONS = {
    'sport': [
        {
            'question': 'Tennisda gʻalaba uchun nechta set kerak?',
            'options': ['1', '2', '3', '4'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Futbolda jamoa nechta oʻyinchidan iborat?',
            'options': ['10', '11', '12', '9'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Olimpiada oʻyinlari necha yilda bir marta oʻtkaziladi?',
            'options': ['2', '3', '4', '5'],
            'correct': 2,
            'reward': 10000
        },
        {
            'question': 'Basketbolda oʻyin necha daqiqadan iborat?',
            'options': ['40', '48', '45', '50'],
            'correct': 0,
            'reward': 10000
        },
        {
            'question': 'Voleybolda jamoa nechta oʻyinchidan iborat?',
            'options': ['5', '6', '7', '8'],
            'correct': 1,
            'reward': 10000
        }
    ],
    'geography': [
        {
            'question': 'Dunyodagi eng katta davlat qaysi?',
            'options': ['AQSH', 'Xitoy', 'Rossiya', 'Kanada'],
            'correct': 2,
            'reward': 10000
        },
        {
            'question': 'Amazon daryosi qaysi qitada joylashgan?',
            'options': ['Afrika', 'Janubiy Amerika', 'Shimoliy Amerika', 'Osiyo'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Eng baland togʻ qaysi?',
            'options': ['K2', 'Everest', 'Kilimandjaro', 'Fuji'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Sahroi Kabir qayerda joylashgan?',
            'options': ['Avstraliya', 'Afrika', 'Osiyo', 'Yaqin Sharq'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Fransiyaning poytaxti qaysi shahar?',
            'options': ['London', 'Berlin', 'Parij', 'Madrid'],
            'correct': 2,
            'reward': 10000
        }
    ],
    'history': [
        {
            'question': 'Birinchi jahon urushi qachon boshlangan?',
            'options': ['1914', '1915', '1916', '1913'],
            'correct': 0,
            'reward': 10000
        },
        {
            'question': 'Amerika Qoʻshma Shtatlari mustaqilligini qachon eʼlon qilgan?',
            'options': ['1776', '1789', '1799', '1801'],
            'correct': 0,
            'reward': 10000
        },
        {
            'question': 'Qadimgi Misr piramidalari qayerda joylashgan?',
            'options': ['Giza', 'Kairo', 'Luxor', 'Alexandria'],
            'correct': 0,
            'reward': 10000
        },
        {
            'question': 'Rim imperiyasi qachon qulagan?',
            'options': ['410 yil', '476 yil', '500 yil', '395 yil'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Ikkinchi jahon urushi qachon tugagan?',
            'options': ['1944', '1945', '1946', '1943'],
            'correct': 1,
            'reward': 10000
        }
    ],
    'chemistry': [
        {
            'question': 'Suvning kimyoviy formulasi qanday?',
            'options': ['CO2', 'H2O', 'O2', 'NaCl'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Kimyo jadvalidagi birinchi element qaysi?',
            'options': ['Gidrogen', 'Geliy', 'Litiy', 'Uglerod'],
            'correct': 0,
            'reward': 10000
        },
        {
            'question': 'Oksigenning atom raqami qancha?',
            'options': ['6', '7', '8', '9'],
            'correct': 2,
            'reward': 10000
        },
        {
            'question': 'Asoslar qanday taʼsir koʻrsatadi?',
            'options': ['Nordon', 'Ishqoriy', 'Neytral', 'Shirin'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Kimyoviy elementlarning qancha turi mavjud?',
            'options': ['94', '118', '92', '100'],
            'correct': 1,
            'reward': 10000
        }
    ],
    'uzbekistan': [
        {
            'question': 'Oʻzbekiston poytaxti qaysi shahar?',
            'options': ['Samarqand', 'Buxoro', 'Toshkent', 'Andijon'],
            'correct': 2,
            'reward': 10000
        },
        {
            'question': 'Oʻzbekiston mustaqilligini qachon eʼlon qilgan?',
            'options': ['1990', '1991', '1992', '1989'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Oʻzbekistonning eng katta daryosi qaysi?',
            'options': ['Sirdaryo', 'Amudaryo', 'Zarafshon', 'Qashqadaryo'],
            'correct': 1,
            'reward': 10000
        },
        {
            'question': 'Oʻzbekiston qaysi qitada joylashgan?',
            'options': ['Yevropa', 'Afrika', 'Osiyo', 'Amerika'],
            'correct': 2,
            'reward': 10000
        },
        {
            'question': 'Oʻzbekistonning pul birligi nima?',
            'options': ['Dollar', 'Rubl', 'Soʻm', 'Tenge'],
            'correct': 2,
            'reward': 10000
        }
    ]
}

def log_action(action: str, user_id: int, details: str = ''):
    """Harakatlarni log qilish"""
    try:
        db.execute(
            'INSERT INTO logs (action, user_id, details, created_at) VALUES (?, ?, ?, ?)',
            (action, user_id, details, datetime.now())
        )
    except Exception as e:
        logger.error(f"Log yozishda xatolik: {e}")

async def check_admin(user_id: int) -> bool:
    """Foydalanuvchi admin ekanligini tekshirish"""
    try:
        result = db.fetchone('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        return result and result[0] == 1
    except:
        return False

async def get_mandatory_subscriptions() -> list:
    """Majburiy obunalarni olish"""
    try:
        rows = db.fetchall('SELECT entity_username, entity_type, entity_name, invite_link FROM mandatory_subscriptions')
        return [{'username': row[0], 'type': row[1], 'name': row[2], 'invite_link': row[3]} for row in rows]
    except Exception as e:
        logger.error(f"Obunalarni olishda xatolik: {e}")
        return []

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Foydalanuvchi majburiy obunalarga obuna bo'lganligini tekshirish"""
    try:
        subscriptions = await get_mandatory_subscriptions()
        
        if not subscriptions:
            return True
        
        for sub in subscriptions:
            try:
                if sub['type'] in ['channel', 'group']:
                    if sub.get('invite_link'):
                        link = sub['invite_link']
                        if 't.me/' in link:
                            parts = link.split('t.me/')
                            if len(parts) > 1:
                                identifier = parts[1].split('?')[0].replace('/', '')
                                try:
                                    if identifier:
                                        member = await context.bot.get_chat_member(f"@{identifier}", user_id)
                                        if member.status not in ['member', 'administrator', 'creator']:
                                            return False
                                except:
                                    return False
            except Exception as e:
                name = sub.get('name', "Noma'lum")
                logger.error(f"Obunani tekshirishda xatolik {name}: {e}")
                continue
        
        return True
    except Exception as e:
        logger.error(f"Check subscription umumiy xatolik: {e}")
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    try:
        user = update.effective_user
        user_id = user.id
        username = user.username or user.first_name or str(user_id)
        
        # Obunani tekshirish
        is_subscribed = await check_subscription(user_id, context)
        
        if not is_subscribed:
            subscriptions = await get_mandatory_subscriptions()
            
            keyboard = []
            for sub in subscriptions:
                if sub['type'] in ['channel', 'group']:
                    if sub.get('invite_link'):
                        keyboard.append([
                            InlineKeyboardButton(
                                f"✅ {sub['name']} ga obuna bo'lish", 
                                url=sub['invite_link']
                            )
                        ])
                    elif sub.get('username'):
                        keyboard.append([
                            InlineKeyboardButton(
                                f"✅ {sub['name']} ga obuna bo'lish", 
                                url=f"https://t.me/{sub['username']}"
                            )
                        ])
                elif sub['type'] == 'bot':
                    if sub.get('invite_link'):
                        keyboard.append([
                            InlineKeyboardButton(
                                f"🤖 {sub['name']} botini ishga tushirish",
                                url=sub['invite_link']
                            )
                        ])
                    elif sub.get('username'):
                        keyboard.append([
                            InlineKeyboardButton(
                                f"🤖 {sub['name']} botini ishga tushirish",
                                url=f"https://t.me/{sub['username']}?start=start"
                            )
                        ])
            
            keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data='check_subscription')])
            
            await update.message.reply_text(
                f"Hello🖐️ {username}\n\n"
                "🇺🇿: ❌ Kechirasiz, botimizdan foydalanish uchun ushbu kanalga a'zo bo'lishingiz kerak.👇\n\n"
                "🇷🇺: ❌Izvините, vam neobxodimo podpisatsya na etot kanal chtoby ispolzovatʹnashego bota.👇",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Foydalanuvchini bazaga qo'shish
        existing_user = db.fetchone('SELECT * FROM users WHERE user_id = ?', (user_id,))
        
        if not existing_user:
            invited_by = None
            if context.args:
                try:
                    referrer_id = int(context.args[0])
                    referrer = db.fetchone('SELECT * FROM users WHERE user_id = ?', (referrer_id,))
                    if referrer:
                        db.execute(
                            'UPDATE users SET referrals = referrals + 1 WHERE user_id = ?',
                            (referrer_id,)
                        )
                        invited_by = referrer_id
                        
                        try:
                            await context.bot.send_message(
                                chat_id=referrer_id,
                                text=f"🎉 Tabriklaymiz! Yangi do'stingiz qo'shildi.\n"
                                     f"Sizning referrallar soni: {referrer[4] + 1} ta"
                            )
                        except:
                            pass
                except ValueError:
                    pass
            
            db.execute(
                'INSERT INTO users (user_id, username, joined_at, invited_by) VALUES (?, ?, ?, ?)',
                (user_id, username, datetime.now(), invited_by)
            )
            
            log_action('user_joined', user_id, f'username: {username}')
        
        await show_main_menu(update, context)
        
    except Exception as e:
        logger.error(f"Startda xatolik: {e}")
        try:
            await update.message.reply_text("❌ Botda xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")
        except:
            pass

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obunani tekshirish tugmasi"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        is_subscribed = await check_subscription(user_id, context)
        
        if is_subscribed:
            await show_main_menu(update, context)
        else:
            await query.edit_message_text(
                "❌ Hali barcha kanallar/botlarga obuna bo'lmagansiz. Iltimos, barchasiga obuna bo'ling va yana tekshiring."
            )
    except Exception as e:
        logger.error(f"Check subscription callbackda xatolik: {e}")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy menyuni ko'rsatish"""
    try:
        keyboard = [
            [KeyboardButton("1️⃣ Testlarni boshlash")],
            [KeyboardButton("2️⃣ Bonus")],
            [KeyboardButton("3️⃣ Hisobim")],
            [KeyboardButton("4️⃣ Do'stlarni taklif qilish")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        if update.callback_query:
            await update.callback_query.message.reply_text("Asosiy menyu👇", reply_markup=reply_markup)
        else:
            await update.message.reply_text("Asosiy menyu👇", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Show main menuda xatolik: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarlarni qayta ishlash"""
    try:
        text = update.message.text
        
        if text == "1️⃣ Testlarni boshlash":
            await start_tests(update, context)
        elif text == "2️⃣ Bonus":
            await bonus(update, context)
        elif text == "3️⃣ Hisobim":
            await show_account(update, context)
        elif text == "4️⃣ Do'stlarni taklif qilish":
            await invite_friends(update, context)
    except Exception as e:
        logger.error(f"Handle messageda xatolik: {e}")

async def start_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Testlarni boshlash"""
    try:
        user_id = update.effective_user.id
        
        completed_tests = db.fetchall('SELECT category FROM quiz_progress WHERE user_id = ? AND completed = 1', (user_id,))
        
        if completed_tests:
            test_list = ", ".join([cat[0] for cat in completed_tests])
            await update.message.reply_text(
                f"❗️Siz quyidagi testlarni allaqachon yakunlagansiz: {test_list}\n"
                f"Har bir testni faqat 1 marta o'tishingiz mumkin."
            )
        
        await update.message.reply_text(
            "❗️Testni faqat 1 marta o'tishingiz mumkin\n\n"
            "Boshlamoqchi bo'lgan testni tanlang👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏆 Sport", callback_data='quiz_sport')],
                [InlineKeyboardButton("🌍 Geografiya", callback_data='quiz_geography')],
                [InlineKeyboardButton("📜 Tarix", callback_data='quiz_history')],
                [InlineKeyboardButton("🧪 Kimyo", callback_data='quiz_chemistry')],
                [InlineKeyboardButton("🇺🇿 O'zbekiston", callback_data='quiz_uzbekistan')]
            ])
        )
    except Exception as e:
        logger.error(f"Start testsda xatolik: {e}")

async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quizni boshlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        category = query.data.replace('quiz_', '')
        
        progress = db.fetchone(
            'SELECT completed FROM quiz_progress WHERE user_id = ? AND category = ?',
            (user_id, category)
        )
        
        if progress and progress[0]:
            await query.edit_message_text(
                f"❌ Siz {category} testini allaqachon yakunlagansiz.\n"
                f"Boshqa testni tanlang."
            )
            return
        
        db.execute(
            'INSERT OR REPLACE INTO quiz_progress (user_id, category, question_number, score, completed) VALUES (?, ?, 0, 0, 0)',
            (user_id, category)
        )
        
        context.user_data['quiz_category'] = category
        context.user_data['quiz_score'] = 0
        context.user_data['current_question'] = 0
        
        await send_quiz_question(update, context)
    except Exception as e:
        logger.error(f"Start quizda xatolik: {e}")

async def send_quiz_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quiz savolini yuborish"""
    try:
        query = update.callback_query
        category = context.user_data.get('quiz_category')
        question_num = context.user_data.get('current_question', 0)
        
        if not category or question_num >= len(QUIZ_QUESTIONS.get(category, [])):
            await finish_quiz(update, context)
            return
        
        question = QUIZ_QUESTIONS[category][question_num]
        
        keyboard = []
        for i, option in enumerate(question['options']):
            keyboard.append([InlineKeyboardButton(option, callback_data=f'answer_{i}')])
        
        await query.edit_message_text(
            f"{question_num + 1}-savol: {question['question']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Send quiz questionda xatolik: {e}")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Javobni qayta ishlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        answer_index = int(query.data.replace('answer_', ''))
        category = context.user_data.get('quiz_category')
        question_num = context.user_data.get('current_question', 0)
        
        if not category:
            return
        
        question = QUIZ_QUESTIONS[category][question_num]
        is_correct = (answer_index == question['correct'])
        
        if is_correct:
            context.user_data['quiz_score'] = context.user_data.get('quiz_score', 0) + 1
            reward = question['reward']
            
            db.execute(
                'UPDATE users SET balance = balance + ? WHERE user_id = ?',
                (reward, user_id)
            )
            
            result = db.fetchone('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            new_balance = result[0] if result else 0
            
            await query.edit_message_text(
                f"✅ To'g'ri javob berdingiz!\n\n"
                f"💰 Hisobingizda {new_balance} soʻm mavjud!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ Keyingi savol", callback_data='next_question')]
                ])
            )
        else:
            await query.edit_message_text(
                f"❌ Noto'g'ri javob!\n"
                f"To'g'ri javob: {question['options'][question['correct']]}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ Keyingi savol", callback_data='next_question')]
                ])
            )
        
        context.user_data['current_question'] = question_num + 1
        db.execute(
            'UPDATE quiz_progress SET question_number = ?, score = ? WHERE user_id = ? AND category = ?',
            (context.user_data['current_question'], context.user_data.get('quiz_score', 0), user_id, category)
        )
    except Exception as e:
        logger.error(f"Handle answerda xatolik: {e}")

async def next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keyingi savol"""
    try:
        query = update.callback_query
        await query.answer()
        
        await send_quiz_question(update, context)
    except Exception as e:
        logger.error(f"Next questionda xatolik: {e}")

async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quizni yakunlash"""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        category = context.user_data.get('quiz_category', '')
        score = context.user_data.get('quiz_score', 0)
        total = len(QUIZ_QUESTIONS.get(category, []))
        
        db.execute(
            'UPDATE quiz_progress SET completed = 1 WHERE user_id = ? AND category = ?',
            (user_id, category)
        )
        
        context.user_data.clear()
        
        await query.edit_message_text(
            f"🎉 Test yakunlandi!\n\n"
            f"🏆 Natijangiz: {score}/{total}\n"
            f"💰 Umumiy yutuq: {score * 10000} so'm\n\n"
            f"Yana test ishlash uchun /start bosing."
        )
    except Exception as e:
        logger.error(f"Finish quizda xatolik: {e}")

async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bonus bo'limi"""
    try:
        await update.message.reply_text(
            "😊 Yangiliklarni kutib qoling, ushbu bo'lim tez kunlarda tayyorlanadi!\n\n"
            "🔥 Tez kunlarda tayyorlanadi va hammasi sizlar uchun."
        )
    except Exception as e:
        logger.error(f"Bonusda xatolik: {e}")

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hisobni ko'rsatish"""
    try:
        user_id = update.effective_user.id
        user = db.fetchone('SELECT balance, wallet, referrals FROM users WHERE user_id = ?', (user_id,))
        
        if user:
            balance, wallet, referrals = user
            wallet_text = wallet if wallet else "(kiritmagan)"
        else:
            balance, wallet_text, referrals = 0, "(kiritmagan)", 0
        
        message = f"""🎟 Mening hisobim

💰 Hisobdagi mablag': {balance} so'm
👥 Takliflar soni: {referrals} ta
💸 To'lovlar isboti: {PAYMENT_CHANNEL}
💳 Hamyon: {wallet_text}

⚠️ Hamyon to'g'ri ekanligiga ishonch hosil qiling.
⏱️ To'lovlar 12–24 soat ichida amalga oshiriladi.

🔽 Pul yechish uchun tugmani bosing:"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Hamyonni yangilash", callback_data='update_wallet')],
            [InlineKeyboardButton("💰 Pul yechib olish", callback_data='withdraw_money')]
        ]
        
        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Show accountda xatolik: {e}")

async def update_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hamyonni yangilash"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "💳 Hamyon raqamini kiriting\n\n"
            "Namuna: 8600 1234 5678 9000"
        )
        return SETTING_WALLET
    except Exception as e:
        logger.error(f"Update walletda xatolik: {e}")

async def set_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hamyon raqamini saqlash"""
    try:
        wallet = update.message.text
        user_id = update.effective_user.id
        
        cleaned_wallet = ''.join(filter(str.isdigit, wallet))
        
        if len(cleaned_wallet) != 16:
            await update.message.reply_text("❌ Iltimos, to'g'ri hamyon raqamini kiriting. Raqam 16 ta raqamdan iborat bo'lishi kerak.")
            return SETTING_WALLET
        
        db.execute(
            'UPDATE users SET wallet = ? WHERE user_id = ?',
            (cleaned_wallet, user_id)
        )
        
        await update.message.reply_text("✅ Hamyon raqamingiz muvaffaqiyatli yangilandi!")
        await show_account(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Set walletda xatolik: {e}")

async def withdraw_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pul yechish"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        user = db.fetchone('SELECT wallet, referrals, balance FROM users WHERE user_id = ?', (user_id,))
        
        if not user or not user[0] or user[0] == "(kiritmagan)":
            await query.edit_message_text("❌ Iltimos, avval hamyon raqamingizni kiriting.")
            return ConversationHandler.END
        
        wallet, referrals, balance = user
        
        if referrals < 15:
            await query.edit_message_text(
                f"⚠️ Kamida 15 ta do'st taklif qilishingiz kerak.\n"
                f"Sizda: {referrals} ta"
            )
            return ConversationHandler.END
        
        context.user_data['withdrawing'] = True
        context.user_data['wallet'] = wallet
        context.user_data['balance'] = balance
        
        await query.edit_message_text(
            f"💰 Hisobingizda: {balance} so'm\n"
            f"💳 Hamyon raqamingiz: {wallet}\n\n"
            f"💸 Necha so'm yechmoqchisiz? (Eng kam miqdor: 10,000 so'm)"
        )
        return WITHDRAW_AMOUNT
    except Exception as e:
        logger.error(f"Withdraw moneyda xatolik: {e}")

async def process_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pul yechishni qayta ishlash"""
    try:
        amount = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Iltimos, faqat raqam kiriting.")
        return WITHDRAW_AMOUNT
    
    try:
        if amount < 10000:
            await update.message.reply_text("❌ Eng kam miqdor 10,000 so'm")
            return WITHDRAW_AMOUNT
        
        user_balance = context.user_data.get('balance', 0)
        if amount > user_balance:
            await update.message.reply_text(
                f"❌ Hisobingizda mablag' yetarli emas. Kamroq summa kiriting.\n"
                f"💰 Mavjud mablag': {user_balance} so'm"
            )
            return WITHDRAW_AMOUNT
        
        user_id = update.effective_user.id
        wallet = context.user_data.get('wallet', '')
        
        db.execute(
            'UPDATE users SET balance = balance - ? WHERE user_id = ?',
            (amount, user_id)
        )
        
        withdrawal_id = db.insert_and_get_id(
            'INSERT INTO withdrawals (user_id, amount, wallet, created_at) VALUES (?, ?, ?, ?)',
            (user_id, amount, wallet, datetime.now())
        )
        
        user_info = db.fetchone('SELECT username FROM users WHERE user_id = ?', (user_id,))
        username = user_info[0] if user_info else "Noma'lum"
        
        await context.bot.send_message(
            chat_id=PAYMENT_CHANNEL,
            text=f"🔄 Yangi pul yechish so'rovi #{withdrawal_id}\n\n"
                 f"👤 Foydalanuvchi: @{username}\n"
                 f"🆔 ID: {user_id}\n"
                 f"💰 Miqdor: {amount:,} so'm\n"
                 f"💳 Hamyon: {wallet}\n"
                 f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                 f"💰 Holat: ⏳ Kutilmoqda",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Pul o'tkazildi", callback_data=f'confirm_payment_{withdrawal_id}')]
            ])
        )
        
        context.user_data.pop('withdrawing', None)
        
        await update.message.reply_text(
            "✅ Sizning pul yechish arizangiz adminga yuborildi.\n"
            "⏱️ 12-24 soat ichida hamyoningizga tushadi.\n"
            "🎉 Rahmat!"
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Process withdrawalda xatolik: {e}")

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pul o'tkazilganini tasdiqlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        admin_id = query.from_user.id
        
        if not await check_admin(admin_id):
            await query.answer("❌ Siz admin emassiz!", show_alert=True)
            return
        
        withdrawal_id = int(query.data.replace('confirm_payment_', ''))
        
        withdrawal = db.fetchone('''
            SELECT w.user_id, w.amount, w.wallet, u.username 
            FROM withdrawals w 
            JOIN users u ON w.user_id = u.user_id 
            WHERE w.id = ?
        ''', (withdrawal_id,))
        
        if not withdrawal:
            await query.answer("❌ So'rov topilmadi!", show_alert=True)
            return
        
        user_id, amount, wallet, username = withdrawal
        
        db.execute(
            'UPDATE withdrawals SET status = "completed", processed_at = ?, admin_id = ? WHERE id = ?',
            (datetime.now(), admin_id, withdrawal_id)
        )
        
        admin_username = query.from_user.username or "Admin"
        await query.edit_message_text(
            f"✅ Pul yechish amalga oshirildi #{withdrawal_id}\n\n"
            f"👤 Foydalanuvchi: @{username}\n"
            f"🆔 ID: {user_id}\n"
            f"💰 Miqdor: {amount:,} so'm\n"
            f"💳 Hamyon: {wallet}\n"
            f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"💰 Holat: ✅ Amalga oshirildi\n"
            f"👨‍💼 Admin: @{admin_username}"
        )
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 Tabriklaymiz! Pul yechish so'rovingiz tasdiqlandi.\n\n"
                     f"💰 Miqdor: {amount:,} so'm\n"
                     f"💳 Hamyoningizga: {wallet}\n"
                     f"⏱️ Hisobingizga 12-24 soat ichida tushadi.\n\n"
                     f"💸 To'lov isboti: {PAYMENT_CHANNEL}"
            )
        except Exception as e:
            logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
        
        log_action('withdrawal_confirmed', admin_id, f'withdrawal_id: {withdrawal_id}, amount: {amount}')
    except Exception as e:
        logger.error(f"Confirm paymentda xatolik: {e}")

async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Do'stlarni taklif qilish"""
    try:
        user_id = update.effective_user.id
        bot_username = (await context.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        
        message = f"""💵 100 ming soʻmlik savollarga javob topish uchun doʻstlaringiz yordam beradi.

{PAYMENT_CHANNEL} — toʻlov isbotlari.

💰 Pullik savollarni boshlash uchun:
{referral_link}

🎯 Har bir taklif qilgan do'stingiz uchun bonuslar olasiz!"""
        
        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Invite friendsda xatolik: {e}")

# ============ ADMIN PANEL ============

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    try:
        user_id = update.effective_user.id
        
        if not await check_admin(user_id):
            await update.message.reply_text("❌ Siz admin emassiz!")
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Foydalanuvchilar statistikasi", callback_data='admin_stats')],
            [InlineKeyboardButton("📢 Majburiy obunalar", callback_data='admin_subscriptions')],
            [InlineKeyboardButton("📨 Xabar yuborish", callback_data='admin_broadcast')],
            [InlineKeyboardButton("💸 Pul yechish so'rovlari", callback_data='admin_withdrawals')],
            [InlineKeyboardButton("👑 Adminlarni boshqarish", callback_data='admin_manage_admins')],
            [InlineKeyboardButton("📈 Top foydalanuvchilar", callback_data='admin_top_users')],
            [InlineKeyboardButton("📋 Loglar", callback_data='admin_logs')],
            [InlineKeyboardButton("🔙 Asosiy menyu", callback_data='back_to_main')]
        ]
        
        await update.message.reply_text(
            "👑 Admin Panel\n\n"
            "Kerakli bo'limni tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Admin panelda xatolik: {e}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchilar statistikasi"""
    try:
        query = update.callback_query
        await query.answer()
        
        total_users = db.fetchone('SELECT COUNT(*) FROM users')[0] or 0
        new_today = db.fetchone('SELECT COUNT(*) FROM users WHERE DATE(joined_at) = DATE("now")')[0] or 0
        total_balance = db.fetchone('SELECT SUM(balance) FROM users')[0] or 0
        total_referrals = db.fetchone('SELECT SUM(referrals) FROM users')[0] or 0
        pending_withdrawals = db.fetchone('SELECT COUNT(*) FROM withdrawals WHERE status = "pending"')[0] or 0
        completed_withdrawals = db.fetchone('SELECT COUNT(*) FROM withdrawals WHERE status = "completed"')[0] or 0
        total_withdrawn = db.fetchone('SELECT SUM(amount) FROM withdrawals WHERE status = "completed"')[0] or 0
        total_subscriptions = db.fetchone('SELECT COUNT(*) FROM mandatory_subscriptions')[0] or 0
        
        message = f"""📊 Bot statistikasi:

👥 Foydalanuvchilar:
├─ Umumiy: {total_users} ta
└─ Bugun qo'shilgan: {new_today} ta

💰 Moliya:
├─ Umumiy balans: {total_balance:,} so'm
├─ Umumiy takliflar: {total_referrals} ta
├─ Pul yechish so'rovlari:
│  ├─ Kutilayotgan: {pending_withdrawals} ta
│  └─ Bajarilgan: {completed_withdrawals} ta
└─ Yechilgan summa: {total_withdrawn:,} so'm

⚙️ Boshqaruv:
├─ Majburiy obunalar: {total_subscriptions} ta
└─ Adminlar: {len(ADMIN_IDS)} ta"""
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yangilash", callback_data='admin_stats')],
                [InlineKeyboardButton("📈 Detal stat", callback_data='admin_detailed_stats')],
                [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
            ])
        )
    except Exception as e:
        logger.error(f"Admin statsda xatolik: {e}")

async def admin_detailed_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detalli statistika"""
    try:
        query = update.callback_query
        await query.answer()
        
        top_users = db.fetchall('''
            SELECT username, balance, referrals, joined_at 
            FROM users 
            WHERE is_admin = FALSE 
            ORDER BY balance DESC 
            LIMIT 10
        ''')
        
        top_referrers = db.fetchall('''
            SELECT username, referrals, balance 
            FROM users 
            WHERE is_admin = FALSE 
            ORDER BY referrals DESC 
            LIMIT 10
        ''')
        
        message = "🏆 TOP 10 Foydalanuvchilar (balans bo'yicha):\n\n"
        for i, (username, balance, referrals, joined_at) in enumerate(top_users, 1):
            username_display = username or "Noma'lum"
            joined_date = joined_at[:10] if joined_at else "Noma'lum"
            message += f"{i}. @{username_display}\n"
            message += f"   💰 Balans: {balance:,} so'm\n"
            message += f"   👥 Takliflar: {referrals} ta\n"
            message += f"   📅 Qo'shilgan: {joined_date}\n\n"
        
        message += "\n🎯 TOP 10 Taklif qiluvchilar:\n\n"
        for i, (username, referrals, balance) in enumerate(top_referrers, 1):
            username_display = username or "Noma'lum"
            message += f"{i}. @{username_display}\n"
            message += f"   👥 Takliflar: {referrals} ta\n"
            message += f"   💰 Balans: {balance:,} so'm\n\n"
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Asosiy stat", callback_data='admin_stats')],
                [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
            ])
        )
    except Exception as e:
        logger.error(f"Admin detailed statsda xatolik: {e}")

async def admin_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Majburiy obunalarni boshqarish"""
    try:
        query = update.callback_query
        await query.answer()
        
        subscriptions = db.fetchall('SELECT entity_username, entity_type, entity_name, invite_link FROM mandatory_subscriptions')
        
        if not subscriptions:
            message = "📢 Majburiy obunalar ro'yxati bo'sh"
        else:
            message = "📢 Majburiy obunalar ro'yxati:\n\n"
            for i, (username, type_, name, invite_link) in enumerate(subscriptions, 1):
                icon = "📢" if type_ == 'channel' else "🤖" if type_ == 'bot' else "👥"
                link = invite_link or f"https://t.me/{username}" if username else "Link yo'q"
                message += f"{i}. {icon} {name or 'Nomalum'}\n"
                message += f"   👉 Link: {link}\n"
                message += f"   📝 Turi: {type_}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Obuna qo'shish", callback_data='add_subscription')],
            [InlineKeyboardButton("➖ Obuna o'chirish", callback_data='remove_subscription')],
            [InlineKeyboardButton("🔄 Ro'yxatni yangilash", callback_data='admin_subscriptions')],
            [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Admin subscriptionsda xatolik: {e}")

async def add_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obuna qo'shishni boshlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "📢 Yangi majburiy obuna qo'shish\n\n"
            "Obuna turini tanlang:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanal", callback_data='sub_type_channel')],
                [InlineKeyboardButton("🤖 Bot", callback_data='sub_type_bot')],
                [InlineKeyboardButton("👥 Guruh", callback_data='sub_type_group')],
                [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_subscriptions')]
            ])
        )
        return ADMIN_WAITING_SUB_TYPE
    except Exception as e:
        logger.error(f"Add subscription startda xatolik: {e}")

async def set_subscription_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obuna turini tanlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        sub_type = query.data.replace('sub_type_', '')
        context.user_data['admin_sub_type'] = sub_type
        
        type_names = {'channel': 'kanal', 'bot': 'bot', 'group': 'guruh'}
        
        await query.edit_message_text(
            f"📢 Yangi {type_names.get(sub_type, sub_type)} qo'shish\n\n"
            f"Iltimos, {type_names.get(sub_type, sub_type)} username'ini yuboring:\n"
            f"Masalan: kanal_nomi\n"
            f"Yoki: @kanal_username\n"
            f"Yoki: https://t.me/kanal_nomi"
        )
        return ADMIN_ADD_SUBSCRIPTION
    except Exception as e:
        logger.error(f"Set subscription typeda xatolik: {e}")

async def add_subscription_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obuna qo'shish"""
    try:
        user_input = update.message.text.strip()
        sub_type = context.user_data.get('admin_sub_type')
        user_id = update.effective_user.id
        
        if not sub_type:
            await update.message.reply_text("❌ Obuna turi aniqlanmadi. /admin dan qayta boshlang.")
            return ConversationHandler.END
        
        entity_username = user_input
        
        if entity_username.startswith('@'):
            entity_username = entity_username[1:]
        
        if 't.me/' in entity_username:
            parts = entity_username.split('t.me/')
            if len(parts) > 1:
                entity_username = parts[1].split('?')[0].replace('/', '')
        
        entity_username = entity_username.strip()
        
        if not entity_username:
            await update.message.reply_text("❌ Username aniqlanmadi")
            return ConversationHandler.END
        
        invite_link = f"https://t.me/{entity_username}"
        entity_name = entity_username
        entity_id = entity_username
        
        existing = db.fetchone('SELECT 1 FROM mandatory_subscriptions WHERE entity_username = ?', (entity_username,))
        if existing:
            await update.message.reply_text(f"❌ @{entity_username} allaqachon ro'yxatda!")
            return ConversationHandler.END
        
        db.execute(
            'INSERT INTO mandatory_subscriptions (entity_id, entity_username, entity_name, entity_type, added_at, added_by, invite_link) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (entity_id, entity_username, entity_name, sub_type, datetime.now(), user_id, invite_link)
        )
        
        await update.message.reply_text(
            f"✅ {sub_type.capitalize()} muvaffaqiyatli qo'shildi:\n"
            f"📢 Nomi: {entity_name}\n"
            f"🔗 Havola: {invite_link}\n"
            f"👤 Username: @{entity_username}"
        )
        
        log_action('subscription_added', user_id, f'{sub_type}: {entity_name}')
        
        context.user_data.clear()
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Add subscription processda xatolik: {e}")
        await update.message.reply_text(f"❌ Xatolik yuz berdi: {str(e)[:100]}")
        return ConversationHandler.END

async def remove_subscription_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obuna o'chirish uchun tanlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        subscriptions = db.fetchall('SELECT id, entity_name, entity_type FROM mandatory_subscriptions')
        
        if not subscriptions:
            await query.edit_message_text("❌ Obunalar topilmadi")
            return
        
        keyboard = []
        for sub_id, name, type_ in subscriptions:
            icon = "📢" if type_ == 'channel' else "🤖" if type_ == 'bot' else "👥"
            name_display = name or f"ID: {sub_id}"
            keyboard.append([InlineKeyboardButton(f"{icon} {name_display}", callback_data=f'remove_sub_{sub_id}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_subscriptions')])
        
        await query.edit_message_text(
            "O'chirmoqchi bo'lgan obunani tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Remove subscription selectionda xatolik: {e}")

async def remove_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obunani o'chirish"""
    try:
        query = update.callback_query
        await query.answer()
        
        sub_id = int(query.data.replace('remove_sub_', ''))
        
        sub_info = db.fetchone('SELECT entity_name FROM mandatory_subscriptions WHERE id = ?', (sub_id,))
        
        if sub_info:
            db.execute('DELETE FROM mandatory_subscriptions WHERE id = ?', (sub_id,))
            
            log_action('subscription_removed', query.from_user.id, f'id: {sub_id}, name: {sub_info[0]}')
            
            await query.edit_message_text(f"✅ Obuna '{sub_info[0]}' o'chirildi")
        
        await admin_subscriptions(update, context)
    except Exception as e:
        logger.error(f"Remove subscriptionda xatolik: {e}")

async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pul yechish so'rovlarini ko'rish"""
    try:
        query = update.callback_query
        await query.answer()
        
        withdrawals = db.fetchall('''
            SELECT w.id, u.username, w.amount, w.wallet, w.status, w.created_at
            FROM withdrawals w
            JOIN users u ON w.user_id = u.user_id
            ORDER BY w.created_at DESC
            LIMIT 10
        ''')
        
        if not withdrawals:
            message = "💸 Pul yechish so'rovlari topilmadi"
        else:
            message = "💸 Pul yechish so'rovlari:\n\n"
            for w_id, username, amount, wallet, status, created_at in withdrawals:
                status_icon = "🟢" if status == 'completed' else "🟡" if status == 'pending' else "🔴"
                username_display = username or "Noma'lum"
                created_date = created_at[:10] if created_at else "Noma'lum"
                message += f"{status_icon} ID: {w_id}\n"
                message += f"👤 @{username_display}\n"
                message += f"💰 Summa: {amount:,} so'm\n"
                message += f"💳 Hamyon: {wallet}\n"
                message += f"📊 Holat: {status}\n"
                message += f"📅 Sana: {created_date}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Yangilash", callback_data='admin_withdrawals')],
            [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Admin withdrawalsda xatolik: {e}")

async def admin_manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adminlarni boshqarish"""
    try:
        query = update.callback_query
        await query.answer()
        
        admins = db.fetchall('SELECT user_id, username FROM users WHERE is_admin = TRUE')
        
        message = "👑 Adminlar ro'yxati:\n\n"
        for i, (admin_id, username) in enumerate(admins, 1):
            username_display = username or "Noma'lum"
            message += f"{i}. @{username_display}\n"
            message += f"   🆔 ID: {admin_id}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Admin qo'shish", callback_data='add_admin')],
            [InlineKeyboardButton("➖ Admin o'chirish", callback_data='remove_admin')],
            [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Admin manage adminsda xatolik: {e}")

async def add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin qo'shishni boshlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "➕ Yangi admin qo'shish\n\n"
            "Foydalanuvchi ID'sini yuboring:"
        )
        return ADMIN_ADD_ADMIN
    except Exception as e:
        logger.error(f"Add admin startda xatolik: {e}")

async def add_admin_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin qo'shish"""
    try:
        new_admin_id = int(update.message.text)
        
        existing = db.fetchone('SELECT 1 FROM users WHERE user_id = ?', (new_admin_id,))
        if not existing:
            db.execute('INSERT INTO users (user_id, is_admin, joined_at) VALUES (?, ?, ?)',
                      (new_admin_id, True, datetime.now()))
        else:
            db.execute('UPDATE users SET is_admin = TRUE WHERE user_id = ?', (new_admin_id,))
        
        if new_admin_id not in ADMIN_IDS:
            ADMIN_IDS.append(new_admin_id)
        
        await update.message.reply_text(f"✅ Foydalanuvchi {new_admin_id} admin qilindi")
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID format. Faqat raqam kiriting.")
        return ADMIN_ADD_ADMIN
    except Exception as e:
        logger.error(f"Add admin processda xatolik: {e}")
        await update.message.reply_text(f"❌ Xatolik: {str(e)[:100]}")
        return ConversationHandler.END

async def remove_admin_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin o'chirish uchun tanlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        admins = db.fetchall('SELECT user_id, username FROM users WHERE is_admin = TRUE')
        
        keyboard = []
        for admin_id, username in admins:
            if admin_id not in ADMIN_IDS:
                username_display = username or str(admin_id)
                keyboard.append([InlineKeyboardButton(f"❌ @{username_display}", callback_data=f'remove_admin_{admin_id}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data='admin_manage_admins')])
        
        await query.edit_message_text(
            "O'chirmoqchi bo'lgan adminni tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Remove admin selectionda xatolik: {e}")

async def remove_admin_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adminni o'chirish"""
    try:
        query = update.callback_query
        await query.answer()
        
        admin_id = int(query.data.replace('remove_admin_', ''))
        
        if admin_id in ADMIN_IDS:
            await query.answer("❌ Asosiy adminni o'chirib bo'lmaydi!", show_alert=True)
            return
        
        db.execute('UPDATE users SET is_admin = FALSE WHERE user_id = ?', (admin_id,))
        
        await query.answer("✅ Admin o'chirildi")
        await admin_manage_admins(update, context)
    except Exception as e:
        logger.error(f"Remove admin processda xatolik: {e}")

async def admin_top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top foydalanuvchilar"""
    try:
        query = update.callback_query
        await query.answer()
        
        top_users = db.fetchall('''
            SELECT username, balance, referrals, joined_at 
            FROM users 
            WHERE is_admin = FALSE 
            ORDER BY balance DESC 
            LIMIT 10
        ''')
        
        message = "🏆 TOP 10 Foydalanuvchilar (balans bo'yicha):\n\n"
        for i, (username, balance, referrals, joined_at) in enumerate(top_users, 1):
            username_display = username or "Noma'lum"
            joined_date = joined_at[:10] if joined_at else "Noma'lum"
            message += f"{i}. @{username_display}\n"
            message += f"   💰 Balans: {balance:,} so'm\n"
            message += f"   👥 Takliflar: {referrals} ta\n"
            message += f"   📅 Qo'shilgan: {joined_date}\n\n"
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yangilash", callback_data='admin_top_users')],
                [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
            ])
        )
    except Exception as e:
        logger.error(f"Admin top usersda xatolik: {e}")

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabar yuborishni boshlash"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "📨 Hammaga xabar yuborish\n\n"
            "Yubormoqchi bo'lgan xabaringizni yuboring:"
        )
        return ADMIN_BROADCAST
    except Exception as e:
        logger.error(f"Admin broadcast startda xatolik: {e}")

async def admin_broadcast_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarni yuborish"""
    try:
        message_text = update.message.text
        user_id = update.effective_user.id
        
        users = db.fetchall('SELECT user_id FROM users WHERE is_admin = FALSE')
        
        sent_count = 0
        failed_count = 0
        
        processing_msg = await update.message.reply_text("⏳ Xabar yuborilmoqda... 0%")
        
        total_users = len(users)
        
        for i, (uid,) in enumerate(users):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=message_text
                )
                sent_count += 1
                
                if i % 10 == 0 or i == total_users - 1:
                    progress = int((i / total_users) * 100) if total_users > 0 else 0
                    await processing_msg.edit_text(f"⏳ Xabar yuborilmoqda... {progress}%")
                    
            except Exception as e:
                logger.error(f"Xabar yuborishda xatolik {uid}: {e}")
                failed_count += 1
            
            await asyncio.sleep(0.1)
        
        db.execute(
            'INSERT INTO admin_messages (admin_id, message, sent_at, receivers_count) VALUES (?, ?, ?, ?)',
            (user_id, message_text[:100], datetime.now(), sent_count)
        )
        
        await processing_msg.edit_text(
            f"✅ Xabar yuborish yakunlandi!\n\n"
            f"📊 Natijalar:\n"
            f"✅ Muvaffaqiyatli: {sent_count} ta\n"
            f"❌ Xatolik: {failed_count} ta\n"
            f"📩 Jami: {total_users} ta"
        )
        
        log_action('broadcast_sent', user_id, f'sent: {sent_count}, failed: {failed_count}')
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Admin broadcast processda xatolik: {e}")
        return ConversationHandler.END

async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Loglarni ko'rish"""
    try:
        query = update.callback_query
        await query.answer()
        
        logs = db.fetchall('''
            SELECT action, user_id, details, created_at 
            FROM logs 
            ORDER BY created_at DESC 
            LIMIT 20
        ''')
        
        if not logs:
            message = "📋 Loglar topilmadi"
        else:
            message = "📋 Oxirgi 20 ta harakat:\n\n"
            for action, user_id, details, created_at in logs:
                created_time = created_at[:19] if created_at else "Noma'lum"
                message += f"🕒 {created_time}\n"
                message += f"🔧 {action}\n"
                message += f"👤 ID: {user_id}\n"
                if details:
                    message += f"📝 {details[:50]}\n"
                message += "─" * 30 + "\n"
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yangilash", callback_data='admin_logs')],
                [InlineKeyboardButton("🔙 Orqaga", callback_data='admin_back')]
            ])
        )
    except Exception as e:
        logger.error(f"Admin logsda xatolik: {e}")

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panelga qaytish"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Yangi Update obyekti yaratish (message bilan)
        class FakeUpdate:
            def __init__(self, effective_user, message):
                self.effective_user = effective_user
                self.message = message
        
        fake_update = FakeUpdate(query.from_user, query.message)
        
        await admin_panel(fake_update, context)
    except Exception as e:
        logger.error(f"Admin backda xatolik: {e}")

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy menyuga qaytish"""
    try:
        query = update.callback_query
        await query.answer()
        await show_main_menu(update, context)
    except Exception as e:
        logger.error(f"Back to mainda xatolik: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conversationni bekor qilish"""
    try:
        context.user_data.clear()
        await update.message.reply_text("❌ Amal bekor qilindi.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Cancelda xatolik: {e}")

def main():
    """Botni ishga tushirish"""
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN .env faylda aniqlanmagan!")
        return
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # User conversation handler (wallet va withdrawal)
        user_conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(update_wallet, pattern='^update_wallet$'),
                CallbackQueryHandler(withdraw_money, pattern='^withdraw_money$')
            ],
            states={
                SETTING_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_wallet)],
                WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdrawal)]
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            name="user_conversation",
            persistent=False
        )
        
        # Admin conversation handler
        admin_conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(add_subscription_start, pattern='^add_subscription$'),
                CallbackQueryHandler(add_admin_start, pattern='^add_admin$'),
                CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast$')
            ],
            states={
                ADMIN_WAITING_SUB_TYPE: [
                    CallbackQueryHandler(set_subscription_type, pattern='^sub_type_')
                ],
                ADMIN_ADD_SUBSCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_subscription_process)
                ],
                ADMIN_ADD_ADMIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_process)
                ],
                ADMIN_BROADCAST: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_process)
                ]
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            name="admin_conversation",
            persistent=False
        )
        
        # Handlers qo'shish (tartib muhim!)
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_panel))
        application.add_handler(user_conv_handler)
        application.add_handler(admin_conv_handler)
        
        # Callback handlers
        application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern='^check_subscription$'))
        application.add_handler(CallbackQueryHandler(start_quiz, pattern='^quiz_'))
        application.add_handler(CallbackQueryHandler(handle_answer, pattern='^answer_'))
        application.add_handler(CallbackQueryHandler(next_question, pattern='^next_question$'))
        application.add_handler(CallbackQueryHandler(confirm_payment, pattern='^confirm_payment_'))
        
        # Admin callback handlers
        application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
        application.add_handler(CallbackQueryHandler(admin_detailed_stats, pattern='^admin_detailed_stats$'))
        application.add_handler(CallbackQueryHandler(admin_subscriptions, pattern='^admin_subscriptions$'))
        application.add_handler(CallbackQueryHandler(remove_subscription_selection, pattern='^remove_subscription$'))
        application.add_handler(CallbackQueryHandler(remove_subscription, pattern='^remove_sub_'))
        application.add_handler(CallbackQueryHandler(admin_withdrawals, pattern='^admin_withdrawals'))
        application.add_handler(CallbackQueryHandler(admin_manage_admins, pattern='^admin_manage_admins$'))
        application.add_handler(CallbackQueryHandler(remove_admin_selection, pattern='^remove_admin$'))
        application.add_handler(CallbackQueryHandler(remove_admin_process, pattern='^remove_admin_'))
        application.add_handler(CallbackQueryHandler(admin_top_users, pattern='^admin_top_users$'))
        application.add_handler(CallbackQueryHandler(admin_logs, pattern='^admin_logs$'))
        application.add_handler(CallbackQueryHandler(admin_back, pattern='^admin_back$'))
        application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
        
        # Asosiy message handler (eng oxirida!)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Botni ishga tushirish
        print("🤖 Bot ishga tushmoqda...")
        print(f"✅ Admin IDs: {ADMIN_IDS}")
        print(f"✅ Payment Channel: {PAYMENT_CHANNEL}")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Botni ishga tushirishda xatolik: {e}")

if __name__ == '__main__':
    main()
