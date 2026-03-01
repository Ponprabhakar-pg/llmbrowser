import base64
import logging

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Wait for DOM mutations to settle: resolves after SETTLE_DELAY ms of inactivity
# or when the hard timeout fires, whichever comes first.
_SETTLE_JS = """
(timeoutMs) => new Promise(resolve => {
    const SETTLE_DELAY = 300;
    let settled = false;
    let debounceTimer = null;
    let hardTimer = null;

    const finish = () => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        clearTimeout(debounceTimer);
        clearTimeout(hardTimer);
        resolve();
    };

    const reschedule = () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(finish, SETTLE_DELAY);
    };

    const observer = new MutationObserver(reschedule);
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
    });

    reschedule();                             // settle immediately if DOM is already quiet
    hardTimer = setTimeout(finish, timeoutMs); // safety valve
})
"""


def screenshot_to_base64(screenshot_bytes: bytes) -> str:
    return base64.b64encode(screenshot_bytes).decode("utf-8")


async def safe_wait(page: Page, timeout: int = 5_000) -> None:
    """
    Wait for the page to settle using a MutationObserver debounce.

    Resolves after 300 ms of DOM inactivity, or when the hard timeout fires.
    More reliable than network-idle polling for SPAs, streaming pages, and
    sites that hold open long-poll connections.

    Falls back to domcontentloaded load-state if JavaScript evaluation fails.
    """
    try:
        await page.evaluate(_SETTLE_JS, timeout)
    except PlaywrightTimeoutError:
        pass  # hard timeout inside JS already resolved — this is expected
    except Exception as e:
        logger.warning("safe_wait JS failed (%s), falling back to load-state.", e)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except PlaywrightTimeoutError:
            logger.warning("safe_wait: domcontentloaded timed out, continuing.")
