"""Bounded downloads of public social videos for Telegram's Bot API."""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


# Telegram's hosted Bot API accepts uploads up to 50 MB. Leave room below it.
MAX_VIDEO_BYTES = 45_000_000
MAX_TEMP_BYTES = 90_000_000
MAX_DOWNLOAD_SECONDS = 8 * 60
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class MediaError(Exception):
    """A video could not be downloaded or delivered."""


class MediaTooLarge(MediaError):
    """A selected format exceeded the configured temporary-space budget."""


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
        if video and audio and item.get("ext") == "mp4":
            progressive.append(item)
        elif video and not audio and item.get("ext") == "mp4":
            video_only.append(item)
        elif audio and not video and item.get("ext") in {"m4a", "mp4"}:
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


def _download_one(url: str, format_id: str, directory: Path, deadline: float) -> Path:
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
        if downloaded[filename] > MAX_VIDEO_BYTES or sum(downloaded.values()) > MAX_TEMP_BYTES:
            raise MediaTooLarge("Файл превысил лимит Telegram.")

    options = {
        "format": format_id,
        "outtmpl": str(directory / "video.%(ext)s"),
        "merge_output_format": "mp4",
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
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.extract_info(url, download=True)
    video = directory / "video.mp4"
    if not video.is_file():
        raise MediaError("Видео в формате MP4 недоступно.")
    if video.stat().st_size > MAX_VIDEO_BYTES:
        raise MediaTooLarge("Видео превышает лимит Telegram.")
    return video


def download_video(url: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Download at bounded quality; caller must clean up the returned directory."""
    import deno
    import yt_dlp

    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    metadata_options = {
        "noplaylist": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 1,
        "extractor_retries": 1,
        "socket_timeout": 20,
        "js_runtimes": {"deno": {"path": deno.find_deno_bin()}},
    }
    try:
        with yt_dlp.YoutubeDL(metadata_options) as downloader:
            info = downloader.extract_info(url, download=False)
    except Exception as error:
        raise MediaError("Не удалось открыть видео. Оно может быть приватным или недоступным без входа.") from error
    if not isinstance(info, dict) or info.get("entries") is not None or info.get("is_live"):
        raise MediaError("Поддерживаются только отдельные опубликованные видео, не эфиры и не плейлисты.")
    candidates = format_candidates(info)
    if not candidates:
        raise MediaError("Нет подходящего формата MP4 размером до 45 МБ.")
    last_error: Exception | None = None
    for format_id in candidates[:6]:
        if time.monotonic() > deadline:
            break
        temporary = tempfile.TemporaryDirectory(prefix="assistant-video-")
        try:
            video = _download_one(url, format_id, Path(temporary.name), deadline)
            return temporary, video
        except Exception as error:
            last_error = error
            temporary.cleanup()
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
    temporary, video = download_video(url)
    try:
        send_video(chat_id, message_id, video, token)
    finally:
        temporary.cleanup()
