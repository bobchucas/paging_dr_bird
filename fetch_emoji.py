#!/usr/bin/env python3
"""
fetch_emoji.py — Fetches custom emoji data from Slack.

Two auth modes (use whichever you have):

  MODE 1 — Official API token (preferred, works for automation)
    Requires a User OAuth Token (xoxp-...) with admin.emoji:read scope.
    Usage:
        SLACK_TOKEN=xoxp-your-token python fetch_emoji.py

  MODE 2 — Browser session (quick workaround, no admin app needed)
    Extract two values from your browser's DevTools while on any Slack page:
      SLACK_D_COOKIE : the value of the 'd' cookie (starts with xoxd-)
                       DevTools → Network → any emoji.adminList request → Headers → Cookie → d=...
      SLACK_XC_TOKEN : the session token from the POST body (starts with xoxc-)
                       DevTools → Network → same request → Payload → token=...
    Usage:
        SLACK_D_COOKIE=xoxd-... SLACK_XC_TOKEN=xoxc-... python fetch_emoji.py

    Note: browser session tokens expire (hours/days). Re-extract when they stop working.

Requirements:
    pip install requests
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

# Auth — script picks whichever is set
SLACK_TOKEN   = os.environ.get("SLACK_TOKEN")    # xoxp- token (official API)
SLACK_D_COOKIE = os.environ.get("SLACK_D_COOKIE") # xoxd- cookie (browser session)
SLACK_XC_TOKEN = os.environ.get("SLACK_XC_TOKEN") # xoxc- token (browser session)

WORKSPACE     = os.environ.get("SLACK_WORKSPACE", "transferroom")  # subdomain
OUTPUT_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "emoji.json")


# ─────────────────────────────────────────────────────────────────────────────
# Fetch via official API (xoxp- token)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_official(session):
    """Pages through admin.emoji.list using a proper user token."""
    all_emoji = []
    cursor = None
    page = 1

    while True:
        print(f"  Page {page}...", end=" ", flush=True)
        params = {"limit": 1000}
        if cursor:
            params["cursor"] = cursor

        resp = session.get(
            "https://slack.com/api/admin.emoji.list",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            error = data.get("error", "unknown")
            print(f"\n\nSlack API error: {error}")
            if error == "not_allowed_token_type":
                print("→ You need a USER token (xoxp-...), not a bot token.")
            elif error == "missing_scope":
                print("→ Add 'admin.emoji:read' to your app's User Token Scopes and reinstall.")
            elif error == "not_an_admin":
                print("→ The token owner must be a workspace admin.")
            sys.exit(1)

        emoji = data.get("emoji", [])
        all_emoji.extend(emoji)
        print(f"{len(emoji)} emoji (total: {len(all_emoji)})")

        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        page += 1

    return all_emoji


# ─────────────────────────────────────────────────────────────────────────────
# Fetch via browser session (xoxd- cookie + xoxc- token)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_browser_session(session):
    """Pages through emoji.adminList using a browser session."""
    all_emoji = []
    page = 1

    while True:
        print(f"  Page {page}...", end=" ", flush=True)

        resp = session.post(
            f"https://{WORKSPACE}.slack.com/api/emoji.adminList",
            data={
                "token": SLACK_XC_TOKEN,
                "page":  page,
                "count": 1000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            error = data.get("error", "unknown")
            print(f"\n\nSlack API error: {error}")
            if error in ("invalid_auth", "not_authed"):
                print("→ Session has expired. Re-extract SLACK_D_COOKIE and SLACK_XC_TOKEN from your browser.")
            sys.exit(1)

        emoji = data.get("emoji", [])
        all_emoji.extend(emoji)

        paging = data.get("paging", {})
        total_pages = paging.get("pages", 1)
        print(f"{len(emoji)} emoji (total: {len(all_emoji)}, page {page}/{total_pages})")

        if page >= total_pages:
            break
        page += 1

    return all_emoji


# ─────────────────────────────────────────────────────────────────────────────
# Fetch user avatars
# ─────────────────────────────────────────────────────────────────────────────
def fetch_avatars(session, browser_mode):
    """Returns a dict of user_id → avatar_url. Silently skips on failure."""
    try:
        print("  Fetching user avatars...", end=" ", flush=True)
        if browser_mode:
            resp = session.post(
                f"https://{WORKSPACE}.slack.com/api/users.list",
                data={"token": SLACK_XC_TOKEN, "limit": 1000},
                timeout=30,
            )
        else:
            resp = session.get(
                "https://slack.com/api/users.list",
                params={"limit": 1000},
                timeout=30,
            )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            print(f"skipped ({data.get('error', 'unknown error')})")
            return {}

        avatars = {}
        for member in data.get("members", []):
            uid     = member.get("id")
            profile = member.get("profile", {})
            avatar  = profile.get("image_192") or profile.get("image_72") or ""
            if uid and avatar:
                avatars[uid] = avatar

        print(f"{len(avatars)} avatars fetched")
        return avatars

    except Exception as e:
        print(f"skipped ({e})")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Shared processing
# ─────────────────────────────────────────────────────────────────────────────
def process_data(raw_emoji):
    """Groups emoji by creator and computes stats."""
    real_emoji = [e for e in raw_emoji if not e.get("is_alias")]
    aliases    = [e for e in raw_emoji if e.get("is_alias")]

    creators = {}
    for e in real_emoji:
        uid  = e.get("user_id") or "unknown"
        name = (e.get("user_display_name") or "Unknown").strip()

        if uid not in creators:
            creators[uid] = {"user_id": uid, "display_name": name, "emoji": []}
        if name and name != "Unknown":
            creators[uid]["display_name"] = name

        creators[uid]["emoji"].append({
            "name":    e["name"],
            "created": e.get("created"),
            "url":     e.get("url", ""),
        })

    leaderboard = sorted(creators.values(), key=lambda x: len(x["emoji"]), reverse=True)
    for i, c in enumerate(leaderboard):
        c["rank"]   = i + 1
        c["count"]  = len(c["emoji"])
        c["avatar"] = ""  # filled in later by fetch_avatars

    return {
        "total":             len(real_emoji),
        "total_with_aliases": len(raw_emoji),
        "total_aliases":     len(aliases),
        "total_creators":    len(creators),
        "leaderboard":       leaderboard,
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    session = requests.Session()

    if SLACK_TOKEN:
        print("Auth mode: official API token (xoxp-)")
        session.headers["Authorization"] = f"Bearer {SLACK_TOKEN}"
        raw = fetch_official(session)

    elif SLACK_D_COOKIE and SLACK_XC_TOKEN:
        print("Auth mode: browser session (xoxd- cookie + xoxc- token)")
        session.cookies.set("d", SLACK_D_COOKIE, domain=f"{WORKSPACE}.slack.com")
        raw = fetch_browser_session(session)

    else:
        print("Error: no credentials found.\n")
        print("Option 1 — official API token:")
        print("  SLACK_TOKEN=xoxp-... python fetch_emoji.py\n")
        print("Option 2 — browser session:")
        print("  SLACK_D_COOKIE=xoxd-... SLACK_XC_TOKEN=xoxc-... python fetch_emoji.py")
        print("\n  Extract both from DevTools → Network → any emoji.adminList request:")
        print("    SLACK_D_COOKIE : Headers tab → Cookie → find d=...")
        print("    SLACK_XC_TOKEN : Payload tab → find token=...")
        sys.exit(1)

    print(f"\nProcessing {len(raw)} total emoji (including aliases)...")
    data = process_data(raw)

    print("Fetching profile pictures...")
    browser_mode = bool(SLACK_D_COOKIE and SLACK_XC_TOKEN)
    avatars = fetch_avatars(session, browser_mode)
    for entry in data["leaderboard"]:
        entry["avatar"] = avatars.get(entry["user_id"], "")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n✓ Written to {OUTPUT_FILE}")
    print(f"  {data['total']} original emoji ({data['total_aliases']} aliases excluded)")
    print(f"  {data['total_creators']} creators")
    if data["leaderboard"]:
        top = data["leaderboard"][0]
        print(f"  Current leader: {top['display_name']} with {top['count']} emoji "
              f"({top['count'] / data['total'] * 100:.1f}%)")


if __name__ == "__main__":
    main()
