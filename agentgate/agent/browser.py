"""Turns a Playwright page into text observations and performs browser actions.

Actions raise ActionError with a message written for the model ("the element is
covered by a pop-up…") instead of leaking Playwright stack traces.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

MAX_TEXT_CHARS = 6000

# Tags every visible interactive element with data-ag-id and describes it.
SNAPSHOT_JS = r"""
() => {
  document.querySelectorAll('[data-ag-id]').forEach(el => el.removeAttribute('data-ag-id'));
  const els = document.querySelectorAll('a[href], button, input, select, textarea, [role=button]');
  const out = [];
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.type === 'hidden') continue;
    const id = out.length;
    el.setAttribute('data-ag-id', String(id));
    let label = el.getAttribute('aria-label') || '';
    if (!label && el.id) {
      const l = document.querySelector(`label[for="${el.id}"]`);
      if (l) label = l.innerText;
    }
    if (!label) label = el.innerText || el.placeholder || el.name || '';
    const item = { id, tag: el.tagName.toLowerCase(), label: label.trim().replace(/\s+/g, ' ').slice(0, 80) };
    if (el.tagName === 'INPUT') { item.type = el.type; item.value = el.type === 'password' ? (el.value ? '••••' : '') : el.value; }
    if (el.tagName === 'TEXTAREA') item.value = el.value;
    if (el.tagName === 'SELECT') { item.options = [...el.options].map(o => o.text.trim()); item.value = el.options[el.selectedIndex]?.text.trim(); }
    if (el.tagName === 'A') item.href = el.getAttribute('href');
    out.push(item);
  }
  const alerts = [...document.querySelectorAll('[role=alert]')].map(e => e.innerText.trim()).filter(Boolean);
  return { url: location.href, title: document.title, text: document.body.innerText, elements: out, alerts };
}
"""


class ActionError(Exception):
    """An action failed in a way the model should hear about and can react to."""


@dataclass
class Snapshot:
    url: str
    title: str
    text: str
    elements: list[dict]
    alerts: list[str]

    @property
    def fingerprint(self) -> str:
        """Changes whenever the visible page changes; used to detect clicks that did nothing."""
        return hashlib.sha1(f"{self.url}\n{self.text}".encode()).hexdigest()

    def render(self) -> str:
        text = self.text if len(self.text) <= MAX_TEXT_CHARS else self.text[:MAX_TEXT_CHARS] + "\n…(truncated)"
        elements = "\n".join(_describe(e) for e in self.elements) or "(none)"
        alerts = "".join(f"\n⚠ {a}" for a in self.alerts)
        return (f"URL: {self.url}\nTitle: {self.title}{alerts}\n--- Visible text ---\n{text}\n"
                f"--- Interactive elements ---\n{elements}")


def _describe(el: dict) -> str:
    tag = el["tag"]
    if tag == "a":
        return f'[{el["id"]}] link "{el["label"]}" -> {el.get("href", "")}'
    if tag == "input":
        value = f' value="{el["value"]}"' if el.get("value") else ""
        return f'[{el["id"]}] input[{el.get("type", "text")}] "{el["label"]}"{value}'
    if tag == "select":
        return f'[{el["id"]}] select "{el["label"]}" selected="{el.get("value", "")}" options: {" | ".join(el.get("options", []))}'
    return f'[{el["id"]}] {tag} "{el["label"]}"'


def _friendly(e: PlaywrightError) -> str:
    text = str(e)
    if "intercepts pointer events" in text:
        return "The element is covered by another element (for example a pop-up or cookie banner). Dismiss it first."
    if isinstance(e, PlaywrightTimeoutError):
        return "Timed out waiting for the element to be ready."
    return text.splitlines()[0]


class Browser:
    def __init__(self, page: Page, timeout_ms: int = 3000):
        self.page = page
        self.timeout_ms = timeout_ms
        self.last: Snapshot | None = None

    # --- observation ---------------------------------------------------------

    def snapshot(self) -> Snapshot:
        self.last = Snapshot(**self.page.evaluate(SNAPSHOT_JS))
        return self.last

    def observe(self) -> str:
        return self.snapshot().render()

    def read_text(self, start: int = 0) -> str:
        """The page's full visible text from `start`, for pages longer than the snapshot shows."""
        text = self.page.evaluate("() => document.body.innerText")
        return text[start:start + MAX_TEXT_CHARS] or "(no more text)"

    # --- actions -------------------------------------------------------------

    def _settle(self) -> None:
        self.page.wait_for_timeout(150)
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass

    def _locate(self, element):
        try:
            loc = self.page.locator(f'[data-ag-id="{int(element)}"]')
        except (TypeError, ValueError):
            raise ActionError(f"Element must be a number from the snapshot, got {element!r}.") from None
        if loc.count() == 0:
            raise ActionError(f"No element [{element}] on the current page. Use numbers from the latest snapshot.")
        return loc.first

    def click(self, element) -> None:
        try:
            self._locate(element).click(timeout=self.timeout_ms)
        except PlaywrightError as e:
            raise ActionError(_friendly(e)) from None
        self._settle()

    def fill(self, fields: list[dict]) -> list[str | None]:
        """Fill several inputs/selects at once. Returns each input's resulting value
        (None for dropdowns: select_option already fails if the option doesn't exist)."""
        if not fields:
            raise ActionError("No fields given.")
        final = []
        for f in fields:
            loc = self._locate(f.get("element"))
            value = str(f.get("value", ""))
            try:
                if loc.evaluate("e => e.tagName") == "SELECT":
                    try:
                        loc.select_option(label=value, timeout=self.timeout_ms)
                    except PlaywrightError:
                        loc.select_option(value=value, timeout=self.timeout_ms)
                    final.append(None)
                else:
                    loc.fill(value, timeout=self.timeout_ms)
                    final.append(loc.input_value())
            except PlaywrightError as e:
                raise ActionError(f"Element [{f.get('element')}]: {_friendly(e)}") from None
        return final

    def navigate(self, url: str) -> None:
        """Go to a URL on the same site (relative or absolute). Other sites are refused."""
        target = urljoin(self.page.url, url)
        here, there = urlparse(self.page.url), urlparse(target)
        if (here.scheme, here.netloc) != (there.scheme, there.netloc):
            raise ActionError("Navigation is limited to the current site.")
        try:
            self.page.goto(target)
        except PlaywrightError as e:
            raise ActionError(_friendly(e)) from None
        self._settle()

    def go_back(self) -> None:
        self.page.go_back()
        self._settle()

    def download(self, element) -> tuple[str, bytes]:
        """Click a link that triggers a file download and return (filename, contents)."""
        loc = self._locate(element)
        try:
            with self.page.expect_download(timeout=8000) as info:
                loc.click(timeout=self.timeout_ms)
            dl = info.value
            data = open(dl.path(), "rb").read()
        except PlaywrightTimeoutError:
            self._settle()
            raise ActionError("Clicking that did not start a download. Check you are still signed in and "
                              "that this is the document download link.") from None
        except PlaywrightError as e:
            raise ActionError(_friendly(e)) from None
        return dl.suggested_filename, data

    def screenshot(self, path: str) -> None:
        self.page.screenshot(path=path, type="jpeg", quality=60)
