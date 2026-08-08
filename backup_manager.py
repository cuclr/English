"""Safe local backup and restore helpers for the SQLite vocabulary database."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from threading import RLock


class BackupError(RuntimeError):
    """Raised when a database backup cannot be created or restored safely."""


@dataclass(frozen=True)
class BackupInfo:
    name: str
    created_at: str
    size_label: str
    kind_label: str


class DatabaseBackupManager:
    """Create, inspect, and restore SQLite snapshots stored beside the database."""

    _operation_lock = RLock()
    _required_tables = {"study_days", "words", "learning_records"}

    def __init__(self, database_path: Path, backup_dir: Path | None = None):
        self.database_path = Path(database_path)
        self.backup_dir = Path(backup_dir or self.database_path.parent / "backups")

    def list_backups(self) -> list[BackupInfo]:
        if not self.backup_dir.exists():
            return []

        paths = [path for path in self.backup_dir.glob("*.db") if path.is_file()]
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return [self._backup_info(path) for path in paths]

    def create_backup(self, kind: str = "manual") -> BackupInfo:
        with self._operation_lock:
            return self._create_backup(kind)

    def _create_backup(self, kind: str) -> BackupInfo:
        if kind not in {"manual", "before-restore"}:
            raise BackupError("不支持的备份类型。")
        if not self.database_path.is_file():
            raise BackupError("没有找到当前数据库，无法创建备份。")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.backup_dir / f"{kind}-{timestamp}.db"

        try:
            self._copy_database(self.database_path, destination)
            self._validate_database(destination)
        except (OSError, sqlite3.Error, BackupError) as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"创建备份失败：{exc}") from exc

        return self._backup_info(destination)

    def restore_backup(self, backup_name: str) -> BackupInfo:
        with self._operation_lock:
            source = self._resolve_backup(backup_name)
            self._validate_database(source)
            safety_backup = self._create_backup("before-restore")

            try:
                self._copy_database(source, self.database_path)
                self._validate_database(self.database_path)
            except (OSError, sqlite3.Error, BackupError) as exc:
                try:
                    safety_source = self._resolve_backup(safety_backup.name)
                    self._copy_database(safety_source, self.database_path)
                except (OSError, sqlite3.Error, BackupError) as recovery_exc:
                    raise BackupError(
                        "恢复失败，当前数据也无法自动回退。请保留备份文件并停止使用应用："
                        f"{recovery_exc}"
                    ) from exc
                raise BackupError("恢复失败，已自动恢复到操作前的数据。") from exc

            return safety_backup

    def _resolve_backup(self, backup_name: str) -> Path:
        if not backup_name or Path(backup_name).name != backup_name:
            raise BackupError("备份文件名称无效。")

        backup_root = self.backup_dir.resolve()
        candidate = (self.backup_dir / backup_name).resolve()
        if candidate.parent != backup_root or candidate.suffix.lower() != ".db":
            raise BackupError("备份文件名称无效。")
        if not candidate.is_file():
            raise BackupError("没有找到所选备份文件。")
        return candidate

    @staticmethod
    def _copy_database(source_path: Path, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source_path)) as source:
            with closing(sqlite3.connect(destination_path)) as destination:
                source.backup(destination)
                destination.commit()

    @staticmethod
    def _validate_database(path: Path) -> None:
        try:
            with closing(sqlite3.connect(path)) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
        except sqlite3.Error as exc:
            raise BackupError(f"备份文件无法读取：{exc}") from exc
        if not result or result[0] != "ok":
            raise BackupError("备份文件完整性校验未通过。")
        if not DatabaseBackupManager._required_tables.issubset(tables):
            raise BackupError("所选文件不是兼容的背单词数据库备份。")

    @staticmethod
    def _backup_info(path: Path) -> BackupInfo:
        stat = path.stat()
        kind_label = "恢复前安全备份" if path.name.startswith("before-restore-") else "手动备份"
        return BackupInfo(
            name=path.name,
            created_at=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            size_label=DatabaseBackupManager._format_size(stat.st_size),
            kind_label=kind_label,
        )

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
