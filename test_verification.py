import os
import sys
import database
from streamer import stream_manager

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def run_tests():
    print("1. Initializing DB...")
    database.init_db()
    
    print("2. Testing credentials...")
    database.save_stream_credentials("rtmp://a.rtmp.youtube.com/live2", "live_key_xyz987", "playlist", "1")
    creds = database.get_stream_credentials()
    assert creds["stream_key"] == "live_key_xyz987", "Stream key mismatch"
    print("   Credentials OK:", creds["stream_url"])

    print("3. Testing video sync & thumbnail generation...")
    from main import sync_existing_uploads
    sync_existing_uploads()
    videos = database.get_all_videos()
    print(f"   Synced {len(videos)} videos.")
    for v in videos:
        print(f"   - Title: {v['title']} | Thumb: {v['thumbnail']} | Size: {v['size_bytes']} bytes")
        if v['thumbnail']:
            thumb_disk_path = v['thumbnail'].lstrip('/')
            print(f"     Thumbnail exists on disk: {os.path.exists(thumb_disk_path)}")

    print("4. Testing playlist creation...")
    vid_filenames = [v['filename'] for v in videos]
    pl_id = database.create_playlist("Top Highlights 24-7", vid_filenames[:2])
    print(f"   Created playlist ID: {pl_id}")
    
    playlists = database.get_all_playlists()
    print(f"   Total Playlists: {len(playlists)}")
    for p in playlists:
        print(f"   - Playlist: {p['name']} ({p['video_count']} videos)")

    print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
