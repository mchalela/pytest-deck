"""Check that a release tag matches the version in the source tree.

The release workflow runs this before anything is built or uploaded, because a
wrong version on PyPI cannot be replaced. It compares the tag against every
place the version is written and requires a dated CHANGELOG section for it.
With ``--notes`` it also writes that section to a file, which becomes the body
of the GitHub release.

Usage::

    python .github/scripts/release_guard.py v1.2.3 [--notes OUT.md]

Standard library only, so it runs before any dependency is installed.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TAG_RE = re.compile(r"v(\d+\.\d+\.\d+)")


def _search(path, pattern):
    """Return group 1 of the first match of ``pattern`` in ``path``, or None."""
    match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


def source_versions():
    """Return every version written in the tree, keyed by where it lives."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    return {
        "pyproject.toml": pyproject["project"]["version"],
        "pytest_deck/__init__.py": _search(
            "pytest_deck/__init__.py", r'^__version__ = "([^"]+)"$'
        ),
        "docs/conf.py (fallback)": _search("docs/conf.py", r'^\s+release = "([^"]+)"$'),
        "frontend/package.json": package["version"],
        "frontend/package-lock.json": lock["version"],
        'frontend/package-lock.json (packages[""])': lock["packages"][""]["version"],
    }


def changelog_section(version):
    """Return the body of the dated CHANGELOG section for ``version``, or None."""
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    heading = re.compile(rf"## {re.escape(version)}, \d{{4}}-\d{{2}}-\d{{2}}")
    start = next((i for i, line in enumerate(lines) if heading.fullmatch(line)), None)
    if start is None:
        return None
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def main(argv=None):
    """Run the checks; return 0 when the tag is safe to release."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the pushed tag, for example v1.2.3")
    parser.add_argument("--notes", help="write the CHANGELOG section to this file")
    args = parser.parse_args(argv)

    match = TAG_RE.fullmatch(args.tag)
    if match is None:
        print(f"error: tag {args.tag!r} is not of the form vX.Y.Z")
        return 1
    version = match.group(1)

    errors = [
        f"{where} says {found!r}, the tag says {version!r}"
        for where, found in source_versions().items()
        if found != version
    ]

    notes = changelog_section(version)
    if notes is None:
        errors.append(f"CHANGELOG.md has no '## {version}, YYYY-MM-DD' heading")
    elif not notes:
        errors.append(f"CHANGELOG.md section for {version} is empty")

    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1

    if args.notes:
        Path(args.notes).write_text(notes + "\n", encoding="utf-8")
    print(f"ok: {args.tag} matches every version site and has a CHANGELOG section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
