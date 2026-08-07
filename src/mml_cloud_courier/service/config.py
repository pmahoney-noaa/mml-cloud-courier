"""Service configuration: the data directory layout and settings.json.

Everything the host, worker, and API must agree on lives here. The data
directory defaults to %ProgramData%\\MML Cloud Transfer; tests and console
runs point MMLCC_DATA_DIR (or --data-dir) at a temp directory instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from mml_cloud_courier.core.slicing import SizePolicy

DEFAULT_PORT = 47821


def default_data_dir() -> Path:
    env = os.environ.get("MMLCC_DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "MML Cloud Transfer"


@dataclass(frozen=True)
class ServiceConfig:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    auto_resume_on_startup: bool = True
    poll_interval: float = 1.0
    stall_probe_interval: float = 60.0
    sse_interval: float = 0.5
    file_workers: int = 4
    size_policy: SizePolicy | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "api_token"

    @property
    def credentials_dir(self) -> Path:
        return self.data_dir / "credentials"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


_SETTINGS_KEYS = {
    "host": str,
    "port": int,
    "auto_resume_on_startup": bool,
    "poll_interval": float,
    "stall_probe_interval": float,
    "sse_interval": float,
    "file_workers": int,
}


def load_config(
    data_dir: str | os.PathLike[str] | None = None, *, port: int | None = None
) -> ServiceConfig:
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    config = ServiceConfig(data_dir=root)
    settings_file = config.settings_path
    if settings_file.exists():
        raw = json.loads(settings_file.read_text(encoding="utf-8"))
        updates = {
            key: cast(raw[key]) for key, cast in _SETTINGS_KEYS.items() if key in raw
        }
        if raw.get("size_policy"):
            updates["size_policy"] = SizePolicy.parse(raw["size_policy"])
        config = replace(config, **updates)
    if port is not None:
        config = replace(config, port=port)
    return config
