import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from .reporting import _is_credential_attribute


SNAPSHOT_DIRECTORY = re.compile(r"^\d{8}_\d{6}(?:_(?:[2-9]|[1-9]\d+))?$")
IST = ZoneInfo("Asia/Kolkata")

_LEGACY_TOP_LEVEL_KEYS = frozenset({"metadata", "total", "channels"})
_LEGACY_METADATA_KEYS = frozenset(
    {"status", "started_at", "finished_at", "provider_count"}
)
_LEGACY_CHANNEL_KEYS = frozenset(
    {"status", "complete", "summary", "metadata", "orders"}
)
_UNRECOGNIZED_WARNING = "Unrecognized report directory preserved"
_DELETE_WARNING = "Report directory could not be deleted"


@dataclass(frozen=True)
class CleanupResult:
    deleted: Tuple[Path, ...]
    preserved: Tuple[Path, ...]
    warnings: Tuple[str, ...]


def _finished_at(metadata: Mapping[str, Any]) -> Optional[datetime]:
    value = metadata.get("finished_at")
    if not isinstance(value, str):
        return None
    try:
        finished_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if finished_at.tzinfo is None or finished_at.utcoffset() is None:
        return None
    return finished_at


def _contains_credential_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_credential_attribute(key) or _contains_credential_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_credential_key(item) for item in value)
    return False


def _is_legacy_report(payload: Any, metadata: Mapping[str, Any]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if set(payload) != _LEGACY_TOP_LEVEL_KEYS:
        return False
    if not _LEGACY_METADATA_KEYS.issubset(metadata):
        return False
    channels = payload.get("channels")
    if not isinstance(channels, Mapping):
        return False
    if not all(
        isinstance(channel, Mapping)
        and _LEGACY_CHANNEL_KEYS.issubset(channel)
        for channel in channels.values()
    ):
        return False
    return not _contains_credential_key(payload)


def _finished_report_date(path: Path) -> Optional[date]:
    aggregate_json = path / "aggregate.json"
    if aggregate_json.is_symlink() or not aggregate_json.is_file():
        return None
    try:
        with aggregate_json.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    finished_at = _finished_at(metadata)
    if finished_at is None:
        return None
    marked = "generator" in metadata or "schema_version" in metadata
    if marked:
        if metadata.get("generator") != "walletQuota" or metadata.get("schema_version") != 1:
            return None
    elif not _is_legacy_report(payload, metadata):
        return None
    return finished_at.astimezone(IST).date()


def cleanup_expired_reports(report_directory: Path, today_ist: date) -> CleanupResult:
    report_directory = Path(report_directory)
    root = report_directory.resolve()
    deleted = []
    preserved = []
    warnings = []

    for candidate in report_directory.iterdir():
        if candidate.is_symlink() or not candidate.is_dir():
            preserved.append(candidate)
            continue
        if not SNAPSHOT_DIRECTORY.fullmatch(candidate.name):
            preserved.append(candidate)
            warnings.append(_UNRECOGNIZED_WARNING)
            continue
        try:
            direct_child = candidate.resolve().parent == root
        except OSError:
            direct_child = False
        if not direct_child:
            preserved.append(candidate)
            warnings.append(_UNRECOGNIZED_WARNING)
            continue
        finished_date = _finished_report_date(candidate)
        if finished_date is None:
            preserved.append(candidate)
            warnings.append(_UNRECOGNIZED_WARNING)
            continue
        if finished_date >= today_ist:
            preserved.append(candidate)
            continue
        try:
            shutil.rmtree(candidate)
        except OSError:
            preserved.append(candidate)
            warnings.append(_DELETE_WARNING)
        else:
            deleted.append(candidate)

    return CleanupResult(tuple(deleted), tuple(preserved), tuple(warnings))
