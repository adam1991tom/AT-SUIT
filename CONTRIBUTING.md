<img src="branding/logo/atsuit-wordmark.png" alt="AT-SUIT" width="200">

# Working on AT-SUIT

- Branch from `main`, open a pull request, and let CI go green before merging. `main` is always releasable.
- Run the tests: `cd server && python -m pytest -q`. Set `ATSUIT_TEST_MODELS` to a folder holding the speech model to include the captions test.
- Keep each module in `server/atsuit/modules/` self-contained: its routes, its checks, and a migration in `db.py` for any table it adds. Never edit an old migration; add a new one.
- Anything a venue might change belongs in the console's Venue and Site sections, not in a config file.
- User-facing changes get a line in `CHANGELOG.md`.
- Releases: bump `VERSION`, add the CHANGELOG section, merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

## Logo and brand

The logo, icons, colours and font live in `branding/` with a guide in
`branding/manual/`. Don't edit the generated files by hand: change
`branding/make_brand.py` and run it (`--app-only` just refreshes the copies
in `server/atsuit/static/brand/` and `node-app/`).
