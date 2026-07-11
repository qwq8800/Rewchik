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
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "-1004458436938"))
ALLOWED_CHAT_USERNAME = "RewchikChat"

# --- Главный администратор бота ---
# Имеет полные права администратора чата на уровне бота (открывает /settings,
# создаёт и назначает роли, банит/кикает/мутит и т.д.) независимо от того,
# выданы ли ему реальные права администратора в самом Telegram-чате.
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8375758522"))
SUPER_ADMIN_USERNAME = "Luvkryacan"

# --- Главный администратор бота ---
# Имеет все права всегда (может назначать роли, менять настройки, банить и т.д.),
# даже если Telegram не выдал ему статус admin/creator в самом чате.
SUPER_ADMIN_ID = 8375758522
SUPER_ADMIN_USERNAME = "Luvkryacan"

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
    "welcome_text": "👋 Добро пожаловать в {chat_title}, {user_mention}!\nПравила чата: /rules\nВсе команды бота: /help",
    "farewell_enabled": "1",
    "farewell_text": "👋 {user_mention} покинул(а) чат.",
    "captcha_enabled": "1",
    "captcha_timeout_sec": "120",        # сколько времени даётся новичку на подтверждение
    "warn_expiry_days": "30",            # предупреждения старше N дней не учитываются при эскалации наказаний
    "report_cooldown_sec": "120",        # антиспам жалоб: не чаще раза в N секунд с одного участника
    "nickname_change_cooldown_sec": "172800",  # смена имени — не чаще раза в 2 суток
    "nickname_min_length": "2",
    "nickname_max_length": "32",
    # --- Антирейд ---
    "raid_detection_enabled": "1",
    "raid_join_threshold": "5",          # столько вступлений за окно ниже — считается рейдом
    "raid_window_sec": "60",
    "raid_auto_lockdown_enabled": "1",   # при обнаружении рейда автоматически ограничивать чат
    "lockdown_active": "0",              # текущее состояние блокировки (управляется ботом, не редактируется вручную)
    "saved_permissions_json": "",        # права чата до блокировки — для восстановления при /unlock
    "pay_min_amount": "1",
    "pay_max_amount": "10000",
    "giveaway_max_duration_min": "1440",  # максимум 24 часа
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
    "coinflip_min_bet": "5",
    "coinflip_max_bet": "500",
    "slots_min_bet": "5",
    "slots_max_bet": "300",
    "duel_min_bet": "5",
    "duel_max_bet": "1000",
    "duel_timeout_sec": "300",
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

# Кастомные роли чата (раздел "Роли" ТЗ): администратор выдаёт роль с определённым
# набором прав из фиксированного словаря PERMISSIONS. Не путать с реальными правами
# Telegram (creator/administrator) — это дополнительный уровень поверх них.
ROLE_MODERATOR = "moderator"

PERMISSIONS = {
    "moderate": "Варнить/мутить/снимать мут (/warn, /mute, /unmute)",
    "kick_ban": "Кикать/банить (/kick, /ban, /unban)",
    "manage_settings": "Менять настройки чата (/settings, /setflood, стоп-слова, домены)",
    "view_stats": "Смотреть статистику чата (/stats)",
    "manage_reports": "Обрабатывать жалобы (/report — кнопки мут/бан/отклонить)",
}

# Роли по умолчанию: (role_key, title, permissions_csv)
DEFAULT_CUSTOM_ROLES = [
    ("moderator", "Модератор", "moderate,view_stats,manage_reports"),
]


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

# Магазин по умолчанию (раздел "Экономика" ТЗ, упрощённая версия: только косметические
# бейджи, отображаемые в /rank и /inventory — без игровых преимуществ, чтобы не ломать
# баланс модерации/вовлечения).
DEFAULT_SHOP_ITEMS = [
    ("badge_star", "🌟 Бейдж «Звезда»", "Косметический бейдж рядом с ником в /rank", 200),
    ("badge_crown", "👑 Бейдж «Корона»", "Косметический бейдж рядом с ником в /rank", 500),
    ("badge_fire", "🔥 Бейдж «Огонь»", "Косметический бейдж рядом с ником в /rank", 150),
    ("badge_diamond", "💎 Бейдж «Бриллиант»", "Косметический бейдж рядом с ником в /rank", 1000),
]

# Предыдущее значение welcome_text по умолчанию — используется миграцией в db.py,
# чтобы обновить приветствие на уже развёрнутых ботах, но НЕ трогать текст, если
# администратор чата настроил его вручную через /setwelcome.
LEGACY_DEFAULT_WELCOME_TEXTS = [
    "👋 Добро пожаловать в {chat_title}, {user_mention}!\nПеред тем как начать общение, ознакомься с правилами чата.",
]


