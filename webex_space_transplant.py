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
SPACE_LINK_BASE = "webexteams://im?space="
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
    token, username_slug, _ = prompt_for_valid_token()
    output_csv = f"{username_slug}_{OUTPUT_CSV}"
    LOGGER.debug("Output CSV path: %s", output_csv)

    include_ask_spaces = prompt_yes_no(
        'Include spaces starting with "Ask" even without eurl.io?'
    )
    LOGGER.debug('Include "Ask" spaces: %s', include_ask_spaces)
    return run_export_mode(token, output_csv, include_ask_spaces)


if __name__ == "__main__":
    raise SystemExit(main())
