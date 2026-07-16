# YouScriber

Desktop GUI + CLI utility for extracting YouTube subtitles (from videos, playlists, or channels) and preparing the text for LLMs. It cleans VTT/SRT files by removing timecodes and tags, deduplicates "growing" auto-generated subtitles, formats them with metadata, and merges them into chunks optimized for LLM context windows (~50k/~200k characters). 

It does **not** download video or audio, and `ffmpeg` is **not** required.

![YouScriber GUI](TODO) <!-- Placeholder for GUI screenshot -->

## Architecture & Features
- **Clean Architecture:** All business logic is encapsulated in `core.py`. The GUI (CustomTkinter) and CLI are thin wrappers. Pure functions are tested offline (22 pytest tests, no network required). 
- **Anti-Rate-Limit Measures:** Uses a single batch request for subtitles instead of per-language requests to prevent HTTP 429 errors. Includes random 1-2s pauses, retries with exponential backoff, and impersonates `player_client=android_vr` to bypass current bot protections.
- **Graceful Degradation:** If a video lacks subtitles, it outputs an explicit placeholder rather than an empty file. A single failed video will not interrupt a batch process.
- **Local Mode:** Can process already downloaded `.vtt`, `.srt`, or `.txt` files locally without network access.
- **Standalone App:** Can be built as a standalone `.app` using PyInstaller.

## Maintenance (One Command)
If YouTube updates its anti-bot protections and extraction stops working, simply update the core dependency:
```bash
pip install -U yt-dlp
```

## Usage

| Method | Best For | How to Run |
|---|---|---|
| **GUI** | Everyday use, easy configuration | `python gui.py` |
| **CLI** | Automation, batch processing from files | `python grab_subs.py URL1 URL2` |
| **Standalone App** | Running without Python installed | Build: `pyinstaller YouScriber.spec` |

### CLI Example
```bash
# Process multiple URLs or pass a file with URLs:
python grab_subs.py "https://youtube.com/watch?v=..."
python grab_subs.py --urls-file urls.txt
```

## Known Limitations
- **No e2e testing on real videos:** End-to-end tests require network access and are currently executed manually (see `FINALIZE.md` checklist).
- **No CI pipeline.**
- **Cookies are disabled by default.**

---
*Built with AI-assisted development.*

---

# YouScriber (RU)

Desktop GUI + CLI утилита для извлечения субтитров YouTube (видео, плейлисты, каналы) и подготовки текста под LLM. Она очищает VTT/SRT от таймкодов и тегов, дедуплицирует «нарастающие» авто-субтитры, форматирует текст с метаданными и сливает его в чанки под контекстные окна (~50k/~200k символов). 

Утилита **не качает** видео или аудио, `ffmpeg` **не нужен**.

![YouScriber GUI](TODO) <!-- Placeholder for GUI screenshot -->

## Архитектура и фичи
- **Чистая архитектура:** Вся бизнес-логика находится в `core.py`. GUI (CustomTkinter) и CLI — тонкие обёртки. Чистые функции тестируются офлайн (22 pytest-теста без сети). Есть `Makefile`.
- **Anti-rate-limit решения:** Один пакетный запрос субтитров вместо запроса-на-язык (против HTTP 429), случайные паузы 1–2 с, ретраи с backoff, `player_client=android_vr` против текущих защит.
- **Понятная деградация:** Нет субтитров → явный плейсхолдер, а не пустой файл. Одно упавшее видео не рвёт пакет.
- **Локальный режим:** Обработка уже скачанных `.vtt`, `.srt`, `.txt` без сети.
- **Standalone .app:** Собирается через PyInstaller (проверено на macOS).

## Обслуживание одной командой
Если YouTube меняет защиту и загрузка ломается, достаточно обновить основную зависимость:
```bash
pip install -U yt-dlp
```

## Использование

| Интерфейс | Для чего | Запуск |
|---|---|---|
| **GUI** | Повседневное использование | `python gui.py` |
| **CLI** | Автоматизация, пакетная обработка | `python grab_subs.py URL1 URL2` |
| **Standalone App** | Работа без Python | Сборка: `pyinstaller YouScriber.spec` |

### Пример CLI
```bash
python grab_subs.py "https://youtube.com/watch?v=..."
python grab_subs.py --urls-file urls.txt
```

## Известные ограничения
- **Нет e2e-тестов на реальном видео:** Требуют сети, сейчас проверяются по ручному чек-листу (см. `FINALIZE.md`).
- **Нет CI.**
- **Cookies выключены по умолчанию.**

---
*Создано при поддержке ИИ (AI-assisted development).*
