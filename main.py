from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, Depends, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil
import os
import re
import urllib.parse
from typing import List, Optional, Dict, Any

import database
from streamer import stream_manager

app = FastAPI(title="24/7 YouTube Live Stream Studio")

UPLOAD_BASE = "uploads"
PLAYLIST_BASE = "playlists"
THUMBNAIL_BASE = os.path.join("static", "thumbnails")

# Ensure root directories exist
for directory in [UPLOAD_BASE, PLAYLIST_BASE, THUMBNAIL_BASE, "static", "templates"]:
    os.makedirs(directory, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="templates")


def get_user_upload_dir(user_id: int) -> str:
    path = os.path.join(UPLOAD_BASE, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path

def get_user_playlist_dir(user_id: int) -> str:
    path = os.path.join(PLAYLIST_BASE, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path

def get_user_thumbnail_dir(user_id: int) -> str:
    path = os.path.join(THUMBNAIL_BASE, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


# Helper function to generate clean short title from filename
def clean_title(filename: str) -> str:
    name_without_ext = os.path.splitext(filename)[0]
    cleaned = re.sub(r'^(vidssave\.com\s*|yt5s\.com\s*|y2mate\.is\s*)+', '', name_without_ext, flags=re.IGNORECASE)
    cleaned = re.sub(r'(\s*\d{3,4}[pP]|\s*HD|\s*FHD|\s*4K|\s*HQ)+$', '', cleaned)
    cleaned = cleaned.strip(" -_")
    return cleaned if cleaned else name_without_ext


@app.on_event("startup")
async def on_startup():
    database.init_db()

@app.on_event("shutdown")
async def on_shutdown():
    """Ensure any running FFmpeg stream is cleanly terminated when FastAPI server stops"""
    print("FastAPI shutting down - stopping all active stream processes...")
    stream_manager.stop_all_streams()

def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes < 1024:
        return f"{size_bytes or 0} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

templates.env.filters["format_size"] = format_size

# --- Authentication Helpers ---

def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    return database.get_user_by_session(token)

def require_user(request: Request) -> Optional[Dict[str, Any]]:
    """Returns user or raises redirect."""
    return get_current_user_optional(request)

# --- Auth Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
        
    error_code = request.query_params.get("error")
    error_msg = ""
    if error_code == "invalid_credentials":
        error_msg = "Invalid username/email or password."
    elif error_code == "required":
        error_msg = "Please sign in to access YouTube Studio."
        
    success_code = request.query_params.get("success")
    success_msg = ""
    if success_code == "registered":
        success_msg = "Account created successfully! Please sign in."
        
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error_msg": error_msg, "success_msg": success_msg}
    )

@app.post("/login")
async def login_submit(
    request: Request,
    username_or_email: str = Form(...),
    password: str = Form(...)
):
    user = database.authenticate_user(username_or_email, password)
    if not user:
        return RedirectResponse(url="/login?error=invalid_credentials", status_code=303)
        
    token = database.create_session(user["id"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=30 * 24 * 60 * 60,
        samesite="lax"
    )
    return response

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = get_current_user_optional(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
        
    error_code = request.query_params.get("error")
    error_msg = ""
    if error_code == "exists":
        error_msg = "A user with that username or email already exists."
    elif error_code == "password_mismatch":
        error_msg = "Passwords do not match."
    elif error_code == "short_password":
        error_msg = "Password must be at least 6 characters."
        
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"error_msg": error_msg}
    )

@app.post("/signup")
async def signup_submit(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    if password != confirm_password:
        return RedirectResponse(url="/signup?error=password_mismatch", status_code=303)
    if len(password) < 6:
        return RedirectResponse(url="/signup?error=short_password", status_code=303)
        
    result = database.create_user(username, email, password)
    if not result["success"]:
        return RedirectResponse(url="/signup?error=exists", status_code=303)
        
    token = database.create_session(result["user_id"])
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=30 * 24 * 60 * 60,
        samesite="lax"
    )
    return response

@app.get("/logout")
@app.post("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        database.delete_session(token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

# --- Protected Page Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?error=required", status_code=303)
    
    uid = user["id"]
    stream_status = stream_manager.get_status()
    streams = database.get_all_live_streams(uid)
    videos = database.get_all_videos(uid)
    playlists = database.get_all_playlists(uid)
    
    return templates.TemplateResponse(
        request=request,
        name="index.html", 
        context={
            "current_user": user,
            "status": stream_status,
            "stream_count": len(streams),
            "video_count": len(videos),
            "playlist_count": len(playlists),
            "playlists": playlists[:5],
            "recent_videos": videos[:6]
        }
    )

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?error=required", status_code=303)
    
    uid = user["id"]
    videos = database.get_all_videos(uid)
    playlists = database.get_all_playlists(uid)
    return templates.TemplateResponse(
        request=request, 
        name="upload.html", 
        context={
            "current_user": user,
            "videos": videos,
            "playlists": playlists
        }
    )

def refresh_playlist_file(user_id: int, playlist_id: int):
    """Regenerates the concat text file for FFmpeg streaming"""
    playlist = database.get_playlist_by_id(playlist_id, user_id)
    if not playlist:
        return
    
    upload_dir = get_user_upload_dir(user_id)
    playlist_dir = get_user_playlist_dir(user_id)
    
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', playlist["name"])
    playlist_path = os.path.join(playlist_dir, f"playlist_{playlist_id}_{clean_name}.txt")
    
    video_paths = [os.path.join(upload_dir, item["video_filename"]) for item in playlist["video_items"]]
    stream_manager.create_playlist_file(video_paths, playlist_path)
    return playlist_path

@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?error=required", status_code=303)
    
    uid = user["id"]
    streams = database.get_all_live_streams(uid)
    videos = database.get_all_videos(uid)
    playlists = database.get_all_playlists(uid)
    
    # Decorate streams with running status and source display title
    playlists_map = {p["id"]: p["name"] for p in playlists}
    for s in streams:
        s["is_running"] = stream_manager.is_running(s["id"])
        s_status = stream_manager.get_status(s["id"])
        s["uptime_seconds"] = s_status.get("uptime_seconds", 0)
        s["last_error"] = s_status.get("last_error", "")
        
        if s["source_type"] == "playlist":
            try:
                pl_id = int(s["selected_source"])
                s["source_display"] = f"Playlist: {playlists_map.get(pl_id, f'Playlist #{pl_id}')}"
            except Exception:
                s["source_display"] = f"Playlist #{s['selected_source']}"
        else:
            s["source_display"] = f"Video: {s['selected_source']}"

    return templates.TemplateResponse(
        request=request,
        name="stream.html",
        context={
            "current_user": user,
            "streams": streams,
            "videos": videos,
            "playlists": playlists,
        }
    )

@app.get("/stream/{stream_id}", response_class=HTMLResponse)
async def stream_detail_page(request: Request, stream_id: int):
    user = get_current_user_optional(request)
    if not user:
        return RedirectResponse(url="/login?error=required", status_code=303)

    uid = user["id"]
    stream_data = database.get_live_stream_by_id(stream_id, uid)
    if not stream_data:
        return RedirectResponse(url="/stream", status_code=303)

    playlists = database.get_all_playlists(uid)
    videos = database.get_all_videos(uid)
    playlists_map = {p["id"]: p["name"] for p in playlists}

    # Decorate stream with running status & source display
    stream_data["is_running"] = stream_manager.is_running(stream_id)
    s_status = stream_manager.get_status(stream_id)
    stream_data["uptime_seconds"] = s_status.get("uptime_seconds", 0)
    stream_data["last_error"] = s_status.get("last_error", "")

    playlist_items = []
    if stream_data["source_type"] == "playlist":
        try:
            pl_id = int(stream_data["selected_source"])
            stream_data["source_display"] = f"Playlist: {playlists_map.get(pl_id, f'Playlist #{pl_id}')}"
            pl = database.get_playlist_by_id(pl_id, uid)
            if pl:
                playlist_items = pl.get("video_items", [])
        except Exception:
            stream_data["source_display"] = f"Playlist #{stream_data['selected_source']}"
    else:
        stream_data["source_display"] = f"Video: {stream_data['selected_source']}"

    return templates.TemplateResponse(
        request=request,
        name="stream_detail.html",
        context={
            "current_user": user,
            "stream": stream_data,
            "status": s_status,
            "playlist_items": playlist_items,
            "playlists": playlists,
            "videos": videos,
        }
    )

# --- Multiple Live Stream CRUD & Control APIs ---

@app.post("/api/streams/create")
async def create_stream_api(
    request: Request,
    title: str = Form(...),
    stream_url: str = Form(...),
    stream_key: str = Form(...),
    source_type: str = Form(...),
    selected_source: str = Form(...)
):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    title = title.strip() or "24/7 Live Stream"
    stream_url = stream_url.strip() or "rtmp://a.rtmp.youtube.com/live2"
    stream_key = stream_key.strip()
    
    if not stream_key:
        return JSONResponse(status_code=400, content={"success": False, "error": "Stream key is required"})
    if stream_key.startswith(("rtmp://", "http://", "https://")):
        return JSONResponse(status_code=400, content={"success": False, "error": "You pasted a URL in the Stream Key field. Please copy your private Stream Key from YouTube Studio."})
        
    stream_id = database.create_live_stream(
        user_id=uid,
        title=title,
        stream_url=stream_url,
        stream_key=stream_key,
        source_type=source_type,
        selected_source=selected_source
    )
    return JSONResponse(content={"success": True, "stream_id": stream_id})

@app.post("/api/streams/{stream_id}/edit")
async def edit_stream_api(
    request: Request,
    stream_id: int,
    title: str = Form(...),
    stream_url: str = Form(...),
    stream_key: str = Form(...),
    source_type: str = Form(...),
    selected_source: str = Form(...)
):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    title = title.strip() or "24/7 Live Stream"
    stream_url = stream_url.strip() or "rtmp://a.rtmp.youtube.com/live2"
    stream_key = stream_key.strip()
    
    if not stream_key:
        return JSONResponse(status_code=400, content={"success": False, "error": "Stream key is required"})
    if stream_key.startswith(("rtmp://", "http://", "https://")):
        return JSONResponse(status_code=400, content={"success": False, "error": "You pasted a URL in the Stream Key field. Please copy your private Stream Key from YouTube Studio."})
        
    database.update_live_stream(
        stream_id=stream_id,
        user_id=uid,
        title=title,
        stream_url=stream_url,
        stream_key=stream_key,
        source_type=source_type,
        selected_source=selected_source
    )
    return JSONResponse(content={"success": True})

@app.post("/api/streams/{stream_id}/delete")
async def delete_stream_api(request: Request, stream_id: int):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    # Verify ownership before stopping
    stream_data = database.get_live_stream_by_id(stream_id, uid)
    if not stream_data:
        return JSONResponse(status_code=404, content={"success": False, "error": "Stream not found"})
        
    if stream_manager.is_running(stream_id):
        stream_manager.stop_stream(stream_id)
        
    database.delete_live_stream(stream_id, uid)
    return JSONResponse(content={"success": True})

@app.post("/api/streams/{stream_id}/start")
async def start_single_stream_api(request: Request, stream_id: int):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    stream_data = database.get_live_stream_by_id(stream_id, uid)
    if not stream_data:
        return JSONResponse(status_code=404, content={"success": False, "error": "Stream configuration not found"})
    
    upload_dir = get_user_upload_dir(uid)
    source_path = ""
    if stream_data["source_type"] == "video":
        source_path = os.path.join(upload_dir, stream_data["selected_source"])
    else:
        try:
            pl_id = int(stream_data["selected_source"])
            playlist = database.get_playlist_by_id(pl_id, uid)
            if playlist and playlist["video_items"]:
                source_path = refresh_playlist_file(uid, pl_id)
            else:
                return JSONResponse(status_code=400, content={"success": False, "error": "Selected playlist is empty. Add videos first."})
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid playlist selected"})

    if not source_path or not os.path.exists(source_path):
        return JSONResponse(status_code=400, content={"success": False, "error": "Video source file not found on disk"})

    stream_manager.start_stream(
        stream_id=stream_id,
        video_source=source_path,
        stream_url=stream_data["stream_url"],
        stream_key=stream_data["stream_key"],
        source_type=stream_data["source_type"],
        title=stream_data["title"]
    )
    return JSONResponse(content={"success": True, "stream_id": stream_id})

@app.post("/api/streams/{stream_id}/stop")
async def stop_single_stream_api(request: Request, stream_id: int):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    # Verify ownership
    stream_data = database.get_live_stream_by_id(stream_id, uid)
    if not stream_data:
        return JSONResponse(status_code=404, content={"success": False, "error": "Stream not found"})
        
    stream_manager.stop_stream(stream_id)
    return JSONResponse(content={"success": True, "stream_id": stream_id})

@app.get("/api/stream_status")
async def get_all_stream_status_api():
    return JSONResponse(content=stream_manager.get_status())

# --- Upload & Asset API ---

@app.post("/api/upload_videos")
async def upload_videos(request: Request, files: List[UploadFile] = File(...)):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    upload_dir = get_user_upload_dir(uid)
    thumbnail_dir = get_user_thumbnail_dir(uid)
    
    saved_items = []
    for file in files:
        if not file.filename:
            continue
            
        safe_filename = os.path.basename(file.filename)
        file_path = os.path.join(upload_dir, safe_filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        thumb_filename = f"{os.path.splitext(safe_filename)[0]}.jpg"
        thumb_rel_path = f"/static/thumbnails/{uid}/{thumb_filename}"
        thumb_full_path = os.path.join(thumbnail_dir, thumb_filename)
        
        stream_manager.generate_thumbnail(file_path, thumb_full_path)
        
        title = clean_title(safe_filename)
        database.save_video_metadata(
            user_id=uid,
            filename=safe_filename,
            title=title,
            thumbnail=thumb_rel_path,
            size_bytes=size_bytes
        )
        
        saved_items.append({
            "filename": safe_filename,
            "title": title,
            "thumbnail": thumb_rel_path,
            "size_bytes": size_bytes,
            "size_formatted": format_size(size_bytes)
        })
        
    return JSONResponse(content={"success": True, "files": saved_items})

@app.post("/api/delete_video")
async def delete_video(request: Request, filename: str = Form(...)):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    upload_dir = get_user_upload_dir(uid)
    thumbnail_dir = get_user_thumbnail_dir(uid)
    
    file_path = os.path.join(upload_dir, filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"Error removing video file: {e}")
            
    thumb_path = os.path.join(thumbnail_dir, f"{os.path.splitext(filename)[0]}.jpg")
    if os.path.exists(thumb_path):
        try:
            os.remove(thumb_path)
        except Exception:
            pass
            
    database.delete_video_record(uid, filename)
    return JSONResponse(content={"success": True, "deleted": filename})

# --- Playlist API ---

@app.post("/api/playlists")
async def create_playlist_api(request: Request, name: str = Form(...), videos: List[str] = Form([])):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name cannot be empty")
    
    # Resolve filenames to video ids for this user
    video_ids = []
    for filename in videos:
        vid = database.get_video(uid, filename)
        if vid:
            video_ids.append(vid["id"])

    playlist_id = database.create_playlist(uid, name, video_ids)
    refresh_playlist_file(uid, playlist_id)
    return JSONResponse(content={"success": True, "playlist_id": playlist_id, "name": name})

@app.post("/api/playlists/{playlist_id}/add_videos")
async def add_videos_to_playlist_api(request: Request, playlist_id: int, videos: List[str] = Form(...)):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    database.add_videos_to_playlist(uid, playlist_id, videos)
    refresh_playlist_file(uid, playlist_id)
    return JSONResponse(content={"success": True})

@app.post("/api/playlists/{playlist_id}/remove_item")
async def remove_item_from_playlist_api(request: Request, playlist_id: int, item_id: int = Form(...)):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    database.remove_video_from_playlist(uid, playlist_id, item_id)
    refresh_playlist_file(uid, playlist_id)
    return JSONResponse(content={"success": True})

@app.post("/api/playlists/{playlist_id}/delete")
async def delete_playlist_api(request: Request, playlist_id: int):
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    uid = user["id"]
    playlist = database.get_playlist_by_id(playlist_id, uid)
    if playlist:
        playlist_dir = get_user_playlist_dir(uid)
        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', playlist["name"])
        playlist_path = os.path.join(playlist_dir, f"playlist_{playlist_id}_{clean_name}.txt")
        if os.path.exists(playlist_path):
            try:
                os.remove(playlist_path)
            except Exception:
                pass
    database.delete_playlist(uid, playlist_id)
    return JSONResponse(content={"success": True})
