"""Load the Discord token for startup."""

import os
import subprocess
import sys
from getpass import getpass

from .paths import PROJECT_ROOT


TOKEN_FILE = PROJECT_ROOT / "discord-token.txt"


def read_saved_key(filename):
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f"$secret = Get-Content -LiteralPath '{filename}' | ConvertTo-SecureString; "
        "[System.Net.NetworkCredential]::new('', $secret).Password"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        raise RuntimeError(f"Could not read {filename} from {PROJECT_ROOT}.")
    return key


def discord_token():
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if token:
        return token
    if os.name == "nt" and TOKEN_FILE.is_file():
        try:
            return read_saved_key(TOKEN_FILE.name)
        except RuntimeError:
            print("The saved Windows token could not be read.")
    return None


def save_discord_token(token):
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$secret = ConvertTo-SecureString -String ([Console]::In.ReadToEnd()) -AsPlainText -Force; "
        "$secret | ConvertFrom-SecureString"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        input=token,
        capture_output=True,
        text=True,
    )
    encrypted = result.stdout.strip()
    if result.returncode != 0 or not encrypted:
        raise OSError("Could not encrypt the Discord bot token.")
    TOKEN_FILE.write_text(encrypted + "\n", encoding="ascii")


def startup_settings():
    token = discord_token()
    setup_token = token is None
    if setup_token:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise SystemExit("Set DISCORD_TOKEN, or run UmaBot in an interactive terminal for setup.")
        print("UmaBot terminal setup. Use the bot token from your Discord application.")
        token = getpass("Discord bot token (hidden): ").strip()
        if not token:
            raise SystemExit("A Discord bot token is required.")
    return token, setup_token
