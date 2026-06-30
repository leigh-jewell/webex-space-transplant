#!/usr/bin/env python3
import argparse
import csv
import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

EURL_API = "https://eurl.io/api/shortid"
WEBEX_API_BASE = "https://webexapis.com/v1"
DEFAULT_INPUT = "en_master.csv"
DEFAULT_TIMEOUT_SECONDS = 20


@dataclass
class AuditRowResult:
    row_number: int
    eurl: str
    old_name: str
    new_name: str
    status: str
    detail: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit en_master.csv by resolving each EURL and correcting space names."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"CSV file to audit (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path. If omitted, the input file is updated in place.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a .bak backup when updating in place.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("WEBEX_ACCESS_TOKEN", "").strip(),
        help="Webex token for non-eurl links like webexteams://im?space=<id> (defaults to WEBEX_ACCESS_TOKEN).",
    )
    return parser.parse_args()


def resolve_header_name(fieldnames: List[str], candidates: List[str]) -> Optional[str]:
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def load_csv_rows(path: str) -> Tuple[List[Dict[str, str]], List[str], str, str]:
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        fieldnames = list(reader.fieldnames)
        space_name_header = resolve_header_name(
            fieldnames, ["space_name", "space name", "space", "title", "room_name"]
        )
        eurl_header = resolve_header_name(fieldnames, ["eurl", "url", "space_link"])

        if space_name_header is None:
            raise ValueError(
                'CSV requires a space-name header (space_name/space name/space/title/room_name)'
            )
        if eurl_header is None:
            raise ValueError('CSV requires an EURL header (eurl/url/space_link)')

        rows = [{key: (value or "") for key, value in row.items()} for row in reader]
        return rows, fieldnames, space_name_header, eurl_header


def fetch_json(url: str, token: str = "") -> Dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_shortid(link: str) -> Optional[str]:
    parsed = urlparse(link.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower().endswith("eurl.io"):
        if parsed.fragment:
            return parsed.fragment.strip()
        path_value = parsed.path.strip("/")
        if path_value:
            return path_value
    return None


def extract_space_id(link: str) -> Optional[str]:
    parsed = urlparse(link.strip())
    if parsed.scheme == "webexteams":
        query_values = parse_qs(parsed.query)
        values = query_values.get("space", [])
        if values and values[0].strip():
            return values[0].strip()

    query_values = parse_qs(parsed.query)
    values = query_values.get("space", [])
    if values and values[0].strip():
        return values[0].strip()
    return None


def resolve_room_name_from_eurl(link: str) -> str:
    shortid = extract_shortid(link)
    if not shortid:
        raise ValueError("cannot extract eurl short ID")

    payload = fetch_json(f"{EURL_API}/{quote(shortid)}")
    if payload.get("responseCode") != 0:
        raise ValueError(f"eurl responseCode={payload.get('responseCode')}")

    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("eurl returned empty title")
    return title


def resolve_room_name_from_webex_space_link(link: str, token: str) -> str:
    space_id = extract_space_id(link)
    if not space_id:
        raise ValueError("cannot extract Webex space ID from link")
    if not token:
        raise ValueError("Webex token is required for webexteams space links")

    payload = fetch_json(f"{WEBEX_API_BASE}/rooms/{quote(space_id)}", token=token)
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("Webex API returned empty room title")
    return title


def resolve_room_name(link: str, token: str) -> str:
    normalized_link = link.strip()
    if not normalized_link:
        raise ValueError("empty EURL")

    parsed = urlparse(normalized_link)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower().endswith("eurl.io"):
        return resolve_room_name_from_eurl(normalized_link)

    if parsed.scheme == "webexteams" or "space=" in parsed.query:
        return resolve_room_name_from_webex_space_link(normalized_link, token)

    raise ValueError("unsupported link format")


def normalize_name(value: str) -> str:
    return " ".join(value.split()).strip().casefold()


def write_csv(path: str, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def maybe_backup(path: str) -> str:
    backup_path = f"{path}.bak"
    shutil.copyfile(path, backup_path)
    return backup_path


def print_results(results: List[AuditRowResult]) -> None:
    updated = [result for result in results if result.status == "updated"]
    unchanged = [result for result in results if result.status == "unchanged"]
    failed = [result for result in results if result.status == "failed"]

    print("\nAudit summary:")
    print(f"Rows processed: {len(results)}")
    print(f"Updated names: {len(updated)}")
    print(f"Already correct: {len(unchanged)}")
    print(f"Failed lookups: {len(failed)}")

    if updated:
        print("\nUpdated rows:")
        for result in updated:
            print(
                f"- Row {result.row_number}: '{result.old_name}' -> '{result.new_name}' ({result.eurl})"
            )

    if failed:
        print("\nFailed rows:")
        for result in failed:
            print(f"- Row {result.row_number}: {result.detail} ({result.eurl})")


def main() -> int:
    args = parse_args()
    output_path = args.output or args.input

    try:
        rows, fieldnames, space_name_header, eurl_header = load_csv_rows(args.input)
    except FileNotFoundError:
        print(f"Input CSV not found: {args.input}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"Permission denied reading input CSV: {args.input}", file=sys.stderr)
        return 1
    except csv.Error as err:
        print(f"Could not parse CSV ({args.input}): {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"Invalid CSV ({args.input}): {err}", file=sys.stderr)
        return 1

    results: List[AuditRowResult] = []
    total_rows = len(rows)

    for index, row in enumerate(rows, start=2):
        eurl = str(row.get(eurl_header, "")).strip()
        old_name = str(row.get(space_name_header, "")).strip()
        print(f"\rAuditing row {index - 1}/{total_rows}...", end="", flush=True)
        try:
            resolved_name = resolve_room_name(eurl, args.token)
            if normalize_name(old_name) != normalize_name(resolved_name):
                row[space_name_header] = resolved_name
                results.append(
                    AuditRowResult(
                        row_number=index,
                        eurl=eurl,
                        old_name=old_name,
                        new_name=resolved_name,
                        status="updated",
                    )
                )
            else:
                results.append(
                    AuditRowResult(
                        row_number=index,
                        eurl=eurl,
                        old_name=old_name,
                        new_name=old_name,
                        status="unchanged",
                    )
                )
        except (HTTPError, URLError, json.JSONDecodeError, ValueError) as err:
            results.append(
                AuditRowResult(
                    row_number=index,
                    eurl=eurl,
                    old_name=old_name,
                    new_name=old_name,
                    status="failed",
                    detail=str(err),
                )
            )
    print()

    backup_path = ""
    if not args.output and not args.no_backup:
        try:
            backup_path = maybe_backup(args.input)
        except OSError as err:
            print(f"Failed to create backup for {args.input}: {err}", file=sys.stderr)
            return 1

    try:
        write_csv(output_path, fieldnames, rows)
    except OSError as err:
        print(f"Failed to write output CSV ({output_path}): {err}", file=sys.stderr)
        return 1

    if backup_path:
        print(f"Backup created: {backup_path}")
    print(f"Updated CSV written: {output_path}")
    print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
