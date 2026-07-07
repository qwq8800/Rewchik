"""
Конфигурация бота.
Бот жёстко привязан к ОДНОМУ чату — @RewchikChat.
Любой апдейт из другого чата игнорируется на уровне middleware (см. main.py).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Токен бота ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# --- Единственный разрешённый чат ---
ALLOWED_CHAT_ID = -1004458436938
ALLOWED_CHAT_USERNAME = "RewchikChat"

# --- База данных ---
# На Railway подключите Volume и смонтируйте его в /data, затем задайте
# переменную окружения DB_PATH=/data/bot.db — иначе SQLite-файл будет
# уничтожаться при каждом новом деплое (файловая система эфемерна).
_default_db_path = "/data/bot.db" if os.path.isdir("/data") else "bot.db"
DB_PATH = os.getenv("DB_PATH", _default_db_path)

# --- Значения по умолчанию для модерации (хранятся в БД, это только "фабричные" значения) ---
DEFAULT_SETTINGS = {
    "antiflood_enabled": "1",
    "antiflood_window_sec": "10",      # окно антифлуда
    "antiflood_limit": "8",            # сообщений за окно -> нарушение
    "antispam_enabled": "1",
    "antispam_new_user_minutes": "10",  # "новый" участник, если < N минут в чате
    "antispam_similarity": "0.85",      # порог нечёткого сравнения (0..1)
    "antiad_enabled": "1",
    "welcome_enabled": "1",
    "welcome_text": "👋 Добро пожаловать в {chat_title}, {user_mention}!\nПеред тем как начать общение, ознакомься с правилами чата.",
    # Лестница наказаний (в секундах, для мутов). После исчерпания списка — бан.
    "punishment_ladder": "300,1800,10800,86400",
}

# Стоп-слова для антирекламы/антиспама по умолчанию (админ может дополнить через панель)
DEFAULT_BANNED_WORDS = [
    "заработок без вложений",
    "крипто-сигналы",
    "инвестиции с гарантией",
    "казино",
]

DEFAULT_AD_DOMAINS = [
    "bit.ly",
]
