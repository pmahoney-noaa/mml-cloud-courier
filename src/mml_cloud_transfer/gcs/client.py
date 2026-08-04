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
) -> GcsContext:
    """Build a context from one of three credential sources.

    Priority: explicit emulator endpoint (anonymous) > explicit service
    account key file > Application Default Credentials.
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

    if credentials_path is not None:
        from google.oauth2 import service_account

        path = Path(credentials_path)
        if not path.exists():
            raise FileNotFoundError(f"credentials file not found: {credentials_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )
        project = credentials.project_id
    else:
        import google.auth

        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
        )

    client = storage.Client(project=project, credentials=credentials)
    return GcsContext(
        client=client,
        session=AuthorizedSession(credentials),
        endpoint=_DEFAULT_ENDPOINT,
        bucket=bucket,
    )
