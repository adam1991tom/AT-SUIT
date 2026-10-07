# Working on AT-SUIT

- Branch from `main`, open a pull request, and let CI go green before merging. `main` is always releasable.
- Run the tests: `cd server && python -m pytest -q`. Set `ATSUIT_TEST_MODELS` to a folder holding the speech model to include the captions test.
- Keep each module in `server/atsuit/modules/` self-contained: its routes, its checks, and a migration in `db.py` for any table it adds. Never edit an old migration; add a new one.
- Anything a venue might change belongs in the Admin pages, not in a config file.
- User-facing changes get a line in `CHANGELOG.md`.
- Releases: bump `VERSION`, add the CHANGELOG section, merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`.
