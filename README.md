# locsync — Android ⇄ Google Sheet localization sync

Two-way sync between `res/values/strings.xml` (keys = source of truth) and a
Google Sheet (translations = source of truth), designed to run in CI.

## Layout
- `locsync/` — Python package (`push`, `pull`, `validate`)
- `locsync.config.json` — sheet id, locales, res dir, fallback mode
- `.github/workflows/localization.yml` — CI wiring
- `app/src/main/res/values/strings.xml` — sample source strings
- `tests/test_local.py` — offline sanity tests (no network)

## Sheet schema
One tab per locale. Header row: `key | value | comment | translated`.
Column A is key-locked: only `strings.xml` may add/remove keys.

## Local usage
```bash
pip install -r requirements.txt
export GOOGLE_SA_JSON=/path/to/service-account.json   # or the JSON itself
python -m locsync push       # xml → sheet (adds/removes keys, keeps values)
python -m locsync pull       # sheet → xml (regenerates values-<locale>/strings.xml)
python -m locsync validate   # CI gate: missing keys, placeholder mismatches
```

Run offline sanity tests:
```bash
python3 -m pytest tests
```

## Behavior
- **push**: copies the source xml text into the source locale's tab (an edited source string replaces the old one); adds new keys as empty untranslated rows; drops rows for keys removed from xml; never overwrites existing translations; stray sheet keys are logged and dropped.
- **pull**: writes `values-<locale>/strings.xml` from the sheet and never touches the source locale's `values/strings.xml`; rows with empty value or `translated=false` fall back to source (per `fallback` config: `source` or `skip`). Plurals and string-arrays live only in xml and are copied through.
  Before writing, each value is cleaned up:
  - a value wrapped in one pair of double quotes (`"Consulta a nuestros "`) loses
    that pair; the inner text is kept exactly, and Android quoting is re-added only
    when it has leading/trailing spaces. Quotes inside the text stay escaped (`\"`).
  - iOS `%@` becomes `%s`; with several placeholders each `%@` becomes `%1$s`,
    `%2$s`, … by position. `%1$@` becomes `%1$s`.
- **validate** (CI gate, non-zero exit on failure):
  - missing keys per locale
  - placeholder count/order mismatch (`%1$s`, `%d`, …)
  - a translation with placeholders whose source has none
  - an iOS placeholder (`%@`, `%1$@`) in a translation
  - a translation wrapped in quotes as a whole when its source is not (a warning
    when the source is quoted too, since `push` mirrors Android quoting into the
    sheet and `pull` removes it)
  - keys in the sheet that are no longer in the source xml: a warning, since the
    post-merge `push` drops them (an error only with `--strict`). Deleting a
    string in a PR therefore doesn't block it.

## CI
- PR touching source xml → `push`.
- Merge to `main` and daily cron → `pull` + `validate`, opens a PR with regenerated translations if anything changed.

Secret required: `GOOGLE_SA_JSON` (service account with edit access to the sheet).
