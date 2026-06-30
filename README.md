# Webex Space Transplant

Small CLI script that uses the [Webex Developer API](https://developer.webex.com/) to:

1. Fetch all spaces you are a member of
2. Search space titles for `eurl.io` / `https://eurl.io...`
3. Print matches in a two-column table (`Space Name`, `URL`)
4. Save the same matches to `{username}_webex_space_transplant.csv`

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

The script prompts for your Webex Developer API token.

- If `WEBEX_ACCESS_TOKEN` is set, that token is used instead of prompting.
- Token input is masked with `*` as you type.
- Token is verified after entry; if invalid, you are prompted again.
- You can choose whether to also include spaces that start with `Ask` even when no `eurl.io` URL is present.
- A live status bar is shown while spaces are being scanned.
- `-d` writes debug output to a timestamped log file and does not log the API token.
- A startup banner is printed when the tool launches.

## Output

- **Terminal output:** two-column table of matching spaces (`Space Name`, `URL`)
  - Space names are truncated with `…` when needed to fit terminal width.
- **Run summary:** totals for spaces scanned, matched, EURL matches, and Ask rooms.
- **CSV output:** `{username}_webex_space_transplant.csv` with headers:
  - `space_name`
  - `url`
  - `space_link`

CSV filename is prefixed with your username from Webex profile (for example, `alex_webex_space_transplant.csv`).

`space_link` is written as a Webex deep link in the format `webexteams://im?space=<encoded_room_id>`.

If no matches are found, it prints a message and does not create the CSV file.