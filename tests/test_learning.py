from agentgate.learning.memory import ANY, Lesson, Memory
from agentgate.learning.reflect import demonstration, reflect, trace_digest
from agentgate.llm import JSONResponse
from agentgate.suites.invoice import Task

TASK = Task("medium-globex-enter", "enter_invoice", "globex", 5, "medium")
TRACE = [
    {"event": "plan", "plan": ["Sign in"]},
    {"step": 1, "action": "fill", "target": "Email, Password", "ok": True, "verified": True, "outcome": "Filled 2"},
    {"step": 2, "action": "click", "target": "Continue", "ok": True, "verified": True, "outcome": "Clicked"},
    {"step": 3, "action": "click", "target": "Show", "ok": False, "verified": False,
     "feedback": "The element is covered by another element"},
    {"step": 4, "action": "done", "args": {"answer": "x"}},
]


def lesson(text, vendor="globex", kind="enter_invoice", source="reflection"):
    return Lesson(text, kind, vendor, "medium", source)


def test_retrieval_prefers_same_vendor_and_kind_and_ignores_other_portals(tmp_path):
    m = Memory(tmp_path / "l.jsonl")
    m.add([lesson("On Globex, dismiss the survey modal before clicking invoice links."),
           lesson("Acme uses month-first dates.", vendor="acme"),
           lesson("Always download the PDF before filling the AP form.", vendor=ANY),
           lesson("Count unpaid invoices across all pages.", kind="count_unpaid")])
    got = [l.text for l in m.retrieve("enter_invoice", "globex", "medium", "Record invoice from Globex in AP")]
    assert got[0].startswith("On Globex")
    assert "Acme uses month-first dates." not in got
    assert "Always download the PDF before filling the AP form." in got


def test_memory_persists_and_skips_near_duplicates(tmp_path):
    path = tmp_path / "l.jsonl"
    m = Memory(path)
    assert m.add([lesson("Dismiss the survey modal on Globex before clicking links.")]) == 1
    assert m.add([lesson("Dismiss the survey modal on Globex before clicking any links.")]) == 0
    assert len(Memory(path).lessons) == 1


def test_demonstrations_replace_older_ones(tmp_path):
    m = Memory(tmp_path / "l.jsonl")
    m.add([lesson("old way", source="demonstration")])
    m.add([lesson("new way", source="demonstration")])
    assert [l.text for l in m.lessons] == ["new way"]


def test_trace_digest_uses_labels_and_feedback():
    digest = trace_digest(TRACE)
    assert "#2 click on 'Continue' → ok" in digest
    assert "#3 click on 'Show' → FAILED: The element is covered" in digest


class FakeReflector:
    def __init__(self, lessons):
        self.lessons, self.prompts = lessons, []

    def extract(self, system, prompt, images, schema, name):
        self.prompts.append(prompt)
        return JSONResponse({"lessons": self.lessons}, "")


def test_reflection_drops_lessons_that_memorize_values():
    llm = FakeReflector([
        {"lesson": "On Globex, close the survey modal (No thanks) before opening invoices.", "applies_to": "this_vendor"},
        {"lesson": "The total for 2026-GLX-0412 is 1234.56.", "applies_to": "this_vendor"},
        {"lesson": "Download the PDF before filling the AP form.", "applies_to": "all_vendors"},
    ])
    lessons = reflect(llm, TASK, "Globex Industrial GmbH", "Record invoice 2026-GLX-0412", "wrong_result", TRACE, 1)
    assert [l.vendor for l in lessons] == ["globex", ANY]
    assert all("1234" not in l.text for l in lessons)


def test_reflection_never_sees_the_expected_answer():
    llm = FakeReflector([])
    reflect(llm, TASK, "Globex", "Record the invoice", "wrong_result", TRACE, 1)
    prompt = llm.prompts[0]
    assert "result was wrong" in prompt and "expected" not in prompt.lower()


def test_demonstration_outline_from_verified_steps():
    demo = demonstration(TASK, "Globex Industrial GmbH", TRACE, 2)
    assert demo.source == "demonstration"
    assert "fill 'Email, Password' → click 'Continue' → done" in demo.text  # failed step left out
