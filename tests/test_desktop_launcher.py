import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import app as vocabulary_app
import browser_launcher


PROJECT_DIR = Path(__file__).resolve().parents[1]


class DesktopLauncherTest(unittest.TestCase):
    def test_health_endpoint_identifies_the_app(self):
        vocabulary_app.app.config.update(TESTING=True)
        response = vocabulary_app.app.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"app": "english-vocabulary", "status": "ok"},
        )

    def test_launcher_uses_localhost_and_has_clear_errors(self):
        batch = (PROJECT_DIR / "start_app.bat").read_text(encoding="utf-8")
        app_source = (PROJECT_DIR / "app.py").read_text(encoding="utf-8")

        self.assertIn("http://127.0.0.1:5000", batch)
        self.assertIn("title English Vocabulary", batch)
        self.assertIn('host="127.0.0.1"', app_source)
        self.assertNotIn('host="0.0.0.0"', app_source)
        self.assertIn("未找到虚拟环境", batch)
        self.assertIn("未找到 app.py", batch)
        self.assertIn("Python 环境异常", batch)

    def test_ready_check_requires_the_expected_app(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"app": "english-vocabulary", "status": "ok"}
        ).encode("utf-8")

        with patch("browser_launcher.urlopen", return_value=response):
            self.assertTrue(browser_launcher.is_app_ready("http://127.0.0.1:5000"))

        response.__enter__.return_value.read.return_value = b'{"status":"ok"}'
        with patch("browser_launcher.urlopen", return_value=response):
            self.assertFalse(browser_launcher.is_app_ready("http://127.0.0.1:5000"))

    def test_browser_opens_only_after_server_is_ready(self):
        with (
            patch.object(sys, "argv", ["browser_launcher.py", "http://127.0.0.1:5000"]),
            patch("browser_launcher.wait_for_app", return_value=True),
            patch("browser_launcher.webbrowser.open") as open_browser,
        ):
            exit_code = browser_launcher.main()

        self.assertEqual(exit_code, 0)
        open_browser.assert_called_once_with("http://127.0.0.1:5000", new=2)


if __name__ == "__main__":
    unittest.main()
