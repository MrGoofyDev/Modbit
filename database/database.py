import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Case:
    case_id: str
    timestamp: str


class Database:
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = path
        else:
            database_dir = Path(__file__).resolve().parent
            modex_path = database_dir / "modex.db"
            legacy_path = database_dir / "modbit.db"
            if modex_path.exists() and legacy_path.exists():
                raise RuntimeError(
                    "Both database/modex.db and the legacy database/modbit.db exist. "
                    "Choose the database containing the records to keep, then move "
                    "the other file outside the database directory before starting Modex."
                )
            self.path = legacy_path if legacy_path.exists() else modex_path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    async def _run(self, function: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(function, *args)

    async def initialize(self) -> None:
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS case_counter (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    next_value INTEGER NOT NULL CHECK (next_value > 0)
                );
                INSERT OR IGNORE INTO case_counter (id, next_value) VALUES (1, 1);

                CREATE TABLE IF NOT EXISTS moderation_cases (
                    case_id TEXT PRIMARY KEY,
                    server_id INTEGER NOT NULL,
                    target_user_id INTEGER,
                    moderator_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    duration_seconds INTEGER,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed', 'failed'))
                );
                CREATE INDEX IF NOT EXISTS idx_cases_server_target
                    ON moderation_cases (server_id, target_user_id, timestamp);

                CREATE TABLE IF NOT EXISTS warnings (
                    warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    case_id TEXT NOT NULL UNIQUE,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    removed_at TEXT,
                    removed_by INTEGER,
                    removal_case_id TEXT,
                    FOREIGN KEY (case_id) REFERENCES moderation_cases (case_id),
                    FOREIGN KEY (removal_case_id) REFERENCES moderation_cases (case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_active_target
                    ON warnings (server_id, target_user_id, active, warning_id);
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            counter = connection.execute(
                "SELECT next_value FROM case_counter WHERE id = 1"
            ).fetchone()
            if counter is None:
                raise sqlite3.DatabaseError("The case counter is missing.")

            highest_existing_number = 0
            for row in connection.execute(
                "SELECT case_id FROM moderation_cases"
            ):
                case_id = str(row["case_id"])
                prefix, separator, number = case_id.partition("-")
                if prefix in {"MODEX", "MODBIT"} and separator and number.isdigit():
                    highest_existing_number = max(highest_existing_number, int(number))

            next_value = max(int(counter["next_value"]), highest_existing_number + 1)
            connection.execute(
                "UPDATE case_counter SET next_value = ? WHERE id = 1",
                (next_value,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _next_case_id(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT next_value FROM case_counter WHERE id = 1"
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("The case counter is missing.")
        number = int(row["next_value"])
        connection.execute(
            "UPDATE case_counter SET next_value = ? WHERE id = 1", (number + 1,)
        )
        return f"MODEX-{number:04d}"

    @classmethod
    def _insert_case(
        cls,
        connection: sqlite3.Connection,
        server_id: int,
        target_user_id: int | None,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None,
        expires_at: str | None,
        status: str,
    ) -> Case:
        case_id = cls._next_case_id(connection)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO moderation_cases (
                case_id, server_id, target_user_id, moderator_id, action,
                reason, timestamp, duration_seconds, expires_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                server_id,
                target_user_id,
                moderator_id,
                action,
                reason,
                timestamp,
                duration_seconds,
                expires_at,
                status,
            ),
        )
        return Case(case_id=case_id, timestamp=timestamp)

    async def create_pending_case(
        self,
        server_id: int,
        target_user_id: int | None,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
        expires_at: str | None = None,
    ) -> Case:
        return await self._run(
            self._create_pending_case_sync,
            server_id,
            target_user_id,
            moderator_id,
            action,
            reason,
            duration_seconds,
            expires_at,
        )

    async def reserve_case_id(self) -> Case:
        return await self._run(self._reserve_case_id_sync)

    def _reserve_case_id_sync(self) -> Case:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            case_id = self._next_case_id(connection)
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connection.commit()
            return Case(case_id=case_id, timestamp=timestamp)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def create_completed_case(
        self,
        case: Case,
        server_id: int,
        target_user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
        expires_at: str | None = None,
    ) -> None:
        await self._run(
            self._create_completed_case_sync,
            case,
            server_id,
            target_user_id,
            moderator_id,
            action,
            reason,
            duration_seconds,
            expires_at,
        )

    def _create_completed_case_sync(
        self,
        case: Case,
        server_id: int,
        target_user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None,
        expires_at: str | None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO moderation_cases (
                    case_id, server_id, target_user_id, moderator_id, action,
                    reason, timestamp, duration_seconds, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed')
                """,
                (
                    case.case_id,
                    server_id,
                    target_user_id,
                    moderator_id,
                    action,
                    reason,
                    case.timestamp,
                    duration_seconds,
                    expires_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_pending_case_sync(
        self,
        server_id: int,
        target_user_id: int | None,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None,
        expires_at: str | None,
    ) -> Case:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            case = self._insert_case(
                connection,
                server_id,
                target_user_id,
                moderator_id,
                action,
                reason,
                duration_seconds,
                expires_at,
                "pending",
            )
            connection.commit()
            return case
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def finish_case(self, case_id: str, status: str) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Case status must be completed or failed.")
        await self._run(self._finish_case_sync, case_id, status)

    def _finish_case_sync(self, case_id: str, status: str) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE moderation_cases SET status = ?
                WHERE case_id = ? AND status = 'pending'
                """,
                (status, case_id),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError(f"Could not update case {case_id}.")
        finally:
            connection.close()

    async def create_warning(
        self,
        server_id: int,
        target_user_id: int,
        moderator_id: int,
        reason: str,
    ) -> Case:
        return await self._run(
            self._create_warning_sync, server_id, target_user_id, moderator_id, reason
        )

    def _create_warning_sync(
        self, server_id: int, target_user_id: int, moderator_id: int, reason: str
    ) -> Case:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            case = self._insert_case(
                connection,
                server_id,
                target_user_id,
                moderator_id,
                "WARN",
                reason,
                None,
                None,
                "completed",
            )
            connection.execute(
                """
                INSERT INTO warnings (
                    server_id, target_user_id, case_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (server_id, target_user_id, case.case_id, reason, case.timestamp),
            )
            connection.commit()
            return case
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def remove_latest_warning(
        self,
        server_id: int,
        target_user_id: int,
        moderator_id: int,
        reason: str,
    ) -> tuple[Case, str] | None:
        return await self._run(
            self._remove_latest_warning_sync,
            server_id,
            target_user_id,
            moderator_id,
            reason,
        )

    def _remove_latest_warning_sync(
        self, server_id: int, target_user_id: int, moderator_id: int, reason: str
    ) -> tuple[Case, str] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            warning = connection.execute(
                """
                SELECT warning_id, case_id FROM warnings
                WHERE server_id = ? AND target_user_id = ? AND active = 1
                ORDER BY warning_id DESC LIMIT 1
                """,
                (server_id, target_user_id),
            ).fetchone()
            if warning is None:
                connection.rollback()
                return None

            case = self._insert_case(
                connection,
                server_id,
                target_user_id,
                moderator_id,
                "UNWARN",
                reason,
                None,
                None,
                "completed",
            )
            removed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                UPDATE warnings
                SET active = 0, removed_at = ?, removed_by = ?, removal_case_id = ?
                WHERE warning_id = ? AND active = 1
                """,
                (removed_at, moderator_id, case.case_id, warning["warning_id"]),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("The warning could not be removed.")
            connection.commit()
            return case, str(warning["case_id"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
