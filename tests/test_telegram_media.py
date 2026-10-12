import tempfile
import unittest
import base64
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import simple_server as server
from app import telegram_media
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramMediaTests(unittest.TestCase):
    def test_only_supported_single_video_urls_are_accepted(self):
        examples = [
            "https://www.youtube.com/watch?v=abc123",
            "https://youtube.com/shorts/abc123?feature=share",
            "https://youtu.be/abc123",
            "https://www.instagram.com/reel/abc123/",
            "https://vm.tiktok.com/abc123/",
            "https://www.tiktok.com/@creator/video/123456",
        ]
        for url in examples:
            with self.subTest(url=url):
                self.assertEqual(telegram_media.find_video_url(f"Посмотри {url}!"), url)
        for url in (
            "https://youtube.com/playlist?list=abc",
            "https://youtube.com.evil.example/watch?v=abc",
            "http://127.0.0.1/watch?v=abc",
            "https://www.instagram.com/creator/",
            "https://www.tiktok.com/@creator",
        ):
            with self.subTest(url=url):
                self.assertIsNone(telegram_media.find_video_url(url))

    def test_format_selection_reduces_quality_before_download(self):
        info = {"duration": 120, "formats": [
            {"format_id": "1080", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "mp4a", "filesize": 70_000_000},
            {"format_id": "720", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "filesize_approx": 39_000_000},
            {"format_id": "360", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 12_000_000},
            {"format_id": "unknown-4k", "ext": "mp4", "height": 2160, "vcodec": "avc1", "acodec": "mp4a"},
        ]}
        self.assertEqual(telegram_media.format_candidates(info), ["720", "360"])

    def test_merged_video_size_is_estimated_with_audio(self):
        info = {"duration": 60, "formats": [
            {"format_id": "video", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 40_000_000},
            {"format_id": "audio", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 8_000_000},
        ]}
        self.assertEqual(telegram_media.format_candidates(info), [])
        info["formats"][1]["filesize"] = 3_000_000
        self.assertEqual(telegram_media.format_candidates(info), ["video+audio"])

    def test_non_mp4_source_is_transcoded_to_telegram_mp4(self):
        import imageio_ffmpeg

        info = {"duration": 2, "formats": [{
            "format_id": "webm", "ext": "webm", "height": 360,
            "vcodec": "vp9", "acodec": "opus", "filesize": 1_000_000,
        }]}
        self.assertEqual(telegram_media.format_candidates(info), [])
        self.assertEqual(telegram_media.transcode_candidates(info), ["webm"])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "video.webm"
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
                "-c:v", "libvpx", "-y", str(source),
            ], check=True, timeout=30)
            with patch.object(telegram_media, "stop_pot_provider"):
                output = telegram_media._transcode(source, 2, time.monotonic() + 60)
            self.assertEqual(output.suffix, ".mp4")
            self.assertGreater(output.stat().st_size, 0)
            self.assertLessEqual(output.stat().st_size, telegram_media.MAX_VIDEO_BYTES)

    def test_download_retries_lower_quality_when_actual_size_is_too_large(self):
        info = {"formats": [
            {"format_id": "720", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "filesize_approx": 40_000_000},
            {"format_id": "360", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize_approx": 10_000_000},
        ]}

        class FakeDownloader:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, _url, download=False):
                self_outer.assertFalse(download)
                return info

        self_outer = self
        attempted = []

        def fake_download(_url, format_id, directory, _deadline, _cookie_file, _extra_options):
            attempted.append(format_id)
            if format_id == "720":
                raise telegram_media.MediaTooLarge("oversized")
            video = directory / "video.mp4"
            video.write_bytes(b"video")
            return video

        with patch("yt_dlp.YoutubeDL", FakeDownloader), patch.object(telegram_media, "_download_one", side_effect=fake_download):
            temporary, video = telegram_media.download_video("https://youtu.be/abc123")
        try:
            self.assertEqual(attempted, ["720", "360"])
            self.assertEqual(video.read_bytes(), b"video")
        finally:
            temporary.cleanup()

    def test_download_uses_transcoder_when_only_large_source_exists(self):
        info = {"duration": 120, "formats": [{
            "format_id": "large", "ext": "webm", "height": 720,
            "vcodec": "vp9", "acodec": "opus", "filesize": 80_000_000,
        }]}

        class FakeDownloader:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, _url, download=False):
                return info

        def fake_download(_url, format_id, directory, _deadline, _cookie_file, _extra_options, max_bytes):
            self.assertEqual(format_id, "large")
            self.assertGreater(max_bytes, 80_000_000)
            source = directory / "video.webm"
            source.write_bytes(b"source")
            return source

        def fake_transcode(source, duration, _deadline):
            self.assertEqual(duration, 120)
            output = source.parent / "compressed.mp4"
            output.write_bytes(b"compressed")
            return output

        with patch("yt_dlp.YoutubeDL", FakeDownloader), patch.object(telegram_media, "_download_one", side_effect=fake_download), patch.object(telegram_media, "_transcode", side_effect=fake_transcode):
            temporary, video = telegram_media.download_video("https://youtu.be/abc123")
        try:
            self.assertEqual(video.read_bytes(), b"compressed")
        finally:
            temporary.cleanup()

    def test_youtube_retries_with_pot_provider_after_metadata_failure(self):
        info = {"formats": [{
            "format_id": "small", "ext": "mp4", "height": 360,
            "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000,
        }]}
        profiles = []

        class FakeDownloader:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, _url, download=False):
                profiles.append(self.options.get("extractor_args"))
                if len(profiles) == 1:
                    raise RuntimeError("Sign in to confirm you're not a bot")
                return info

        def fake_download(_url, _format_id, directory, _deadline, _cookie_file, extra_options):
            self.assertIn("extractor_args", extra_options)
            video = directory / "video.mp4"
            video.write_bytes(b"video")
            return video

        with patch("yt_dlp.YoutubeDL", FakeDownloader), patch.object(telegram_media, "ensure_pot_provider", return_value=True), patch.object(telegram_media, "_download_one", side_effect=fake_download):
            temporary, video = telegram_media.download_video("https://youtube.com/shorts/abc")
        try:
            self.assertEqual(profiles[0], None)
            self.assertEqual(profiles[1]["youtube"]["player_client"][0], "mweb")
            self.assertEqual(video.read_bytes(), b"video")
        finally:
            temporary.cleanup()

    def test_multipart_upload_streams_file_in_chunks(self):
        class FakeResponse:
            status = 200

            def read(self, _limit):
                return b'{"ok":true}'

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                self.chunks = []
                self.headers = {}

            def putrequest(self, method, path):
                self.request = (method, path)

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def send(self, chunk):
                self.chunks.append(len(chunk))

            def getresponse(self):
                return FakeResponse()

            def close(self):
                pass

        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"x" * (1024 * 1024))
            with patch.object(telegram_media.http.client, "HTTPSConnection", return_value=connection):
                telegram_media.send_video(555, 10, video, "test-token")
        self.assertEqual(connection.request, ("POST", "/bottest-token/sendVideo"))
        self.assertEqual(sum(connection.chunks), int(connection.headers["Content-Length"]))
        self.assertLessEqual(max(connection.chunks), 64 * 1024)

    def test_webhook_queues_link_without_calling_model(self):
        with isolated_storage(), patch.dict("os.environ", {"TG_VIDEO_DOWNLOAD_ENABLED": "true"}), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "queue_telegram_media", return_value=True) as queued, patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(telegram_update("Смотри https://youtu.be/abc123"))
            self.assertEqual(result["handled"], "media_queued")
            self.assertEqual(queued.call_args.args[2], "https://youtu.be/abc123")
            complete.assert_not_called()

    def test_webhook_queues_link_from_caption(self):
        update = telegram_update("ignored")
        update["message"].pop("text")
        update["message"]["caption"] = "https://www.instagram.com/reel/abc123/"
        with isolated_storage(), patch.dict("os.environ", {"TG_VIDEO_DOWNLOAD_ENABLED": "true"}), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "queue_telegram_media", return_value=True) as queued:
            result = server.handle_telegram_update(update)
        self.assertEqual(result["handled"], "media_queued")
        self.assertEqual(queued.call_args.args[2], "https://www.instagram.com/reel/abc123/")

    def test_webhook_ignores_video_links_while_downloads_are_disabled(self):
        with isolated_storage(), patch.dict("os.environ", {"TG_VIDEO_DOWNLOAD_ENABLED": "false"}), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "queue_telegram_media") as queued, patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(telegram_update("https://www.tiktok.com/@creator/video/123456"))
        self.assertEqual(result["reason"], "media_disabled")
        queued.assert_not_called()
        complete.assert_not_called()

    def test_unaddressed_message_from_another_bot_is_ignored(self):
        update = telegram_update("Видео скачано")
        update["message"]["from"] = {"id": 1234, "is_bot": True, "username": "OtherDownloader"}
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_BOT_TO_BOT", True), patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(update)
        self.assertEqual(result["reason"], "unaddressed_bot_message")
        complete.assert_not_called()

    def test_youtube_bot_check_has_specific_error(self):
        for raw_error in ("Sign in to confirm you're not a bot", "Failed to extract any player response"):
            with self.subTest(raw_error=raw_error):
                error = RuntimeError(raw_error)
                message = str(telegram_media._source_error("https://youtube.com/shorts/abc", error))
                self.assertIn("Render", message)
                self.assertNotIn("приватным", message)

    def test_cookie_file_is_temporary(self):
        cookies = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n"
        with patch.dict("os.environ", {"YT_DLP_COOKIES_B64": base64.b64encode(cookies).decode()}):
            with telegram_media._cookie_file() as path:
                self.assertEqual(path.read_bytes(), cookies)
            self.assertFalse(path.exists())

    def test_status_message_is_deleted_after_video(self):
        class OneItemQueue:
            def __init__(self):
                self.called = False
                self.done = False

            def get(self):
                if self.called:
                    raise StopIteration
                self.called = True
                return 555, 10, "https://youtu.be/abc", "test-chat"

            def task_done(self):
                self.done = True

        queue = OneItemQueue()
        with isolated_storage(), patch.object(server, "TELEGRAM_MEDIA_QUEUE", queue), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "download_and_send") as download, patch("app.telegram_moderation.api_call", side_effect=[{"message_id": 42}, True]) as api:
            with self.assertRaises(StopIteration):
                server.telegram_media_worker()
        download.assert_called_once()
        self.assertEqual(api.call_args_list[0].args[1], "sendMessage")
        self.assertEqual(api.call_args_list[1].args[1], "deleteMessage")
        self.assertEqual(api.call_args_list[1].args[2]["message_id"], 42)
        self.assertTrue(queue.done)

    def test_status_message_is_deleted_after_download_error(self):
        class OneItemQueue:
            def __init__(self):
                self.called = False

            def get(self):
                if self.called:
                    raise StopIteration
                self.called = True
                return 555, 10, "https://youtu.be/abc", "test-chat"

            def task_done(self):
                pass

        with patch.object(server, "TELEGRAM_MEDIA_QUEUE", OneItemQueue()), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "download_and_send", side_effect=telegram_media.MediaError("ошибка")), patch.object(server, "try_send_telegram_message") as sent, patch("app.telegram_moderation.api_call", side_effect=[{"message_id": 42}, True]) as api:
            with self.assertRaises(StopIteration):
                server.telegram_media_worker()
        sent.assert_called_once_with(555, "ошибка")
        self.assertEqual(api.call_args_list[1].args[1], "deleteMessage")


if __name__ == "__main__":
    unittest.main()
