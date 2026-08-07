"""Authenticated GCS context: client library handle + raw HTTP session.

The client library is used for what it does well (metadata, listing,
single-shot uploads, compose, session initiation). The raw session exists
because resumable chunk PUTs and ranged GETs are driven by us — we persist
session URIs across process death and resume from the server's committed
offset, which the library does not expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from google.cloud import storage

_DEFAULT_ENDPOINT = "https://storage.googleapis.com"
_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]


@dataclass(frozen=True)
class GcsContext:
    client: storage.Client
    session: requests.Session
    endpoint: str
    bucket: str


def make_context(
    bucket: str,
    *,
    credentials_path: str | None = None,
    emulator_endpoint: str | None = None,
    credentials_info: dict | None = None,
    project: str | None = None,
) -> GcsContext:
    """Build a context from one of four credential sources.

    Priority: explicit emulator endpoint (anonymous) > in-memory credential
    dict (the DPAPI store hands these over) > explicit service account key
    file > Application Default Credentials. ``credentials_info`` dispatches
    on its "type" field, matching Google's own file formats:
    "service_account" (a key JSON) or "authorized_user" (a stored OAuth
    refresh token — the client refreshes it autonomously, which is what
    makes profile jobs run unattended). ``project`` only matters for
    authorized_user, which carries no project of its own; object-level
    operations never send it, so the placeholder is harmless.
    """
    if emulator_endpoint is not None:
        from google.auth.credentials import AnonymousCredentials

        endpoint = emulator_endpoint.rstrip("/")
        client = storage.Client(
            project="mmlct",
            credentials=AnonymousCredentials(),
            client_options={"api_endpoint": endpoint},
        )
        return GcsContext(
            client=client, session=requests.Session(), endpoint=endpoint, bucket=bucket
        )

    from google.auth.transport.requests import AuthorizedSession

    if credentials_info is not None:
        kind = credentials_info.get("type")
        if kind == "service_account":
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_info(
                credentials_info, scopes=_SCOPES
            )
            client_project = credentials.project_id
        elif kind == "authorized_user":
            from google.oauth2.credentials import Credentials as UserCredentials

            credentials = UserCredentials.from_authorized_user_info(
                credentials_info, scopes=_SCOPES
            )
            client_project = project or "mmlct"
        else:
            raise ValueError(f"unsupported credential type: {kind!r}")
        client = storage.Client(project=client_project, credentials=credentials)
        return GcsContext(
            client=client,
            session=AuthorizedSession(credentials),
            endpoint=_DEFAULT_ENDPOINT,
            bucket=bucket,
        )

    if credentials_path is not None:
        from google.oauth2 import service_account

        path = Path(credentials_path)
        if not path.exists():
            raise FileNotFoundError(f"credentials file not found: {credentials_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(path), scopes=_SCOPES
        )
        project = credentials.project_id
    else:
        import google.auth

        credentials, project = google.auth.default(scopes=_SCOPES)

    client = storage.Client(project=project, credentials=credentials)
    return GcsContext(
        client=client,
        session=AuthorizedSession(credentials),
        endpoint=_DEFAULT_ENDPOINT,
        bucket=bucket,
    )
