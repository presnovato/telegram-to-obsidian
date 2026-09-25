# TikTok / X → Obsidian

[Русский](#tiktok--x--obsidian) · English summary below.

> **English summary.** A local, single-user Telegram bot that turns links to public
> TikTok and X (Twitter) posts — and forwarded Telegram messages — into Markdown notes
> with media in your Obsidian vault. It runs on your own Windows PC, writes straight to
> the vault filesystem, and has no server or database. Three capture modes:
> `vault` (note only), `chat` (send back to the chat only) and `both` (default).
> Python 3.13; TikTok download uses **yt-dlp**, X uses public embed mirrors and needs no
> yt-dlp. Windows-only for now (the single-instance lock and the parent watchdog use
> Windows APIs).

Локальный Telegram-бот: присылаешь ссылку на публичный пост → бот скачивает медиа и
метаданные, кладёт файлы в папку вложений Obsidian и создаёт Markdown-заметку с
frontmatter и встроенным медиа. Можно также **переслать боту сообщение** из любого
чата или канала — текст, форматирование и вложения сохранятся заметкой.

Полностью локальный и однопользовательский: работает на твоём ПК, пишет прямо в
файловую систему vault. Без сервера, без базы данных. Внутренняя документация проекта
не публикуется.

## Что умеет

- **TikTok** — видео без watermark и фото-карусели. Заметки в `1. Входящие\TikTok`.
- **X (Twitter)** — видео, фото, карусели, GIF. Если у поста своих медиа нет, берётся
  медиа цитируемого поста. Ссылку можно слать как есть: подменять домен на
  `fixupx.com` не нужно (уже подменённые ссылки тоже принимаются).
  Заметки в `1. Входящие\Twitter`.
- **Пересланные сообщения Telegram** — текст с форматированием превращается в Markdown,
  фото/видео/документы скачиваются в общую папку медиа, ссылка на оригинал идёт во
  frontmatter. TikTok/X-ссылки внутри форварда дополнительно обрабатываются обычным
  пайплайном, а Telegram-заметка ссылается на созданные заметки разделом «Связанные посты».
  Заметки в `1. Входящие\Telegram`.

Медиа всех источников лежит в одной папке вложений (`Meta\Медиафайлы`); имена файлов X
префиксуются `x_`, чтобы их ID не столкнулись с aweme-ID TikTok.

### Режимы: куда уходит пост

| Режим | Что делает |
|---|---|
| `vault` | Только заметка и медиа в Obsidian, в чат ничего не шлётся |
| `chat` | Только в чат, vault не трогается и заметка не пишется |
| `both` | Заметка в vault + медиа в чат (по умолчанию) |

Переключение — `/mode [vault\|chat\|both]`; выбор переживает перезапуск (хранится в
`state.json` рядом с ботом). Стартовое значение задаётся `CAPTURE_MODE` в `.env` и
применяется, пока `state.json` не появился. Разово, не меняя режим, допиши в сообщение
со ссылкой `!chat`, `!vault` или `!both`:

```
!chat https://x.com/user/status/1234567890
```

## Требования

- **Windows 10/11.** Пока только Windows: single-instance-лок построен на `msvcrt`, а
  сторож родительского процесса — на `OpenProcess`/`WaitForSingleObject`. На Linux/macOS
  бот не запустится без переписывания этих двух мест.
- **Python 3.13** ([python.org](https://www.python.org/downloads/)); при установке отметь
  «Add python.exe to PATH».

## Установка

1. Создай бота у [@BotFather](https://t.me/BotFather): команда `/newbot`, придумай имя и
   username, скопируй токен вида `1234567890:ABC...`.
2. Узнай свой Telegram user id у [@userinfobot](https://t.me/userinfobot) — пришли ему
   любое сообщение, он ответит числом. Только этот id получит доступ к боту.
3. Скачай репозиторий и создай виртуальное окружение:

   ```powershell
   cd C:\path\to\tiktok-to-obsidian
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Скопируй пример конфига и заполни его:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Обязательные переменные: `TELEGRAM_TOKEN` (из шага 1), `ALLOWED_USER_ID` (из шага 2) и
   `VAULT_ROOT` — полный путь к корню твоего хранилища Obsidian, например
   `C:\path\to\vault`. Дефолта у `VAULT_ROOT` нет: без него бот не стартует.
   Полный список переменных с комментариями — в `.env.example`.

5. Запусти бота (окно должно оставаться открытым, пока бот нужен):

   ```powershell
   .venv\Scripts\python.exe -m tiktok_obsidian
   ```

   В логе появится `bot starting; vault=...`. Отправь боту `/start` и пришли ссылку.
   Ссылки, отправленные пока бот был выключен, обработаются при следующем запуске
   (long polling забирает накопленные апдейты; Telegram хранит их 24 часа).

### OCR каруселей (необязательно)

Если у фото-поста несколько картинок (карусель), бот может распознать текст Tesseract
и положить его в раздел транскрипта. Одиночное фото и видео не трогаются.

Установи Tesseract по Windows-инсталлятору
[UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki); при установке отметь
языковые пакеты `rus` и `eng`. Если `tesseract.exe` не попал в `PATH`, укажи полный путь
в `.env`:

```dotenv
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_LANGS=rus+eng
```

OCR включён по умолчанию (`OCR_ENABLED=true`). Отсутствующий Tesseract не мешает
запуску: бот предупредит, а заметки сохранятся без транскрипта. Чтобы совсем отключить
распознавание, задай `OCR_ENABLED=false`.

## yt-dlp

**yt-dlp — это тот самый компонент, которым скачиваются посты TikTok** (и видео, и
фото-карусели). X в нём не нуждается: там бот ходит в публичные зеркала эмбедов
(`api.fxtwitter.com`, `api.vxtwitter.com`), которым не нужны ни аккаунт, ни cookies.

TikTok меняет свою защиту от ботов регулярно, и ломается это **в первую очередь**:
после очередного изменения сайта старые версии yt-dlp начинают отдавать ошибки вроде
«No video formats found» или «Unable to extract». Лечится обновлением экстрактора:

```
/update_ytdlp
```

Бот сам выполнит `python -m pip install -U yt-dlp[curl-cffi]`. **После обновления нужно
перезапустить бота** (из Obsidian: статус-бар `t2o` → Restart bot; вручную — закрыть
окно и запустить снова), потому что уже загруженный в память старый модуль продолжает
работать.

Отдельная хрупкость: фото-карусели TikTok бот тянет через **приватный метод
экстрактора yt-dlp** (web-данные `imagePost.images[]`). Этот метод не является публичным
API и при обновлении yt-dlp может измениться; тогда фото-посты ломаются, даже если
видео продолжают работать. Есть фолбэк на сторонний `tikwm.com`, но он тоже периодически
недоступен (403). Если сломались именно фото — это ожидаемое место поломки.

`curl-cffi` из `requirements.txt` нужен, потому что yt-dlp использует его для
browser-impersonation при антибот-проверке TikTok.

## Команды

| Команда | Что делает |
|---|---|
| `/start` | Кратко объясняет, что делать |
| `/mode [vault\|chat\|both]` | Показать или сменить режим захвата (переживает перезапуск) |
| `/status` | Режим, версия yt-dlp, пути vault/заметок/медиа |
| `/update_ytdlp` | Обновить yt-dlp через pip (затем перезапустить бота) |
| `/errors` | Последние ERROR/WARNING из лога |

Комментарий, добавленный к сообщению со ссылкой, попадает в раздел «Впечатления» заметки.

## Запуск вместе с Obsidian (необязательный плагин)

В папке `obsidian-launcher/` лежит плагин `t2o-launcher` — супервизор процессов: стартует
бота при открытии хранилища и гасит при закрытии, показывает статус в статус-баре,
перезапускает упавшего бота. С Telegram и заметками он не работает.

Нужен Node.js LTS. Сборка:

```powershell
cd obsidian-launcher
npm ci
npm run build
```

Затем скопируй `main.js` и `manifest.json` в папку плагина внутри своего vault:

```powershell
$pluginDir = "C:\path\to\vault\.obsidian\plugins\t2o-launcher"
New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
Copy-Item main.js, manifest.json -Destination $pluginDir
```

Включи плагин в Obsidian: Settings → Community plugins → включи `TikTok → Obsidian bot`.
В его настройках укажи:

- **Python executable** — полный путь к `.venv\Scripts\python.exe` из репозитория бота
  (именно `python.exe`, не `pythonw.exe`: под `pythonw` у бота нет stdout и ломается лог);
- **Bot working directory** — корень репозитория бота.

Оба поля по умолчанию пустые, и плагин не запустит бота, пока они не заполнены. Остальные
настройки — аргументы, автозапуск, путь к логу.

Установка без ручного копирования: `npm run deploy` использует env-переменную
`T2O_VAULT_DIR` или личный файл `deploy.local.json` (`{"vaultDir": "..."}`), который в git
не попадает.

Статусы: `t2o: запуск…` / `работает` / `остановлен` / `запущен вне Obsidian` / `ошибка`.
Клик — меню Start / Stop / Restart / Open bot log. Упавший бот рестартится сам
(5 с → 30 с → 120 с; после 3 падений за 10 минут — `ошибка` без рестарта). Второй
экземпляр невозможен: бот берёт эксклюзивный лок `bot.lock` (второй выход — код 3).

## Ограничения

- **Форварды Telegram — потолок Bot API 20 МБ** на файл. Что больше — не скачивается, а
  попадает в предупреждения заметки. Голосовые/аудио/кружки/стикеры/опросы/геопозиции
  тоже не сохраняются.
- **Бот работает, только пока запущен.** Это long polling на твоём ПК, не вебхук и не
  облако; закрыл процесс — новые сообщения обрабатываются при следующем запуске (пока
  Telegram хранит апдейты, 24 часа).
- **Только Windows** (см. «Требования»).
- Один пользователь на бота: доступ по `ALLOWED_USER_ID`, другие игнорируются.

## Разработка

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

Тесты покрывают чистые функции `core/` (разбор URL, имена файлов, шаблон заметки, дедуп по
ФС) — без сети и без Telegram. Плагин: `cd obsidian-launcher`, затем `npm ci`,
`npm test`, `npx tsc --noEmit`.

## Зависимости

Все прямые зависимости — под разрешительными лицензиями, совместимыми с MIT.

Бот (`requirements.txt`):

- [aiogram](https://github.com/aiogram/aiogram) — MIT
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Unlicense (public domain); extra
  `curl-cffi` — MIT
- [python-dotenv](https://github.com/theskumar/python-dotenv) — BSD-3-Clause
- [aiohttp-socks](https://github.com/romis2012/aiohttp-socks) — Apache-2.0
- [pytesseract](https://github.com/madmaze/pytesseract) — Apache-2.0
- [Pillow](https://python-pillow.org/) — MIT-CMU

Разработка (`requirements-dev.txt`): pytest — MIT, pytest-asyncio — Apache-2.0.

Плагин (`obsidian-launcher/package.json`): esbuild — MIT, TypeScript — Apache-2.0,
`obsidian` (только типы) — MIT, `@types/node` — MIT.

## Лицензия

MIT — см. [LICENSE](LICENSE). © 2026 Roman Zhuzhin.
