"""Audit public repository languages and render a reproducible profile graphic.

Live mode uses GitHub language bytes. Snapshot mode can overlay local checkouts
for review before publication. An overlay is explicitly labeled as an estimate.
The tool never creates commits, changes repository visibility, or edits history.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

EXTENSIONS = {
    ".py": "Python",
    ".java": "Java",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".h": "C",
    ".hpp": "C++",
    ".c": "C",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".cjs": "JavaScript",
    ".mjs": "JavaScript",
    ".sql": "SQL",
    ".dart": "Dart",
    ".swift": "Swift",
    ".r": "R",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".kt": "Kotlin",
    ".m": "Objective-C",
    ".ejs": "EJS",
    ".ipynb": "Jupyter Notebook",
    ".cmake": "CMake",
    ".go": "Go",
    ".rs": "Rust",
    ".vue": "Vue",
    ".rb": "Ruby",
}
COLORS = {
    "Python": "#3572A5",
    "Java": "#b07219",
    "C++": "#f34b7d",
    "TypeScript": "#3178c6",
    "JavaScript": "#b6a300",
    "SQL": "#b6a0e1",
    "Swift": "#F05138",
    "Dart": "#00B4AB",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "R": "#198CE7",
    "Shell": "#648b34",
}


class AuditError(ValueError):
    pass


def github_json(route: str):
    headers = {
        "User-Agent": "portfolio-source-audit",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request("https://api.github.com" + route, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise AuditError(
                "GitHub declined the request or the rate limit was reached"
            ) from error
        raise AuditError(f"GitHub returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise AuditError("Could not connect securely to GitHub") from error


def live_repositories(owner: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
        raise AuditError("Invalid GitHub account name")
    repos = []
    for page in range(1, 101):
        batch = github_json(f"/users/{owner}/repos?per_page=100&page={page}&type=owner")
        if not isinstance(batch, list):
            raise AuditError("Unexpected repository response")
        repos.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise AuditError("Repository pagination exceeded the supported limit")
    repos = [repo for repo in repos if not repo.get("fork") and not repo.get("private")]

    def inspect(repo):
        return {
            "name": repo["name"],
            "fork": False,
            "languages": github_json(f"/repos/{owner}/{repo['name']}/languages"),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(inspect, repos))


def validate_repositories(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise AuditError("Repository data must be a list")
    names = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not row["name"]
        ):
            raise AuditError("Each repository needs a name")
        if row["name"] in names:
            raise AuditError("Repository names must be unique")
        names.add(row["name"])
        languages = row.get("languages")
        if not isinstance(languages, dict) or any(
            not isinstance(key, str) or type(value) is not int or value < 0
            for key, value in languages.items()
        ):
            raise AuditError("Language counts must be nonnegative integer byte counts")
    return rows


def git(root: Path, *arguments: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=input,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AuditError("The checkout could not be inspected with Git")
    return result.stdout


def scan_checkout(root: Path) -> dict:
    root = root.resolve()
    if git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root):
        raise AuditError("The checkout path must point to a repository root")
    raw_paths = git(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    )
    paths = sorted(set(part.decode("utf-8") for part in raw_paths.split(b"\0") if part))
    raw_attributes = git(
        root,
        "check-attr",
        "-z",
        "--stdin",
        "linguist-generated",
        "linguist-vendored",
        "linguist-detectable",
        input=b"\0".join(path.encode() for path in paths) + b"\0",
    )
    parts = raw_attributes.decode().split("\0")
    attributes = collections.defaultdict(dict)
    for index in range(0, len(parts) - 1, 3):
        name, key, value = parts[index : index + 3]
        attributes[name][key] = value
    languages, excluded, sources = collections.Counter(), [], []
    for name in paths:
        path = root / name
        attrs = attributes[name]
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
        ):
            continue
        if any(
            attrs.get(key) in {"true", "set"}
            for key in ["linguist-generated", "linguist-vendored"]
        ):
            excluded.append(name)
            continue
        if attrs.get("linguist-detectable") in {"false", "unset"}:
            excluded.append(name)
            continue
        language = EXTENSIONS.get(path.suffix.lower())
        if path.name == "CMakeLists.txt":
            language = "CMake"
        elif path.name == "Dockerfile":
            language = "Dockerfile"
        elif path.name == "Procfile":
            language = "Procfile"
        if language == "SQL" and attrs.get("linguist-detectable") not in {
            "true",
            "set",
        }:
            continue
        if language:
            size = path.stat().st_size
            languages[language] += size
            sources.append({"path": name, "language": language, "bytes": size})
    return {
        "languages": dict(languages),
        "excluded": excluded,
        "sources": sources,
        "branch": git(root, "branch", "--show-current").decode().strip(),
    }


def summarize(
    rows: list[dict], owner: str, overlays: dict[str, Path] | None = None
) -> dict:
    rows = json.loads(json.dumps(validate_repositories(rows)))
    overlays = overlays or {}
    by_name = {row["name"]: row for row in rows}
    for name, checkout in overlays.items():
        if name not in by_name:
            raise AuditError(f"Unknown checkout repository: {name}")
        inspected = scan_checkout(checkout)
        by_name[name]["languages"] = inspected["languages"]
        by_name[name]["local_review"] = inspected
    totals = collections.Counter()
    for row in rows:
        if not row.get("fork"):
            totals.update(row["languages"])
    total = totals.total()
    if total == 0:
        raise AuditError("No source language bytes were found")
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return {
        "owner": owner,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "Public owned repositories, excluding forks",
        "method": "Local source estimate over GitHub baseline"
        if overlays
        else "GitHub language API bytes",
        "local_estimate": bool(overlays),
        "repository_count": sum(not row.get("fork") for row in rows),
        "total_bytes": total,
        "largest_language": ordered[0][0],
        "languages": [
            {"name": name, "bytes": count, "percent": 100 * count / total}
            for name, count in ordered
        ],
        "repositories": rows,
        "notes": [
            "Source volume is not a measure of skill or contribution quality.",
            "Generated and vendored files follow repository attributes.",
            "Local extension based estimates can differ from GitHub Linguist detection.",
        ]
        if overlays
        else ["Source volume is not a measure of skill or contribution quality."],
    }


def render_svg(audit: dict) -> str:
    languages = audit["languages"]
    width, bar_width = 880, 816
    rows = (len(languages) + 2) // 3
    height = 138 + rows * 32 + 42
    title = "Proposed source mix" if audit["local_estimate"] else "Source languages"
    escape = html.escape
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
        f'<title id="title">{title}</title>',
        f'<desc id="description">{escape(audit["scope"])}. '
        + escape(
            ", ".join(
                f"{row['name']} {row['percent']:.1f} percent" for row in languages
            )
        )
        + "</desc>",
        f'<rect width="{width - 1}" height="{height - 1}" x=".5" y=".5" rx="14" fill="#ffffff" stroke="#d1d9e0"/>',
        f'<text x="32" y="38" fill="#1f2328" font-family="system-ui,sans-serif" font-size="21" font-weight="650">{title}</text>',
        '<text x="32" y="62" fill="#59636e" font-family="system-ui,sans-serif" font-size="13">Public source bytes. Generated artifacts are excluded.</text>',
        '<defs><clipPath id="bar"><rect x="32" y="84" width="816" height="12" rx="6"/></clipPath></defs>',
        '<g clip-path="url(#bar)">',
    ]
    x = 32.0
    for row in languages:
        length = bar_width * row["percent"] / 100
        color = COLORS.get(row["name"], "#8c959f")
        pieces.append(
            f'<rect x="{x:.4f}" y="84" width="{length:.4f}" height="12" fill="{color}"><title>{escape(row["name"])}: {row["percent"]:.2f}%</title></rect>'
        )
        x += length
    pieces.append("</g>")
    for index, row in enumerate(languages):
        x = 32 + (index % 3) * 272
        y = 132 + (index // 3) * 32
        color = COLORS.get(row["name"], "#8c959f")
        pieces.append(f'<circle cx="{x + 5}" cy="{y - 5}" r="5" fill="{color}"/>')
        pieces.append(
            f'<text x="{x + 19}" y="{y}" fill="#1f2328" font-family="system-ui,sans-serif" font-size="14">{escape(row["name"])} <tspan fill="#59636e">{row["percent"]:.1f}%</tspan></text>'
        )
    label = (
        "Local review estimate. GitHub will recalculate after publication."
        if audit["local_estimate"]
        else f"{audit['repository_count']} repositories · Updated {audit['generated_at'][:10]}"
    )
    pieces.append(
        f'<text x="32" y="{height - 22}" fill="#59636e" font-family="system-ui,sans-serif" font-size="12">{escape(label)}</text></svg>'
    )
    return "\n".join(pieces)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="zohaib-cheema")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--checkout", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--output", type=Path, default=Path("data/languages.json"))
    parser.add_argument("--svg", type=Path, default=Path("assets/languages.svg"))
    args = parser.parse_args(argv)
    try:
        if args.snapshot:
            snapshot = json.loads(args.snapshot.read_text())
            rows = snapshot.get("repos", snapshot.get("repositories"))
        else:
            rows = live_repositories(args.owner)
        overlays = {}
        for value in args.checkout:
            name, separator, path = value.partition("=")
            if not separator or not name or not path or name in overlays:
                raise AuditError("Checkouts must use distinct NAME=PATH pairs")
            overlays[name] = Path(path)
        audit = summarize(rows, args.owner, overlays)
        for destination in [args.output, args.svg]:
            destination.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        args.svg.write_text(render_svg(audit), encoding="utf-8")
        print(
            json.dumps(
                {
                    "largest_language": audit["largest_language"],
                    "method": audit["method"],
                    "output": str(args.output),
                    "svg": str(args.svg),
                },
                indent=2,
            )
        )
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"Audit failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
