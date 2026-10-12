import tempfile
import unittest
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

        def fake_download(_url, format_id, directory, _deadline):
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
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "queue_telegram_media", return_value=True) as queued, patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(telegram_update("Смотри https://youtu.be/abc123"))
            self.assertEqual(result["handled"], "media_queued")
            self.assertEqual(queued.call_args.args[2], "https://youtu.be/abc123")
            complete.assert_not_called()

    def test_webhook_queues_link_from_caption(self):
        update = telegram_update("ignored")
        update["message"].pop("text")
        update["message"]["caption"] = "https://www.instagram.com/reel/abc123/"
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "queue_telegram_media", return_value=True) as queued:
            result = server.handle_telegram_update(update)
        self.assertEqual(result["handled"], "media_queued")
        self.assertEqual(queued.call_args.args[2], "https://www.instagram.com/reel/abc123/")


if __name__ == "__main__":
    unittest.main()
