"""
Batch subtitle grabber for YouScriber.

Downloads original Russian auto-captions (ru-orig, fallback ru) for a list of
YouTube videos and writes cleaned, timecode-free transcripts as .txt files
ready for LLM / book processing.

No PO-token workaround needed: current yt-dlp + android_vr client works fine.
The real fix vs. the old config is using Russian subtitle languages instead of
the hardcoded English ones.
"""

import pathlib
import shutil
import yt_dlp

import core

URLS = [
    "https://www.youtube.com/watch?v=dBMBbL-z-gc",   # Первичное вскрытие-признание
    "https://www.youtube.com/watch?v=CpvepxkIdcU",   # Попытка нарцисса выйти на свет
    "https://www.youtube.com/watch?v=mm4__KEGNEo",   # Диагностическая сессия
    "https://www.youtube.com/watch?v=mxyUalwy3ts",   # Опыт телесной терапии
    "https://www.youtube.com/watch?v=8UdaBdFPvrw",   # О Перчатке Бесконечности
    "https://www.youtube.com/watch?v=vsX5LmbDeHc",   # Созависимый и нарцисс
    "https://www.youtube.com/watch?v=Ih1o1SRqa3k",   # Перчатка Бесконечности - теория
    "https://www.youtube.com/watch?v=NTR5hOLskPA",   # Ананкастное расстройство
    "https://www.youtube.com/watch?v=CparFizLgew",   # Диссоциальное расстройство
    "https://www.youtube.com/watch?v=ryaL_ujMsBA",   # Шизоидное расстройство
    "https://www.youtube.com/watch?v=U3G7I7ZJvZc",   # Неслышимость
    "https://www.youtube.com/watch?v=rEMgnNRxLpY",   # Интервью с Юлией Каспржак
    "https://www.youtube.com/watch?v=AvTJYuVvntg",   # Интегральный подход (04.11.23)
    "https://www.youtube.com/watch?v=u4NY0SZ2Alg",   # Интегральный подход (29.05.24)
]

OUT_DIR = pathlib.Path(__file__).parent / "output" / "transcripts"
TMP_DIR = pathlib.Path(__file__).parent / "output" / "_raw_vtt"


def safe_name(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in " -_()").strip()[:150]


def grab(url: str) -> tuple[str, str | None]:
    """Download best Russian subtitle for one video. Returns (title, txt_path)."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": ["ru-orig", "ru", "en"],
        "subtitlesformat": "vtt",
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        "outtmpl": str(TMP_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not info:
        return url, None

    title = info.get("title", info.get("id", "video"))
    vid = info.get("id", "")

    # Pick the best available .vtt for this id, preferring ru-orig > ru > en
    candidates = list(TMP_DIR.glob(f"{vid}*.vtt"))
    sub = None
    for tag in (".ru-orig.", ".ru.", ".en.", "."):
        for f in candidates:
            if tag in f.name:
                sub = f
                break
        if sub:
            break
    if not sub:
        return title, None

    cleaned = core.clean_vtt_content(sub.read_text(encoding="utf-8", errors="ignore"))
    if len(cleaned.strip()) < 25:
        return title, None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{safe_name(title)}.txt"
    out.write_text(f"# {title}\n# {url}\n\n{cleaned}\n", encoding="utf-8")
    return title, str(out)


def main():
    ok, fail = [], []
    for i, url in enumerate(URLS, 1):
        print(f"[{i}/{len(URLS)}] {url}", flush=True)
        try:
            title, path = grab(url)
            if path:
                print(f"    OK  -> {path}", flush=True)
                ok.append(title)
            else:
                print("    FAIL: no usable subtitles", flush=True)
                fail.append(url)
        except Exception as e:
            print(f"    ERROR: {e}", flush=True)
            fail.append(url)

    # Remove intermediate raw .vtt downloads; keep only cleaned transcripts.
    shutil.rmtree(TMP_DIR, ignore_errors=True)

    print(f"\nDONE: {len(ok)} ok, {len(fail)} failed")
    print(f"Transcripts in: {OUT_DIR}")
    if fail:
        print("Failed:")
        for u in fail:
            print("  ", u)


if __name__ == "__main__":
    main()
