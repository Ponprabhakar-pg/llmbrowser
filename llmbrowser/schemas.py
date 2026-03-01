from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


@dataclass
class HumanHelpContext:
    """
    Context passed to the ``on_human_needed`` callback when the agent calls
    ``request_human_help``.

    Fields:
        reason:         The agent's own explanation of why it needs help.
        url:            Current page URL.
        title:          Current page title.
        screenshot_b64: Base64-encoded JPEG of the current viewport — lets the
                        human see exactly what the agent sees.
        session_id:     Stable identifier for this toolkit session — useful for
                        correlating pause and resume events in async/webhook flows.
    """

    reason:         str
    url:            str
    title:          str
    screenshot_b64: str
    session_id:     str


@dataclass
class CaptchaContext:
    """
    Context passed to the ``on_captcha_detected`` callback.

    Fields:
        type:    CAPTCHA variant — ``"recaptcha_v2"``, ``"recaptcha_v3"``,
                 or ``"hcaptcha"``.
        sitekey: Public sitekey extracted from the page — required by all
                 third-party solver APIs (CapSolver, 2captcha, etc.).
        url:     Current page URL — also required by solver APIs.
    """

    type:    str
    sitekey: str
    url:     str


class BrowserAction(BaseModel):
    thought: str = Field(
        ...,
        description="Your reasoning for the next action.",
    )
    action: Literal["click", "type", "scroll", "wait", "navigate", "key", "done"] = Field(
        ...,
        description="The action to perform.",
    )
    target_id: Optional[int] = Field(
        None,
        description="The numeric ID of the tagged element on screen.",
        ge=1,
    )
    value: Optional[str] = Field(
        None,
        description="Text to type, URL to navigate to, or key name to press (e.g. 'Enter', 'Tab').",
    )
    scroll_direction: Optional[Literal["up", "down", "left", "right"]] = Field(
        None,
        description="Direction to scroll.",
    )
    scroll_amount: Optional[int] = Field(
        default=300,
        description="Pixels to scroll.",
        ge=1,
        le=5000,
    )

    @model_validator(mode="after")
    def validate_action_requirements(self):
        """
        Cross-field validation — runs after all fields are set, including
        those that use their default value (None / 300), which field_validator
        does not cover in Pydantic v2.
        """
        action = self.action
        if action in ("click", "type") and self.target_id is None:
            raise ValueError(f"Action '{action}' requires a target_id.")
        if action == "type" and not self.value:
            raise ValueError("Action 'type' requires a non-empty value.")
        if action == "navigate" and not self.value:
            raise ValueError("Action 'navigate' requires a URL value.")
        if action == "key" and not self.value:
            raise ValueError("Action 'key' requires a key name (e.g. 'Enter', 'Tab', 'Escape').")
        return self


class PageState(BaseModel):
    """Represents the current observable state of the browser page."""

    url: str
    title: str
    screenshot_b64: str = Field(
        ...,
        description="Base64-encoded JPEG screenshot.",
    )
    simplified_dom: str = Field(
        default="",
        description="Simplified DOM tree as text. Empty when state_mode='aria'.",
    )
    aria_tree: Optional[str] = Field(
        default=None,
        description=(
            "ARIA accessibility tree as compact text. "
            "Available when state_mode='aria' or 'both'. "
            "More token-efficient than simplified_dom."
        ),
    )
    interactive_elements: list[dict] = Field(
        default_factory=list,
        description="List of interactive elements with their IDs and metadata.",
    )
    scroll_position: dict = Field(
        default_factory=dict,
        description="Current scroll position {x, y}.",
    )
    page_height: int = Field(
        default=0,
        description="Total scrollable height of the page in pixels.",
    )
