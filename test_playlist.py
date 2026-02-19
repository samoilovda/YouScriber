import yt_dlp
import json

def test_playlist_extraction():
    url = "https://www.youtube.com/playlist?list=PLzMcBGfZo4-l1Ze18tU_qYd8d-6k-gKzF" # a short playlist (likely) or just use a known one. 
    # Actually, let's use a very short dummy playlist or trust the flag.
    # We can use a test URL from yt-dlp test suite if known, but a real URL is better.
    # This is "Tech With Tim - Python for Beginners" - might be long.
    
    # Let's use a single video as a playlist (some URLs work like that) or just a known short list.
    # Better: just checking if 'extract_flat' returns entries without downloading.
    
    ydl_opts = {
        'extract_flat': True, # This should return metadata without downloading
        'dump_single_json': True,
        'quiet': True
    }
    
    print("Attempting to fetch playlist metadata with extract_flat=True...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
             # We can use a random playlist, e.g. "PLzMcBGfZo4-l1Ze18tU_qYd8d-6k-gKzF"
            info = ydl.extract_info("https://www.youtube.com/playlist?list=PLzMcBGfZo4-l1Ze18tU_qYd8d-6k-gKzF", download=False)
            if 'entries' in info:
                print(f"Success! Found {len(info['entries'])} entries.")
                print("First entry sample:", info['entries'][0])
                print("Is 'entries' a list?", isinstance(info['entries'], list))
            else:
                print("No 'entries' found. Keys:", info.keys())
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_playlist_extraction()
