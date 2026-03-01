class BrowserToolError(Exception):
    """Base exception for all llmbrowser errors."""


class NavigationError(BrowserToolError):
    """Raised when page navigation fails."""


class ActionExecutionError(BrowserToolError):
    """Raised when an action cannot be executed."""


class ElementNotFoundError(BrowserToolError):
    """Raised when a target element cannot be located."""


class PageNotReadyError(BrowserToolError):
    """Raised when the page is not in a usable state."""
