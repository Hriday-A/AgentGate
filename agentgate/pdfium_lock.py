"""pdfium (the C library inside pypdfium2) is not thread-safe.

Calling it from two threads at once crashes the whole process with a segfault, so every
pdfium call in AgentGate (reading text, rendering pages, making scans) holds this one lock,
and closes its documents before releasing it. Otherwise the garbage collector could free
pdfium objects later from whichever thread happens to be running.
"""
import threading

PDFIUM_LOCK = threading.RLock()
