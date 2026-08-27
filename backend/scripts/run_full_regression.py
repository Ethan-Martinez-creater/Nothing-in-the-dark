"""One-command backend/frontend regression gate; E2E is opt-in via --e2e."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e2e", action="store_true", help="also run live Playwright gates")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    run([sys.executable, "-m", "pytest", "tests", "-q"], root / "backend")
    run(["npm", "test"], root / "frontend")
    run(["npm", "run", "build"], root / "frontend")
    if args.e2e:
        run(["npm", "run", "e2e:smoke"], root / "frontend")
        run(["npm", "run", "e2e:interact"], root / "frontend")
    print("full regression gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
