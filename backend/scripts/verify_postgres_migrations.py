"""Reproducible PostgreSQL migration-chain verifier for a disposable test DB.

Set COIFESP_PG_TEST_URL to an empty/disposable database whose name contains
"test" or "ci". The script intentionally refuses production-looking names.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def run(*args: str, env: dict[str, str], cwd: Path) -> None:
    completed = subprocess.run(args, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    url = os.environ.get("COIFESP_PG_TEST_URL", "")
    if not url:
        print("COIFESP_PG_TEST_URL is required", file=sys.stderr)
        return 2
    database = urlparse(url).path.rsplit("/", 1)[-1].lower()
    if not any(marker in database for marker in ("test", "ci")):
        print("refusing destructive migration verification on a non-test database", file=sys.stderr)
        return 2
    backend = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    python = sys.executable
    run(python, "-m", "alembic", "downgrade", "base", env=env, cwd=backend)
    run(python, "-m", "alembic", "upgrade", "head", env=env, cwd=backend)
    run(python, "-m", "alembic", "downgrade", "-1", env=env, cwd=backend)
    run(python, "-m", "alembic", "upgrade", "head", env=env, cwd=backend)
    run(python, "-m", "alembic", "current", env=env, cwd=backend)
    print("PostgreSQL base->head and downgrade/upgrade verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
