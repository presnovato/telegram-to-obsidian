"""Обёртка над источниками: probe (метаданные) и download (медиа).

yt-dlp — основной загрузчик видео. Если он не отдаёт форматы, проверяем фото-путь,
а для видео используем TikWM (hdplay → play → wmplay). Фото/карусели загружаются
из web-данных TikTok с TikWM как фолбэком.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as _YtDownloadError

from .. import config
from ..core.models import PostMeta
from ..core.text import strip_ansi
from ..core.urls import normalize_for_ytdlp, video_id_from_url
from .net import open_url

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TIKWM_API = "https://www.tikwm.com/api/"
# Короткие формы ссылки: /t/CODE, vm./vt. — за ними может стоять и видео, и фото-пост.
_SHORT_URL = re.compile(r"^https?://(?:vm\.|vt\.)?(?:www\.|m\.)?tiktok\.com/(?:t/)?[\w.-]+/?$", re.I)
_ID_FROM_ERROR = re.compile(r"\b\d{15,25}\b")
_CTYPE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class _QuietLogger:
    """Глушит вывод yt-dlp там, где сбой ожидаем и обрабатывается фолбэком."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class DownloadError(Exception):
    """Понятная доменная ошибка скачивания (обёртка над сбоями yt-dlp)."""


def _with_proxy(opts: dict) -> dict:
    """Прокси для yt-dlp: SOCKS он понимает нативно, в отличие от urllib."""
    if config.YTDLP_PROXY:
        opts["proxy"] = config.YTDLP_PROXY
    return opts


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def update_ytdlp(timeout: int = 300) -> tuple[bool, str]:
    """Обновляет yt-dlp через pip (по явной команде заказчика). Возвращает (успех, хвост вывода)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[curl-cffi]"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip завис и был остановлен (лимит {timeout} с)"
    output = (proc.stdout + proc.stderr).strip()
    tail = "\n".join(output.splitlines()[-8:])
    return proc.returncode == 0, tail


def installed_ytdlp_version(timeout: int = 60) -> str | None:
    """Версия из свежего интерпретатора. Импортированный модуль до рестарта
    старый, поэтому ytdlp_version() после обновления врёт — спрашиваем заново."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.metadata as m; print(m.version('yt-dlp'))",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = proc.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


_resolved_cache: dict[str, str] = {}


def _resolve_short_url(url: str) -> str:
    """Разворачивает короткую ссылку (/t/, vm., vt.) в полную. При сбое возвращает исходную.

    Без этого фото-пост за короткой ссылкой неизлечим: yt-dlp сам идёт по редиректу,
    получает путь /photo/ и падает с «Unsupported URL» — переписать ссылку внутри него
    уже некому. Разворачиваем сами, чтобы `normalize_for_ytdlp` увидела /photo/.
    """
    if not _SHORT_URL.match(url):
        return url
    if url in _resolved_cache:
        return _resolved_cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
    try:
        final = _head_final(req)
    except OSError as e:  # одна попытка не удалась — повторяем один раз
        log.info("повтор разворота короткой ссылки %s: %s", url, e)
        try:
            final = _head_final(req)
        except OSError as e2:  # сеть/редирект не удались — пусть попробует сам yt-dlp
            log.warning("не удалось развернуть короткую ссылку %s: %s", url, e2)
            return url
    _resolved_cache[url] = final
    if len(_resolved_cache) > 32:
        _resolved_cache.pop(next(iter(_resolved_cache)))
    return final


def _head_final(req: urllib.request.Request) -> str:
    """Финал HEAD-редиректа (единичная попытка, бросает OSError)."""
    with open_url(req, 20) as resp:
        return resp.url or req.full_url


def _prepare_url(url: str) -> str:
    """Ссылка в форме, которую понимает экстрактор yt-dlp: развёрнутая и с /video/."""
    return normalize_for_ytdlp(_resolve_short_url(url))


def _author(info: dict) -> str:
    for key in ("creator", "uploader", "uploader_id", "channel", "artist"):
        val = info.get(key)
        if val:
            return str(val)
    return "unknown"


def _caption(info: dict) -> str:
    return str(info.get("description") or info.get("title") or "").strip()


def _upload_date(info: dict) -> str:
    raw = info.get("upload_date")  # YYYYMMDD
    if raw and len(str(raw)) == 8:
        s = str(raw)
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _tikwm_fetch(url: str) -> dict:
    """Один запрос к tikwm.com: метаданные + images[]/play. Разворачивает короткие ссылки.

    Бросает DownloadError при ошибке API (code != 0) или сети.
    """
    api = f"{_TIKWM_API}?url={quote(url, safe='')}&hd=1"
    req = urllib.request.Request(api, headers={"User-Agent": _UA})
    try:
        raw = open_url(req, 25).read().decode("utf-8", "replace")
    except OSError as e:
        raise DownloadError(f"tikwm недоступен: {e}") from e
    try:
        resp = json.loads(raw)
    except ValueError as e:
        raise DownloadError(f"tikwm: не JSON: {e}") from e
    if resp.get("code") != 0:
        raise DownloadError(f"tikwm: {resp.get('msg') or 'ошибка API'}")
    return resp.get("data") or {}


def _tikwm_meta(url: str) -> PostMeta:
    """PostMeta из данных tikwm (фолбэк probe, когда yt-dlp не осилил ссылку — обычно фото)."""
    d = _tikwm_fetch(url)
    author = d.get("author") or {}
    images = [str(u) for u in (d.get("images") or [])]
    ts = d.get("create_time")
    raw_duration = d.get("duration")
    try:
        duration = int(raw_duration) if raw_duration not in (None, "") else None
    except (TypeError, ValueError):
        duration = None
    upload_date = ""
    if ts:
        upload_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    return PostMeta(
        video_id=str(d.get("id") or ""),
        author=str(author.get("nickname") or author.get("unique_id") or "unknown"),
        caption=str(d.get("title") or "").strip(),
        url=url,
        upload_date=upload_date,
        duration=duration,
        is_carousel=len(images) > 1,
        is_photo=bool(images),
    )


def _has_video_format(info: dict) -> bool:
    formats = info.get("formats") or []
    return any(f.get("vcodec") not in (None, "none") for f in formats)


def _looks_like_carousel(info: dict) -> bool:
    formats = info.get("formats") or []
    if _has_video_format(info):
        return False
    # Фото-пост (photomode): обложка отдаётся как photomode-изображение.
    thumbs = info.get("thumbnails") or []
    if any("photomode" in (t.get("url") or "") for t in thumbs):
        return True
    if info.get("entries"):
        return True
    return not formats  # пусто → скорее всего слайд-шоу


def _base_meta(
    info: dict,
    url: str,
    *,
    is_photo: bool | None = None,
    is_carousel: bool | None = None,
) -> PostMeta:
    detected_carousel = _looks_like_carousel(info)
    return PostMeta(
        video_id=str(info.get("id") or ""),
        author=_author(info),
        caption=_caption(info),
        url=info.get("webpage_url") or url,
        upload_date=_upload_date(info),
        duration=info.get("duration"),
        is_carousel=detected_carousel if is_carousel is None else is_carousel,
        is_photo=detected_carousel if is_photo is None else is_photo,
    )


def probe_post(url: str) -> PostMeta:
    """Метаданные поста без скачивания (для дедупа и предупреждений). Бросает DownloadError.

    yt-dlp — основной источник (видео и прямые /photo/ ссылки). Если он не осилил ссылку
    (частый случай: короткая ссылка на фото-пост → «Unsupported URL»), фолбэк на tikwm.
    """
    # Тихий логгер: провал probe ожидаемо ловится фолбэком на tikwm, незачем спамить ERROR.
    opts = _with_proxy(
        {
            "quiet": True,
            "no_warnings": True,
            "color": "no_color",
            "retries": 3,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "skip_download": True,
            "noplaylist": False,
            "logger": _QuietLogger(),
        }
    )
    resolved_url = _resolve_short_url(url)
    prepared_url = normalize_for_ytdlp(resolved_url)
    photo_hint = "/photo/" in urlparse(resolved_url).path.lower()
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(prepared_url, download=False)
    except _YtDownloadError as yt_err:
        error_text = str(yt_err)
        error_id = _ID_FROM_ERROR.search(error_text)
        video_id = (
            video_id_from_url(resolved_url)
            or video_id_from_url(prepared_url)
            or (error_id.group(0) if error_id else "")
        )
        if (
            photo_hint
            or "comfortable for some audiences" in error_text
            or "no video formats found" in error_text.lower()
        ):
            extracted = _extract_ytdlp_web(url, video_id)
            if extracted and extracted[1]:
                web_info, image_urls = extracted
                return _base_meta(
                    web_info,
                    url,
                    is_photo=True,
                    is_carousel=len(image_urls) > 1,
                )
        try:
            return _tikwm_meta(url)
        except DownloadError:
            raise DownloadError(strip_ansi(str(yt_err))) from yt_err
    if info is None:
        raise DownloadError("yt-dlp не вернул метаданные")
    if info.get("_type") == "playlist" and info.get("entries"):
        # Один пост может прийти как плейлист-обёртка — берём первую запись как базу.
        first = next((e for e in info["entries"] if e), None)
        if first:
            info = {**first, "id": info.get("id") or first.get("id")}

    base = _base_meta(info, url, is_photo=photo_hint)
    if not base.is_photo and not _has_video_format(info):
        extracted = _extract_ytdlp_web(url, base.video_id)
        if extracted and extracted[1]:
            web_info, image_urls = extracted
            return _base_meta(
                web_info or info,
                url,
                is_photo=True,
                is_carousel=len(image_urls) > 1,
            )
        # Пустой web-путь сам по себе не отличает видео без форматов от фото-поста.
        # TikWM вернёт тип медиа и даст сохранить метаданные видео, если images[] пуст.
        try:
            return _tikwm_meta(url)
        except DownloadError:
            raise DownloadError(
                "похоже на фото-пост, но картинки получить не удалось — "
                "попробуй /update_ytdlp"
            )
    return base


def _detect_watermark(info: dict) -> bool:
    """Best-effort: помечаем watermark, если выбранный формат явно про него говорит."""
    downloads = info.get("requested_downloads") or []
    for d in downloads:
        marker = f"{d.get('format_id', '')} {d.get('format_note', '')}".lower()
        if "watermark" in marker and "no watermark" not in marker and "nowm" not in marker:
            return True
    return False


def _image_urls_from_web_data(web_data: dict) -> list[str]:
    images = ((web_data or {}).get("imagePost") or {}).get("images") or []
    urls: list[str] = []
    for img in images:
        url_list = (img.get("imageURL") or img.get("imageUrl") or {}).get("urlList") or []
        if url_list:
            urls.append(url_list[0])
    return urls


def _extract_ytdlp_web(url: str, video_id: str) -> tuple[dict, list[str]] | None:
    """Извлекает метаданные и картинки через web-данные TikTok без fatal login gate."""
    if not video_id:
        return None
    prepared_url = _prepare_url(url)
    if not re.search(r"/video/\d+(?:[/?#]|$)", prepared_url, re.I):
        prepared_url = f"https://www.tiktok.com/@/video/{video_id}"
    try:
        with YoutubeDL(
            _with_proxy(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "color": "no_color",
                    "retries": 3,
                    "extractor_retries": 3,
                    "socket_timeout": 30,
                    "logger": _QuietLogger(),
                }
            )
        ) as ydl:
            ie = ydl.get_info_extractor("TikTok")
            ie.initialize()
            web_data, _status = ie._extract_web_data_and_status(
                prepared_url, video_id, fatal=False
            )
            if not web_data:
                return None
            image_urls = _image_urls_from_web_data(web_data)
            try:
                info = ie._parse_aweme_video_web(
                    web_data, prepared_url, video_id, extract_flat=True
                )
            except Exception as e:  # noqa: BLE001 — приватный API, картинки всё ещё пригодны
                log.warning("yt-dlp web metadata parse failed: %s", e)
                info = {}
            return info, image_urls
    except Exception as e:  # noqa: BLE001 — приватный API yt-dlp, мягкая деградация
        log.warning("yt-dlp web-extract фолбэк не сработал: %s", e)
        return None


def _images_from_ytdlp_web(url: str, video_id: str) -> list[str]:
    """Фолбэк: URL слайдов через web-данные yt-dlp (imagePost.images[]), если tikwm недоступен.

    Приватный метод экстрактора — при обновлении yt-dlp может измениться. Мягкая деградация.
    """
    extracted = _extract_ytdlp_web(url, video_id)
    return extracted[1] if extracted else []


def _carousel_image_urls(url: str, video_id: str) -> list[str]:
    """URL всех слайдов фото-поста. Основной источник — web-данные yt-dlp, фолбэк — tikwm.

    Приоритет обратный историческому: tikwm стал отдавать 403 на весь хост (Cloudflare/гео),
    так что «публичным API, который всегда есть» он больше не является. Держим его вторым —
    на случай, если однажды сломается приватный web-путь экстрактора.
    """
    images = _images_from_ytdlp_web(url, video_id)
    if images:
        return images
    try:
        return [str(u) for u in (_tikwm_fetch(url).get("images") or [])]
    except DownloadError as e:
        log.warning("tikwm не отдал картинки: %s", e)
        return []


def _url_ext(u: str, default: str = "jpg") -> str:
    """Расширение файла из URL (до query)."""
    tail = urlparse(u).path.rsplit("/", 1)[-1]
    if "." in tail:
        ext = tail.rsplit(".", 1)[-1].lower()
        if 1 <= len(ext) <= 5 and ext.isalnum():
            return ext
    return default


def _download_images(urls: list[str], media_dir: Path, video_id: str) -> list[str]:
    """Качает картинки карусели в media_dir под именами {id}_NN.ext (атомарно, на том же диске).

    Расширение берём из Content-Type ответа (URL часто без расширения), fallback — из URL.
    Все слайды сперва копятся во временной папке и переносятся в media_dir одним блоком
    только после того, как скачаны все — иначе сбой на середине карусели (например,
    таймаут SSL-хендшейка) оставит в media_dir файлы уже скачанных слайдов, и дедуп по ФС
    ложно посчитает недокачанный пост сохранённым.
    """
    tmp_names: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ttdl_", dir=media_dir.parent) as tmp:
        tmp_dir = Path(tmp)
        for i, u in enumerate(urls, start=1):
            req = urllib.request.Request(
                u, headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"}
            )
            try:
                with open_url(req, 30) as resp:
                    data = resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            except OSError as e:  # HTTPError, таймаут, SSL — тоже OSError
                raise DownloadError(f"слайд {i}/{len(urls)}: {e}") from e
            ext = _CTYPE_EXT.get(ctype) or _url_ext(u)
            name = f"{video_id}_{i:02d}.{ext}"
            (tmp_dir / name).write_bytes(data)
            tmp_names.append(name)

        names: list[str] = []
        for name in tmp_names:
            dest = media_dir / name
            (tmp_dir / name).replace(dest)  # тот же диск → атомарно
            names.append(dest.name)
    return names


def _finish_photo(meta: PostMeta, media_files: list[str], image_urls: list[str]) -> PostMeta:
    """PostMeta фото-ветки: картинки уже скачаны."""
    return PostMeta(
        video_id=meta.video_id,
        author=meta.author,
        caption=meta.caption,
        url=meta.url,
        upload_date=meta.upload_date,
        duration=meta.duration,
        is_carousel=meta.is_carousel or len(image_urls) > 1,
        is_photo=True,
        watermarked=False,
        media_files=media_files,
    )


def _download_tikwm_video(
    data: dict,
    tmp_dir: Path,
    video_id: str,
    *,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """Потоково скачивает первый подходящий TikWM вариант в папку на диске vault.

    max_bytes задаётся только для chat-пути. Vault-загрузка не ограничивается
    UPLOAD_LIMIT_MB.
    """
    failures: list[str] = []
    attempted = False
    for field in ("hdplay", "play", "wmplay"):
        url = str(data.get(field) or "").strip()
        if not url:
            continue
        attempted = True
        dest = tmp_dir / f"{video_id}.mp4"
        dest.unlink(missing_ok=True)
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": _UA, "Referer": "https://www.tiktok.com/"},
            )
            with open_url(req, 60) as response:
                declared = int(response.headers.get("content-length") or 0)
                if max_bytes is not None and declared > max_bytes:
                    raise OSError(
                        f"размер {declared} байт превышает лимит чата {max_bytes} байт"
                    )
                written = 0
                with dest.open("wb") as stream:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise OSError(
                                f"размер превышает лимит чата {max_bytes} байт"
                            )
                        stream.write(chunk)
            if not dest.is_file() or dest.stat().st_size == 0:
                raise OSError("TikWM вернул пустой видеофайл")
            return dest.name, field
        except Exception as exc:  # noqa: BLE001 — HTTPError, таймаут, лимит или запись
            dest.unlink(missing_ok=True)
            failures.append(f"{field}: {exc}")

    if not attempted:
        failures.append("TikWM не вернул hdplay, play или wmplay")
    raise DownloadError("; ".join(failures))


def download_post(
    url: str,
    media_dir: Path,
    meta: PostMeta,
    *,
    max_bytes: int | None = None,
) -> PostMeta:
    """Качает медиа в media_dir под именами {video_id}[_NN].ext. Возвращает обновлённый PostMeta.

    Фото-карусель → все слайды {id}_01.jpeg…; видео → {id}.ext. Аудио слайд-шоу не сохраняем.
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    video_id = meta.video_id

    if meta.is_photo or meta.is_carousel:
        image_urls = _carousel_image_urls(url, video_id)
        if not image_urls:
            raise DownloadError(
                "это фото-карусель, но не удалось получить изображения "
                "(возможно, экстрактор устарел — попробуй /update_ytdlp)"
            )
        return _finish_photo(meta, _download_images(image_urls, media_dir, video_id), image_urls)

    # Видео: временная папка ОБЯЗАТЕЛЬНО на том же диске, что и vault — иначе
    # финальный os.replace падает с WinError 17 (перенос C:→D:). Здесь же
    # переименование атомарно — Obsidian/Git не увидят полуфайл.
    with tempfile.TemporaryDirectory(prefix="ttdl_", dir=media_dir.parent) as tmp:
        tmp_dir = Path(tmp)
        opts = _with_proxy(
            {
                "quiet": True,
                "no_warnings": True,
                "color": "no_color",
                "retries": 3,
                "extractor_retries": 3,
                "socket_timeout": 30,
                "noplaylist": False,
                "outtmpl": str(tmp_dir / "%(id)s_%(autonumber)s.%(ext)s"),
                "writeinfojson": False,
            }
        )
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(_prepare_url(url), download=True)
        except _YtDownloadError as e:
            yt_error = strip_ansi(str(e))
            if "no video formats found" not in yt_error.lower():
                raise DownloadError(yt_error) from e

            # Сначала пробуем извлечь фото из TikTok web-данных.
            image_urls = _images_from_ytdlp_web(url, video_id)
            if image_urls:
                log.info("видео-ветка пуста, сохраняю фото из web-данных: %s", url)
                try:
                    return _finish_photo(
                        meta, _download_images(image_urls, media_dir, video_id), image_urls
                    )
                except Exception as photo_error:  # noqa: BLE001
                    raise DownloadError(
                        f"yt-dlp failed: {yt_error}; TikTok photo fallback failed: {photo_error}"
                    ) from e

            # Пустой photo-path не означает, что пост — фото: запрашиваем видео-варианты TikWM.
            try:
                tikwm_data = _tikwm_fetch(url)
            except Exception as tikwm_error:  # noqa: BLE001 — API/network failure
                raise DownloadError(
                    f"yt-dlp failed: {yt_error}; TikWM metadata lookup failed: {tikwm_error}"
                ) from e

            image_urls = [str(item) for item in (tikwm_data.get("images") or [])]
            if image_urls:
                log.info("фото через tikwm после пустого yt-dlp web: %s", url)
                try:
                    return _finish_photo(
                        meta, _download_images(image_urls, media_dir, video_id), image_urls
                    )
                except Exception as photo_error:  # noqa: BLE001
                    raise DownloadError(
                        f"yt-dlp failed: {yt_error}; TikWM photo fallback failed: {photo_error}"
                    ) from e

            try:
                filename, path = _download_tikwm_video(
                    tikwm_data, tmp_dir, video_id, max_bytes=max_bytes
                )
            except DownloadError as tikwm_error:
                raise DownloadError(
                    f"yt-dlp failed: {yt_error}; TikWM video fallback failed: {tikwm_error}"
                ) from e

            log.info("видео через tikwm (%s): id=%s", path, video_id)
            try:
                media_files = _place_files([tmp_dir / filename], media_dir, video_id)
            except DownloadError as save_error:
                raise DownloadError(
                    f"yt-dlp failed: {yt_error}; TikWM video save failed: {save_error}"
                ) from e
            return PostMeta(
                video_id=meta.video_id,
                author=meta.author,
                caption=meta.caption,
                url=meta.url,
                upload_date=meta.upload_date,
                duration=meta.duration,
                is_carousel=False,
                is_photo=False,
                watermarked=(path == "wmplay"),
                media_files=media_files,
            )

        watermarked = _detect_watermark(info or {})
        produced = sorted(p for p in tmp_dir.iterdir() if p.is_file())
        media_files = _place_files(produced, media_dir, video_id)

    if not media_files:
        raise DownloadError("yt-dlp завершился, но файлы не появились")

    return PostMeta(
        video_id=meta.video_id,
        author=meta.author,
        caption=meta.caption,
        url=meta.url,
        upload_date=meta.upload_date,
        duration=meta.duration,
        is_carousel=False,
        is_photo=False,
        watermarked=watermarked,
        media_files=media_files,
    )


def _place_files(produced: list[Path], media_dir: Path, video_id: str) -> list[str]:
    """Переименовывает скачанные файлы в схему {video_id}[_NN].ext. Возвращает итоговые имена."""
    images = [p for p in produced if p.suffix.lower() in IMAGE_EXTS]
    videos = [p for p in produced if p.suffix.lower() not in IMAGE_EXTS]

    names: list[str] = []
    try:
        if videos and not images:
            # Обычное видео — берём самый крупный файл на случай мусорных остатков.
            main = max(videos, key=lambda p: p.stat().st_size)
            dest = media_dir / f"{video_id}{main.suffix.lower()}"
            main.replace(dest)
            names.append(dest.name)
        else:
            # Карусель (или картинки + аудио): встраиваем только изображения, по порядку.
            for i, img in enumerate(images, start=1):
                dest = media_dir / f"{video_id}_{i:02d}{img.suffix.lower()}"
                img.replace(dest)
                names.append(dest.name)
    except OSError as e:
        raise DownloadError(f"не удалось сохранить файлы: {e}") from e
    return names
