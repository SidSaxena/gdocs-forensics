"""Self-authentication: read the *local* browser's cookies for docs.google.com.

Design rule for this project: each person runs the tool on their own machine,
logged into their own Google account. We never accept or transmit anyone else's
credentials. We read cookies straight from the local browser cookie store, use
them to call Google's own endpoints as that signed-in user, and never persist
them anywhere except an explicit, user-requested cookie cache.

Chromium-family browsers (Chrome/Chromium/Edge/Brave) keep separate cookie
stores per profile. By default browser_cookie3 reads the "Default" profile; pass
``profile`` to target another one (matched by folder, display name, or account
email via the browser's "Local State").
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
from typing import Optional

DOCS_DOMAIN = "docs.google.com"

SUPPORTED_BROWSERS = ("chrome", "safari", "firefox", "edge", "brave", "chromium")

# Chromium-family user-data directories per platform.
_CHROMIUM_DIRS = {
    "chrome": {
        "darwin": "~/Library/Application Support/Google/Chrome",
        "win32": "~/AppData/Local/Google/Chrome/User Data",
        "linux": "~/.config/google-chrome",
    },
    "chromium": {
        "darwin": "~/Library/Application Support/Chromium",
        "win32": "~/AppData/Local/Chromium/User Data",
        "linux": "~/.config/chromium",
    },
    "edge": {
        "darwin": "~/Library/Application Support/Microsoft Edge",
        "win32": "~/AppData/Local/Microsoft/Edge/User Data",
        "linux": "~/.config/microsoft-edge",
    },
    "brave": {
        "darwin": "~/Library/Application Support/BraveSoftware/Brave-Browser",
        "win32": "~/AppData/Local/BraveSoftware/Brave-Browser/User Data",
        "linux": "~/.config/BraveSoftware/Brave-Browser",
    },
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _user_data_dir(browser: str) -> Optional[str]:
    spec = _CHROMIUM_DIRS.get(browser.lower())
    if not spec:
        return None
    path = os.path.expanduser(spec[_platform_key()])
    return path if os.path.isdir(path) else None


def list_profiles(browser: str = "chrome") -> list[dict]:
    """Return [{dir, name, account}] for a Chromium-family browser."""
    base = _user_data_dir(browser)
    if not base:
        return []
    ls_path = os.path.join(base, "Local State")
    out = []
    try:
        cache = json.load(open(ls_path, encoding="utf-8")) \
            .get("profile", {}).get("info_cache", {})
        for d, info in cache.items():
            out.append({"dir": d, "name": info.get("name", ""),
                        "account": info.get("user_name", "")})
    except Exception:
        # Fall back to scanning for profile folders that contain a cookie store.
        for d in os.listdir(base):
            if d == "Default" or d.startswith("Profile "):
                out.append({"dir": d, "name": "", "account": ""})
    return out


def _resolve_profile_cookie_file(browser: str, profile: str) -> str:
    """Map a profile identifier (folder / display name / account email) to its
    cookie file path for a Chromium-family browser."""
    base = _user_data_dir(browser)
    if not base:
        raise ValueError(f"Could not locate the {browser} user-data directory on "
                         f"this platform; pass --cookies instead.")
    profiles = list_profiles(browser)
    p = profile.strip().lower()
    match = next((pr for pr in profiles
                  if p in (pr["dir"].lower(), pr["name"].lower(),
                           pr["account"].lower())), None)
    if not match:
        avail = ", ".join(f"{pr['dir']} ({pr['name'] or pr['account']})"
                          for pr in profiles) or "none found"
        raise ValueError(f"No {browser} profile matches {profile!r}. "
                         f"Available: {avail}")
    pdir = os.path.join(base, match["dir"])
    # Newer Chrome stores cookies under <profile>/Network/Cookies.
    for rel in ("Network/Cookies", "Cookies"):
        cand = os.path.join(pdir, *rel.split("/"))
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"No cookie store found in {pdir}.")


def load_cookiejar(
    browser: str = "chrome",
    cookie_file: Optional[str] = None,
    profile: Optional[str] = None,
) -> http.cookiejar.CookieJar:
    """Return a cookiejar scoped to google.com.

    - ``cookie_file``: a Netscape cookies.txt the user exported (overrides all).
    - ``profile``: for Chromium-family browsers, which profile to read.
    Otherwise the browser's default profile is used.
    """
    if cookie_file:
        jar = http.cookiejar.MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar

    import browser_cookie3

    loader = {
        "chrome": browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "safari": browser_cookie3.safari,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "brave": browser_cookie3.brave,
    }.get(browser.lower())
    if loader is None:
        raise ValueError(
            f"Unsupported browser {browser!r}. Choose one of {SUPPORTED_BROWSERS}."
        )

    kwargs = {"domain_name": "google.com"}
    if profile and browser.lower() in _CHROMIUM_DIRS:
        kwargs["cookie_file"] = _resolve_profile_cookie_file(browser, profile)
    elif profile:
        raise ValueError(f"--profile is only supported for Chromium-family "
                         f"browsers {tuple(_CHROMIUM_DIRS)}, not {browser!r}.")

    # domain_name filters to google cookies only; we never read unrelated sites.
    return loader(**kwargs)


def list_google_accounts(jar, max_index: int = 8) -> list[dict]:
    """When several accounts share one browser session, enumerate them by their
    ``authuser`` index. We probe ``/document/u/N/`` and read the active account's
    email; indices past the last signed-in account redirect back to 0, so we stop
    when the email repeats account 0's."""
    import re
    import requests

    s = requests.Session()
    s.cookies = requests.cookies.merge_cookies(s.cookies, jar)
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    accounts: list[dict] = []
    zero_email = None
    for n in range(max_index):
        try:
            body = s.get(f"https://docs.google.com/document/u/{n}/", timeout=20).text
        except Exception:
            break
        lab = re.findall(r"Google Account:[^(]*\(([^)]+)\)", body)
        if lab:
            email = lab[0]
        else:
            found = re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", body)
            email = found[0] if found else None
        if n == 0:
            zero_email = email
        elif email and email == zero_email:
            break  # redirected to account 0 → no account at this index
        accounts.append({"authuser": n, "email": email})
    return accounts


def has_auth_cookies(jar: http.cookiejar.CookieJar) -> bool:
    """Heuristic: a signed-in Google session carries SID/SAPISID-family cookies."""
    names = {c.name for c in jar}
    return bool(names & {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"})
