"""Bounded downloads of public social videos for Telegram's Bot API."""

from __future__ import annotations

import http.client
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.pot_provider import ensure_started as ensure_pot_provider, stop_if_owned as stop_pot_provider


# Telegram's hosted Bot API accepts uploads up to 50 MB. Leave room below it.
MAX_VIDEO_BYTES = 45_000_000
MAX_SOURCE_BYTES = 240_000_000
MIN_FREE_DISK_BYTES = 80_000_000
MAX_DOWNLOAD_SECONDS = 8 * 60
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class MediaError(Exception):
    """A video could not be downloaded or delivered."""


class MediaTooLarge(MediaError):
    """A selected format exceeded the configured temporary-space budget."""


@contextmanager
def _cookie_file():
    """Keep optional yt-dlp cookies out of the repository and remove them after use."""
    encoded = os.getenv("YT_DLP_COOKIES_B64", "").strip()
    if not encoded:
        yield None
        return
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise MediaError("Неверная настройка cookies для загрузчика видео.") from error
    if not data.startswith((b"# Netscape HTTP Cookie File", b"# HTTP Cookie File")):
        raise MediaError("Неверный формат cookies для загрузчика видео.")
    with tempfile.TemporaryDirectory(prefix="assistant-cookies-") as directory:
        path = Path(directory) / "cookies.txt"
        path.write_bytes(data)
        path.chmod(0o600)
        yield path


def _source_error(url: str, error: Exception) -> MediaError:
    message = str(error).lower()
    if "youtube.com" in url or "youtu.be" in url:
        if "sign in to confirm" in message or "not a bot" in message or "failed to extract any player response" in message:
            return MediaError("YouTube не отдал данные ролика серверу бота. Возможно, запросы с Render ограничены проверкой доступа.")
    return MediaError("Не удалось открыть видео. Оно может быть приватным или недоступным без входа.")


def _pot_options(url: str) -> dict[str, object]:
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        return {}
    if not ensure_pot_provider():
        return {}
    return {"extractor_args": {"youtube": {"player_client": ["mweb", "web_safari", "android_vr"]}}}


def find_video_url(text: str) -> str | None:
    """Return the first supported single-video URL in a Telegram message."""
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,!?;:)]}>»")
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError:
            continue
        path = parts.path.lower()
        if parts.username or parts.password or port not in (None, 80, 443):
            continue
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if (path == "/watch" and parse_qs(parts.query).get("v")) or path.startswith(("/shorts/", "/live/")):
                return url
        elif host == "youtu.be" and path.strip("/"):
            return url
        elif host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            if path.startswith(("/reel/", "/reels/", "/p/", "/tv/")):
                return url
        elif host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}:
            if "/video/" in path or path.startswith("/t/") or host in {"vm.tiktok.com", "vt.tiktok.com"}:
                return url
    return None


def _size(format_info: dict[str, object], duration: object) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = format_info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    bitrate = format_info.get("tbr") or format_info.get("vbr") or format_info.get("abr")
    if isinstance(bitrate, (int, float)) and bitrate > 0 and isinstance(duration, (int, float)) and duration > 0:
        return int(duration * bitrate * 1000 / 8)
    return None


def format_candidates(info: dict[str, object], max_bytes: int = MAX_VIDEO_BYTES) -> list[str]:
    """Prefer the highest MP4 resolution expected to fit, then lower formats."""
    formats = [item for item in (info.get("formats") or []) if isinstance(item, dict)]
    duration = info.get("duration")
    progressive = []
    video_only = []
    audio_only = []
    for item in formats:
        format_id = str(item.get("format_id") or "")
        if not format_id or item.get("has_drm"):
            continue
        video = item.get("vcodec") not in (None, "none")
        audio = item.get("acodec") not in (None, "none")
        compatible_video = str(item.get("vcodec") or "").startswith("avc1")
        compatible_audio = str(item.get("acodec") or "").startswith("mp4a")
        if video and audio and item.get("ext") == "mp4" and compatible_video and compatible_audio:
            progressive.append(item)
        elif video and not audio and item.get("ext") == "mp4" and compatible_video:
            video_only.append(item)
        elif audio and not video and item.get("ext") in {"m4a", "mp4"} and compatible_audio:
            audio_only.append(item)

    # A modest audio stream leaves the most space for video.
    audio_only.sort(key=lambda item: (abs(float(item.get("abr") or 128) - 128), str(item.get("format_id"))))
    choices: list[tuple[int, int, str]] = []
    for video in progressive:
        size = _size(video, duration)
        if (size is not None and size > max_bytes) or (size is None and int(video.get("height") or 0) > 720):
            continue
        height = int(video.get("height") or 0)
        choices.append((height, 1 if size is not None else 0, str(video["format_id"])))
    for video in video_only:
        for audio in audio_only[:2]:
            video_size = _size(video, duration)
            audio_size = _size(audio, duration)
            size = video_size + audio_size if video_size is not None and audio_size is not None else None
            if (size is not None and size > max_bytes) or (size is None and int(video.get("height") or 0) > 720):
                continue
            height = int(video.get("height") or 0)
            choices.append((height, 1 if size is not None else 0, f"{video['format_id']}+{audio['format_id']}"))
    # Unknown sizes are attempted conservatively; a progress hook enforces the real cap.
    choices.sort(key=lambda choice: (choice[0], choice[1]), reverse=True)
    return list(dict.fromkeys(choice[2] for choice in choices))


def transcode_candidates(info: dict[str, object]) -> list[str]:
    """Choose bounded source streams when a ready-to-send MP4 is unavailable."""
    formats = [item for item in (info.get("formats") or []) if isinstance(item, dict)]
    duration = info.get("duration")
    videos = [item for item in formats if item.get("format_id") and not item.get("has_drm")
              and item.get("vcodec") not in (None, "none")]
    audios = [item for item in formats if item.get("format_id") and not item.get("has_drm")
              and item.get("vcodec") in (None, "none") and item.get("acodec") not in (None, "none")]
    audios.sort(key=lambda item: _size(item, duration) or MAX_SOURCE_BYTES)
    choices = []
    for video in videos:
        if int(video.get("height") or 0) > 1080:
            continue
        audio = None if video.get("acodec") not in (None, "none") else (audios[0] if audios else None)
        if video.get("acodec") in (None, "none") and audio is None:
            continue
        size = _size(video, duration)
        audio_size = _size(audio, duration) if audio else 0
        if size is not None and audio_size is not None and size + audio_size > MAX_SOURCE_BYTES:
            continue
        selector = str(video["format_id"])
        if audio:
            selector += f"+{audio['format_id']}"
        choices.append((int(video.get("height") or 0), size is not None, selector))
    choices.sort(reverse=True)
    return list(dict.fromkeys(choice[2] for choice in choices))


def _download_one(
    url: str, format_id: str, directory: Path, deadline: float,
    cookie_file: Path | None = None, extra_options: dict[str, object] | None = None,
    max_bytes: int = MAX_VIDEO_BYTES,
) -> Path:
    import deno
    import imageio_ffmpeg
    import yt_dlp

    downloaded: dict[str, int] = {}

    def progress(status: dict[str, object]) -> None:
        if time.monotonic() > deadline:
            raise MediaError("Время загрузки видео истекло.")
        if status.get("status") != "downloading":
            return
        filename = str(status.get("filename") or "current")
        downloaded[filename] = int(status.get("downloaded_bytes") or 0)
        if downloaded[filename] > max_bytes or sum(downloaded.values()) > max_bytes:
            raise MediaTooLarge("Исходный файл слишком велик для обработки на сервере.")

    options = {
        "format": format_id,
        "outtmpl": str(directory / "video.%(ext)s"),
        "merge_output_format": "mkv" if max_bytes > MAX_VIDEO_BYTES else "mp4",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "socket_timeout": 20,
        "progress_hooks": [progress],
    }
    options.update(extra_options or {})
    if cookie_file is not None:
        options["cookiefile"] = str(cookie_file)
    proxy = os.getenv("YT_DLP_PROXY_URL", "").strip()
    if proxy:
        options["proxy"] = proxy
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.extract_info(url, download=True)
    outputs = [path for path in directory.glob("video.*") if path.is_file() and path.suffix not in {".part", ".ytdl"}]
    if len(outputs) != 1:
        raise MediaError("Загрузка видео не завершилась.")
    video = outputs[0]
    if video.stat().st_size > max_bytes:
        raise MediaTooLarge("Исходный файл слишком велик для обработки на сервере.")
    return video


def _transcode(source: Path, duration: object, deadline: float) -> Path:
    """Stream a source file through FFmpeg into a Telegram-sized H.264/AAC MP4."""
    import imageio_ffmpeg

    if not isinstance(duration, (int, float)) or duration <= 0:
        raise MediaError("Не удалось определить длительность видео для сжатия.")
    if duration > 60 * 60:
        raise MediaTooLarge("Видео слишком длинное для обработки на сервере.")
    stop_pot_provider()  # Release its Deno process before FFmpeg uses memory.
    output = source.parent / "compressed.mp4"
    remaining = max(1, int(deadline - time.monotonic()))
    if remaining <= 1:
        raise MediaError("Время обработки видео истекло.")
    audio_bitrate = 96_000
    for ratio in (0.90, 0.72):
        output.unlink(missing_ok=True)
        target_bits = int(MAX_VIDEO_BYTES * ratio * 8 / duration)
        video_bitrate = max(80_000, target_bits - audio_bitrate)
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
            "-b:v", str(video_bitrate), "-maxrate", str(video_bitrate), "-bufsize", str(video_bitrate * 2),
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-y", str(output),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=max(1, int(deadline - time.monotonic())))
        except subprocess.TimeoutExpired as error:
            output.unlink(missing_ok=True)
            raise MediaError("Время сжатия видео истекло.") from error
        if result.returncode != 0:
            output.unlink(missing_ok=True)
            raise MediaError("Не удалось преобразовать видео в MP4.")
        if output.stat().st_size <= MAX_VIDEO_BYTES:
            return output
    output.unlink(missing_ok=True)
    raise MediaTooLarge("Видео не удалось сжать до размера, допустимого Telegram.")


def download_video(url: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Download at bounded quality; caller must clean up the returned directory."""
    import deno
    import yt_dlp

    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    last_error: Exception | None = None
    with _cookie_file() as cookie_file:
        for attempt in range(2):
            extra_options = {} if attempt == 0 else _pot_options(url)
            if attempt == 1 and not extra_options:
                break
            metadata_options = {
                "noplaylist": True,
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "retries": 0,
                "extractor_retries": 0,
                "socket_timeout": 12,
                "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
                **extra_options,
            }
            if cookie_file is not None:
                metadata_options["cookiefile"] = str(cookie_file)
            proxy = os.getenv("YT_DLP_PROXY_URL", "").strip()
            if proxy:
                metadata_options["proxy"] = proxy
            try:
                with yt_dlp.YoutubeDL(metadata_options) as downloader:
                    info = downloader.extract_info(url, download=False)
            except Exception as error:
                last_error = _source_error(url, error)
                continue
            if not isinstance(info, dict) or info.get("entries") is not None or info.get("is_live"):
                raise MediaError("Поддерживаются только отдельные опубликованные видео, не эфиры и не плейлисты.")
            candidates = format_candidates(info)
            for format_id in candidates[:6 if attempt == 0 else 3]:
                if time.monotonic() > deadline:
                    break
                temporary = tempfile.TemporaryDirectory(prefix="assistant-video-")
                try:
                    video = _download_one(url, format_id, Path(temporary.name), deadline, cookie_file, extra_options)
                    return temporary, video
                except Exception as error:
                    last_error = error
                    temporary.cleanup()
            # Keep both the source and output on disk with space to spare for merging.
            free_disk = shutil.disk_usage(tempfile.gettempdir()).free
            source_limit = min(MAX_SOURCE_BYTES, (free_disk - MIN_FREE_DISK_BYTES - MAX_VIDEO_BYTES) // 2)
            if source_limit <= MAX_VIDEO_BYTES:
                last_error = MediaTooLarge("На сервере недостаточно места для сжатия видео.")
                continue
            for format_id in transcode_candidates(info)[:2]:
                if time.monotonic() > deadline:
                    break
                temporary = tempfile.TemporaryDirectory(prefix="assistant-video-")
                try:
                    source = _download_one(url, format_id, Path(temporary.name), deadline, cookie_file, extra_options, source_limit)
                    video = _transcode(source, info.get("duration"), deadline)
                    return temporary, video
                except Exception as error:
                    last_error = error
                    temporary.cleanup()
            if time.monotonic() > deadline:
                break
    if isinstance(last_error, MediaError):
        raise last_error
    raise MediaError("Не удалось скачать видео в пределах 45 МБ. Попробуй более короткий ролик.") from last_error


def send_video(chat_id: object, message_id: object | None, video: Path, token: str) -> None:
    """Stream multipart upload in small chunks instead of buffering the video in RAM."""
    size = video.stat().st_size
    if size > MAX_VIDEO_BYTES:
        raise MediaTooLarge("Видео превышает лимит Telegram.")
    boundary = f"assistant-{uuid.uuid4().hex}"
    fields: dict[str, str] = {"chat_id": str(chat_id), "supports_streaming": "true"}
    if message_id is not None:
        fields["reply_parameters"] = json.dumps({"message_id": message_id, "allow_sending_without_reply": True})
    prefix = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        for name, value in fields.items()
    )
    prefix += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"video.mp4\"\r\n"
        "Content-Type: video/mp4\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    connection = http.client.HTTPSConnection("api.telegram.org", timeout=120)
    try:
        connection.putrequest("POST", f"/bot{token}/sendVideo")
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(len(prefix) + size + len(suffix)))
        connection.endheaders()
        connection.send(prefix)
        with video.open("rb") as source:
            while chunk := source.read(64 * 1024):
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        payload = response.read(64 * 1024)
        if response.status != 200 or not json.loads(payload).get("ok"):
            raise MediaError("Telegram не принял видео.")
    except (OSError, ValueError) as error:
        raise MediaError("Не удалось отправить видео в Telegram.") from error
    finally:
        connection.close()


def download_and_send(url: str, chat_id: object, message_id: object | None, token: str) -> None:
    try:
        temporary, video = download_video(url)
    finally:
        stop_pot_provider()
    try:
        send_video(chat_id, message_id, video, token)
    finally:
        temporary.cleanup()
