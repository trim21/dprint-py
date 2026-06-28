"""Sync project.version to match upstream context.version in pyproject.toml."""

import sys
import tomllib
from pathlib import Path

PYPROJECT = Path("pyproject.toml")


def main() -> int:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    pack_binary = data["tool"]["pack-binary"]
    upstream_version = pack_binary["context"]["version"]
    project_version = pack_binary["project"]["version"]
    new_project_version = upstream_version + ".0"

    if project_version == new_project_version:
        print(f"project.version already matches: {project_version}")
        return 0

    print(f"Updating project.version: {project_version} -> {new_project_version}")

    content = PYPROJECT.read_text()

    # Try replacing each occurrence of the old version string,
    # parse as TOML, and verify the target field matches.
    needle = f"'{project_version}'"
    replacement = f"'{new_project_version}'"
    found = False

    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            break

        candidate = content[:idx] + replacement + content[idx + len(needle) :]

        try:
            parsed = tomllib.loads(candidate)
        except Exception:
            start = idx + len(needle)
            continue

        if parsed["tool"]["pack-binary"]["project"]["version"] == new_project_version:
            content = candidate
            found = True
            break

        start = idx + len(needle)

    if not found:
        print(
            f"Error: could not find a replacement for '{project_version}' "
            f"that yields version '{new_project_version}' in "
            f"[tool.pack-binary.project]",
            file=sys.stderr,
        )
        return 1

    PYPROJECT.write_text(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
