"""Self-authentication: read the *local* browser's cookies for docs.google.com.

Design rule for this project: each person runs the tool on their own machine,
logged into their own Google account. We never accept or transmit anyone else's
credentials. We read cookies straight from the local browser cookie store, use
them to call Google's own endpoints as that signed-in user, and never persist
them anywhere except an explicit, user-requested cookie cache.
"""

from __future__ import annotations

import http.cookiejar
from typing import Optional

DOCS_DOMAIN = "docs.google.com"

SUPPORTED_BROWSERS = ("chrome", "safari", "firefox", "edge", "brave", "chromium")


def load_cookiejar(
    browser: str = "chrome",
    cookie_file: Optional[str] = None,
) -> http.cookiejar.CookieJar:
    """Return a cookiejar scoped to google.com.

    If ``cookie_file`` is given (Netscape cookies.txt that the user exported
    themselves), use that. Otherwise read the named local browser's store via
    browser_cookie3, which handles macOS Keychain decryption for Chromium.
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

    # domain_name filters to google cookies only; we never read unrelated sites.
    return loader(domain_name="google.com")


def has_auth_cookies(jar: http.cookiejar.CookieJar) -> bool:
    """Heuristic: a signed-in Google session carries SID/SAPISID-family cookies."""
    names = {c.name for c in jar}
    return bool(names & {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"})
