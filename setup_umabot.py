"""Create UmaBot's virtual environment, install dependencies, and start the bot."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-only", action="store_true", help="Prepare the environment without starting Discord.")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.exit(1, "UmaBot needs Python 3.11 or newer. Python 3.13 is tested in CI.\n")

    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not python.is_file():
            print("Creating .venv", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(environment)], cwd=root, check=True)
        print("Installing UmaBot dependencies", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "--disable-pip-version-check", "install", "-r", "requirements.txt"],
            cwd=root,
            check=True,
        )
        if not args.install_only:
            print("Starting UmaBot", flush=True)
            return subprocess.call([str(python), "-m", "umabot"], cwd=root)
    except subprocess.CalledProcessError as error:
        print("Setup failed. See the output above for the command that failed.", file=sys.stderr)
        return error.returncode
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
