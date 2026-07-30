import json
import os
from pathlib import Path


DEFAULT_PREFIX = "!"
MAX_PREFIX_LENGTH = 10
PREFIX_FILE = Path("data") / "prefixes.json"


def normalize_prefix(prefix: str) -> str:
    prefix = str(prefix).strip()
    if not prefix:
        raise ValueError("Prefix cannot be blank.")
    if len(prefix) > MAX_PREFIX_LENGTH:
        raise ValueError(f"Prefix cannot be longer than {MAX_PREFIX_LENGTH} characters.")
    if any(ch.isspace() for ch in prefix):
        raise ValueError("Prefix cannot contain spaces.")
    return prefix


def load_prefixes(path: str | os.PathLike = PREFIX_FILE) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(raw, dict):
        return {}

    prefixes: dict[str, str] = {}
    for guild_id, prefix in raw.items():
        try:
            prefixes[str(guild_id)] = normalize_prefix(prefix)
        except ValueError:
            continue
    return prefixes


def save_prefixes(data: dict[str, str], path: str | os.PathLike = PREFIX_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)

    os.replace(tmp_path, path)


def get_prefix(
    guild_id: int | None,
    *,
    path: str | os.PathLike = PREFIX_FILE,
    data: dict[str, str] | None = None,
) -> str:
    if guild_id is None:
        return DEFAULT_PREFIX
    prefixes = load_prefixes(path) if data is None else data
    return prefixes.get(str(guild_id), DEFAULT_PREFIX) or DEFAULT_PREFIX


def set_prefix(
    guild_id: int,
    prefix: str,
    *,
    path: str | os.PathLike = PREFIX_FILE,
    data: dict[str, str] | None = None,
) -> str:
    normalized = normalize_prefix(prefix)
    prefixes = load_prefixes(path) if data is None else dict(data)
    prefixes[str(guild_id)] = normalized
    save_prefixes(prefixes, path)
    return normalized
