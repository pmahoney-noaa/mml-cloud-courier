"""User OAuth: the installed-app flow with a loopback redirect.

This half runs in the interactive session (CLI now, GUI in Phase 5) —
the browser cannot open in session 0. The resulting refresh token is
handed to the service, which refreshes access tokens autonomously
thereafter; that hand-off is what makes user-OAuth profiles work
unattended after logoff.

No desktop OAuth client ID ships yet, so the client configuration is
injected (a client_secret_*.json from Google Cloud Console) via
--client-config or MMLCC_OAUTH_CLIENT. For installed apps the
"client secret" is not genuinely secret — standard, and stated plainly
in the spec. Phase 6 packages a default client ID.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]


def load_client_config(path: str | None) -> dict:
    source = path or os.environ.get("MMLCC_OAUTH_CLIENT")
    if not source:
        raise ValueError(
            "no OAuth client configuration: pass --client-config (or set"
            " MMLCC_OAUTH_CLIENT) to a client_secret_*.json downloaded from"
            " Google Cloud Console > APIs & Services > Credentials >"
            " Create credentials > OAuth client ID > Desktop app"
        )
    config = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    if "installed" not in config:
        raise ValueError(
            f"{source} is not an installed-app OAuth client configuration"
            " (missing the 'installed' key); create a 'Desktop app' client"
        )
    return config


def authorized_user_payload(creds) -> dict:
    """The service-side format: exactly what
    google.oauth2.credentials.Credentials.from_authorized_user_info eats."""
    return {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "scopes": list(creds.scopes or SCOPES),
    }


def run_login(
    client_config: dict,
    *,
    open_browser: bool = True,
    port: int = 0,
    flow_factory=None,
    timeout_seconds: int | None = None,
) -> dict:
    """Run the browser flow; return an authorized-user payload.

    access_type=offline and prompt=consent force Google to (re)issue a
    refresh token — without them a re-consenting user gets access tokens
    only, and the profile would die at the first refresh after logoff.

    timeout_seconds bounds the wait for the browser round trip: the GUI
    runs this on a background thread, and an abandoned tab must not hang
    it forever.
    """
    if flow_factory is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow_factory = InstalledAppFlow.from_client_config
    flow = flow_factory(client_config, scopes=SCOPES)
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        access_type="offline",
        prompt="consent",
        timeout_seconds=timeout_seconds,
    )
    if not creds.refresh_token:
        raise ValueError(
            "Google did not return a refresh token; remove this app's access"
            " at https://myaccount.google.com/permissions and sign in again"
        )
    return authorized_user_payload(creds)
