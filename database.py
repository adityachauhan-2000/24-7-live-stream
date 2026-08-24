import sqlite3
import os
import hashlib
import secrets
from typing import List, Dict, Any, Optional
from datetime import datetime

DB_PATH = "stream_app.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    # 100,000 rounds of PBKDF2 SHA256
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    return pwd_hash, salt

def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    pwd_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(pwd_hash, expected_hash)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # User Sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # Stream credentials table (legacy fallback)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stream_credentials (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            stream_url TEXT NOT NULL DEFAULT 'rtmp://a.rtmp.youtube.com/live2',
            stream_key TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'video',
            selected_source TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Live Streams Table — with user_id isolation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            stream_url TEXT NOT NULL DEFAULT 'rtmp://a.rtmp.youtube.com/live2',
            stream_key TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'playlist',
            selected_source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    
    # Videos metadata table — with user_id isolation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            filename TEXT NOT NULL,
            title TEXT NOT NULL,
            thumbnail TEXT DEFAULT '',
            size_bytes INTEGER DEFAULT 0,
            duration_sec REAL DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, filename)
        )
    """)
    
    # Playlists table — with user_id isolation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name)
        )
    """)
    
    # Playlist items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
        )
    """)

    # Ensure default row exists in stream_credentials
    cursor.execute("""
        INSERT OR IGNORE INTO stream_credentials (id, stream_url, stream_key, source_type, selected_source)
        VALUES (1, 'rtmp://a.rtmp.youtube.com/live2', '', 'video', '')
    """)

    # ── Schema migrations for existing DBs ──────────────────────────────────────
    # Add user_id to live_streams if missing
    try:
        cursor.execute("ALTER TABLE live_streams ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass  # column already exists

    # Add user_id to videos if missing (old schema used filename as PK)
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # Add id to videos if missing (old schema: filename was PK)
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN id INTEGER")
    except Exception:
        pass

    # Add user_id to playlists if missing
    try:
        cursor.execute("ALTER TABLE playlists ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # Add video_id to playlist_items if missing (replaces video_filename)
    try:
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN video_id INTEGER")
    except Exception:
        pass

    # Add video_filename to playlist_items if missing (keep for backwards compat)
    try:
        cursor.execute("ALTER TABLE playlist_items ADD COLUMN video_filename TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()

# --- User Auth Functions ---

def create_user(username: str, email: str, password: str) -> Dict[str, Any]:
    username = username.strip().lower()
    email = email.strip().lower()
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, email))
    if cursor.fetchone():
        conn.close()
        return {"success": False, "error": "Username or Email already exists"}
        
    pwd_hash, salt = hash_password(password)
    try:
        cursor.execute("""
            INSERT INTO users (username, email, password_hash, salt)
            VALUES (?, ?, ?, ?)
        """, (username, email, pwd_hash, salt))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "user_id": user_id, "username": username, "email": email}
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}

def authenticate_user(identifier: str, password: str) -> Optional[Dict[str, Any]]:
    identifier = identifier.strip().lower()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, username, email, password_hash, salt, created_at 
        FROM users 
        WHERE username = ? OR email = ?
    """, (identifier, identifier))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    user = dict(row)
    if verify_password(password, user["salt"], user["password_hash"]):
        return {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "created_at": user["created_at"]
        }
    return None

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    return token

def get_user_by_session(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.id, u.username, u.email, u.created_at
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ?
    """, (token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_session(token: str):
    if not token:
        return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

# --- Multiple Live Streams Management (user-scoped) ---

def create_live_stream(user_id: int, title: str, stream_url: str, stream_key: str, source_type: str, selected_source: str) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO live_streams (user_id, title, stream_url, stream_key, source_type, selected_source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (user_id, title.strip(), stream_url.strip(), stream_key.strip(), source_type, selected_source))
    stream_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return stream_id

def get_all_live_streams(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, title, stream_url, stream_key, source_type, selected_source, created_at, updated_at FROM live_streams WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_live_stream_by_id(stream_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, title, stream_url, stream_key, source_type, selected_source, created_at, updated_at FROM live_streams WHERE id = ? AND user_id = ?",
        (stream_id, user_id)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_live_stream(stream_id: int, user_id: int, title: str, stream_url: str, stream_key: str, source_type: str, selected_source: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE live_streams
        SET title = ?, stream_url = ?, stream_key = ?, source_type = ?, selected_source = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (title.strip(), stream_url.strip(), stream_key.strip(), source_type, selected_source, stream_id, user_id))
    conn.commit()
    conn.close()

def delete_live_stream(stream_id: int, user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM live_streams WHERE id = ? AND user_id = ?", (stream_id, user_id))
    conn.commit()
    conn.close()

# --- Legacy Stream Credentials Helper ---

def get_stream_credentials() -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT stream_url, stream_key, source_type, selected_source, updated_at FROM stream_credentials WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "stream_url": "rtmp://a.rtmp.youtube.com/live2",
        "stream_key": "",
        "source_type": "video",
        "selected_source": "",
        "updated_at": ""
    }

def save_stream_credentials(stream_url: str, stream_key: str, source_type: str = "video", selected_source: str = ""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO stream_credentials (id, stream_url, stream_key, source_type, selected_source, updated_at)
        VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            stream_url = excluded.stream_url,
            stream_key = excluded.stream_key,
            source_type = excluded.source_type,
            selected_source = excluded.selected_source,
            updated_at = CURRENT_TIMESTAMP
    """)
    conn.commit()
    conn.close()

# --- Video Metadata (user-scoped) ---

def save_video_metadata(user_id: int, filename: str, title: str, thumbnail: str, size_bytes: int, duration_sec: float = 0.0):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO videos (user_id, filename, title, thumbnail, size_bytes, duration_sec, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, filename) DO UPDATE SET
            title = excluded.title,
            thumbnail = excluded.thumbnail,
            size_bytes = excluded.size_bytes,
            duration_sec = excluded.duration_sec
    """, (user_id, filename, title, thumbnail, size_bytes, duration_sec))
    conn.commit()
    conn.close()

def get_all_videos(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, filename, title, thumbnail, size_bytes, duration_sec, uploaded_at FROM videos WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_video(user_id: int, filename: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, filename, title, thumbnail, size_bytes, duration_sec, uploaded_at FROM videos WHERE user_id = ? AND filename = ?",
        (user_id, filename)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_video_record(user_id: int, filename: str):
    conn = get_db()
    cursor = conn.cursor()
    # Get video id first for playlist_items deletion
    cursor.execute("SELECT id FROM videos WHERE user_id = ? AND filename = ?", (user_id, filename))
    row = cursor.fetchone()
    if row:
        video_id = row["id"]
        cursor.execute("DELETE FROM playlist_items WHERE video_id = ?", (video_id,))
    cursor.execute("DELETE FROM videos WHERE user_id = ? AND filename = ?", (user_id, filename))
    conn.commit()
    conn.close()

# --- Playlists (user-scoped) ---

def create_playlist(user_id: int, name: str, video_ids: List[int]) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO playlists (user_id, name) VALUES (?, ?)", (user_id, name))
    playlist_id = cursor.lastrowid
    
    for pos, vid_id in enumerate(video_ids):
        cursor.execute("""
            INSERT INTO playlist_items (playlist_id, video_id, position)
            VALUES (?, ?, ?)
        """, (playlist_id, vid_id, pos))
        
    conn.commit()
    conn.close()
    return playlist_id

def get_all_playlists(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, name, created_at FROM playlists WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    playlist_rows = cursor.fetchall()
    
    playlists = []
    for row in playlist_rows:
        p_dict = dict(row)
        cursor.execute("""
            SELECT pi.id, pi.video_id, pi.position, v.filename AS video_filename, v.title, v.thumbnail, v.size_bytes
            FROM playlist_items pi
            JOIN videos v ON pi.video_id = v.id
            WHERE pi.playlist_id = ?
            ORDER BY pi.position ASC, pi.id ASC
        """, (p_dict["id"],))
        items = [dict(item) for item in cursor.fetchall()]
        p_dict["items"] = items
        p_dict["video_items"] = items
        p_dict["video_count"] = len(items)
        playlists.append(p_dict)
        
    conn.close()
    return playlists

def get_playlist_by_id(playlist_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, name, created_at FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
        
    p_dict = dict(row)
    cursor.execute("""
        SELECT pi.id, pi.video_id, pi.position, v.filename AS video_filename, v.title, v.thumbnail, v.size_bytes
        FROM playlist_items pi
        JOIN videos v ON pi.video_id = v.id
        WHERE pi.playlist_id = ?
        ORDER BY pi.position ASC, pi.id ASC
    """, (playlist_id,))
    items = [dict(item) for item in cursor.fetchall()]
    p_dict["items"] = items
    p_dict["video_items"] = items
    p_dict["video_count"] = len(items)
    conn.close()
    return p_dict

def add_videos_to_playlist(user_id: int, playlist_id: int, video_filenames: List[str]):
    """Add videos by filename (looks up video ids for the user)."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Verify playlist belongs to user
    cursor.execute("SELECT id FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return
    
    cursor.execute("SELECT MAX(position) FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    max_pos_row = cursor.fetchone()
    current_pos = (max_pos_row[0] + 1) if max_pos_row and max_pos_row[0] is not None else 0
    
    for filename in video_filenames:
        # Look up video id for this user
        cursor.execute("SELECT id FROM videos WHERE user_id = ? AND filename = ?", (user_id, filename))
        vid_row = cursor.fetchone()
        if vid_row:
            cursor.execute("""
                INSERT INTO playlist_items (playlist_id, video_id, position)
                VALUES (?, ?, ?)
            """, (playlist_id, vid_row["id"], current_pos))
            current_pos += 1
        
    conn.commit()
    conn.close()

def remove_video_from_playlist(user_id: int, playlist_id: int, item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    # Verify playlist belongs to user
    cursor.execute("SELECT id FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id))
    if cursor.fetchone():
        cursor.execute("DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?", (item_id, playlist_id))
        conn.commit()
    conn.close()

def delete_playlist(user_id: int, playlist_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND playlist_id IN (SELECT id FROM playlists WHERE user_id = ?)", (playlist_id, user_id))
    cursor.execute("DELETE FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id))
    conn.commit()
    conn.close()
