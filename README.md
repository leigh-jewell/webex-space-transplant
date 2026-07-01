# Webex Space Transplant

Small CLI script that uses the [Webex Developer API](https://developer.webex.com/) to:

1. Fetch all spaces you are a member of
2. Search space titles for `eurl.io` / `https://eurl.io...`
3. Print matches in a two-column table (`Space Name`, `URL`)
4. Save the same matches to `webex_space_transplant.csv`
5. Optionally compare your room membership to a master list and export missing spaces

## Requirements

- Python 3.12+

## Run

```bash
python3 webex_space_transplant.py
```

To enable debug logging:

```bash
python3 webex_space_transplant.py -d
```

To use an environment variable instead of the interactive prompt:

```bash
export WEBEX_ACCESS_TOKEN="your_token_here"
python3 webex_space_transplant.py
```

The script prompts for your Webex Developer API token when running export or membership-audit flows.

- If `WEBEX_ACCESS_TOKEN` is set, that token is used instead of prompting.
- Token input is masked with `*` as you type.
- Token is verified after entry; if invalid, you are prompted again.
- If token verification fails with SSL certificate errors, configure `SSL_CERT_FILE` (and optionally `SSL_CERT_DIR`) to a valid CA truststore bundle.
- You can choose to run an interactive EURL join flow from a CSV list.
- The EURL join flow does **not** require a Webex token; only a join email is required.
- You can choose to run an interactive master-membership audit, then provide a master CSV path.
- You can choose whether to also include spaces that start with `Ask` even when no `eurl.io` URL is present.
- A live status bar is shown while spaces are being scanned.
- `-d` writes debug output to a timestamped log file and does not log the API token.
- A startup banner is printed when the tool launches.

### Join spaces via EURL from CSV

Use this mode to send join requests to EURL for each row in a CSV containing `eurl` links.

```bash
python3 webex_space_transplant.py --join-from-csv
```

Optional arguments:

```bash
python3 webex_space_transplant.py --join-from-csv ./my_spaces.csv --join-email your.name@company.com
```

- `--join-from-csv` with no value defaults to `webex_missing_spaces.csv`
- No Webex token is required for this mode.
- Supported link format for this mode: `https://eurl.io/#...` (or `https://eurl.io/<shortid>`)
- Results are written to `webex_join_results.csv` with:
  - `space_name`
  - `eurl`
  - `shortid`
  - `status`
  - `detail`

### Check against a master spaces list

Use this mode to compare your current memberships with a known list (default filename: `master.csv`).

```bash
python3 webex_space_transplant.py --check-master-membership
```

Or pass a different path:

```bash
python3 webex_space_transplant.py --check-master-membership --master-csv ./path/to/master.csv
```

Master CSV headers:

- `space_name` (required; aliases supported: `space`, `title`, `room_name`)
- `eurl` (required; alias supported: `url`)

This mode prints spaces you are **not** a member of and writes `webex_missing_spaces.csv` with:

- `space_name`
- `eurl`

If you run the script without membership flags, it also offers this as an interactive option:

1. `Audit your spaces against a master CSV list?`
2. `Enter master CSV path [master.csv]:`

### Audit and refresh `master.csv` space names

Use the audit script to resolve each EURL and update `space_name` when it differs from the title returned by EURL:

```bash
python3 audit_master.py --input master.csv
```

By default this updates `master.csv` in place and creates `master.csv.bak`.

To write to a separate output file:

```bash
python3 audit_master.py --input master.csv --output master_audited.csv
```

For rows that use `webexteams://im?space=...`, provide a Webex token:

```bash
export WEBEX_ACCESS_TOKEN="your_token_here"
python3 audit_master.py --input master.csv
```

## Output

- **Terminal output:** two-column table of matching spaces (`Space Name`, `URL`)
  - Space names are truncated with `…` when needed to fit terminal width.
- **Run summary:** totals for spaces scanned, matched, EURL matches, and Ask rooms.
- **CSV output:** `webex_space_transplant.csv` with headers:
  - `space_name`
  - `url`
  - `space_link`


`space_link` is written as a Webex deep link in the format `webexteams://im?space=<encoded_room_id>`.

If no matches are found, it prints a message and does not create the CSV file.
