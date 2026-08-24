import os
import sys
import asyncio
from fastapi import UploadFile
from main import app, upload_videos, create_playlist_api, add_videos_to_playlist_api, remove_item_from_playlist_api, delete_playlist_api, start_stream, stop_stream, get_stream_status_api
import database

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

async def test_full_flow():
    print("--- Starting End-to-End API Flow Test ---")
    
    # 1. Check DB videos
    videos = database.get_all_videos()
    print(f"1. Database currently has {len(videos)} videos.")
    assert len(videos) > 0, "Expected existing videos to be indexed."

    # 2. Test Playlist Creation
    pl_name = "Automation Test Playlist"
    response = await create_playlist_api(name=pl_name, videos=[videos[0]["filename"]])
    print(f"2. Created playlist response: {response.body.decode('utf-8')}")
    
    playlists = database.get_all_playlists()
    test_pl = next((p for p in playlists if p["name"] == pl_name), None)
    assert test_pl is not None, "Playlist not found in DB"
    assert test_pl["video_count"] == 1, "Expected 1 video in playlist"
    print(f"   Playlist ID: {test_pl['id']}, Videos: {test_pl['video_count']}")

    # 3. Test Add More Videos to Playlist
    if len(videos) > 1:
        await add_videos_to_playlist_api(test_pl["id"], [videos[1]["filename"]])
        pl_updated = database.get_playlist_by_id(test_pl["id"])
        print(f"3. Added video. New video count: {pl_updated['video_count']}")
        assert pl_updated["video_count"] == 2

    # 4. Test Stream Credential Persistence via start_stream
    print("4. Testing start_stream with DB credential save...")
    stream_res = await start_stream(
        stream_url="rtmp://a.rtmp.youtube.com/live2",
        stream_key="live_secret_key_8899",
        source_type="playlist",
        video_source=None,
        playlist_id=test_pl["id"]
    )
    
    creds = database.get_stream_credentials()
    print(f"   Saved DB Credentials: {creds}")
    assert creds["stream_key"] == "live_secret_key_8899"
    assert creds["source_type"] == "playlist"
    assert creds["selected_source"] == str(test_pl["id"])

    # 5. Check Stream Status
    status_res = await get_stream_status_api()
    print(f"5. Stream Status API: {status_res.body.decode('utf-8')}")

    # 6. Stop Stream
    await stop_stream()
    print("6. Stream stopped successfully.")

    # 7. Cleanup test playlist
    await delete_playlist_api(test_pl["id"])
    print("7. Cleanup complete. All integration tests passed!")

if __name__ == "__main__":
    asyncio.run(test_full_flow())
