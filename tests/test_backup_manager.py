from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backup_manager import BackupError, DatabaseBackupManager


class DatabaseBackupManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "instance" / "vocabulary.db"
        self.database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE study_days (id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE words (word TEXT NOT NULL)")
            connection.execute("CREATE TABLE learning_records (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO words VALUES ('original')")
            connection.commit()
        self.manager = DatabaseBackupManager(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_creates_and_lists_valid_local_backup(self):
        backup = self.manager.create_backup()

        self.assertTrue((self.manager.backup_dir / backup.name).is_file())
        self.assertEqual(backup.kind_label, "手动备份")
        self.assertEqual(self.manager.list_backups()[0].name, backup.name)
        with closing(sqlite3.connect(self.manager.backup_dir / backup.name)) as connection:
            self.assertEqual(
                connection.execute("SELECT word FROM words").fetchone()[0],
                "original",
            )

    def test_restore_replaces_data_and_keeps_safety_backup(self):
        backup = self.manager.create_backup()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE words SET word = 'changed'")
            connection.commit()

        safety_backup = self.manager.restore_backup(backup.name)

        with closing(sqlite3.connect(self.database)) as connection:
            restored_word = connection.execute("SELECT word FROM words").fetchone()[0]
        self.assertEqual(restored_word, "original")
        self.assertTrue(safety_backup.name.startswith("before-restore-"))
        with closing(sqlite3.connect(self.manager.backup_dir / safety_backup.name)) as connection:
            previous_word = connection.execute("SELECT word FROM words").fetchone()[0]
        self.assertEqual(previous_word, "changed")

    def test_restore_rejects_paths_outside_backup_directory(self):
        with self.assertRaises(BackupError):
            self.manager.restore_backup("../vocabulary.db")

    def test_restore_rejects_unrelated_sqlite_database(self):
        self.manager.backup_dir.mkdir(parents=True)
        unrelated = self.manager.backup_dir / "unrelated.db"
        with closing(sqlite3.connect(unrelated)) as connection:
            connection.execute("CREATE TABLE something_else (id INTEGER)")
            connection.commit()

        with self.assertRaisesRegex(BackupError, "不是兼容"):
            self.manager.restore_backup(unrelated.name)


if __name__ == "__main__":
    unittest.main()
