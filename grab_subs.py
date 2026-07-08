"""
Batch subtitle grabber for YouScriber (CLI).

Downloads subtitles + metadata for a list of YouTube URLs and writes cleaned,
timecode-free transcripts as .txt files ready for LLM / book processing.
Reuses the same download/cleaning logic as the desktop GUI (core.py) so both
front ends stay in sync.

Usage:
    python grab_subs.py URL [URL ...]
    python grab_subs.py --urls-file urls.txt
    python grab_subs.py --urls-file urls.txt --out-dir output/transcripts --sub-langs ru-orig,ru,en
"""

import argparse
import pathlib
import sys
import uuid

import core

DEFAULT_OUT_DIR = pathlib.Path(__file__).parent / "output" / "transcripts"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-download and clean YouTube subtitles.")
    parser.add_argument("urls", nargs="*", help="YouTube video URLs to process")
    parser.add_argument("--urls-file", type=pathlib.Path,
                        help="Path to a text file with one URL per line (# comments allowed)")
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT_DIR,
                        help="Directory to write cleaned .txt transcripts to (default: %(default)s)")
    parser.add_argument("--sub-langs", default=core.DEFAULT_SUB_LANGS,
                        help="Comma-separated subtitle language preference (default: %(default)s)")
    parser.add_argument("--browser", default="None",
                        help="Browser to pull cookies from (chrome/firefox/safari/...), "
                             "or a cookies.txt path. Default: none.")
    return parser.parse_args(argv)


def load_urls(args: argparse.Namespace) -> list:
    urls = list(args.urls)
    if args.urls_file:
        if not args.urls_file.exists():
            sys.exit(f"URLs file not found: {args.urls_file}")
        for line in args.urls_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def main(argv=None) -> int:
    args = parse_args(argv)
    urls = load_urls(args)
    if not urls:
        sys.exit("No URLs given. Pass URLs as arguments or use --urls-file. See --help.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"cli-{uuid.uuid4().hex[:8]}"

    ok, fail = [], []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}", flush=True)
        try:
            files = core.download_video_subtitles(
                url=url,
                session_id=session_id,
                browser=args.browser,
                sub_langs=args.sub_langs,
                group_by_playlist=False,
                error_callback=lambda msg: print(f"    {msg}", flush=True),
            )
        except core.OperationCancelled:
            break
        except Exception as e:
            print(f"    ERROR: {e}", flush=True)
            fail.append(url)
            continue

        if not files:
            fail.append(url)
            continue

        wrote_real_transcript = False
        for src in files:
            content = src.read_text(encoding="utf-8")
            dest = args.out_dir / src.name
            dest.write_text(content, encoding="utf-8")
            if core.NO_SUBTITLES_PLACEHOLDER not in content:
                wrote_real_transcript = True
                print(f"    OK  -> {dest}", flush=True)

        if wrote_real_transcript:
            ok.append(url)
        else:
            print("    FAIL: no usable subtitles", flush=True)
            fail.append(url)

    print(f"\nDONE: {len(ok)} ok, {len(fail)} failed")
    print(f"Transcripts in: {args.out_dir}")
    if fail:
        print("Failed:")
        for u in fail:
            print("  ", u)

    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
