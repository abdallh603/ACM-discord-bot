"""Check the Git index for runtime files and credential-like strings without printing secrets."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    paths = [p for p in result.stdout.decode().split("\0") if p]
    if not paths:
        raise SystemExit("No staged/tracked release files to check.")
    failures = []
    for name in paths:
        path = Path(name)
        if ((path.name.startswith(".env") and path.name != ".env.example")
                or any(part in {"data", ".venv", ".test-deps", "__pycache__"} for part in path.parts)
                or path.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pyc"}):
            failures.append(f"{name}: runtime/private file must not be tracked")
            continue
        blob = subprocess.run(["git", "show", f":{name}"], cwd=ROOT, check=True, capture_output=True).stdout
        content = blob.decode("utf-8", errors="replace")
        for number, line in enumerate(content.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                failures.append(f"{name}:{number}: credential-like content; inspect locally")
    if failures:
        raise SystemExit("Release check failed:\n" + "\n".join(failures))
    print(f"Release check passed: {len(paths)} indexed files; no runtime files or recognized credential patterns.")


if __name__ == "__main__":
    main()
