"""Password configuration and verification for remote browser access."""

from __future__ import annotations

import argparse
from getpass import getpass
import hashlib
import hmac
import json
from pathlib import Path
import secrets


PBKDF2_ITERATIONS = 600_000


class RemoteAccessManager:
    """Store remote-access credentials outside the source-controlled files."""

    def __init__(self, config_path: Path):
        self.config_path = config_path

    def load_config(self) -> dict | None:
        if not self.config_path.is_file():
            return None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        required = {"salt", "password_hash", "secret_key"}
        return data if required.issubset(data) else None

    def is_configured(self) -> bool:
        return self.load_config() is not None

    def secret_key(self) -> str:
        config = self.load_config()
        return config["secret_key"] if config else secrets.token_hex(32)

    def verify_password(self, password: str) -> bool:
        config = self.load_config()
        if config is None:
            return False
        candidate = self._hash_password(
            password,
            bytes.fromhex(config["salt"]),
            int(config.get("iterations", PBKDF2_ITERATIONS)),
        )
        return hmac.compare_digest(candidate, config["password_hash"])

    def save_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("访问密码至少需要 8 个字符。")
        salt = secrets.token_bytes(16)
        current = self.load_config() or {}
        config = {
            "salt": salt.hex(),
            "password_hash": self._hash_password(
                password, salt, PBKDF2_ITERATIONS
            ),
            "iterations": PBKDF2_ITERATIONS,
            "secret_key": current.get("secret_key", secrets.token_hex(32)),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _hash_password(password: str, salt: bytes, iterations: int) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        ).hex()


def configure_password(manager: RemoteAccessManager, replace: bool = False) -> int:
    if manager.is_configured() and not replace:
        print("远程访问密码已经配置。如需更换，请运行：")
        print(r".venv\Scripts\python.exe remote_access.py password")
        return 0

    password = getpass("请设置远程访问密码（至少 8 个字符）: ")
    confirmation = getpass("请再次输入密码: ")
    if password != confirmation:
        print("两次输入的密码不一致，未保存任何修改。")
        return 1
    try:
        manager.save_password(password)
    except ValueError as exc:
        print(exc)
        return 1
    print("远程访问密码已安全保存。")
    return 0


def configure_password_gui(
    manager: RemoteAccessManager, replace: bool = False
) -> int:
    """Show a small masked-input window for non-technical Windows users."""
    if manager.is_configured() and not replace:
        print("远程访问密码已经配置。")
        return 0

    import tkinter as tk
    from tkinter import messagebox

    result = {"exit_code": 1}
    window = tk.Tk()
    window.title("设置远程访问密码")
    window.resizable(False, False)
    window.attributes("-topmost", True)

    frame = tk.Frame(window, padx=24, pady=22)
    frame.pack()
    tk.Label(frame, text="设置手机访问密码", font=("Microsoft YaHei UI", 14, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
    )
    tk.Label(frame, text="至少 8 个字符，密码只保存在本机。", fg="#58645b").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 18)
    )
    tk.Label(frame, text="访问密码").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
    password_entry = tk.Entry(frame, width=28, show="●")
    password_entry.grid(row=2, column=1, pady=6)
    tk.Label(frame, text="再次输入").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)
    confirmation_entry = tk.Entry(frame, width=28, show="●")
    confirmation_entry.grid(row=3, column=1, pady=6)

    def save() -> None:
        password = password_entry.get()
        if password != confirmation_entry.get():
            messagebox.showerror("无法保存", "两次输入的密码不一致。", parent=window)
            return
        try:
            manager.save_password(password)
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=window)
            return
        result["exit_code"] = 0
        messagebox.showinfo("设置完成", "远程访问密码已安全保存。", parent=window)
        window.destroy()

    save_button = tk.Button(frame, text="保存密码", width=18, command=save)
    save_button.grid(row=4, column=0, columnspan=2, pady=(18, 0))
    window.bind("<Return>", lambda _event: save())
    password_entry.focus_set()
    window.eval("tk::PlaceWindow . center")
    window.mainloop()
    return result["exit_code"]


def main() -> int:
    parser = argparse.ArgumentParser(description="管理背单词应用的远程访问密码")
    parser.add_argument(
        "command",
        choices=("ensure", "password", "ensure-gui", "password-gui"),
        help="初始化或更换密码",
    )
    args = parser.parse_args()
    manager = RemoteAccessManager(
        Path(__file__).resolve().parent / "instance" / "remote_access.json"
    )
    if args.command.endswith("-gui"):
        return configure_password_gui(
            manager, replace=args.command == "password-gui"
        )
    return configure_password(manager, replace=args.command == "password")


if __name__ == "__main__":
    raise SystemExit(main())
