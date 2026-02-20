import yt_dlp
import sys

print(f"Python: {sys.version}")
print(f"yt-dlp: {yt_dlp.version.__version__}")

url = "https://www.youtube.com/watch?v=IzzuVMWrU9U"
ydl_opts = {
    'cookiesfrombrowser': ('chrome',),
    'quiet': False,
    'extract_flat': True,
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        print(f"Success! Title: {info.get('title')}")
except Exception as e:
    print(f"Error: {e}")
