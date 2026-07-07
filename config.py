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
    "anticaps_enabled": "1",
    "anticaps_min_length": "10",        # проверять капс только от N символов
    "anticaps_ratio": "0.7",            # доля заглавных букв, после которой -> нарушение
    "antimention_enabled": "1",
    "antimention_limit": "5",           # максимум упоминаний в одном сообщении
    "antirepeat_enabled": "1",
    "antirepeat_min_length": "6",       # "ААААААА" от скольки одинаковых символов подряд
    "welcome_enabled": "1",
    "welcome_text": "👋 Добро пожаловать в {chat_title}, {user_mention}!\nПеред тем как начать общение, ознакомься с правилами чата.",
    "farewell_enabled": "1",
    "farewell_text": "👋 {user_mention} покинул(а) чат.",
    "captcha_enabled": "1",
    "captcha_timeout_sec": "120",        # сколько времени даётся новичку на подтверждение
    "reputation_enabled": "1",
    "reputation_cooldown_sec": "3600",   # раз в сколько времени можно выдать +1 репутации тому же человеку
    # --- Экономика ---
    "economy_enabled": "1",
    "currency_name": "монет",
    "starting_balance": "100",
    "message_reward": "1",              # монет за сообщение (учитывается вместе с антифлуд-кулдауном XP)
    "message_reward_cooldown_sec": "30",  # не начислять монеты чаще, чем раз в N секунд
    "daily_bonus_amount": "50",
    "daily_bonus_cooldown_sec": "86400",
    "levelup_bonus_amount": "20",        # бонус монет при повышении уровня
    # --- Мини-игры ---
    "minigames_enabled": "1",
    "dice_min_bet": "5",
    "dice_max_bet": "500",
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

# Кастомные роли чата (раздел "Роли" ТЗ, упрощённая версия: одна выдаваемая роль
# поверх стандартных прав Telegram creator/administrator).
ROLE_MODERATOR = "moderator"

# Достижения по умолчанию (раздел "Достижения" ТЗ).
# type: "messages" | "level" | "reputation"
DEFAULT_ACHIEVEMENTS = [
    ("first_message", "🌱 Новичок", "Написать первое сообщение в чате", "messages", 1),
    ("chatty", "💬 Активный участник", "Написать 100 сообщений", "messages", 100),
    ("veteran", "🏅 Ветеран чата", "Написать 1000 сообщений", "messages", 1000),
    ("level5", "⭐ Опытный", "Достичь 5 уровня", "level", 5),
    ("level10", "🌟 Легенда чата", "Достичь 10 уровня", "level", 10),
    ("respected", "🤝 Уважаемый", "Набрать 10 репутации", "reputation", 10),
]


