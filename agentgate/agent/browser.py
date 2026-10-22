"""Turns a Playwright page into text observations and executes agent actions."""
from __future__ import annotations

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
  return { url: location.href, title: document.title, text: document.body.innerText, elements: out };
}
"""


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


class Browser:
    def __init__(self, page: Page, timeout_ms: int = 3000):
        self.page = page
        self.timeout_ms = timeout_ms

    def observe(self) -> str:
        snap = self.page.evaluate(SNAPSHOT_JS)
        text = snap["text"]
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n…(truncated)"
        elements = "\n".join(_describe(e) for e in snap["elements"]) or "(none)"
        return (
            f"URL: {snap['url']}\nTitle: {snap['title']}\n"
            f"--- Visible text ---\n{text}\n--- Interactive elements ---\n{elements}"
        )

    def _settle(self) -> None:
        self.page.wait_for_timeout(150)
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass

    def _locate(self, element):
        loc = self.page.locator(f'[data-ag-id="{int(element)}"]')
        if loc.count() == 0:
            raise LookupError(f"No element [{element}] on the current page. Use numbers from the latest snapshot.")
        return loc.first

    def execute(self, name: str, args: dict) -> str:
        try:
            if name == "click":
                self._locate(args["element"]).click(timeout=self.timeout_ms)
                self._settle()
                return f"Clicked [{args['element']}]."
            if name == "type":
                loc = self._locate(args["element"])
                loc.fill(str(args.get("text", "")), timeout=self.timeout_ms)
                if args.get("press_enter"):
                    loc.press("Enter")
                    self._settle()
                return f"Typed into [{args['element']}]."
            if name == "select":
                loc = self._locate(args["element"])
                option = str(args.get("option", ""))
                try:
                    loc.select_option(label=option, timeout=self.timeout_ms)
                except PlaywrightError:
                    loc.select_option(value=option, timeout=self.timeout_ms)
                return f"Selected {option!r} in [{args['element']}]."
            if name == "go_back":
                self.page.go_back()
                self._settle()
                return "Went back."
            return f"Unknown action {name!r}."
        except (KeyError, ValueError, TypeError) as e:
            return f"Invalid arguments for {name}: {e}"
        except LookupError as e:
            return str(e)
        except PlaywrightTimeoutError as e:
            if "intercepts pointer events" in str(e):
                return "Action failed: the element is covered by another element (for example a pop-up or banner). Dismiss it first."
            return "Action failed: timed out waiting for the element to be actionable."
        except PlaywrightError as e:
            return f"Action failed: {str(e).splitlines()[0]}"

    def screenshot(self, path: str) -> None:
        self.page.screenshot(path=path, type="jpeg", quality=60)
