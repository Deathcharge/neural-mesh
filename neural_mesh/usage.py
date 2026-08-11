"""Optional privacy-minimized JSONL usage persistence."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_MAX_RECORD_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Aggregate metadata persisted for one council run.

    Prompt text, response text, provider exceptions, and credentials are
    deliberately absent.
    """

    timestamp: datetime
    task_sha256: str
    agreement_level: str
    providers_queried: int
    providers_succeeded: int
    requested_max_tokens: int
    reported_input_tokens: int
    reported_output_tokens: int
    token_reporting_complete: bool
    reported_cost_usd: Decimal
    cost_reporting_complete: bool
    duration_ms: int

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if len(self.task_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.task_sha256
        ):
            raise ValueError("task_sha256 must be 64 lowercase hexadecimal characters")
        if not self.agreement_level:
            raise ValueError("agreement_level must not be empty")
        for name, value in (
            ("providers_queried", self.providers_queried),
            ("providers_succeeded", self.providers_succeeded),
            ("requested_max_tokens", self.requested_max_tokens),
            ("reported_input_tokens", self.reported_input_tokens),
            ("reported_output_tokens", self.reported_output_tokens),
            ("duration_ms", self.duration_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.reported_cost_usd, Decimal):
            raise TypeError("reported_cost_usd must be a Decimal")
        if not self.reported_cost_usd.is_finite() or self.reported_cost_usd < 0:
            raise ValueError("reported_cost_usd must be finite and non-negative")

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-serializable record."""

        return {
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "task_sha256": self.task_sha256,
            "agreement_level": self.agreement_level,
            "providers_queried": self.providers_queried,
            "providers_succeeded": self.providers_succeeded,
            "requested_max_tokens": self.requested_max_tokens,
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "token_reporting_complete": self.token_reporting_complete,
            "reported_cost_usd": str(self.reported_cost_usd),
            "cost_reporting_complete": self.cost_reporting_complete,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class UsageStatistics:
    """Bounded aggregate over stored usage records."""

    records: int
    invalid_records: int
    truncated: bool
    agreement_distribution: Mapping[str, int]
    providers_queried: int
    providers_succeeded: int
    reported_input_tokens: int
    reported_output_tokens: int
    reported_cost_usd: Decimal
    incomplete_cost_records: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "records": self.records,
            "invalid_records": self.invalid_records,
            "truncated": self.truncated,
            "agreement_distribution": dict(self.agreement_distribution),
            "providers_queried": self.providers_queried,
            "providers_succeeded": self.providers_succeeded,
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "reported_cost_usd": str(self.reported_cost_usd),
            "incomplete_cost_records": self.incomplete_cost_records,
        }


@runtime_checkable
class UsageStore(Protocol):
    """Optional persistence target used by the consensus engine."""

    async def append(self, record: UsageRecord) -> None:
        """Persist one aggregate usage record."""


class JsonlUsageStore:
    """Append-only local JSONL store with bounded size and streaming reads."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
        durable: bool = True,
        max_backup_files: int = 0,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        candidate = Path(path).expanduser()
        if candidate.is_symlink():
            raise ValueError("usage path must not be a symbolic link")
        if candidate.exists() and candidate.is_dir():
            raise ValueError("usage path must not be a directory")
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int):
            raise TypeError("max_file_bytes must be an integer")
        if max_file_bytes < _MAX_RECORD_BYTES:
            raise ValueError(f"max_file_bytes must be at least {_MAX_RECORD_BYTES}")
        if not isinstance(durable, bool):
            raise TypeError("durable must be a boolean")
        if isinstance(max_backup_files, bool) or not isinstance(max_backup_files, int):
            raise TypeError("max_backup_files must be an integer")
        if not 0 <= max_backup_files <= 100:
            raise ValueError("max_backup_files must be between 0 and 100")
        if isinstance(lock_timeout_seconds, bool) or not isinstance(
            lock_timeout_seconds, int | float
        ):
            raise TypeError("lock_timeout_seconds must be a number")
        if not 0.01 <= lock_timeout_seconds <= 30.0:
            raise ValueError("lock_timeout_seconds must be between 0.01 and 30")
        self.path = candidate.resolve(strict=False)
        self.max_file_bytes = max_file_bytes
        self.durable = durable
        self.max_backup_files = max_backup_files
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._write_lock = threading.Lock()

    async def append(self, record: UsageRecord) -> None:
        """Append one record without blocking the event loop."""

        await asyncio.to_thread(self._append_sync, record)

    async def statistics(self, *, max_records: int = 100_000) -> UsageStatistics:
        """Stream at most ``max_records`` records and return aggregate statistics."""

        if isinstance(max_records, bool) or not isinstance(max_records, int):
            raise TypeError("max_records must be an integer")
        if max_records < 1 or max_records > 1_000_000:
            raise ValueError("max_records must be between 1 and 1000000")
        return await asyncio.to_thread(self._statistics_sync, max_records)

    def _append_sync(self, record: UsageRecord) -> None:
        payload = (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > _MAX_RECORD_BYTES:
            raise ValueError("usage record exceeds the record-size limit")

        with self._write_lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _exclusive_file_lock(self.lock_path, self.lock_timeout_seconds):
                if self.path.is_symlink():
                    raise OSError("usage path must not be a symbolic link")
                if (
                    self.path.exists()
                    and self.path.stat().st_size + len(payload) > self.max_file_bytes
                ):
                    if self.max_backup_files == 0:
                        raise OSError("usage store has reached max_file_bytes")
                    _rotate(self.path, self.max_backup_files)
                descriptor = os.open(self.path, _append_flags(), 0o600)
                try:
                    file_status = os.fstat(descriptor)
                    if not stat.S_ISREG(file_status.st_mode):
                        raise OSError("usage path must be a regular file")
                    if file_status.st_size + len(payload) > self.max_file_bytes:
                        raise OSError("usage record cannot fit within max_file_bytes")
                    with os.fdopen(descriptor, "ab", closefd=True) as stream:
                        descriptor = -1
                        stream.write(payload)
                        stream.flush()
                        if self.durable:
                            os.fsync(stream.fileno())
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)

    def _statistics_sync(self, max_records: int) -> UsageStatistics:
        if not self.path.exists():
            return _empty_statistics()
        if self.path.is_symlink():
            raise OSError("usage path must not be a symbolic link")

        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(self.path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("usage path must be a regular file")
        except BaseException:
            os.close(descriptor)
            raise

        records = 0
        invalid_records = 0
        truncated = False
        agreements: dict[str, int] = {}
        providers_queried = 0
        providers_succeeded = 0
        input_tokens = 0
        output_tokens = 0
        cost = Decimal("0")
        incomplete_cost_records = 0
        processed_records = 0

        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            for raw_line in stream:
                if processed_records >= max_records:
                    truncated = True
                    break
                processed_records += 1
                if len(raw_line) > _MAX_RECORD_BYTES:
                    invalid_records += 1
                    continue
                try:
                    item = json.loads(raw_line)
                    agreement = _required_string(item, "agreement_level")
                    record_providers_queried = _required_non_negative_int(
                        item,
                        "providers_queried",
                    )
                    record_providers_succeeded = _required_non_negative_int(
                        item,
                        "providers_succeeded",
                    )
                    record_input_tokens = _required_non_negative_int(
                        item,
                        "reported_input_tokens",
                    )
                    record_output_tokens = _required_non_negative_int(
                        item,
                        "reported_output_tokens",
                    )
                    record_cost = Decimal(_required_string(item, "reported_cost_usd"))
                    if record_cost < 0:
                        raise ValueError("negative cost")
                    if not record_cost.is_finite():
                        raise ValueError("non-finite cost")
                except (
                    InvalidOperation,
                    TypeError,
                    UnicodeDecodeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    invalid_records += 1
                    continue
                records += 1
                agreements[agreement] = agreements.get(agreement, 0) + 1
                providers_queried += record_providers_queried
                providers_succeeded += record_providers_succeeded
                input_tokens += record_input_tokens
                output_tokens += record_output_tokens
                cost += record_cost
                if item.get("cost_reporting_complete") is not True:
                    incomplete_cost_records += 1

        return UsageStatistics(
            records=records,
            invalid_records=invalid_records,
            truncated=truncated,
            agreement_distribution=agreements,
            providers_queried=providers_queried,
            providers_succeeded=providers_succeeded,
            reported_input_tokens=input_tokens,
            reported_output_tokens=output_tokens,
            reported_cost_usd=cost,
            incomplete_cost_records=incomplete_cost_records,
        )


def _required_string(item: object, key: str) -> str:
    if not isinstance(item, dict):
        raise TypeError("usage record must be an object")
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_non_negative_int(item: object, key: str) -> int:
    if not isinstance(item, dict):
        raise TypeError("usage record must be an object")
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _empty_statistics() -> UsageStatistics:
    return UsageStatistics(
        records=0,
        invalid_records=0,
        truncated=False,
        agreement_distribution={},
        providers_queried=0,
        providers_succeeded=0,
        reported_input_tokens=0,
        reported_output_tokens=0,
        reported_cost_usd=Decimal("0"),
        incomplete_cost_records=0,
    )


def _append_flags() -> int:
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    return flags


@contextmanager
def _exclusive_file_lock(path: Path, timeout_seconds: float) -> Iterator[None]:
    if path.is_symlink():
        raise OSError("usage lock path must not be a symbolic link")
    descriptor = os.open(path, _append_flags(), 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("usage lock path must be a regular file")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                _try_lock(descriptor)
                locked = True
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise TimeoutError("usage store lock acquisition timed out") from error
                time.sleep(0.01)
        yield
    finally:
        if locked:
            _unlock(descriptor)
        os.close(descriptor)


def _try_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    module: Any
    if os.name == "nt":
        module = importlib.import_module("msvcrt")
        module.locking(descriptor, module.LK_NBLCK, 1)
    else:
        module = importlib.import_module("fcntl")
        module.flock(descriptor, module.LOCK_EX | module.LOCK_NB)


def _unlock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    module: Any
    if os.name == "nt":
        module = importlib.import_module("msvcrt")
        module.locking(descriptor, module.LK_UNLCK, 1)
    else:
        module = importlib.import_module("fcntl")
        module.flock(descriptor, module.LOCK_UN)


def _rotate(path: Path, max_backup_files: int) -> None:
    candidates = [
        path.with_name(f"{path.name}.{index}") for index in range(1, max_backup_files + 1)
    ]
    if any(candidate.is_symlink() for candidate in candidates):
        raise OSError("usage backup path must not be a symbolic link")
    for index in range(max_backup_files, 0, -1):
        source = path if index == 1 else candidates[index - 2]
        if source.exists():
            os.replace(source, candidates[index - 1])


__all__ = [
    "JsonlUsageStore",
    "UsageRecord",
    "UsageStatistics",
    "UsageStore",
]
