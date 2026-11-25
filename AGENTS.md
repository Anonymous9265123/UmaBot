# UmaBot contributor guidance

- Follow the [README](README.md) for setup. Run `python -m ruff check .` and `python -m unittest discover -s tests` with the project's virtual environment.
- Use the existing shared feed and HTTP helpers so commands and background jobs share cached data, in-progress requests, and per-host cooldowns.
- Reuse catalog transformations and search indexes across commands and autocomplete. Refresh derived data when its source catalog changes.
- Before expanding startup preloads, estimate cold transfer size and retained RAM. Count shared downloads once and include separately allocated rows and indexes.
- Mock upstream responses in automated tests. For cache and concurrency changes, assert request counts as well as returned results.
- Report local benchmarks separately from live Discord latency. Keep test counts and polling intervals in their existing sources.
