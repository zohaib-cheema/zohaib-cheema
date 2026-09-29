# Profile tools

The tools use Python 3.11 or newer. Tests use pytest. Git is required for local checkout inspection.

## Regenerate the language graphic

```bash
python tools/portfolio_audit.py
```

This reads GitHub's public repository language API and writes `data/languages.json` and `assets/languages.svg`. Optional `GITHUB_TOKEN` authentication increases the API rate limit. Credentials are read from the environment and are not written to output.

For an offline review, supply a previously saved JSON snapshot and one or more local checkout overlays:

```bash
python tools/portfolio_audit.py --snapshot snapshot.json --checkout DeFacto=/path/to/DeFacto
```

The snapshot accepts a `repos` or `repositories` list with repository names, fork status, and language byte dictionaries. Local scans use tracked files plus untracked files allowed by Git ignore rules. They respect generated and vendored attributes, include explicitly detectable SQL, and skip symbolic links. They are labeled as local estimates because extension based detection can differ from GitHub Linguist.

The graphic includes all detected languages. Source volume measures file composition and does not establish expertise or contribution quality. The manual GitHub workflow produces an artifact for review rather than making automatic activity commits.

## Calendar previews

```bash
python tools/calendar_preview.py --sample --start 2025-11-03 --end 2026-11-02 --output previews/calendar
```

The sample uses reproducible variation, small daily counts, and changing repository focus. Every generated page labels it as an illustration. Use `--allow-gaps` for a sample with rest days or `--seed` for another reproducible distribution.

To inspect existing local history instead:

```bash
python tools/calendar_preview.py --local --start 2025-11-03 --end 2026-11-02 --checkout /path/to/repo --email YOUR_EMAIL
```

Local mode reads author dates from the selected branch, filters the selected author, and deduplicates shared commit hashes. It does not rewrite timestamps. A CSV with `date`, `count`, and optional `repository` columns can also be rendered with `--counts`.

The tools do not create commits, manipulate awards, change repository visibility, or publish reconstructed history. They provide reviewable source measurements and visual planning artifacts.
