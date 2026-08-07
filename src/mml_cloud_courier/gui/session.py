"""Where is the service, and may we talk to it? The GUI reads the bearer
token from the service data dir's api_token file (the installer owns the
ACL grant in Phase 6; the live install already grants the GUI user)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mml_cloud_courier.cli.service_client import ApiClient
from mml_cloud_courier.service.config import load_config
from mml_cloud_courier.service.security import read_token


@dataclass(frozen=True)
class ServiceSession:
    base_url: str
    token_path: Path
    client: ApiClient | None
    error: str | None = None


def discover_session() -> ServiceSession:
    config = load_config()
    base_url = os.environ.get("MMLCT_SERVICE_URL") or config.base_url
    token_path = Path(os.environ.get("MMLCT_TOKEN_FILE") or config.token_path)
    try:
        token = read_token(token_path)
    except (OSError, ValueError):
        return ServiceSession(base_url, token_path, None, error=(
            "Cannot read the service access token at"
            f" {token_path}.\nIs the MML Cloud Transfer service installed,"
            " and does your Windows account have access to its data folder?"
        ))
    return ServiceSession(base_url, token_path, ApiClient(base_url, token))
