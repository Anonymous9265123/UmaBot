"""Load the Discord token for startup."""

import os
import sys
from getpass import getpass


def discord_token():
    """Read the environment token or prompt during interactive development."""
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if token:
        return token
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit("Set DISCORD_TOKEN, or run UmaBot in an interactive terminal for setup.")
    token = getpass("Discord bot token (hidden): ").strip()
    if not token:
        raise SystemExit("A Discord bot token is required.")
    return token
