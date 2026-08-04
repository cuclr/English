from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import app as vocabulary_app
from remote_access import RemoteAccessManager


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RemoteAccessManagerTest(unittest.TestCase):
    def test_password_is_hashed_and_can_be_verified(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "remote_access.json"
            manager = RemoteAccessManager(config_path)
            manager.save_password("safe-password")

            stored = config_path.read_text(encoding="utf-8")
            self.assertNotIn("safe-password", stored)
            self.assertTrue(manager.verify_password("safe-password"))
            self.assertFalse(manager.verify_password("wrong-password"))

    def test_short_password_is_rejected(self):
        with TemporaryDirectory() as directory:
            manager = RemoteAccessManager(Path(directory) / "remote_access.json")
            with self.assertRaises(ValueError):
                manager.save_password("short")


class RemoteLoginTest(unittest.TestCase):
    def test_protected_page_requires_login_and_remembers_success(self):
        with TemporaryDirectory() as directory:
            manager = RemoteAccessManager(Path(directory) / "remote_access.json")
            manager.save_password("safe-password")
            vocabulary_app.app.config.update(
                TESTING=True,
                FORCE_REMOTE_AUTH=True,
                SECRET_KEY=manager.secret_key(),
            )
            try:
                with patch.object(vocabulary_app, "remote_access_manager", manager):
                    client = vocabulary_app.app.test_client()
                    response = client.get("/")
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("/login", response.location)

                    response = client.post(
                        "/login",
                        data={"password": "safe-password", "next": "/settings"},
                    )
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith("/settings"))
                    self.assertEqual(client.get("/").status_code, 200)
            finally:
                vocabulary_app.app.config["FORCE_REMOTE_AUTH"] = False

    def test_external_redirect_is_not_allowed(self):
        with TemporaryDirectory() as directory:
            manager = RemoteAccessManager(Path(directory) / "remote_access.json")
            manager.save_password("safe-password")
            vocabulary_app.app.config.update(
                TESTING=True,
                FORCE_REMOTE_AUTH=True,
                SECRET_KEY=manager.secret_key(),
            )
            try:
                with patch.object(vocabulary_app, "remote_access_manager", manager):
                    response = vocabulary_app.app.test_client().post(
                        "/login",
                        data={
                            "password": "safe-password",
                            "next": "https://example.com",
                        },
                    )
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith("/"))
            finally:
                vocabulary_app.app.config["FORCE_REMOTE_AUTH"] = False


class RemoteAccessScriptTest(unittest.TestCase):
    def test_funnel_targets_localhost_and_never_opens_flask_to_lan(self):
        setup = (PROJECT_DIR / "setup_remote_access.bat").read_text(encoding="utf-8")
        app_source = (PROJECT_DIR / "app.py").read_text(encoding="utf-8")

        self.assertIn("funnel --bg http://127.0.0.1:5000", setup)
        self.assertNotIn("0.0.0.0", setup)
        self.assertIn('host="127.0.0.1"', app_source)
        self.assertNotIn('host="0.0.0.0"', app_source)

    def test_mobile_layout_has_touch_friendly_controls(self):
        css = (PROJECT_DIR / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 480px)", css)
        self.assertIn(".review-actions button { min-height: 50px; }", css)
        self.assertIn(".login-page", css)


if __name__ == "__main__":
    unittest.main()
