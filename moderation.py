"""
Анти-модули модерации.

Каждый детектор реализует принцип из ТЗ (раздел 4.0):
вход -> локальный вердикт (нарушение/нет + "вес" нарушения) -> Punishment Engine решает,
что делать дальше (main.py вызывает punishments.apply_verdict()).

Счётчики антифлуда и недавние сообщения для антиспама хранятся в памяти процесса
(collections.deque), т.к. это "горячие" данные с высокой частотой обновления —
в "большом" ТЗ для этого предусмотрен Redis, для одного чата достаточно памяти процесса.
"""
import time
import difflib
from collections import defaultdict, deque

import db

# user_id -> deque[timestamps] — для антифлуда
_message_timestamps: dict[int, deque] = defaultdict(deque)

# deque[(user_id, text, timestamp)] — недавние сообщения для антиспама (дубликаты)
_recent_messages: deque = deque(maxlen=200)


class Verdict:
    """Результат работы одного анти-модуля."""

    def __init__(self, triggered: bool, module: str = "", severity: int = 0, reason: str = ""):
        self.triggered = triggered
        self.module = module
        self.severity = severity  # 1 = лёгкое (варн), 2 = среднее (мут), 3 = тяжёлое (кик/бан)
        self.reason = reason


async def check_antiflood(user_id: int, now: float = None) -> Verdict:
    if not await db.get_bool_setting("antiflood_enabled"):
        return Verdict(False)

    now = now or time.time()
    window = int(await db.get_setting("antiflood_window_sec"))
    limit = int(await db.get_setting("antiflood_limit"))

    dq = _message_timestamps[user_id]
    dq.append(now)
    while dq and now - dq[0] > window:
        dq.popleft()

    if len(dq) > limit:
        dq.clear()  # сбрасываем счётчик после срабатывания, чтобы не наказывать многократно за одну волну
        return Verdict(True, "antiflood", severity=2, reason=f"Флуд: более {limit} сообщений за {window} сек.")
    return Verdict(False)


async def check_antispam_duplicate(user_id: int, text: str, joined_at: int, now: float = None) -> Verdict:
    if not await db.get_bool_setting("antispam_enabled") or not text:
        return Verdict(False)

    now = now or time.time()
    new_user_minutes = int(await db.get_setting("antispam_new_user_minutes"))
    similarity_threshold = float(await db.get_setting("antispam_similarity"))

    is_new_user = joined_at and (now - joined_at) < new_user_minutes * 60

    verdict = Verdict(False)
    if is_new_user:
        for other_user_id, other_text, ts in _recent_messages:
            if other_user_id == user_id:
                continue
            if now - ts > 300:  # окно "спам-волны" — 5 минут
                continue
            ratio = difflib.SequenceMatcher(None, text.lower(), other_text.lower()).ratio()
            if ratio >= similarity_threshold:
                verdict = Verdict(
                    True, "antispam", severity=1,
                    reason="Похожее сообщение от другого нового участника (подозрение на скоординированный спам)."
                )
                break

    _recent_messages.append((user_id, text, now))
    return verdict


async def check_banned_words(text: str) -> Verdict:
    if not text:
        return Verdict(False)
    words = await db.get_banned_words()
    lowered = text.lower()
    for w in words:
        if w in lowered:
            return Verdict(True, "banned_words", severity=1, reason=f"Запрещённое слово/фраза: «{w}»")
    return Verdict(False)


async def check_antiad(text: str, message_entities_urls: list[str]) -> Verdict:
    if not await db.get_bool_setting("antiad_enabled"):
        return Verdict(False)

    domains = await db.get_ad_domains()
    haystacks = [text.lower()] + [u.lower() for u in (message_entities_urls or [])]
    for domain in domains:
        for h in haystacks:
            if domain in h:
                return Verdict(True, "antiad", severity=2, reason=f"Реклама/чёрный список домена: {domain}")
    return Verdict(False)


async def check_anticaps(text: str) -> Verdict:
    """Избыточный КАПС (раздел 4.2 расширенный антифлуд)."""
    if not await db.get_bool_setting("anticaps_enabled") or not text:
        return Verdict(False)

    min_length = int(await db.get_setting("anticaps_min_length"))
    ratio_threshold = float(await db.get_setting("anticaps_ratio"))

    letters = [c for c in text if c.isalpha()]
    if len(letters) < min_length:
        return Verdict(False)

    upper_count = sum(1 for c in letters if c.isupper())
    ratio = upper_count / len(letters)
    if ratio >= ratio_threshold:
        return Verdict(True, "anticaps", severity=1, reason="Избыточный КАПС в сообщении.")
    return Verdict(False)


async def check_antimention(entities) -> Verdict:
    """Массовые упоминания (mention-spam)."""
    if not await db.get_bool_setting("antimention_enabled") or not entities:
        return Verdict(False)

    limit = int(await db.get_setting("antimention_limit"))
    mention_count = sum(1 for ent in entities if ent.type in ("mention", "text_mention"))
    if mention_count > limit:
        return Verdict(
            True, "antimention", severity=2,
            reason=f"Слишком много упоминаний в одном сообщении ({mention_count} > {limit})."
        )
    return Verdict(False)


async def check_antirepeat(text: str) -> Verdict:
    """Повторяющиеся символы подряд ("ААААААА")."""
    if not await db.get_bool_setting("antirepeat_enabled") or not text:
        return Verdict(False)

    min_length = int(await db.get_setting("antirepeat_min_length"))
    run_char = None
    run_len = 0
    for c in text:
        if c == run_char:
            run_len += 1
        else:
            run_char = c
            run_len = 1
        if run_len >= min_length and c.isalnum():
            return Verdict(True, "antirepeat", severity=1, reason="Повторяющиеся символы подряд.")
    return Verdict(False)


def strongest_verdict(verdicts: list[Verdict]) -> Verdict:
    """Если сработало несколько анти-модулей на одно сообщение — берём самый строгий (раздел 4.0 п.4)."""
    triggered = [v for v in verdicts if v.triggered]
    if not triggered:
        return Verdict(False)
    return max(triggered, key=lambda v: v.severity)
