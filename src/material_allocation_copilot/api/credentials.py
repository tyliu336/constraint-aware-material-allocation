"""Local credential provisioning helper. Execute manually, never at API startup."""

import argparse
import hashlib
import json
import secrets

from material_allocation_copilot.api.auth import CredentialEntry
from material_allocation_copilot.application.access import Principal, Role


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a scoped local API bearer credential.")
    parser.add_argument("--user", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument(
        "--role", action="append", choices=[role.value for role in Role], required=True
    )
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    entry = CredentialEntry(
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        principal=Principal(
            user_id=args.user,
            project_ids=frozenset(args.project),
            roles=frozenset(args.role),
        ),
    )
    print(
        json.dumps(
            {"bearer_token": token, "credential_entry": entry.model_dump(mode="json")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
