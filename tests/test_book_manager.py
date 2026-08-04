from pathlib import Path
import tempfile
import unittest

from book_manager import BookManager, BookManagerError


class BookManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        (self.project_dir / "first.pdf").write_bytes(b"first")
        books_dir = self.project_dir / "books"
        books_dir.mkdir()
        (books_dir / "second.pdf").write_bytes(b"second")
        self.manager = BookManager(self.project_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discovers_root_and_books_folder_pdfs(self):
        books = self.manager.list_books()
        self.assertEqual([book.key for book in books], ["first.pdf", "books/second.pdf"])

    def test_selection_is_persisted(self):
        selected = self.manager.select_book("books/second.pdf")
        reloaded_manager = BookManager(self.project_dir)

        self.assertEqual(selected.name, "second")
        self.assertEqual(reloaded_manager.active_book().key, "books/second.pdf")

    def test_rejects_unknown_book(self):
        with self.assertRaises(BookManagerError):
            self.manager.select_book("books/missing.pdf")


if __name__ == "__main__":
    unittest.main()
