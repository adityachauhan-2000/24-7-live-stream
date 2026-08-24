import pymysql
import os
import hashlib
import secrets
import time
from typing import List, Dict, Any, Optional
from datetime import datetime

DB_HOST = os.getenv("DB_HOST", "db")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "rootpassword")
DB_NAME = os.getenv("DB_NAME", "stream_app")

def get_db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
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
    # Wait for MySQL to be ready
    for _ in range(15):
        try:
            conn = get_db()
            break
        except Exception:
            time.sleep(2)
    else:
        conn = get_db()

    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(255) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            salt VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token VARCHAR(255) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stream_credentials (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            stream_url VARCHAR(255) NOT NULL DEFAULT 'rtmp://a.rtmp.youtube.com/live2',
            stream_key VARCHAR(255) NOT NULL DEFAULT '',
            source_type VARCHAR(50) NOT NULL DEFAULT 'video',
            selected_source VARCHAR(255) NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS live_streams (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            title VARCHAR(255) NOT NULL,
            stream_url VARCHAR(255) NOT NULL DEFAULT 'rtmp://a.rtmp.youtube.com/live2',
            stream_key VARCHAR(255) NOT NULL,
            source_type VARCHAR(50) NOT NULL DEFAULT 'playlist',
            selected_source VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            filename VARCHAR(255) NOT NULL,
            title VARCHAR(255) NOT NULL,
            thumbnail VARCHAR(255) DEFAULT '',
            size_bytes BIGINT DEFAULT 0,
            duration_sec FLOAT DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, filename)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            playlist_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            video_filename VARCHAR(255) DEFAULT NULL,
            FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        INSERT IGNORE INTO stream_credentials (id, stream_url, stream_key, source_type, selected_source)
        VALUES (1, 'rtmp://a.rtmp.youtube.com/live2', '', 'video', '')
    """)

    conn.close()

def create_user(username: str, email: str, password: str) -> Dict[str, Any]:
    username = username.strip().lower()
    email = email.strip().lower()
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
    if cursor.fetchone():
        conn.close()
        return {"success": False, "error": "Username or Email already exists"}
        
    pwd_hash, salt = hash_password(password)
    try:
        cursor.execute("""
            INSERT INTO users (username, email, password_hash, salt)
            VALUES (%s, %s, %s, %s)
        """, (username, email, pwd_hash, salt))
        user_id = cursor.lastrowid
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
        WHERE username = %s OR email = %s
    """, (identifier, identifier))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return None
        
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
    cursor.execute("INSERT INTO sessions (token, user_id) VALUES (%s, %s)", (token, user_id))
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
        WHERE s.token = %s
    """, (token,))
    row = cursor.fetchone()
    conn.close()
    return row if row else None

def delete_session(token: str):
    if not token:
        return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token = %s", (token,))
    conn.close()

def create_live_stream(user_id: int, title: str, stream_url: str, stream_key: str, source_type: str, selected_source: str) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO live_streams (user_id, title, stream_url, stream_key, source_type, selected_source)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, title.strip(), stream_url.strip(), stream_key.strip(), source_type, selected_source))
    stream_id = cursor.lastrowid
    conn.close()
    return stream_id

def get_all_live_streams(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, title, stream_url, stream_key, source_type, selected_source, created_at, updated_at FROM live_streams WHERE user_id = %s ORDER BY id DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return list(rows)

def get_live_stream_by_id(stream_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, title, stream_url, stream_key, source_type, selected_source, created_at, updated_at FROM live_streams WHERE id = %s AND user_id = %s",
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
        SET title = %s, stream_url = %s, stream_key = %s, source_type = %s, selected_source = %s
        WHERE id = %s AND user_id = %s
    """, (title.strip(), stream_url.strip(), stream_key.strip(), source_type, selected_source, stream_id, user_id))
    conn.close()

def delete_live_stream(stream_id: int, user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM live_streams WHERE id = %s AND user_id = %s", (stream_id, user_id))
    conn.close()

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
        INSERT INTO stream_credentials (id, stream_url, stream_key, source_type, selected_source)
        VALUES (1, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stream_url = VALUES(stream_url),
            stream_key = VALUES(stream_key),
            source_type = VALUES(source_type),
            selected_source = VALUES(selected_source)
    """, (stream_url, stream_key, source_type, selected_source))
    conn.close()

def save_video_metadata(user_id: int, filename: str, title: str, thumbnail: str, size_bytes: int, duration_sec: float = 0.0):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO videos (user_id, filename, title, thumbnail, size_bytes, duration_sec)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            title = VALUES(title),
            thumbnail = VALUES(thumbnail),
            size_bytes = VALUES(size_bytes),
            duration_sec = VALUES(duration_sec)
    """, (user_id, filename, title, thumbnail, size_bytes, duration_sec))
    conn.close()

def get_all_videos(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, filename, title, thumbnail, size_bytes, duration_sec, uploaded_at FROM videos WHERE user_id = %s ORDER BY uploaded_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return list(rows)

def get_video(user_id: int, filename: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, filename, title, thumbnail, size_bytes, duration_sec, uploaded_at FROM videos WHERE user_id = %s AND filename = %s",
        (user_id, filename)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_video_record(user_id: int, filename: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM videos WHERE user_id = %s AND filename = %s", (user_id, filename))
    row = cursor.fetchone()
    if row:
        video_id = row["id"]
        cursor.execute("DELETE FROM playlist_items WHERE video_id = %s", (video_id,))
    cursor.execute("DELETE FROM videos WHERE user_id = %s AND filename = %s", (user_id, filename))
    conn.close()

def create_playlist(user_id: int, name: str, video_ids: List[int]) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO playlists (user_id, name) VALUES (%s, %s)", (user_id, name))
    playlist_id = cursor.lastrowid
    
    for pos, vid_id in enumerate(video_ids):
        cursor.execute("""
            INSERT INTO playlist_items (playlist_id, video_id, position)
            VALUES (%s, %s, %s)
        """, (playlist_id, vid_id, pos))
        
    conn.close()
    return playlist_id

def get_all_playlists(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, name, created_at FROM playlists WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    playlist_rows = cursor.fetchall()
    
    playlists = []
    for row in playlist_rows:
        p_dict = dict(row)
        cursor.execute("""
            SELECT pi.id, pi.video_id, pi.position, v.filename AS video_filename, v.title, v.thumbnail, v.size_bytes
            FROM playlist_items pi
            JOIN videos v ON pi.video_id = v.id
            WHERE pi.playlist_id = %s
            ORDER BY pi.position ASC, pi.id ASC
        """, (p_dict["id"],))
        items = list(cursor.fetchall())
        p_dict["items"] = items
        p_dict["video_items"] = items
        p_dict["video_count"] = len(items)
        playlists.append(p_dict)
        
    conn.close()
    return playlists

def get_playlist_by_id(playlist_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, name, created_at FROM playlists WHERE id = %s AND user_id = %s", (playlist_id, user_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
        
    p_dict = dict(row)
    cursor.execute("""
        SELECT pi.id, pi.video_id, pi.position, v.filename AS video_filename, v.title, v.thumbnail, v.size_bytes
        FROM playlist_items pi
        JOIN videos v ON pi.video_id = v.id
        WHERE pi.playlist_id = %s
        ORDER BY pi.position ASC, pi.id ASC
    """, (playlist_id,))
    items = list(cursor.fetchall())
    p_dict["items"] = items
    p_dict["video_items"] = items
    p_dict["video_count"] = len(items)
    conn.close()
    return p_dict

def add_videos_to_playlist(user_id: int, playlist_id: int, video_filenames: List[str]):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM playlists WHERE id = %s AND user_id = %s", (playlist_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return
    
    cursor.execute("SELECT MAX(position) AS max_pos FROM playlist_items WHERE playlist_id = %s", (playlist_id,))
    max_pos_row = cursor.fetchone()
    current_pos = (max_pos_row["max_pos"] + 1) if max_pos_row and max_pos_row["max_pos"] is not None else 0
    
    for filename in video_filenames:
        cursor.execute("SELECT id FROM videos WHERE user_id = %s AND filename = %s", (user_id, filename))
        vid_row = cursor.fetchone()
        if vid_row:
            cursor.execute("""
                INSERT INTO playlist_items (playlist_id, video_id, position)
                VALUES (%s, %s, %s)
            """, (playlist_id, vid_row["id"], current_pos))
            current_pos += 1
        
    conn.close()

def remove_video_from_playlist(user_id: int, playlist_id: int, item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM playlists WHERE id = %s AND user_id = %s", (playlist_id, user_id))
    if cursor.fetchone():
        cursor.execute("DELETE FROM playlist_items WHERE id = %s AND playlist_id = %s", (item_id, playlist_id))
    conn.close()

def delete_playlist(user_id: int, playlist_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM playlist_items WHERE playlist_id = %s AND playlist_id IN (SELECT id FROM playlists WHERE user_id = %s)", (playlist_id, user_id))
    cursor.execute("DELETE FROM playlists WHERE id = %s AND user_id = %s", (playlist_id, user_id))
    conn.close()
