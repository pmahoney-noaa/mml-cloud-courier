"""Profile row -> authenticated GcsContext.

One dispatch for both the worker and the API, so a profile behaves
identically at job runtime and at preflight time. The DPAPI-backed types
load their payload from the store; the legacy types (emulator, key_file,
adc) dispatch exactly as service/worker.py did before Plan 4.
"""

from __future__ import annotations

from collections.abc import Mapping

from mml_cloud_courier.auth.credential_store import CredentialStore
from mml_cloud_courier.gcs.client import GcsContext, make_context


def context_for_profile(
    profile: Mapping,
    store: CredentialStore,
    *,
    make_context_fn=make_context,
) -> GcsContext:
    auth_type = profile["auth_type"]
    bucket = profile["bucket"]
    if auth_type == "emulator":
        return make_context_fn(bucket, emulator_endpoint=profile["credential_ref"])
    if auth_type == "key_file":
        return make_context_fn(bucket, credentials_path=profile["credential_ref"])
    if auth_type in ("service_account_key", "oauth_user"):
        payload = store.load(profile["credential_ref"])
        return make_context_fn(
            bucket,
            credentials_info=payload,
            project=profile["project_id"] or None,
        )
    if auth_type == "adc":
        return make_context_fn(bucket)
    raise ValueError(
        f"profile has unknown auth_type {auth_type!r} — refusing to fall back"
        " to Application Default Credentials; delete and recreate this profile"
    )
