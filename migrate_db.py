import sqlite3

conn = sqlite3.connect('stream_app.db')
c = conn.cursor()

try:
    c.execute('ALTER TABLE videos RENAME TO videos_old')
    c.execute('''CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 0,
        filename TEXT NOT NULL,
        title TEXT NOT NULL,
        thumbnail TEXT DEFAULT '',
        size_bytes INTEGER DEFAULT 0,
        duration_sec REAL DEFAULT 0,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, filename)
    )''')
    c.execute('INSERT INTO videos (filename, title, thumbnail, size_bytes, duration_sec, uploaded_at, user_id) SELECT filename, title, thumbnail, size_bytes, duration_sec, uploaded_at, 0 FROM videos_old')
    c.execute('DROP TABLE videos_old')
    print('Videos migrated.')
except Exception as e:
    print('Videos migration error:', e)

try:
    c.execute('ALTER TABLE playlists RENAME TO playlists_old')
    c.execute('''CREATE TABLE playlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, name)
    )''')
    c.execute('INSERT INTO playlists (id, name, created_at, user_id) SELECT id, name, created_at, 0 FROM playlists_old')
    c.execute('DROP TABLE playlists_old')
    print('Playlists migrated.')
except Exception as e:
    print('Playlists migration error:', e)

try:
    c.execute('ALTER TABLE playlist_items RENAME TO playlist_items_old')
    c.execute('''CREATE TABLE playlist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist_id INTEGER NOT NULL,
        video_id INTEGER NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
        FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
    )''')
    c.execute('''
        INSERT INTO playlist_items (id, playlist_id, video_id, position)
        SELECT pi.id, pi.playlist_id, v.id, pi.position
        FROM playlist_items_old pi
        JOIN videos v ON pi.video_filename = v.filename
    ''')
    c.execute('DROP TABLE playlist_items_old')
    print('Playlist items migrated.')
except Exception as e:
    print('Playlist items migration error:', e)

conn.commit()
conn.close()
