import asyncio
import json
import logging
from pathlib import Path
from typing import Callable, Literal, Optional, Union
from urllib.parse import urlparse

from py_wraps_data_types import Variant

from playwright.async_api import (
    async_playwright,
    Page,
    Browser,
    BrowserContext,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from .injector import ANNOTATION_JS, CLEANUP_JS
from .dom_extractor import DOM_EXTRACTION_JS, INTERACTIVE_ELEMENTS_JS
from .schemas import BrowserAction, CaptchaContext, PageState
from .exceptions import NavigationError, ActionExecutionError, ElementNotFoundError, PageNotReadyError
from .utils import screenshot_to_base64, safe_wait

logger = logging.getLogger(__name__)


def _parse_proxy(proxy: Union[str, dict]) -> dict:
    """
    Normalise a proxy value to Playwright's proxy dict format.

    Accepts either:
      - A URL string:  "http://user:pass@host:8080"
                       "socks5://host:1080"
      - A dict already in Playwright format:
                       {"server": "...", "username": "...", "password": "..."}

    Returns a dict with at minimum {"server": "scheme://host:port"}.
    """
    if isinstance(proxy, dict):
        return proxy

    parsed = urlparse(proxy)
    scheme = parsed.scheme or "http"
    host   = parsed.hostname or ""
    port   = parsed.port

    server = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
    result: dict = {"server": server}

    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password

    return result


class BrowserType(Variant):
    """
    Valid browser choices for LLMBrowser.

    Raw strings pass isinstance() — both of these are valid:
        LLMBrowser(browser=BrowserType.CHROME)
        LLMBrowser(browser="chrome")
    """

    CHROME   = "chrome"
    CHROMIUM = "chromium"
    FIREFOX  = "firefox"
    SAFARI   = "safari"
    WEBKIT   = "webkit"
    MSEDGE   = "msedge"

    @property
    def engine(self) -> str:
        """Playwright engine name for this browser type."""
        return {
            "chrome":   "chromium",
            "chromium": "chromium",
            "msedge":   "chromium",
            "firefox":  "firefox",
            "safari":   "webkit",
            "webkit":   "webkit",
        }[self.value]

    @property
    def is_channel(self) -> bool:
        """
        True when the browser is launched via Playwright's channel= parameter,
        meaning it uses the system-installed binary rather than a managed one.
        """
        return self in {BrowserType.CHROME, BrowserType.MSEDGE}


# Cookie / consent banner dismissal ─────────────────────────────────────────
# Tries known library selectors first (zero false-positives), then falls back
# to text-pattern matching on visible cookie-related containers.
# Returns the matched selector string on success, or null if nothing was found.
_COOKIE_DISMISS_JS = """
() => {
    // ── Priority 1: known cookie-consent library selectors ───────────────
    const KNOWN = [
        '#onetrust-accept-btn-handler',          // OneTrust
        '#CybotCookiebotDialogBodyButtonAccept',  // Cookiebot
        '#didomi-notice-agree-button',            // Didomi
        '#truste-consent-button',                 // TrustArc
        '#gdpr-cookie-accept',                    // GDPR Cookie Consent (WP)
        '.cookielawinfo-btn-accept',              // Cookie Law Info
        '.cc-btn.cc-allow',                       // Cookie Consent by Osano
        '[aria-label="Accept cookies"]',
        '[aria-label="Accept all cookies"]',
        '[data-testid="cookie-accept"]',
        '[data-cookiebanner="accept_button"]',
    ];
    for (const sel of KNOWN) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) { el.click(); return sel; }
        } catch (e) {}
    }

    // ── Priority 2: text-match inside cookie-related containers ──────────
    const CONTAINER_SEL = [
        '[id*="cookie" i]', '[id*="consent" i]', '[id*="gdpr" i]',
        '[id*="cookie-banner" i]', '[id*="privacy-banner" i]',
        '[class*="cookie" i]', '[class*="consent" i]', '[class*="gdpr" i]',
        '[role="dialog"][aria-label*="cookie" i]',
        '[role="dialog"][aria-label*="consent" i]',
    ].join(', ');

    const ACCEPT  = /^(accept( all( cookies?)?)?|allow( all)?|i (accept|agree)|agree|ok|got it|yes|continue|dismiss|close)/i;
    const REJECT  = /^(reject|decline|refuse|deny|no thanks|necessary only|essential only|manage|settings|preferences)/i;

    for (const container of document.querySelectorAll(CONTAINER_SEL)) {
        if (container.offsetParent === null) continue;
        const btns = container.querySelectorAll(
            'button, [role="button"], input[type="button"], input[type="submit"]'
        );
        for (const btn of btns) {
            const text = (btn.textContent || btn.value || '').trim();
            if (ACCEPT.test(text) && !REJECT.test(text)) {
                btn.click();
                return 'generic: ' + text.slice(0, 40);
            }
        }
    }

    return null;
}
"""

# CAPTCHA detection — returns {type, sitekey} or null.
# Covers reCAPTCHA v2 (checkbox), reCAPTCHA v3 (invisible / score-based),
# and hCaptcha.  Checks widget elements first, falls back to script/iframe
# src parsing so it works regardless of how the widget was initialised.
_CAPTCHA_DETECT_JS = """
() => {
    // ── hCaptcha ─────────────────────────────────────────────────────────
    const hc = document.querySelector('.h-captcha[data-sitekey]');
    if (hc) return { type: 'hcaptcha', sitekey: hc.getAttribute('data-sitekey') };

    // ── reCAPTCHA v2 widget element ──────────────────────────────────────
    const rcWidget = document.querySelector('.g-recaptcha[data-sitekey]');
    if (rcWidget) return { type: 'recaptcha_v2', sitekey: rcWidget.getAttribute('data-sitekey') };

    // ── reCAPTCHA v3 — key in script src  (?render=SITEKEY) ─────────────
    for (const s of document.querySelectorAll('script[src*="recaptcha/api.js"]')) {
        const m = s.src.match(/[?&]render=([^&]+)/);
        if (m && m[1] !== 'explicit') return { type: 'recaptcha_v3', sitekey: m[1] };
    }

    // ── reCAPTCHA v2 fallback — key in iframe src  (?k=SITEKEY) ─────────
    for (const f of document.querySelectorAll('iframe[src*="recaptcha"]')) {
        const m = f.src.match(/[?&]k=([^&]+)/);
        if (m) return { type: 'recaptcha_v2', sitekey: m[1] };
    }

    // ── hCaptcha fallback — script src ───────────────────────────────────
    if (document.querySelector('script[src*="hcaptcha.com"]')) {
        const hcEl = document.querySelector('[data-sitekey]');
        if (hcEl) return { type: 'hcaptcha', sitekey: hcEl.getAttribute('data-sitekey') };
    }

    return null;
}
"""

# CAPTCHA token injection — submits the solver's response token back to the page.
# Handles reCAPTCHA v2/v3 (textarea + grecaptcha callback) and hCaptcha.
# Returns true if injection succeeded, false if the page structure was unexpected.
_CAPTCHA_INJECT_JS = """
([token, type]) => {
    // ── hCaptcha ─────────────────────────────────────────────────────────
    if (type === 'hcaptcha') {
        const ta = document.querySelector('[name="h-captcha-response"]');
        if (ta) ta.value = token;
        const widget = document.querySelector('.h-captcha');
        if (widget) {
            const cbName = widget.getAttribute('data-callback');
            if (cbName && typeof window[cbName] === 'function') { window[cbName](token); return true; }
        }
        return !!ta;
    }

    // ── reCAPTCHA (v2 and v3) ────────────────────────────────────────────
    // 1. Set the hidden textarea Googlebot / form submissions read
    const ta = document.getElementById('g-recaptcha-response');
    if (ta) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, token);
        ta.dispatchEvent(new Event('input',  { bubbles: true }));
        ta.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // 2. Fire data-callback if present on the widget element
    const widget = document.querySelector('.g-recaptcha[data-callback]');
    if (widget) {
        const cbName = widget.getAttribute('data-callback');
        if (typeof window[cbName] === 'function') { window[cbName](token); return true; }
    }

    // 3. Walk ___grecaptcha_cfg.clients to find the internal callback
    try {
        const clients = window.___grecaptcha_cfg?.clients || {};
        for (const id of Object.keys(clients)) {
            const walk = (obj, depth) => {
                if (!obj || depth > 7) return null;
                for (const k of Object.keys(obj)) {
                    if (k === 'callback' && typeof obj[k] === 'function') return obj[k];
                    if (obj[k] && typeof obj[k] === 'object') {
                        const r = walk(obj[k], depth + 1);
                        if (r) return r;
                    }
                }
                return null;
            };
            const cb = walk(clients[id], 0);
            if (cb) { cb(token); return true; }
        }
    } catch (_) {}

    return !!ta;
}
"""

# Extra args that only apply to the Chromium engine
_CHROMIUM_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
]

# Stealth init-script — injected before any page JS runs.
# Patches the most common fingerprinting signals used by bot-detection
# systems (Cloudflare, DataDome, PerimeterX, etc.) without breaking page JS.
_STEALTH_JS = """
(function () {
    'use strict';

    // 1. Erase the #1 automation tell: navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });
    try { delete Object.getPrototypeOf(navigator).webdriver; } catch (_) {}

    // 2. Ensure window.chrome looks like a real installed browser
    if (!window.chrome) {
        window.chrome = {
            app: { isInstalled: false },
            runtime: {
                id: undefined,
                connect:     () => {},
                sendMessage: () => {},
                onConnect: { addListener: () => {}, removeListener: () => {} },
                onMessage: { addListener: () => {}, removeListener: () => {} },
            },
            loadTimes: () => ({}),
            csi:        () => ({}),
        };
    }

    // 3. Fix navigator.languages (headless may be empty or single-item)
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true,
    });

    // 4. Fake a plausible plugin/mimeType list (headless Chrome has none)
    if (navigator.plugins.length === 0) {
        const fakePlugins = [
            { name: 'PDF Viewer',          filename: 'internal-pdf-viewer',    description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',   filename: 'internal-pdf-viewer',    description: 'Portable Document Format' },
            { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',    description: 'Portable Document Format' },
            { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'WebKit built-in PDF',  filename: 'internal-pdf-viewer',   description: 'Portable Document Format' },
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = fakePlugins.map(p => Object.assign(Object.create(Plugin.prototype), p, { length: 1 }));
                Object.assign(arr, {
                    item:      i => arr[i] ?? null,
                    namedItem: n => arr.find(p => p.name === n) ?? null,
                    refresh:   () => {},
                    length:    arr.length,
                });
                return arr;
            },
            configurable: true,
        });
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => {
                const mt = [
                    { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                    { type: 'text/pdf',        suffixes: 'pdf', description: 'Portable Document Format' },
                ];
                return Object.assign(mt, {
                    item:      i => mt[i] ?? null,
                    namedItem: n => mt.find(m => m.type === n) ?? null,
                });
            },
            configurable: true,
        });
    }

    // 5. Permissions: return a real-looking state for 'notifications'
    if (navigator.permissions?.query) {
        const _orig = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (params) => {
            if (params?.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission ?? 'default', onchange: null });
            }
            return _orig(params);
        };
    }

    // 6. WebGL: report a real-looking GPU rather than "Google SwiftShader"
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
        if (param === 37445) return 'Intel Inc.';               // UNMASKED_VENDOR_WEBGL
        if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
        return _getParam.call(this, param);
    };

    // 7. Canvas: add imperceptible noise so every run has a unique fingerprint
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
        const ctx2d = this.getContext('2d');
        if (ctx2d) {
            const imageData = ctx2d.getImageData(0, 0, this.width || 1, this.height || 1);
            imageData.data[0] ^= 1;   // flip one LSB — invisible to humans
            ctx2d.putImageData(imageData, 0, 0);
        }
        return _toDataURL.call(this, type, quality);
    };
})();
"""

# ARIA roles that carry no information when they have no accessible name —
# we skip printing them but still recurse into their children.
_ARIA_SKIP_ROLES = frozenset({
    "WebArea", "none", "presentation", "generic", "group",
})


def _aria_to_text(node: dict, depth: int = 0) -> str:
    """
    Recursively serialize a Playwright accessibility snapshot node to compact text.

    Skips wrapper roles (generic, none, presentation, group) that have no
    accessible name, but still recurses into their children so nothing is lost.
    """
    if depth > 15:
        return ""

    role     = node.get("role") or "generic"
    name     = (node.get("name") or "").strip()
    children = node.get("children") or []

    # Skip structural wrappers with no name — just recurse
    if role in _ARIA_SKIP_ROLES and not name:
        return "".join(_aria_to_text(c, depth) for c in children)

    pad   = "  " * depth
    parts = [f"{pad}[{role}]"]
    if name:
        parts.append(name)

    states: list[str] = []
    # checked can be True | False | "mixed" | None — must use identity check
    if node.get("checked") is True:      states.append("checked")
    elif node.get("checked") == "mixed": states.append("mixed")
    if node.get("disabled"):        states.append("disabled")
    if node.get("required"):        states.append("required")
    if node.get("focused"):         states.append("focused")
    expanded = node.get("expanded")
    if expanded is not None:        states.append("expanded" if expanded else "collapsed")
    if states:
        parts.append(f"({', '.join(states)})")

    val = (node.get("value") or "").strip()
    if val and val != name:         parts.append(f'value="{val}"')

    level = node.get("level")
    if level:                       parts.append(f"level={level}")

    desc = (node.get("description") or "").strip()
    if desc and desc != name:       parts.append(f"— {desc[:60]}")

    result = " ".join(parts) + "\n"
    for child in children:
        result += _aria_to_text(child, depth + 1)
    return result


class LLMBrowser:
    """
    Async browser for LLM agents, built on Playwright.

    Usage — async context manager (recommended):
        async with LLMBrowser() as browser:
            await browser.navigate("https://example.com")
            state = await browser.get_state()

    Usage — manual lifecycle:
        browser = LLMBrowser()
        await browser._start()
        ...
        await browser.close()

    Browser choices:
        "chrome"    — system-installed Google Chrome   (default)
        "chromium"  — Playwright-managed Chromium      (auto-installed if missing)
        "firefox"   — Playwright-managed Firefox       (auto-installed if missing)
        "safari"    — Playwright-managed WebKit/Safari (auto-installed if missing)
        "webkit"    — alias for "safari"
        "msedge"    — system-installed Microsoft Edge

    Session persistence:
        async with LLMBrowser(session_file="session.json") as browser:
            ...
        await browser.save_session("session.json")   # save cookies + storage

    Proxy (residential proxies significantly improve bot-detection bypass):
        LLMBrowser(proxy="http://user:pass@proxy.example.com:8080")
        LLMBrowser(proxy="socks5://proxy.example.com:1080")
        LLMBrowser(proxy={"server": "http://...", "username": "u", "password": "p"})

    Remote browser via CDP (Browserbase, Anchor, self-hosted Chromium, etc.):
        LLMBrowser(remote_cdp_url="wss://connect.browserbase.com?apiKey=YOUR_KEY")
        # browser=, headless=, proxy=, slow_mo= are ignored when remote_cdp_url is set.
        # stealth=, session_file=, viewport=, user_agent= all work as normal.
    """

    DEFAULT_TIMEOUT  = 30_000
    DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

    def __init__(
        self,
        headless: bool = True,
        browser: BrowserType = BrowserType.CHROMIUM,
        viewport: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
        user_agent: Optional[str] = None,
        slow_mo: int = 0,
        session_file: Optional[str] = None,
        max_retries: int = 2,
        dismiss_dialogs: bool = True,
        download_dir: Optional[str] = None,
        stealth: bool = False,
        proxy: Optional[Union[str, dict]] = None,
        on_captcha_detected: Optional[Callable] = None,
        remote_cdp_url: Optional[str] = None,
    ):
        if not isinstance(browser, BrowserType):
            valid = [b.value for b in BrowserType]
            raise ValueError(f"Invalid browser '{browser}'. Choose from: {valid}")

        if remote_cdp_url and proxy:
            logger.warning(
                "proxy= is ignored when remote_cdp_url= is set — "
                "the remote browser controls its own network routing."
            )

        self._browser_type: BrowserType = BrowserType(browser)
        self._headless     = headless
        self._viewport     = viewport or self.DEFAULT_VIEWPORT
        self._timeout      = timeout
        self._user_agent   = user_agent
        self._slow_mo      = slow_mo
        self._session_file = session_file
        self._max_retries      = max(0, max_retries)
        self._dismiss_dialogs  = dismiss_dialogs
        self._download_dir     = Path(download_dir) if download_dir else Path.home() / ".llmbrowser" / "downloads"
        self._stealth               = stealth
        self._proxy                 = _parse_proxy(proxy) if proxy else None
        self._on_captcha_detected   = on_captcha_detected
        self._remote_cdp_url        = remote_cdp_url

        self._playwright: Optional[Playwright]     = None
        self._browser:    Optional[Browser]        = None
        self._context:    Optional[BrowserContext] = None

        # Multi-tab state: _page is a property over this list
        self._pages:       list[Page] = []
        self._active_idx:  int        = 0

        # File tracking
        self._downloads: list[str] = []

    # ------------------------------------------------------------------
    # _page property — always points to the currently active tab
    # ------------------------------------------------------------------

    @property
    def _page(self) -> Page:
        if not self._pages:
            raise PageNotReadyError(
                "Browser has not been started. "
                "Use 'async with LLMBrowser()' or call '_start()' first."
            )
        return self._pages[self._active_idx]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _setup_page(self, page: Page) -> None:
        """Attach timeout and event listeners to a freshly-created page."""
        page.set_default_timeout(self._timeout)
        page.on(
            "download",
            lambda dl: asyncio.ensure_future(self._handle_download(dl)),
        )

    async def _handle_download(self, download) -> None:  # type: ignore[type-arg]
        """Save a triggered download to the download directory."""
        self._download_dir.mkdir(parents=True, exist_ok=True)
        name   = download.suggested_filename or f"download_{len(self._downloads) + 1}"
        target = self._download_dir / name

        # Avoid overwriting existing files
        if target.exists():
            stem, suffix = target.stem, target.suffix
            counter = 1
            while target.exists():
                target = self._download_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        await download.save_as(str(target))
        self._downloads.append(str(target))
        logger.info("Download saved: %s", target)

    async def _start(self) -> None:
        self._playwright = await async_playwright().start()

        if self._remote_cdp_url:
            # ── Remote browser via CDP ──────────────────────────────────────
            # Connects to a browser already running somewhere else — a managed
            # service (Browserbase, Anchor, Bright Data), a self-hosted
            # Chromium on a VPS, or a Docker container.
            # CDP is a Chromium protocol; the browser= and headless= params
            # are irrelevant and ignored when remote_cdp_url is set.
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self._remote_cdp_url
            )
            logger.info("Connected to remote browser via CDP: %s", self._remote_cdp_url)
        else:
            # ── Local browser launch ────────────────────────────────────────
            self._browser = await self._launch_browser()

        ctx_kwargs: dict = dict(
            viewport=self._viewport,
            user_agent=self._user_agent,
            java_script_enabled=True,
        )
        # Proxy only applies to local launches; remote browsers control their
        # own network routing (already warned in __init__ if both were set).
        if self._proxy and not self._remote_cdp_url:
            ctx_kwargs["proxy"] = self._proxy
        if self._session_file and Path(self._session_file).exists():
            ctx_kwargs["storage_state"] = self._session_file

        self._context = await self._browser.new_context(**ctx_kwargs)
        if self._stealth:
            await self._context.add_init_script(_STEALTH_JS)
            logger.info("Stealth mode enabled.")
        page = await self._context.new_page()
        self._setup_page(page)
        self._pages      = [page]
        self._active_idx = 0

        if self._remote_cdp_url:
            logger.info("Remote browser session ready.")
        else:
            logger.info("Browser started: %s (headless=%s)", self._browser_type, self._headless)

    async def _launch_browser(self) -> Browser:
        """
        Launch the selected browser, auto-installing Playwright-managed
        binaries on first use if they are missing.
        """
        bt     = self._browser_type
        engine = getattr(self._playwright, bt.engine)

        kwargs: dict = dict(headless=self._headless, slow_mo=self._slow_mo)
        if bt.is_channel:
            kwargs["channel"] = bt.value
        if bt.engine == "chromium":
            kwargs["args"] = _CHROMIUM_ARGS

        try:
            return await engine.launch(**kwargs)
        except Exception as e:
            err = str(e)
            if "Executable doesn't exist" in err or "playwright install" in err:
                if bt.is_channel:
                    name = "Google Chrome" if bt == BrowserType.CHROME else "Microsoft Edge"
                    url  = "google.com/chrome" if bt == BrowserType.CHROME else "microsoft.com/edge"
                    raise RuntimeError(
                        f"{name} is not installed on this system.\n"
                        f"Install it from https://{url}\n"
                        f"Or use a Playwright-managed browser: LLMBrowser(browser='chromium')"
                    ) from e
                await self._install_browser()
                return await engine.launch(**kwargs)
            raise

    async def _install_browser(self) -> None:
        """Run 'playwright install <engine>' automatically on first use."""
        import sys
        target = self._browser_type.engine
        logger.info(
            "Browser binaries for '%s' not found. "
            "Running 'playwright install %s' — this is a one-time setup.",
            target, target,
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to install '{target}' browser binaries automatically.\n"
                f"Error: {stderr.decode().strip()}\n"
                f"Fix: run  playwright install {target}  in your terminal."
            )
        logger.info("'%s' installed successfully.", target)

    async def close(self) -> None:
        """Cleanly shut down the browser and Playwright."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._pages.clear()
            logger.info("Browser closed.")
        except Exception as e:
            logger.warning("Error during browser close: %s", e)

    async def __aenter__(self):
        await self._start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    async def save_session(self, path: str) -> None:
        """
        Persist cookies and localStorage to a JSON file for later reuse.

        Load on the next run by passing session_file= to __init__, or by
        calling load_session() on an already-running browser.
        """
        storage = await self._context.storage_state()
        Path(path).write_text(json.dumps(storage))
        logger.info("Session saved to %s", path)

    async def load_session(self, path: str) -> None:
        """
        Restore session from a saved state file, recreating the browser context.

        Replaces all open tabs with a single fresh page under the restored session.
        """
        if not Path(path).exists():
            raise FileNotFoundError(f"Session file not found: {path}")
        if self._context:
            await self._context.close()
        ctx_kwargs: dict = dict(
            viewport=self._viewport,
            user_agent=self._user_agent,
            java_script_enabled=True,
            storage_state=path,
        )
        if self._proxy and not self._remote_cdp_url:
            ctx_kwargs["proxy"] = self._proxy
        self._context = await self._browser.new_context(**ctx_kwargs)
        if self._stealth:
            await self._context.add_init_script(_STEALTH_JS)
        page = await self._context.new_page()
        self._setup_page(page)
        self._pages      = [page]
        self._active_idx = 0
        logger.info("Session loaded from %s", path)

    # ------------------------------------------------------------------
    # Multi-tab management
    # ------------------------------------------------------------------

    @property
    def tab_count(self) -> int:
        """Number of open tabs."""
        return len(self._pages)

    @property
    def active_tab(self) -> int:
        """Zero-based index of the currently active tab."""
        return self._active_idx

    async def new_tab(self, url: Optional[str] = None) -> int:
        """
        Open a new browser tab and switch to it.

        Args:
            url: Optional URL to navigate to immediately.

        Returns:
            The zero-based index of the new tab.
        """
        page = await self._context.new_page()
        self._setup_page(page)
        self._pages.append(page)
        self._active_idx = len(self._pages) - 1
        if url:
            await self.navigate(url)
        logger.info("Opened new tab (index=%d)", self._active_idx)
        return self._active_idx

    async def switch_tab(self, index: int) -> None:
        """Switch to a tab by its zero-based index."""
        if not 0 <= index < len(self._pages):
            raise ValueError(
                f"Tab index {index} out of range. "
                f"Valid range: 0–{len(self._pages) - 1}."
            )
        self._active_idx = index
        await self._pages[index].bring_to_front()
        logger.info("Switched to tab %d (%s)", index, self._pages[index].url)

    async def close_tab(self) -> None:
        """
        Close the current tab and switch to the nearest remaining tab.

        Raises:
            ActionExecutionError: if this is the last remaining tab.
        """
        if len(self._pages) == 1:
            raise ActionExecutionError("Cannot close the last remaining tab.")
        await self._pages[self._active_idx].close()
        self._pages.pop(self._active_idx)
        self._active_idx = max(0, self._active_idx - 1)
        await self._pages[self._active_idx].bring_to_front()
        logger.info("Closed tab. Now on tab %d (%s)", self._active_idx, self._pages[self._active_idx].url)

    async def list_tabs(self) -> list[dict]:
        """
        Return metadata for all open tabs.

        Returns:
            List of dicts with keys: index, url, title, active.
        """
        return [
            {
                "index":  i,
                "url":    page.url,
                "title":  await page.title(),
                "active": i == self._active_idx,
            }
            for i, page in enumerate(self._pages)
        ]

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> None:
        """
        Navigate to a URL and wait for the page to settle.

        Automatically dismisses cookie/consent banners after load when
        dismiss_dialogs=True (the default).

        Raises:
            NavigationError: if navigation fails or times out.
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        logger.info("Navigating to: %s", url)
        try:
            await self._page.goto(url, wait_until="domcontentloaded")
            await safe_wait(self._page, timeout=10_000)
        except PlaywrightTimeoutError as e:
            raise NavigationError(f"Navigation timed out: {url}") from e
        except Exception as e:
            raise NavigationError(f"Navigation failed: {url} — {e}") from e

        if self._dismiss_dialogs:
            await self.dismiss_cookie_banners()

        if self._on_captcha_detected:
            await self.solve_captcha()

    async def dismiss_cookie_banners(self) -> bool:
        """
        Attempt to dismiss cookie consent banners and GDPR dialogs.

        Tries known library-specific selectors first (OneTrust, CookieBot,
        Didomi, TrustArc, …) then falls back to text-pattern matching on
        any visible cookie-related container.

        Returns:
            True if a banner was dismissed, False if none was found.
        """
        try:
            result = await self._page.evaluate(_COOKIE_DISMISS_JS)
            if result:
                logger.info("Dismissed cookie banner: %s", result)
                await safe_wait(self._page, timeout=2_000)
                return True
            return False
        except Exception as e:
            logger.debug("Cookie banner dismissal failed (non-critical): %s", e)
            return False

    # ------------------------------------------------------------------
    # CAPTCHA detection and solving
    # ------------------------------------------------------------------

    async def detect_captcha(self) -> Optional[CaptchaContext]:
        """
        Detect a CAPTCHA widget on the current page.

        Identifies reCAPTCHA v2 (checkbox), reCAPTCHA v3 (invisible/score),
        and hCaptcha.  Checks widget elements first, then falls back to
        script/iframe src parsing.

        Returns:
            A ``CaptchaContext`` if a CAPTCHA is found, otherwise ``None``.
        """
        try:
            result = await self._page.evaluate(_CAPTCHA_DETECT_JS)
            if result:
                return CaptchaContext(
                    type=result["type"],
                    sitekey=result["sitekey"],
                    url=self._page.url,
                )
            return None
        except Exception as e:
            logger.debug("CAPTCHA detection failed (non-critical): %s", e)
            return None

    async def inject_captcha_token(self, token: str, captcha_type: str) -> bool:
        """
        Inject a solved token into the page and trigger the CAPTCHA callback.

        Works for reCAPTCHA v2/v3 (sets ``#g-recaptcha-response`` and fires
        the ``___grecaptcha_cfg`` callback) and hCaptcha (sets
        ``[name="h-captcha-response"]`` and fires ``data-callback``).

        Args:
            token:        The solver's response token.
            captcha_type: ``"recaptcha_v2"``, ``"recaptcha_v3"``, or
                          ``"hcaptcha"`` — from ``CaptchaContext.type``.

        Returns:
            ``True`` if injection succeeded, ``False`` if the page structure
            was unexpected (token was still set where possible).
        """
        try:
            result = await self._page.evaluate(_CAPTCHA_INJECT_JS, [token, captcha_type])
            logger.info("CAPTCHA token injected (type=%s, success=%s)", captcha_type, result)
            return bool(result)
        except Exception as e:
            logger.warning("CAPTCHA token injection failed: %s", e)
            return False

    async def solve_captcha(self) -> bool:
        """
        Detect any CAPTCHA on the current page, call ``on_captcha_detected``
        to obtain a solved token, and inject it back into the page.

        Called automatically after every ``navigate()`` when
        ``on_captcha_detected`` is configured.  Also available for manual
        calls after dynamic page transitions.

        Returns:
            ``True`` if a CAPTCHA was found and the token was injected.
            ``False`` if no CAPTCHA was detected or no callback is configured.
        """
        if not self._on_captcha_detected:
            return False

        ctx = await self.detect_captcha()
        if not ctx:
            return False

        logger.info(
            "CAPTCHA detected (type=%s, sitekey=%s…) — calling solver.",
            ctx.type, ctx.sitekey[:12],
        )
        try:
            token = await self._on_captcha_detected(ctx)
        except Exception as e:
            logger.error("CAPTCHA solver callback raised an error: %s", e)
            return False

        injected = await self.inject_captcha_token(token, ctx.type)
        if injected:
            await safe_wait(self._page, timeout=5_000)
        return injected

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    # Extensions treated as plain text (read and returned as a string)
    _TEXT_EXTENSIONS: frozenset[str] = frozenset({
        ".txt", ".csv", ".json", ".md", ".html", ".htm",
        ".xml", ".yaml", ".yml", ".log", ".tsv", ".rst",
    })
    _MAX_READ_CHARS: int = 50_000   # ~50 KB

    @property
    def downloads(self) -> list[str]:
        """Paths of all files downloaded in this session, in order."""
        return list(self._downloads)

    async def upload_file(self, element_id: int, file_path: str) -> None:
        """
        Upload a file to a file-input element.

        Injects annotations, finds the input, then calls Playwright's
        set_input_files — works for <input type="file"> elements.

        Args:
            element_id: Numeric ID from the current page state.
            file_path:  Absolute path to the file on the local filesystem.

        Raises:
            FileNotFoundError: if the file does not exist.
            ElementNotFoundError: if the element is not found.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Upload file not found: {file_path}")

        await self._inject_annotations()
        locator = await self._find_element(element_id)
        await locator.set_input_files(str(path))
        logger.info("Uploaded '%s' to element id=%d", path.name, element_id)

    def read_file(self, path: str) -> str:
        """
        Read the content of a downloaded (or any local) file.

        Returns the text content for recognised text formats, and a
        metadata summary (name, size, extension) for binary or unknown files.

        Args:
            path: File path, typically from browser.downloads.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        size = p.stat().st_size
        ext  = p.suffix.lower()

        if ext in self._TEXT_EXTENSIONS:
            content = p.read_text(errors="replace")
            if len(content) > self._MAX_READ_CHARS:
                content = (
                    content[: self._MAX_READ_CHARS]
                    + f"\n\n... (truncated — showing first {self._MAX_READ_CHARS:,} of {size:,} chars)"
                )
            return content

        return (
            f"Binary / unsupported file: {p.name}\n"
            f"Size: {size:,} bytes\n"
            f"Type: {ext or '(no extension)'}\n"
            f"Path: {path}\n"
            "Use a dedicated reader for this file type."
        )

    # ------------------------------------------------------------------
    # State capture
    # ------------------------------------------------------------------

    async def _inject_annotations(self) -> int:
        """Annotate interactive elements and return the count."""
        count = await self._page.evaluate(ANNOTATION_JS)
        logger.debug("Annotated %d interactive elements.", count)
        return count

    async def _cleanup_annotations(self) -> None:
        await self._page.evaluate(CLEANUP_JS)

    async def _capture_screenshot(self) -> str:
        raw = await self._page.screenshot(type="jpeg", quality=85, full_page=False)
        return screenshot_to_base64(raw)

    async def _extract_dom(self) -> str:
        try:
            return await self._page.evaluate(DOM_EXTRACTION_JS)
        except Exception as e:
            logger.warning("DOM extraction failed: %s", e)
            return "(DOM extraction unavailable)"

    async def _extract_interactive_elements(self) -> list[dict]:
        try:
            return await self._page.evaluate(INTERACTIVE_ELEMENTS_JS)
        except Exception as e:
            logger.warning("Interactive element extraction failed: %s", e)
            return []

    async def _extract_aria_tree(self) -> Optional[str]:
        """
        Extract a compact ARIA accessibility tree from the page.

        Uses a JS DOM walk because page.accessibility was removed in
        Playwright ≥ 1.46.  Only emits semantic/role-bearing elements to keep
        token count low (~60 % fewer tokens than the full simplified DOM).

        Returns None if extraction fails or the page has no semantic content.
        """
        _ARIA_JS = """() => {
            const SEMANTIC = new Set([
                'a','button','input','select','textarea',
                'h1','h2','h3','h4','h5','h6',
                'nav','main','header','footer','article',
                'section','aside','form','label',
            ]);
            const lines = [];
            function walk(el, depth) {
                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role');
                const labelledBy = el.getAttribute('aria-labelledby');
                const ariaLabel = el.getAttribute('aria-label') ||
                    (labelledBy && document.getElementById(labelledBy)
                        ?.textContent?.trim());
                const text = el.childElementCount === 0
                    ? el.textContent?.trim().slice(0, 80)
                    : '';
                if (SEMANTIC.has(tag) || role) {
                    const effectiveRole = role || tag;
                    const parts = ['[' + effectiveRole + ']'];
                    if (ariaLabel) parts.push(ariaLabel);
                    else if (text) parts.push(text);
                    lines.push('  '.repeat(depth) + parts.join(' '));
                }
                for (const child of el.children) walk(child, depth + 1);
            }
            if (document.body) walk(document.body, 0);
            return lines.length > 0 ? lines.join('\\n') : null;
        }"""
        try:
            result = await self._page.evaluate(_ARIA_JS)
            return result
        except Exception as e:
            logger.warning("ARIA tree extraction failed: %s", e)
            return None

    async def _get_scroll_info(self) -> tuple[dict, int]:
        info = await self._page.evaluate("""
            () => ({
                x:      window.scrollX,
                y:      window.scrollY,
                height: document.body.scrollHeight,
            })
        """)
        return {"x": info["x"], "y": info["y"]}, info["height"]

    async def get_state(
        self,
        mode: Literal["dom", "aria", "both"] = "dom",
    ) -> PageState:
        """
        Capture the full page state.

        Args:
            mode: Controls which page-structure representation is included.
                "dom"  — simplified DOM tree (default, works with all browsers).
                "aria" — ARIA accessibility tree (~60 % fewer tokens, better
                         structured for LLMs, skips visual-only elements).
                "both" — both representations (useful for debugging/comparison).

        Returns:
            PageState with url, title, screenshot, interactive elements,
            and the requested page structure.
        """
        await self._inject_annotations()

        screenshot_b64       = await self._capture_screenshot()
        interactive_elements = await self._extract_interactive_elements()
        scroll_pos, page_height = await self._get_scroll_info()

        simplified_dom = await self._extract_dom()       if mode in ("dom",  "both") else ""
        aria_tree      = await self._extract_aria_tree() if mode in ("aria", "both") else None

        await self._cleanup_annotations()

        return PageState(
            url=self._page.url,
            title=await self._page.title(),
            screenshot_b64=screenshot_b64,
            simplified_dom=simplified_dom,
            aria_tree=aria_tree,
            interactive_elements=interactive_elements,
            scroll_position=scroll_pos,
            page_height=page_height,
        )

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def _find_element(self, target_id: int):
        """
        Locate an element by its injected data-llm-id.

        Handles elements in the main document, shadow DOM, and same-origin iframes.
        Annotations must already be active (injected by execute_action).

        Raises:
            ElementNotFoundError: if no element with that ID exists.
        """
        # Probe which frame the element lives in. Returns 'frame-N' for iframe
        # elements or null for the main document (and shadow DOM elements).
        frame_id: Optional[str] = await self._page.evaluate(f"""
            () => {{
                if (document.querySelector("[data-llm-id='{target_id}']")) return null;
                for (const iframe of document.querySelectorAll('[data-llm-frame-id]')) {{
                    try {{
                        if (iframe.contentDocument.querySelector("[data-llm-id='{target_id}']"))
                            return iframe.getAttribute('data-llm-frame-id');
                    }} catch (e) {{}}
                }}
                return null;
            }}
        """)

        if frame_id:
            locator = (
                self._page
                .frame_locator(f"[data-llm-frame-id='{frame_id}']")
                .locator(f"[data-llm-id='{target_id}']")
            )
        else:
            locator = self._page.locator(f"[data-llm-id='{target_id}']")

        try:
            await locator.wait_for(state="attached", timeout=5_000)
        except PlaywrightTimeoutError:
            raise ElementNotFoundError(
                f"No element with data-llm-id={target_id}. "
                "The page may have changed since the last state capture."
            )
        return locator

    async def _action_click(self, action: BrowserAction) -> None:
        locator = await self._find_element(action.target_id)
        try:
            await locator.scroll_into_view_if_needed()
            await locator.click(timeout=10_000)
            logger.info("Clicked element id=%d", action.target_id)
        except PlaywrightTimeoutError as e:
            raise ActionExecutionError(
                f"Click timed out on element id={action.target_id}"
            ) from e

    async def _action_type(self, action: BrowserAction) -> None:
        locator = await self._find_element(action.target_id)
        try:
            await locator.scroll_into_view_if_needed()
            await locator.click()
            await locator.fill(action.value)
            logger.info("Typed into element id=%d: '%s'", action.target_id, action.value[:30])
        except PlaywrightTimeoutError as e:
            raise ActionExecutionError(
                f"Type timed out on element id={action.target_id}"
            ) from e

    async def _action_scroll(self, action: BrowserAction) -> None:
        direction: str = str(action.scroll_direction or "down")
        amount:    int = action.scroll_amount    or 300
        delta: dict[str, tuple[int, int]] = {
            "down":  (0,        amount),
            "up":    (0,       -amount),
            "right": (amount,   0),
            "left":  (-amount,  0),
        }
        dx, dy = delta.get(direction) or (0, amount)
        await self._page.evaluate(f"window.scrollBy({dx}, {dy})")
        logger.info("Scrolled %s by %dpx", direction, amount)

    async def _action_wait(self, action: BrowserAction) -> None:
        ms = max(100, min(int(action.value or 1_000), 10_000))
        await self._page.wait_for_timeout(ms)
        logger.info("Waited %dms", ms)

    async def _action_navigate(self, action: BrowserAction) -> None:
        await self.navigate(action.value)

    async def _action_key(self, action: BrowserAction) -> None:
        key = action.value or "Enter"
        await self._page.keyboard.press(key)
        logger.info("Pressed key: %s", key)

    async def _action_done(self, action: BrowserAction) -> None:
        pass

    # ------------------------------------------------------------------
    # Retry / recovery
    # ------------------------------------------------------------------

    # Actions that interact with a specific element and benefit from retrying
    _RETRYABLE_ACTIONS: frozenset[str] = frozenset({"click", "type"})

    async def _recover_before_retry(
        self,
        action: BrowserAction,
        exc: Exception,
        attempt: int,
    ) -> None:
        """
        Apply a recovery strategy before a retry attempt.

        ElementNotFoundError — re-annotate immediately.
            The DOM may have just finished rendering. If nothing changed, the
            same element will get the same ID and the retry will succeed.

        ActionExecutionError (timeout) — exponential back-off then re-annotate.
            The element may be mid-animation, covered by a spinner, or waiting
            for a JS event. Giving it time is usually enough.
        """
        if isinstance(exc, ElementNotFoundError):
            logger.info(
                "Recovery (attempt %d): re-annotating after element not found (id=%s).",
                attempt, action.target_id,
            )
            await self._inject_annotations()
        else:
            wait_ms = 800 * attempt   # 800 ms → 1600 ms → …
            logger.info(
                "Recovery (attempt %d): waiting %d ms then re-annotating after action timeout.",
                attempt, wait_ms,
            )
            await self._page.wait_for_timeout(wait_ms)
            if action.target_id is not None:
                await self._inject_annotations()

    async def _execute_with_retry(
        self,
        action: BrowserAction,
        handler: Callable,
    ) -> None:
        """
        Execute an action handler with up to self._max_retries recovery attempts.

        On each failure the appropriate recovery strategy is applied before the
        next attempt. The original exception is re-raised on the final attempt.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                if attempt > 0:
                    await self._recover_before_retry(action, last_exc, attempt)
                await handler(action)
                if attempt > 0:
                    logger.info(
                        "Action '%s' succeeded on attempt %d/%d.",
                        action.action, attempt + 1, self._max_retries + 1,
                    )
                return
            except (ElementNotFoundError, ActionExecutionError) as exc:
                if attempt == self._max_retries:
                    raise type(exc)(
                        f"{exc} — failed after {self._max_retries + 1} attempt(s)."
                    ) from exc
                last_exc = exc
                logger.warning(
                    "Action '%s' on element id=%s failed (attempt %d/%d): %s — retrying.",
                    action.action, action.target_id,
                    attempt + 1, self._max_retries + 1, exc,
                )

    async def execute_action(self, action_data: dict) -> None:
        """
        Parse and execute a single browser action.

        Element-targeting actions (click, type) are automatically retried up to
        self._max_retries times with progressive recovery strategies:
          - ElementNotFoundError → re-annotate immediately
          - ActionExecutionError → exponential back-off then re-annotate

        Raises:
            ActionExecutionError: if the action fails after all retries.
            ElementNotFoundError: if the target element cannot be found after all retries.
        """
        try:
            action = BrowserAction(**action_data)
        except Exception as e:
            raise ActionExecutionError(f"Invalid action data: {e}") from e

        logger.info("Action: %s | thought: %s", action.action, action.thought[:80])

        # Pre-annotate once for actions that target a specific element
        if action.target_id is not None:
            await self._inject_annotations()

        handlers: dict[str, Callable] = {
            "click":    self._action_click,
            "type":     self._action_type,
            "scroll":   self._action_scroll,
            "wait":     self._action_wait,
            "navigate": self._action_navigate,
            "key":      self._action_key,
            "done":     self._action_done,
        }

        handler = handlers.get(action.action)
        if not handler:
            raise ActionExecutionError(f"Unknown action: '{action.action}'")

        if action.action in self._RETRYABLE_ACTIONS:
            await self._execute_with_retry(action, handler)
        else:
            await handler(action)

        if action.action not in ("wait", "done", "scroll"):
            await safe_wait(self._page, timeout=8_000)

    async def step(
        self,
        action_data: dict,
        mode: Literal["dom", "aria", "both"] = "dom",
    ) -> PageState:
        """
        Execute an action and return the resulting page state.
        Primary method for agent control loops.
        """
        await self.execute_action(action_data)
        return await self.get_state(mode=mode)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def current_url(self) -> str:
        return self._page.url

    async def page_title(self) -> str:
        return await self._page.title()

    async def go_back(self) -> None:
        await self._page.go_back()
        await safe_wait(self._page)

    async def go_forward(self) -> None:
        await self._page.go_forward()
        await safe_wait(self._page)

    async def reload(self) -> None:
        await self._page.reload()
        await safe_wait(self._page)
