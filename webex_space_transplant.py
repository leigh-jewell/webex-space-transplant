#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import os
import re
import shutil
import sys
import termios
import tty
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

API_BASE = "https://webexapis.com/v1"
ROOMS_ENDPOINT = f"{API_BASE}/rooms?max=100"
VERIFY_TOKEN_ENDPOINT = f"{API_BASE}/people/me"
TARGET_DOMAIN = "eurl.io"
URL_PATTERN = re.compile(r"https?://(?:www\.)?eurl\.io[^\s)]*", re.IGNORECASE)
EURL_TOKEN_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?eurl\.io[^\s)]*", re.IGNORECASE
)
OUTPUT_CSV = "webex_space_transplant.csv"
MISSING_MEMBERSHIP_OUTPUT_CSV = "webex_missing_spaces.csv"
JOIN_RESULTS_OUTPUT_CSV = "webex_join_results.csv"
MASTER_SPACES_CSV = "en_master_spaces.csv"
SPACE_LINK_BASE = "webexteams://im?space="
EURL_SHORTID_ENDPOINT = "https://eurl.io/api/shortid"
DEBUG_LOG_PREFIX = "webex_space_transplant_debug"
LOGGER = logging.getLogger("webex_space_transplant")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="write debug information to a log file",
    )
    parser.add_argument(
        "--check-master-membership",
        action="store_true",
        help="compare your room membership against a master CSV list",
    )
    parser.add_argument(
        "--master-csv",
        default=MASTER_SPACES_CSV,
        help=f"path to master spaces CSV (default: {MASTER_SPACES_CSV})",
    )
    parser.add_argument(
        "--join-from-csv",
        nargs="?",
        const="",
        help="join spaces from a CSV containing eurl/url links",
    )
    parser.add_argument(
        "--join-email",
        help="email address to use for EURL join requests (defaults to your Webex profile email)",
    )
    return parser.parse_args()


def configure_logging(enabled: bool) -> Optional[str]:
    if not enabled:
        LOGGER.addHandler(logging.NullHandler())
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"{DEBUG_LOG_PREFIX}_{timestamp}.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    LOGGER.debug("Debug logging enabled")
    return log_path


def print_banner() -> None:
    print("=" * 56)
    print("            Webex Space Transplant")
    print("   Find, review, and export Webex space links")
    print("=" * 56)


def parse_next_link(link_header: str) -> Optional[str]:
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if start != -1 and end != -1:
            return section[start + 1 : end]
    return None


def get_json(url: str, token: str) -> Tuple[Dict, Optional[str]]:
    LOGGER.debug("GET %s", url)
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(request) as response:
        payload = json.loads(response.read().decode("utf-8"))
        next_url = parse_next_link(response.headers.get("Link", ""))
        LOGGER.debug(
            "Response received for %s with %s item(s)",
            url,
            len(payload.get("items", [])) if isinstance(payload, dict) else 0,
        )
        return payload, next_url


def post_json(url: str, body: Dict, token: Optional[str] = None) -> Dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        headers=headers,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
    )
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def iter_rooms(token: str) -> Iterable[Dict]:
    next_url = ROOMS_ENDPOINT
    while next_url:
        payload, next_url = get_json(next_url, token)
        items = payload.get("items", [])
        LOGGER.debug("Processing %s room(s) from current page", len(items))
        for room in items:
            yield room


def find_url_in_title(title: str) -> Optional[str]:
    match = URL_PATTERN.search(title)
    if match:
        return match.group(0)
    if TARGET_DOMAIN in title.lower():
        return TARGET_DOMAIN
    return None


def clean_space_name(title: str) -> str:
    cleaned = URL_PATTERN.sub("", title)
    cleaned = EURL_TOKEN_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -:\t")


def build_space_link(room: Dict) -> str:
    room_id = str(room.get("id", "")).strip()
    if not room_id:
        return ""
    return f"{SPACE_LINK_BASE}{room_id}"


def masked_input(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()

    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")

    chars: List[str] = []
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                sys.stdout.write("\r\n")
                break
            if char == "\x03":
                raise KeyboardInterrupt
            if char in ("\x08", "\x7f"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(char)
            sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return "".join(chars)


def get_username_slug(profile: Dict) -> str:
    emails = profile.get("emails", [])
    if isinstance(emails, list) and emails:
        first_email = str(emails[0]).strip()
        if first_email and "@" in first_email:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", first_email.split("@", 1)[0]).lower()
        if first_email:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", first_email).lower()

    display_name = str(profile.get("displayName", "")).strip()
    if display_name:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", display_name).lower()
    return "user"


def get_primary_email(profile: Dict) -> Optional[str]:
    emails = profile.get("emails", [])
    if isinstance(emails, list) and emails:
        email = str(emails[0]).strip()
        if email:
            return email
    return None


def validate_token(token: str) -> Optional[Dict]:
    try:
        profile, _ = get_json(VERIFY_TOKEN_ENDPOINT, token)
        LOGGER.debug("Token validation succeeded")
        return profile
    except HTTPError as err:
        if err.code == 401:
            LOGGER.debug("Token validation failed with HTTP 401")
            print("Token is invalid. Please try again.", flush=True)
            return None
        LOGGER.debug("Token validation failed with HTTP %s", err.code)
        body = err.read().decode("utf-8", errors="replace")
        print(
            f"Token verification failed ({err.code} {err.reason}).\n{body}",
            file=sys.stderr,
        )
        return None
    except URLError as err:
        LOGGER.debug("Token validation failed with network error: %s", err)
        print(f"Network error while verifying token: {err}", file=sys.stderr)
        return None
    except json.JSONDecodeError as err:
        LOGGER.debug("Token validation failed with JSON decode error: %s", err)
        print(f"Could not parse token verification response: {err}", file=sys.stderr)
        return None


def prompt_for_valid_token() -> Tuple[str, str, Dict]:
    env_token = os.getenv("WEBEX_ACCESS_TOKEN", "").strip()
    if env_token:
        LOGGER.debug("Using token from WEBEX_ACCESS_TOKEN environment variable")
        profile = validate_token(env_token)
        if profile is None:
            print(
                "WEBEX_ACCESS_TOKEN is set but invalid. Update the environment variable and try again.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        username_slug = get_username_slug(profile)
        LOGGER.debug("Using username slug from environment token: %s", username_slug)
        print("Using token from WEBEX_ACCESS_TOKEN.")
        print("Token verified.", flush=True)
        return env_token, username_slug, profile

    while True:
        token = masked_input("Enter your Webex Developer API token: ").strip()
        if not token:
            LOGGER.debug("Empty token entered")
            print("No token entered. Please try again.", flush=True)
            continue
        profile = validate_token(token)
        if profile is not None:
            username_slug = get_username_slug(profile)
            LOGGER.debug("Using username slug: %s", username_slug)
            print("Token verified.", flush=True)
            return token, username_slug, profile


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    default_hint = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_hint}]: ").strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Please answer yes or no.", flush=True)


def prompt_input_with_default(prompt: str, default_value: str) -> str:
    response = input(f"{prompt} [{default_value}]: ").strip()
    return response or default_value


def prompt_master_csv_path(default_path: str) -> str:
    while True:
        response = input(f"Enter master CSV path [{default_path}]: ").strip()
        path = response or default_path
        if os.path.isfile(path):
            return path
        print(f"File not found: {path}. Please try again.", flush=True)


def write_csv(matches: List[Tuple[str, str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["space_name", "url", "space_link"])
        for space_name, found_url, space_link in matches:
            writer.writerow(
                [
                    sanitize_csv_cell(space_name),
                    sanitize_csv_cell(found_url),
                    sanitize_csv_cell(space_link),
                ]
            )


def write_missing_membership_csv(missing_spaces: List[Tuple[str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["space_name", "eurl"])
        for space_name, eurl in missing_spaces:
            writer.writerow([sanitize_csv_cell(space_name), sanitize_csv_cell(eurl)])


def write_join_results_csv(
    join_results: List[Tuple[str, str, str, str, str]], path: str
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["space_name", "eurl", "shortid", "status", "detail"])
        for space_name, eurl, shortid, status, detail in join_results:
            writer.writerow(
                [
                    sanitize_csv_cell(space_name),
                    sanitize_csv_cell(eurl),
                    sanitize_csv_cell(shortid),
                    sanitize_csv_cell(status),
                    sanitize_csv_cell(detail),
                ]
            )


def sanitize_csv_cell(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def truncate_text(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(value) <= max_width:
        return value
    if max_width == 1:
        return "…"
    return f"{value[: max_width - 1]}…"


def print_two_column_output(matches: List[Tuple[str, str, str]]) -> None:
    space_header = "Space Name"
    url_header = "URL"
    terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    url_width = max(len(url_header), *(len(url) for _, url, _ in matches))
    separator_width = 2
    min_space_width = len(space_header)
    max_space_width = max(min_space_width, terminal_width - url_width - separator_width)
    space_width = min(
        max(len(space_header), *(len(space) for space, _, _ in matches)),
        max_space_width,
    )

    separator = f"{'-' * space_width}  {'-' * url_width}"
    print(f"{space_header:<{space_width}}  {url_header:<{url_width}}")
    print(separator)
    for space_name, found_url, _ in matches:
        display_name = truncate_text(space_name, space_width)
        print(f"{display_name:<{space_width}}  {found_url:<{url_width}}")


def print_status_bar(
    processed: int,
    secondary_count: int,
    complete: bool = False,
    processed_label: str = "Spaces scanned",
    secondary_label: str = "Matches",
) -> None:
    width = 24
    if complete:
        bar = "=" * width
    else:
        position = (processed // 10) % width
        segments = ["-"] * width
        segments[position] = ">"
        bar = "".join(segments)
    sys.stdout.write(
        f"\rStatus [{bar}] {processed_label}: {processed}  {secondary_label}: {secondary_count}"
    )
    if complete:
        sys.stdout.write("\n")
    sys.stdout.flush()


def print_summary(
    total_spaces: int, total_matched: int, total_eurl: int, total_ask_rooms: int
) -> None:
    print("\nSummary:")
    print(f"Total spaces: {total_spaces}")
    print(f"Total matched: {total_matched}")
    print(f"Total EURL: {total_eurl}")
    print(f"Total Ask Rooms: {total_ask_rooms}")


def normalize_space_name(name: str) -> str:
    cleaned = clean_space_name(name)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def resolve_header_name(fieldnames: List[str], candidates: List[str]) -> Optional[str]:
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def extract_shortid_from_eurl(link: str) -> Optional[str]:
    parsed = urlparse(link.strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower().endswith("eurl.io"):
        if parsed.fragment:
            return parsed.fragment.strip()
        path = parsed.path.strip("/")
        if path:
            return path
    return None


def load_master_spaces(path: str) -> List[Tuple[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("master CSV has no headers")

        space_name_header = resolve_header_name(
            reader.fieldnames, ["space_name", "space", "title", "room_name"]
        )
        eurl_header = resolve_header_name(reader.fieldnames, ["eurl", "url"])

        if space_name_header is None:
            raise ValueError(
                'master CSV requires a "space_name" header (or space/title/room_name)'
            )
        if eurl_header is None:
            raise ValueError('master CSV requires an "eurl" header (or url)')

        loaded: List[Tuple[str, str]] = []
        for row in reader:
            space_name = str(row.get(space_name_header, "")).strip()
            eurl = str(row.get(eurl_header, "")).strip()
            if space_name:
                loaded.append((space_name, eurl))
        return loaded


def load_join_rows(path: str) -> List[Tuple[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("join CSV has no headers")

        name_header = resolve_header_name(
            reader.fieldnames, ["space_name", "space", "title", "room_name"]
        )
        eurl_header = resolve_header_name(reader.fieldnames, ["eurl", "url"])
        if eurl_header is None:
            raise ValueError('join CSV requires an "eurl" header (or url)')

        rows: List[Tuple[str, str]] = []
        for row in reader:
            space_name = (
                str(row.get(name_header, "")).strip()
                if name_header is not None
                else ""
            )
            eurl = str(row.get(eurl_header, "")).strip()
            if eurl:
                rows.append((space_name, eurl))
        return rows


def map_join_response(response_code: int) -> Tuple[str, str]:
    details = {
        0: ("joined", "Added to Webex space"),
        1: ("invalid_email", "Invalid email"),
        2: ("invalid_url", "Invalid URL"),
        3: ("invalid_email", "Invalid email"),
        4: ("invalid_url", "Invalid URL"),
        5: ("already_member", "Already in Webex space"),
        6: ("failed", "Could not add to Webex space"),
        7: ("joined_with_email", "Added to Webex space; check email for instructions"),
        8: ("failed", "Could not add to Webex space"),
        9: ("pending_moderator", "Request sent to moderator"),
        10: ("inactive_url", "URL is no longer active"),
        12: ("email_not_webex_enabled", "Email is not Webex enabled"),
        13: ("not_permitted", "Not permitted to Webex space"),
        14: ("bot_email_not_allowed", "Bot emails cannot be used"),
    }
    return details.get(response_code, ("failed", f"Unhandled responseCode={response_code}"))


def run_join_from_csv_mode(
    join_csv_path: str, email: str, output_csv: str
) -> int:
    LOGGER.debug("Running join mode with CSV=%s email=%s", join_csv_path, email)
    try:
        join_rows = load_join_rows(join_csv_path)
    except FileNotFoundError:
        print(f"Join CSV not found: {join_csv_path}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"Permission denied reading join CSV: {join_csv_path}", file=sys.stderr)
        return 1
    except csv.Error as err:
        print(f"Could not parse join CSV ({join_csv_path}): {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"Invalid join CSV ({join_csv_path}): {err}", file=sys.stderr)
        return 1

    if not join_rows:
        print(f"No EURL rows found in join CSV: {join_csv_path}", file=sys.stderr)
        return 1

    results: List[Tuple[str, str, str, str, str]] = []
    print_status_bar(0, 0, processed_label="Join rows", secondary_label="Joined")
    joined_count = 0
    for index, (space_name, eurl) in enumerate(join_rows, start=1):
        shortid = extract_shortid_from_eurl(eurl)
        if not shortid:
            results.append(
                (space_name, eurl, "", "unsupported_link", "Only https://eurl.io links are supported")
            )
            continue

        try:
            payload = post_json(
                f"{EURL_SHORTID_ENDPOINT}/{quote(shortid)}",
                {"email": email},
            )
            response_code = int(payload.get("responseCode", -1))
            status, detail = map_join_response(response_code)
            if status in {"joined", "joined_with_email", "already_member"}:
                joined_count += 1
            results.append((space_name, eurl, shortid, status, detail))
        except HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            results.append((space_name, eurl, shortid, "http_error", f"{err.code}: {body}"))
        except URLError as err:
            results.append((space_name, eurl, shortid, "network_error", str(err)))
        except json.JSONDecodeError as err:
            results.append((space_name, eurl, shortid, "invalid_json", str(err)))

        if index % 25 == 0:
            print_status_bar(
                index, joined_count, processed_label="Join rows", secondary_label="Joined"
            )

    print_status_bar(
        len(join_rows),
        joined_count,
        complete=True,
        processed_label="Join rows",
        secondary_label="Joined",
    )
    write_join_results_csv(results, output_csv)

    failed = sum(1 for _, _, _, status, _ in results if status not in {"joined", "joined_with_email", "already_member"})
    print(f"Join requests processed: {len(join_rows)}")
    print(f"Successful/already joined: {joined_count}")
    print(f"Not joined/failed: {failed}")
    print(f"Saved join results CSV: {output_csv}")
    return 0


def print_missing_membership_output(missing_spaces: List[Tuple[str, str]]) -> None:
    space_header = "Space Name"
    eurl_header = "EURL"
    terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    eurl_width = max(len(eurl_header), *(len(eurl) for _, eurl in missing_spaces))
    separator_width = 2
    min_space_width = len(space_header)
    max_space_width = max(min_space_width, terminal_width - eurl_width - separator_width)
    space_width = min(
        max(len(space_header), *(len(space) for space, _ in missing_spaces)),
        max_space_width,
    )

    separator = f"{'-' * space_width}  {'-' * eurl_width}"
    print(f"{space_header:<{space_width}}  {eurl_header:<{eurl_width}}")
    print(separator)
    for space_name, eurl in missing_spaces:
        display_name = truncate_text(space_name, space_width)
        print(f"{display_name:<{space_width}}  {eurl:<{eurl_width}}")


def run_master_membership_mode(token: str, master_csv: str, output_csv: str) -> int:
    LOGGER.debug("Running master membership mode with file: %s", master_csv)
    try:
        master_spaces = load_master_spaces(master_csv)
    except FileNotFoundError:
        print(f"Master CSV not found: {master_csv}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"Permission denied reading master CSV: {master_csv}", file=sys.stderr)
        return 1
    except csv.Error as err:
        print(f"Could not parse master CSV ({master_csv}): {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"Invalid master CSV ({master_csv}): {err}", file=sys.stderr)
        return 1

    if not master_spaces:
        print(f"Master CSV has no spaces to compare: {master_csv}", file=sys.stderr)
        return 1

    member_space_names: set[str] = set()
    scanned = 0
    print_status_bar(scanned, 0, secondary_label="Missing")
    try:
        for room in iter_rooms(token):
            scanned += 1
            title = str(room.get("title", "")).strip()
            if title:
                member_space_names.add(normalize_space_name(title))
            if scanned % 25 == 0:
                print_status_bar(scanned, 0, secondary_label="Missing")
    except HTTPError as err:
        LOGGER.debug("Room scan failed with HTTP %s", err.code)
        body = err.read().decode("utf-8", errors="replace")
        print(
            f"Webex API request failed ({err.code} {err.reason}).\n{body}",
            file=sys.stderr,
        )
        return 1
    except URLError as err:
        LOGGER.debug("Room scan failed with network error: %s", err)
        print(f"Network error: {err}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as err:
        LOGGER.debug("Room scan failed with JSON decode error: %s", err)
        print(f"Could not parse API response: {err}", file=sys.stderr)
        return 1

    missing_spaces: List[Tuple[str, str]] = []
    seen_master_names: set[str] = set()
    for space_name, eurl in master_spaces:
        normalized_name = normalize_space_name(space_name)
        if not normalized_name or normalized_name in seen_master_names:
            continue
        seen_master_names.add(normalized_name)
        if normalized_name not in member_space_names:
            missing_spaces.append((space_name, eurl))
    print_status_bar(scanned, len(missing_spaces), complete=True, secondary_label="Missing")

    if not missing_spaces:
        print("You are already a member of all spaces in the master list.")
        return 0

    missing_spaces.sort(key=lambda entry: entry[0].lower())
    write_missing_membership_csv(missing_spaces, output_csv)
    print("Spaces you are not a member of:\n")
    print_missing_membership_output(missing_spaces)
    print()
    print(f"Saved CSV: {output_csv}")
    print(f"Missing spaces: {len(missing_spaces)}")
    return 0


def run_export_mode(token: str, output_csv: str, include_ask_spaces: bool) -> int:
    LOGGER.debug("Running export mode")
    try:
        matches: List[Tuple[str, str, str]] = []
        scanned = 0
        total_eurl = 0
        total_ask_rooms = 0
        print_status_bar(scanned, len(matches))
        for room in iter_rooms(token):
            scanned += 1
            title = room.get("title", "")
            if not title:
                LOGGER.debug("Skipping room with empty title")
                continue
            space_link = build_space_link(room)
            clean_title = clean_space_name(title)
            found_url = find_url_in_title(title)
            if found_url:
                matches.append((clean_title, found_url, space_link))
                total_eurl += 1
                LOGGER.debug('Matched EURL room: "%s"', clean_title)
            elif include_ask_spaces and clean_title.lower().startswith("ask"):
                matches.append((clean_title, "", space_link))
                total_ask_rooms += 1
                LOGGER.debug('Matched Ask room: "%s"', clean_title)
            if scanned % 25 == 0:
                print_status_bar(scanned, len(matches))
        print_status_bar(scanned, len(matches), complete=True)
        LOGGER.debug(
            "Export complete: total_spaces=%s total_matched=%s total_eurl=%s total_ask=%s",
            scanned,
            len(matches),
            total_eurl,
            total_ask_rooms,
        )
    except HTTPError as err:
        LOGGER.debug("Room scan failed with HTTP %s", err.code)
        body = err.read().decode("utf-8", errors="replace")
        print(
            f"Webex API request failed ({err.code} {err.reason}).\n{body}",
            file=sys.stderr,
        )
        return 1
    except URLError as err:
        LOGGER.debug("Room scan failed with network error: %s", err)
        print(f"Network error: {err}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as err:
        LOGGER.debug("Room scan failed with JSON decode error: %s", err)
        print(f"Could not parse API response: {err}", file=sys.stderr)
        return 1

    if not matches:
        if include_ask_spaces:
            print('No space titles contained eurl.io and no spaces started with "Ask".')
        else:
            print("No space titles contained eurl.io.")
        print_summary(scanned, 0, total_eurl, total_ask_rooms)
        return 0

    matches.sort(key=lambda match: match[0].lower())
    write_csv(matches, output_csv)

    print("Matching spaces:\n")
    print_two_column_output(matches)
    print()
    print(f"Saved CSV: {output_csv}")
    print_summary(scanned, len(matches), total_eurl, total_ask_rooms)
    return 0


def main() -> int:
    args = parse_args()
    debug_log_path = configure_logging(args.debug)
    print_banner()
    print("Get a bearer token from: https://developer.webex.com/")
    if debug_log_path:
        print(f"Debug log: {debug_log_path}")
    token, username_slug, profile = prompt_for_valid_token()
    if args.check_master_membership:
        output_csv = f"{username_slug}_{MISSING_MEMBERSHIP_OUTPUT_CSV}"
        LOGGER.debug("Membership output CSV path: %s", output_csv)
        return run_master_membership_mode(token, args.master_csv, output_csv)
    if args.join_from_csv is not None:
        default_join_csv = f"{username_slug}_{MISSING_MEMBERSHIP_OUTPUT_CSV}"
        join_csv_path = args.join_from_csv or default_join_csv
        email = args.join_email or get_primary_email(profile)
        if not email:
            email = input("Enter the email to use for EURL joins: ").strip()
        if not email:
            print("Join email is required.", file=sys.stderr)
            return 1
        output_csv = f"{username_slug}_{JOIN_RESULTS_OUTPUT_CSV}"
        return run_join_from_csv_mode(join_csv_path, email, output_csv)

    run_join_mode = prompt_yes_no("Join spaces from an EURL CSV list?")
    if run_join_mode:
        default_join_csv = f"{username_slug}_{MISSING_MEMBERSHIP_OUTPUT_CSV}"
        join_csv_path = prompt_input_with_default("Enter join CSV path", default_join_csv)
        email_default = get_primary_email(profile) or ""
        email = (
            prompt_input_with_default("Enter the email to use for EURL joins", email_default)
            if email_default
            else input("Enter the email to use for EURL joins: ").strip()
        )
        if not email:
            print("Join email is required.", file=sys.stderr)
            return 1
        output_csv = f"{username_slug}_{JOIN_RESULTS_OUTPUT_CSV}"
        return run_join_from_csv_mode(join_csv_path, email, output_csv)

    run_membership_audit = prompt_yes_no(
        "Audit your spaces against a master CSV list?"
    )
    if run_membership_audit:
        master_csv_path = prompt_master_csv_path(args.master_csv)
        output_csv = f"{username_slug}_{MISSING_MEMBERSHIP_OUTPUT_CSV}"
        LOGGER.debug(
            "Interactive membership audit selected; CSV=%s output=%s",
            master_csv_path,
            output_csv,
        )
        return run_master_membership_mode(token, master_csv_path, output_csv)

    output_csv = f"{username_slug}_{OUTPUT_CSV}"
    LOGGER.debug("Output CSV path: %s", output_csv)
    include_ask_spaces = prompt_yes_no(
        'Include spaces starting with "Ask" even without eurl.io?'
    )
    LOGGER.debug('Include "Ask" spaces: %s', include_ask_spaces)
    return run_export_mode(token, output_csv, include_ask_spaces)


if __name__ == "__main__":
    raise SystemExit(main())
