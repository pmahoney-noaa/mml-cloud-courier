"""DPAPI-encrypted credential payloads under the service data directory.

Follows the token-file pattern exactly (service/security.py::ensure_token):
create the empty file, cut its ACL, then write — the secret bytes never
exist on disk under a permissive ACL. Grants are by process SID, never
account names (gate fix 6e45d4a). The directory itself gets an inheritable
cut ACL as defence in depth.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from mml_cloud_transfer.auth.dpapi import protect, unprotect
from mml_cloud_transfer.service.security import restrict_acl

_REF_PATTERN = re.compile(r"cred-[0-9a-f]{12}\.dpapi")


class CredentialStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, ref: str) -> Path:
        """The blob path for a ref. Refs come from the profiles table, but a
        path separator smuggled into one must never escape the store."""
        if not _REF_PATTERN.fullmatch(ref):
            raise ValueError(f"not a credential ref: {ref!r}")
        return self._root / ref

    def save(self, payload: dict) -> str:
        self._root.mkdir(parents=True, exist_ok=True)
        restrict_acl(self._root, inheritable=True)
        ref = f"cred-{uuid.uuid4().hex[:12]}.dpapi"
        path = self._root / ref
        path.touch()
        restrict_acl(path)
        path.write_bytes(protect(json.dumps(payload).encode("utf-8")))
        return ref

    def load(self, ref: str) -> dict:
        blob = self.path_for(ref).read_bytes()
        return json.loads(unprotect(blob).decode("utf-8"))

    def delete(self, ref: str) -> None:
        self.path_for(ref).unlink(missing_ok=True)
