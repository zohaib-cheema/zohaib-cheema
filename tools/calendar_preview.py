"""Render local commit activity or an explicitly illustrative calendar.

The sample is a visual planning artifact. This tool never creates a commit,
changes a timestamp, or pushes anything to GitHub. Local activity is also not
equivalent to the GitHub graph, whose attribution and branch rules are separate.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import html
import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


@dataclass(frozen=True)
class Day:
    date: dt.date
    count: int
    repository: str = ""


def dates(start: dt.date, end: dt.date) -> list[dt.date]:
    length = (end - start).days + 1
    if not 1 <= length <= 370:
        raise ValueError("Choose a calendar range with 1 to 370 days")
    return [start + dt.timedelta(days=offset) for offset in range(length)]


def sample_days(
    start: dt.date, end: dt.date, seed: int, repositories: list[str], gaps=False
) -> list[Day]:
    if not repositories or any(
        not isinstance(name, str) or not name.strip() for name in repositories
    ):
        raise ValueError("Provide at least one repository name")
    rng = random.Random(seed)
    current = repositories[0]
    output = []
    weekly_activity = 1.0
    for date in dates(start, end):
        if date.weekday() == 0:
            weekly_activity = rng.choice([0.8, 1, 1, 1.3])
        if rng.random() < 0.16:
            current = rng.choices(
                repositories,
                weights=[2 if index == 0 else 1 for index in range(len(repositories))],
            )[0]
        base = rng.choices([1, 2, 3, 4, 5, 6], weights=[38, 29, 17, 10, 5, 1])[0]
        weekend = 0.75 if date.weekday() >= 5 else 1
        count = max(1, min(7, round(base * weekly_activity * weekend)))
        if gaps and rng.random() < (0.3 if date.weekday() >= 5 else 0.1):
            count = 0
        output.append(Day(date, count, current if count else ""))
    return output


def local_days(
    start: dt.date, end: dt.date, checkouts: list[Path], email: str, ref: str = "main"
) -> list[Day]:
    if not email or "\n" in email:
        raise ValueError("Provide the author email to count")
    if not checkouts:
        raise ValueError("Provide at least one local checkout")
    counts, repos = collections.Counter(), collections.defaultdict(set)
    seen = set()
    for checkout in checkouts:
        result = subprocess.run(
            ["git", "-C", str(checkout), "log", ref, "--format=%H%x09%aI%x09%ae"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise ValueError(f"Could not inspect local history for {checkout.name}")
        for line in result.stdout.splitlines():
            sha, timestamp, author = line.split("\t", 2)
            if author.casefold() != email.casefold() or sha in seen:
                continue
            seen.add(sha)
            # Respect the author timestamp's own date and offset, without rewriting it.
            date = dt.datetime.fromisoformat(timestamp).date()
            if start <= date <= end:
                counts[date] += 1
                repos[date].add(checkout.name)
    return [
        Day(date, counts[date], ", ".join(sorted(repos[date])))
        for date in dates(start, end)
    ]


def read_counts(path: Path, start: dt.date, end: dt.date) -> list[Day]:
    found = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            try:
                date, count = dt.date.fromisoformat(row["date"]), int(row["count"])
            except (KeyError, ValueError) as error:
                raise ValueError(
                    "CSV rows need an ISO date and an integer count"
                ) from error
            if date in found or count < 0:
                raise ValueError("Dates must be unique and counts cannot be negative")
            found[date] = Day(date, count, row.get("repository", ""))
    return [found.get(date, Day(date, 0)) for date in dates(start, end)]


def level(count: int) -> int:
    if count < 0:
        raise ValueError("Counts cannot be negative")
    if count == 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 5:
        return 3
    return 4


def calendar_svg(days: list[Day], sample: bool) -> str:
    if not days:
        raise ValueError("The calendar is empty")
    expected = dates(days[0].date, days[-1].date)
    if [day.date for day in days] != expected:
        raise ValueError("Calendar days must be consecutive and ordered")
    start = days[0].date - dt.timedelta(days=(days[0].date.weekday() + 1) % 7)
    weeks = ((days[-1].date - start).days // 7) + 1
    width = max(420, 72 + weeks * 15)
    title = "Illustrative activity preview" if sample else "Local commit activity"
    subtitle = (
        "Sample dates and counts for video planning"
        if sample
        else "Author dates from the selected local Git history"
    )
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="252" viewBox="0 0 {width} 252" role="img" aria-labelledby="title desc">',
        f'<title id="title">{title}</title><desc id="desc">{subtitle}. Hover a day for its details.</desc>',
        f'<rect x=".5" y=".5" width="{width - 1}" height="251" rx="14" fill="#fff" stroke="#d1d9e0"/>',
        f'<text x="24" y="34" fill="#1f2328" font-family="system-ui,sans-serif" font-size="20" font-weight="650">{title}</text>',
        f'<text x="24" y="57" fill="#59636e" font-family="system-ui,sans-serif" font-size="12">{subtitle}</text>',
    ]
    for weekday, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        pieces.append(
            f'<text x="18" y="{96 + weekday * 15}" font-family="system-ui,sans-serif" font-size="10" fill="#59636e">{label}</text>'
        )
    prior_month = None
    for day in days:
        offset = (day.date - start).days
        week, weekday = offset // 7, offset % 7
        x, y = 58 + week * 15, 86 + weekday * 15
        if day.date.month != prior_month:
            pieces.append(
                f'<text x="{x}" y="77" font-family="system-ui,sans-serif" font-size="10" fill="#59636e">{day.date.strftime("%b")}</text>'
            )
            prior_month = day.date.month
        detail = f"{day.date.strftime('%b %d, %Y')}: {day.count} contributions"
        if day.repository:
            detail += f" in {day.repository}"
        pieces.append(
            f'<rect x="{x}" y="{y}" width="11" height="11" rx="2" fill="{COLORS[level(day.count)]}" data-date="{day.date}" data-count="{day.count}"><title>{html.escape(detail)}</title></rect>'
        )
    footer = (
        "Illustration only. No GitHub history was changed."
        if sample
        else "Local commits can differ from the public GitHub contribution graph."
    )
    pieces.append(
        f'<text x="24" y="226" font-family="system-ui,sans-serif" font-size="11" fill="#59636e">{footer}</text>'
    )
    legend = width - 154
    pieces.append(
        f'<text x="{legend - 29}" y="208" font-family="system-ui,sans-serif" font-size="10" fill="#59636e">Less</text>'
    )
    for index, color in enumerate(COLORS):
        pieces.append(
            f'<rect x="{legend + index * 14}" y="199" width="10" height="10" rx="2" fill="{color}"/>'
        )
    pieces.append(
        f'<text x="{legend + 73}" y="208" font-family="system-ui,sans-serif" font-size="10" fill="#59636e">More</text></svg>'
    )
    return "\n".join(pieces)


def export(days: list[Day], output: Path, sample: bool, seed: int | None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    svg = calendar_svg(days, sample)
    (output / "calendar.svg").write_text(svg, encoding="utf-8")
    with (output / "calendar.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "count", "repository"])
        writer.writerows((day.date, day.count, day.repository) for day in days)
    manifest = {
        "mode": "illustrative sample" if sample else "local or imported counts",
        "start": days[0].date.isoformat(),
        "end": days[-1].date.isoformat(),
        "seed": seed,
        "days": len(days),
        "active_days": sum(day.count > 0 for day in days),
        "maximum_daily_count": max(day.count for day in days),
        "total": sum(day.count for day in days),
        "level_counts": dict(collections.Counter(level(day.count) for day in days)),
        "github_history_changed": False,
    }
    (output / "calendar.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Portfolio calendar preview</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;color:#1f2328;background:#f6f8fa;margin:0;padding:56px 24px}}
main{{max-width:1040px;margin:auto}}h1{{font-size:38px;letter-spacing:-1px}}p{{max-width:72ch;color:#59636e}}
.calendar{{overflow-x:auto;margin:32px 0}}svg{{display:block;max-width:none}}a{{color:#0969da}}
details{{margin-top:32px}}table{{border-collapse:collapse;width:100%;background:#fff}}th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #d1d9e0}}</style>
<main><h1>Portfolio calendar preview</h1><p>{"This is an illustrative calendar for planning a video. It does not represent completed historical work or change your GitHub account." if sample else "This view shows the supplied local activity counts. GitHub applies its own contribution rules."}</p>
<div class="calendar">{svg}</div><p><a href="calendar.csv">Inspect the daily counts</a> · <a href="calendar.json">Inspect the generation settings</a></p>
<details><summary>Daily distribution</summary><table><thead><tr><th>Date</th><th>Count</th><th>Repository focus</th></tr></thead><tbody>"""
    page += "".join(
        f"<tr><td>{day.date.strftime('%b %d, %Y')}</td><td>{day.count}</td><td>{html.escape(day.repository)}</td></tr>"
        for day in days
    )
    page += "</tbody></table></details></main></html>"
    (output / "index.html").write_text(page, encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--sample", action="store_true")
    modes.add_argument("--counts", type=Path)
    modes.add_argument("--local", action="store_true")
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--allow-gaps", action="store_true")
    parser.add_argument(
        "--repositories",
        nargs="+",
        default=["DeFacto", "AwareNest", "Database Systems", "PayPie", "Profile"],
    )
    parser.add_argument("--checkout", type=Path, action="append", default=[])
    parser.add_argument("--email")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--output", type=Path, default=Path("previews/calendar"))
    args = parser.parse_args(argv)
    try:
        if args.sample:
            days = sample_days(
                args.start, args.end, args.seed, args.repositories, args.allow_gaps
            )
        elif args.counts:
            days = read_counts(args.counts, args.start, args.end)
        else:
            days = local_days(args.start, args.end, args.checkout, args.email, args.ref)
        print(
            json.dumps(
                export(
                    days, args.output, args.sample, args.seed if args.sample else None
                ),
                indent=2,
            )
        )
    except (ValueError, OSError) as error:
        parser.exit(2, f"Preview failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
