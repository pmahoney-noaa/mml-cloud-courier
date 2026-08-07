"""mmlct profile subcommands.

Always a client of the service API: credentials live in the service's
DPAPI store, so there is no direct-engine mode here. The OAuth browser
flow is the one interactive step (spec: it must run in the user's
session); its result is handed to the service, which refreshes tokens
autonomously thereafter.
"""

from __future__ import annotations

import json
from pathlib import Path

from mml_cloud_transfer.auth.oauth_flow import load_client_config, run_login
from mml_cloud_transfer.cli.service_client import ApiClient
from mml_cloud_transfer.cli.transfer_command import _api_client


def _find_profile(client: ApiClient, name: str) -> dict | None:
    for profile in client.list_profiles():
        if profile["name"] == name:
            return profile
    return None


def _print_created(result: dict) -> None:
    print(f"Profile {result['name']!r} created and validated against"
          f" gs://{result['bucket']}.")
    print(result["summary"])


def run_profile(args) -> int:
    if not args.service_url:
        print("profile commands need the service: pass --service-url"
              " (or set MMLCT_SERVICE_URL)")
        return 2

    command = args.profile_command

    if command == "add-key":
        try:
            key = json.loads(Path(args.key_file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"cannot read key file: {exc}")
            return 2
        if key.get("type") != "service_account":
            print(f"{args.key_file} is not a service-account key"
                  f" (type={key.get('type')!r}; expected 'service_account')")
            return 2
        client = _api_client(args)
        result = client.create_profile({
            "name": args.name, "bucket": args.bucket,
            "auth_type": "service_account_key", "credential": key,
            "project_id": args.project or key.get("project_id", ""),
            "default_prefix": args.prefix,
            "emulator_endpoint": args.emulator_endpoint,
        })
        _print_created(result)
        print("The service now holds an encrypted copy of this key."
              " You may delete the original file:")
        print(f"  {args.key_file}")
        return 0

    if command == "login":
        config = load_client_config(args.client_config)
        client = _api_client(args)
        client.health()  # a typo'd URL or stopped service fails HERE — before
                         # a browser flow mints a refresh token nobody stores
        if _find_profile(client, args.name) is not None:
            print(f"a profile named {args.name!r} already exists — remove it"
                  " first or pick another name")
            return 1
        print("Tip: for unattended, recurring transfers a least-privilege"
              " service account key (object access to one bucket) is"
              " recommended; Google sign-in suits interactive use.")
        print("A browser window will open for Google sign-in...")
        credential = run_login(config)
        result = client.create_profile({
            "name": args.name, "bucket": args.bucket,
            "auth_type": "oauth_user", "credential": credential,
            "project_id": args.project or "",
            "default_prefix": args.prefix,
            "emulator_endpoint": args.emulator_endpoint,
        })
        _print_created(result)
        return 0

    client = _api_client(args)

    if command == "list":
        profiles = client.list_profiles()
        if not profiles:
            print("No profiles.")
            return 0
        for profile in profiles:
            target = f"gs://{profile['bucket']}/{profile['default_prefix']}".rstrip("/")
            checked = profile["validated_at"] or "never"
            print(f"{profile['name']}: {target} [{profile['auth_type']}]"
                  f" last check: {checked}")
        return 0

    if command in ("check", "remove"):
        profile = _find_profile(client, args.name)
        if profile is None:
            print(f"no profile named {args.name!r}")
            return 1
        if command == "remove":
            client.delete_profile(profile["id"])
            print(f"Profile {args.name!r} removed.")
            return 0
        result = client.check_profile(
            profile["id"], direction=args.direction, prefix=args.prefix
        )
        print(result["summary"])
        return 0 if result["ok"] else 1

    return 2
