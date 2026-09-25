"""Конфигурация: секреты из .env, пути vault, константы домена."""
from __future__ import annotations

import os
import logging
import re
from pathlib import Path

from dotenv import load_dotenv

from .core.mode import CaptureMode, parse_mode
from .core.models import Source

load_dotenv()

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Неконфиг: пустой токен, чужой user id, нет vault. Код выхода 4, без рестарта."""


# Числовые env парсятся на импорте, но падать там нельзя: исключение до main()
# давало бы код 1 (для плагина — краш с рестартами). Поэтому мусор не бросает —
# значение откатывается к дефолту, а проблема копится сюда и выстреливает
# ConfigError из validate(), где main() вернёт код 4 и запишет в лог.
_ENV_PARSE_PROBLEMS: list[str] = []


def _int_env(name: str, default: str) -> int:
    """Целое из env. Мусор — запись в _ENV_PARSE_PROBLEMS + дефолт (см. выше)."""
    raw = os.getenv(name, default).strip()
    try:
        return int(raw)
    except ValueError:
        _ENV_PARSE_PROBLEMS.append(f"{name}={raw!r} — нужно целое число")
        return int(default)


def _float_env(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        return float(raw)
    except ValueError:
        _ENV_PARSE_PROBLEMS.append(f"{name}={raw!r} — нужно число")
        return float(default)

# --- Секреты ---
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_ID: int = _int_env("ALLOWED_USER_ID", "0")

# Прокси для доступа к api.telegram.org (необязательно).
# Пусто → прямое соединение.
# Пример: socks5://127.0.0.1:1080  или  http://user:pass@host:port
TELEGRAM_PROXY: str | None = os.getenv("TELEGRAM_PROXY", "").strip() or None

# Прокси для зеркал X, CDN twimg и картинок TikTok (urllib).
# Только http:// — urllib SOCKS не умеет, socks-значение игнорируется с
# предупреждением (а yt-dlp — см. YTDLP_PROXY ниже — SOCKS понимает).
# Старое имя HTTP_PROXY читается как deprecated-фолбэк: оно же стандартное
# env-имя, влияющее на все библиотеки в процессе, — новым кодом не используется.
def _resolve_media_proxy() -> tuple[str | None, bool]:
    media = os.getenv("MEDIA_PROXY", "").strip() or None
    if media is not None:
        return media, False
    legacy = os.getenv("HTTP_PROXY", "").strip() or None
    return legacy, legacy is not None


MEDIA_PROXY, _MEDIA_PROXY_DEPRECATED = _resolve_media_proxy()

# yt-dlp ходит за видео TikTok сам и SOCKS понимает: отдаём ему media-прокси,
# иначе telegram-прокси (обычно тот же канал).
YTDLP_PROXY: str | None = MEDIA_PROXY or TELEGRAM_PROXY

# --- Пути vault (меняются одной строкой / через .env) ---
# Корень хранилища обязателен и задаётся только через .env: дефолта нет, чтобы
# бот не писал в чужой/случайный vault. Пустое или незаданное значение ловит
# validate() с понятным текстом (см. _VAULT_ROOT_RAW ниже).
_VAULT_ROOT_RAW: str = os.getenv("VAULT_ROOT", "").strip()
VAULT_ROOT: Path = Path(_VAULT_ROOT_RAW)
# Папка вложений Obsidian: сюда кладём медиа. `![[file]]` резолвится без полного пути.
MEDIA_DIR: Path = VAULT_ROOT / os.getenv("MEDIA_SUBDIR", r"Meta\Медиафайлы")
# Целевые папки заметок (подкаталоги ручного буфера «Входящие») — своя на источник.
NOTES_DIR: Path = VAULT_ROOT / os.getenv("NOTES_SUBDIR", r"1. Входящие\TikTok")
NOTES_DIR_TWITTER: Path = VAULT_ROOT / os.getenv("NOTES_SUBDIR_TWITTER", r"1. Входящие\Twitter")
NOTES_DIR_TELEGRAM: Path = VAULT_ROOT / os.getenv("NOTES_SUBDIR_TELEGRAM", r"1. Входящие\Telegram")


def notes_dir_for(source: Source) -> Path:
    """Папка заметок для источника. Медиа у всех общее — MEDIA_DIR."""
    if source is Source.TWITTER:
        return NOTES_DIR_TWITTER
    if source is Source.TELEGRAM:
        return NOTES_DIR_TELEGRAM
    return NOTES_DIR

# --- Логи ---
LOG_DIR: Path = Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
LOG_FILE: Path = LOG_DIR / "bot.log"

# --- Режим захвата ---
# Значение из .env — стартовое. Дальше режим переключается командой /mode и переживает
# перезапуск: выбор пишется в STATE_FILE, .env остаётся дефолтом «на чистую установку».
DEFAULT_MODE_RAW: str = os.getenv("CAPTURE_MODE", "both")
STATE_FILE: Path = Path(
    os.getenv("STATE_FILE", str(Path(__file__).resolve().parent.parent / "state.json"))
)
# Single-instance lock: лежит рядом со state.json, держится открытым весь рантайм.
# Источник правды — сам лок (msvcrt), а не PID внутри: ОС снимает лок при смерти
# процесса, поэтому зависших локов после краша/килла не бывает.
LOCK_FILE: Path = Path(
    os.getenv("LOCK_FILE", str(Path(__file__).resolve().parent.parent / "bot.lock"))
)

# --- Лимиты доставки в чат ---
# Telegram тянет медиа по URL сам, но с потолком: ≈5 МБ фото, 20 МБ прочее.
# Что больше — качаем сами и заливаем файлом (лимит бота на upload — 50 МБ).
URL_SEND_LIMIT_MB: int = _int_env("URL_SEND_LIMIT_MB", "20")
UPLOAD_LIMIT_MB: int = _int_env("UPLOAD_LIMIT_MB", "50")
# Заливка до 50 МБ на медленном канале в 60 с aiogram-умолчания не влезает: длинный
# пер-request таймаут только для аплоадов (поллинга не касается).
UPLOAD_TIMEOUT_S: int = _int_env("UPLOAD_TIMEOUT_S", "300")
# Скачивание форвардов через Bot API: файлы до 20 МБ тем же медленным каналом.
TG_DOWNLOAD_TIMEOUT_S: int = _int_env("TG_DOWNLOAD_TIMEOUT_S", "180")
MEDIA_GROUP_MAX: int = 10  # ограничение Telegram на размер альбома

# --- Пересланные Telegram-сообщения ---
# Сколько ждать остаток альбома / комментарий владельца перед обработкой.
TG_ALBUM_WAIT_S: float = _float_env("TG_ALBUM_WAIT_S", "1.5")
TG_COMMENT_WAIT_S: float = _float_env("TG_COMMENT_WAIT_S", "3")
# Сколько TikTok/X-ссылок из одного форварда обрабатывать (остальные остаются
# в тексте заметки, хвост уходит в предупреждение).
TG_MAX_LINKED_POSTS: int = _int_env("TG_MAX_LINKED_POSTS", "5")

# --- Домен ---
NOTE_NAME_MAX_LEN: int = 80  # лимит символов человекочитаемого имени заметки
CAPTION_WORDS_IN_NAME: int = 8  # сколько первых слов описания брать в имя

# --- OCR фото-каруселей ---
OCR_ENABLED: bool = os.getenv("OCR_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "").strip()
OCR_LANGS: str = os.getenv("OCR_LANGS", "rus+eng").strip() or "rus+eng"


def _mask_proxy(proxy: str | None) -> str:
    """Прокси для лога: схему/хост/порт видно, пароль — нет."""
    if not proxy:
        return "—"
    return re.sub(r"(?<=://)[^@/]+@", "***@", proxy)


def validate() -> None:
    """Проверка обязательных настроек при старте. Бросает ConfigError с внятным текстом."""
    if _MEDIA_PROXY_DEPRECATED:
        log.warning("HTTP_PROXY устарел — переименуй его в MEDIA_PROXY в .env")
    if MEDIA_PROXY and MEDIA_PROXY.lower().startswith("socks"):
        log.warning(
            "MEDIA_PROXY socks:// — urllib его не использует (только http/https); "
            "yt-dlp — да. Для urllib задай http-прокси."
        )
    log.info(
        "прокси: telegram=%s media=%s ytdlp=%s",
        _mask_proxy(TELEGRAM_PROXY),
        _mask_proxy(MEDIA_PROXY),
        _mask_proxy(YTDLP_PROXY),
    )
    problems: list[str] = list(_ENV_PARSE_PROBLEMS)
    if not TELEGRAM_TOKEN:
        problems.append("TELEGRAM_TOKEN не задан (см. .env.example)")
    if ALLOWED_USER_ID == 0:
        problems.append("ALLOWED_USER_ID не задан — бот не будет никого пускать")
    if not _VAULT_ROOT_RAW:
        problems.append(
            "VAULT_ROOT не задан — укажи корень хранилища Obsidian в .env"
        )
    elif not VAULT_ROOT.exists():
        problems.append(f"VAULT_ROOT не существует: {VAULT_ROOT}")
    if parse_mode(DEFAULT_MODE_RAW) is None:
        allowed = ", ".join(m.value for m in CaptureMode)
        problems.append(f"CAPTURE_MODE={DEFAULT_MODE_RAW!r} — допустимо: {allowed}")

    if OCR_ENABLED:
        try:
            import pytesseract

            if TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
            pytesseract.get_tesseract_version()
        except Exception as exc:  # noqa: BLE001 — OCR не должен блокировать запуск
            log.warning("Tesseract OCR недоступен; карусели сохранятся без транскрипта: %s", exc)

    if problems:
        raise ConfigError("Проблемы конфигурации:\n  - " + "\n  - ".join(problems))
