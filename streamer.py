import subprocess
import os
import signal
import time
import threading
import re
from typing import Optional, List, Dict, Any

FFMPEG_BIN = "ffmpeg"

def get_video_duration(video_path: str) -> float:
    """Probes video duration in seconds using FFmpeg"""
    try:
        cmd = [FFMPEG_BIN, '-i', video_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', res.stderr)
        if m:
            hours, mins, secs = m.groups()
            return int(hours) * 3600 + int(mins) * 60 + float(secs)
    except Exception as e:
        print(f"Error getting video duration: {e}")
    return 60.0

class StreamInstance:
    def __init__(self, stream_id: int, title: str, video_source: str, stream_url: str, stream_key: str, source_type: str, playlist_items: Optional[List[Dict[str, Any]]] = None):
        self.stream_id = stream_id
        self.title = title
        self.video_source = video_source
        self.stream_url = stream_url.strip()
        self.stream_key = stream_key.strip()
        self.source_type = source_type
        self.current_source = os.path.basename(video_source)
        self.playlist_items = playlist_items or []
        self.process: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None
        self.last_error = ""
        self.log_lines: List[str] = []

    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_current_playing_video(self) -> Dict[str, Any]:
        """Calculates which specific video is playing in the 24/7 loop right now"""
        if not self.is_running() or not self.started_at:
            if self.playlist_items:
                return {
                    "item": self.playlist_items[0],
                    "index": 0,
                    "elapsed_in_video": 0,
                    "title": self.playlist_items[0].get("title") or self.playlist_items[0].get("video_filename")
                }
            return {
                "item": None,
                "index": 0,
                "elapsed_in_video": 0,
                "title": self.current_source
            }

        if self.source_type == "video" or not self.playlist_items:
            elapsed = time.time() - self.started_at
            return {
                "item": {"video_filename": self.current_source, "title": self.title},
                "index": 0,
                "elapsed_in_video": int(elapsed),
                "title": self.title or self.current_source
            }

        # Calculate for playlist
        total_duration = sum(item.get("duration_sec", 60.0) or 60.0 for item in self.playlist_items)
        if total_duration <= 0:
            total_duration = len(self.playlist_items) * 60.0

        elapsed_total = (time.time() - self.started_at) % total_duration
        current_offset = 0.0

        for idx, item in enumerate(self.playlist_items):
            item_dur = item.get("duration_sec", 60.0) or 60.0
            if current_offset + item_dur >= elapsed_total:
                time_in_item = elapsed_total - current_offset
                return {
                    "item": item,
                    "index": idx,
                    "elapsed_in_video": int(time_in_item),
                    "duration_sec": int(item_dur),
                    "title": item.get("title") or item.get("video_filename")
                }
            current_offset += item_dur

        return {
            "item": self.playlist_items[0],
            "index": 0,
            "elapsed_in_video": 0,
            "title": self.playlist_items[0].get("title") or self.playlist_items[0].get("video_filename")
        }

class StreamManager:
    def __init__(self):
        self.active_streams: Dict[int, StreamInstance] = {}

    def get_ffmpeg_path(self) -> str:
        return "ffmpeg"

    def is_running(self, stream_id: Optional[int] = None) -> bool:
        if stream_id is not None:
            inst = self.active_streams.get(stream_id)
            return inst.is_running() if inst else False
        return any(inst.is_running() for inst in self.active_streams.values())

    def generate_thumbnail(self, video_path: str, thumbnail_path: str) -> bool:
        """Extract a high-quality frame from the video at 1.0s or 0.0s for thumbnail"""
        ffmpeg_exe = self.get_ffmpeg_path()
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        
        cmd = [
            ffmpeg_exe,
            '-y',
            '-ss', '00:00:01',
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2',
            '-vf', 'scale=480:-1',
            thumbnail_path
        ]
        
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if result.returncode == 0 and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
                return True
        except Exception as e:
            print(f"Error generating thumbnail at 1s: {e}")

        # Fallback to start of video
        cmd[3] = '00:00:00'
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            return result.returncode == 0 and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0
        except Exception as e:
            print(f"Error generating thumbnail at 0s: {e}")
            return False

    def _monitor_output(self, inst: StreamInstance):
        """Monitors FFmpeg output and logs lines for stream diagnostics"""
        log_file = f"ffmpeg_stream_{inst.stream_id}.log"
        try:
            with open(log_file, 'w', encoding='utf-8') as log_f:
                for line in iter(inst.process.stderr.readline, b''):
                    decoded = line.decode('utf-8', errors='replace').strip()
                    if decoded:
                        log_f.write(decoded + '\n')
                        log_f.flush()
                        inst.log_lines.append(decoded)
                        if len(inst.log_lines) > 50:
                            inst.log_lines.pop(0)
                        
                        lower_line = decoded.lower()
                        if "error" in lower_line or "fail" in lower_line or "i/o error" in lower_line or "connection refused" in lower_line:
                            inst.last_error = decoded
        except Exception as e:
            print(f"Log monitor error on stream {inst.stream_id}: {e}")

    def start_stream(self, stream_id: int, video_source: str, stream_url: str, stream_key: str, source_type: str = "video", title: str = "Live Stream", playlist_items: Optional[List[Dict[str, Any]]] = None):
        if self.is_running(stream_id):
            self.stop_stream(stream_id)

        inst = StreamInstance(
            stream_id=stream_id,
            title=title,
            video_source=video_source,
            stream_url=stream_url,
            stream_key=stream_key,
            source_type=source_type,
            playlist_items=playlist_items
        )
        self.active_streams[stream_id] = inst
        inst.started_at = time.time()
        
        clean_url = inst.stream_url.rstrip('/')
        clean_key = inst.stream_key.lstrip('/')
        full_rtmp_url = f"{clean_url}/{clean_key}"
        ffmpeg_exe = self.get_ffmpeg_path()

        video_filters = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"

        if source_type == "playlist" or video_source.endswith('.txt'):
            input_args = [
                '-re',
                '-stream_loop', '-1',
                '-f', 'concat',
                '-safe', '0',
                '-i', video_source
            ]
        else:
            input_args = [
                '-re',
                '-stream_loop', '-1',
                '-i', video_source
            ]

        ffmpeg_cmd = [
            ffmpeg_exe,
            '-nostdin'
        ] + input_args + [
            '-vf', video_filters,
            '-r', '30',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-b:v', '2500k',
            '-maxrate', '3000k',
            '-bufsize', '6000k',
            '-pix_fmt', 'yuv420p',
            '-g', '60',
            '-keyint_min', '60',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'flv',
            '-flvflags', 'no_duration_filesize',
            full_rtmp_url
        ]

        print(f"Starting Stream #{stream_id} ('{title}') to {clean_url}/[HIDDEN_KEY]")
        
        inst.process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )

        t = threading.Thread(target=self._monitor_output, args=(inst,), daemon=True)
        t.start()

    def stop_stream(self, stream_id: int):
        inst = self.active_streams.get(stream_id)
        if inst and inst.process and inst.is_running():
            print(f"Stopping Stream #{stream_id} ('{inst.title}')...")
            try:
                if os.name == 'nt':
                    inst.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    inst.process.terminate()
                inst.process.wait(timeout=4)
            except Exception as e:
                print(f"Error stopping stream #{stream_id}: {e}")
                try:
                    inst.process.kill()
                except Exception:
                    pass
            inst.process = None
            inst.started_at = None

    def stop_all_streams(self):
        for stream_id in list(self.active_streams.keys()):
            self.stop_stream(stream_id)

    def get_status(self, stream_id: Optional[int] = None) -> Dict[str, Any]:
        if stream_id is not None:
            inst = self.active_streams.get(stream_id)
            if not inst:
                return {
                    "stream_id": stream_id,
                    "is_running": False,
                    "uptime_seconds": 0,
                    "last_error": "",
                    "recent_logs": [],
                    "current_playing": None
                }
            running = inst.is_running()
            uptime_seconds = int(time.time() - inst.started_at) if running and inst.started_at else 0
            return {
                "stream_id": stream_id,
                "is_running": running,
                "title": inst.title,
                "stream_url": inst.stream_url,
                "current_source": inst.current_source,
                "source_type": inst.source_type,
                "uptime_seconds": uptime_seconds,
                "last_error": inst.last_error,
                "recent_logs": inst.log_lines[-8:] if inst.log_lines else [],
                "current_playing": inst.get_current_playing_video()
            }
        
        all_statuses = {}
        for s_id, inst in self.active_streams.items():
            running = inst.is_running()
            uptime = int(time.time() - inst.started_at) if running and inst.started_at else 0
            all_statuses[s_id] = {
                "stream_id": s_id,
                "is_running": running,
                "title": inst.title,
                "current_source": inst.current_source,
                "uptime_seconds": uptime,
                "last_error": inst.last_error,
                "recent_logs": inst.log_lines[-8:] if inst.log_lines else [],
                "current_playing": inst.get_current_playing_video()
            }
        return {"streams": all_statuses, "total_running": sum(1 for s in all_statuses.values() if s["is_running"])}

    def create_playlist_file(self, video_paths: List[str], playlist_path: str):
        """Creates a concat playlist file for FFmpeg with absolute escaped paths"""
        os.makedirs(os.path.dirname(playlist_path), exist_ok=True)
        with open(playlist_path, 'w', encoding='utf-8') as f:
            for video in video_paths:
                abs_path = os.path.abspath(video).replace('\\', '/')
                escaped_path = abs_path.replace("'", "'\\''")
                f.write(f"file '{escaped_path}'\n")

# Global singleton instance
stream_manager = StreamManager()
