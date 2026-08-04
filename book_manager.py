"""Discover local PDF vocabulary books and persist the active selection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


class BookManagerError(RuntimeError):
    """Raised when a requested local vocabulary book cannot be selected."""


@dataclass(frozen=True)
class BookInfo:
    key: str
    name: str
    path: Path


class BookManager:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.books_dir = self.project_dir / "books"
        self.config_path = self.project_dir / "instance" / "book_config.json"

    def list_books(self) -> list[BookInfo]:
        """List PDFs from the project root and the dedicated books folder."""
        self.books_dir.mkdir(exist_ok=True)
        paths = [*self.project_dir.glob("*.pdf"), *self.books_dir.glob("*.pdf")]
        unique_paths = sorted({path.resolve() for path in paths}, key=lambda p: p.name.lower())
        return [self._to_book_info(path) for path in unique_paths if path.is_file()]

    def active_book(self) -> BookInfo | None:
        books = self.list_books()
        if not books:
            return None

        selected_key = self._read_selected_key()
        selected = next((book for book in books if book.key == selected_key), None)
        if selected is not None:
            return selected

        selected = books[0]
        self._write_selected_key(selected.key)
        return selected

    def select_book(self, key: str) -> BookInfo:
        selected = next((book for book in self.list_books() if book.key == key), None)
        if selected is None:
            raise BookManagerError("所选词书不存在，请刷新页面后重试。")
        self._write_selected_key(selected.key)
        return selected

    def _to_book_info(self, path: Path) -> BookInfo:
        relative_path = path.relative_to(self.project_dir).as_posix()
        return BookInfo(key=relative_path, name=path.stem, path=path)

    def _read_selected_key(self) -> str | None:
        if not self.config_path.is_file():
            return None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("active_book")
        return value if isinstance(value, str) else None

    def _write_selected_key(self, key: str) -> None:
        self.config_path.parent.mkdir(exist_ok=True)
        temporary_path = self.config_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps({"active_book": key}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.config_path)
