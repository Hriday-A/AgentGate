"""Friction: the obstacles a test environment puts in front of the agent.

Each toggle is independent, so an eval can isolate one obstacle
(e.g. "how much does pagination alone cost us?") or use a preset.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Difficulty = Literal["easy", "medium", "hard"]


class Friction(BaseModel):
    # Reject unknown keys so a typo like "popup" fails loudly instead of being ignored.
    model_config = ConfigDict(extra="forbid")

    shuffle_layout: bool = False      # column order, column labels, status wording, sort order
    popups: bool = False              # cookie banner on every page + survey modal on the invoice list
    pagination: bool = False          # invoice list split into pages of 4–6
    renamed_buttons: bool = False     # "Sign in" may become "Continue", "Download PDF" may become "Export"…
    session_timeout: bool = False     # session expires after N authenticated page loads
    session_max_requests: int = Field(default=8, ge=2)
    scanned_pdf_rate: float = Field(default=0.0, ge=0.0, le=1.0)  # share of PDFs that are image-only "scans"


PRESETS: dict[str, Friction] = {
    "easy": Friction(),
    "medium": Friction(
        shuffle_layout=True, popups=True, pagination=True, renamed_buttons=True, scanned_pdf_rate=0.2,
    ),
    "hard": Friction(
        shuffle_layout=True, popups=True, pagination=True, renamed_buttons=True,
        session_timeout=True, scanned_pdf_rate=0.5,
    ),
}


def resolve(difficulty: Difficulty = "medium", overrides: dict[str, Any] | None = None) -> Friction:
    """Start from a preset and apply per-field overrides, validating the result."""
    return Friction(**{**PRESETS[difficulty].model_dump(), **(overrides or {})})
