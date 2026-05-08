"""One-shot Gmail OAuth flow — produces data/gmail_token.json.

Run this once, after dropping your OAuth client (Desktop app) JSON into
`data/credentials.json`. A browser window will open, you grant the
`gmail.modify` scope, and a refresh token is saved for future runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.tools import gmail  # noqa: E402


def main() -> None:
    svc = gmail._get_service()
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Authenticated as {profile['emailAddress']}")
    print(f"Token written to {gmail._token_path()}")
    label_id = gmail._get_handled_label_id()
    print(f"Label `{gmail.HANDLED_LABEL}` id={label_id}")


if __name__ == "__main__":
    main()
