import yt_dlp
import json

def test():
    flat_opts = {
        'extract_flat': True,
        'quiet': False,
        'ignoreerrors': True,
    }
    # use a generic public youtube playlist
    url = "https://www.youtube.com/playlist?list=PLwB_1sMtEq2RMBmQo5v_1Yf1Pmb55p7mF"
    
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        
        if not info:
            print("No info returned")
            return
            
        print("Keys in info:", info.keys())
        if 'entries' in info:
            entries = list(info['entries'])
            print(f"Number of entries: {len(entries)}")
            if entries:
                print("First entry keys:", entries[0].keys())
                print("First entry sample:", {k: entries[0][k] for k in ['id', 'url', 'title'] if k in entries[0]})
        else:
            print("No 'entries' key in info")

if __name__ == "__main__":
    test()
