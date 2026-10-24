"""Regression test: concurrent PDF work used to segfault the process (pdfium isn't thread-safe)."""
from concurrent.futures import ThreadPoolExecutor

from agentgate.extraction import evaluate
from agentgate.extraction.reader import page_images, read_text


def test_concurrent_pdf_reading_and_rendering_does_not_crash():
    samples = evaluate.make_samples(6, 0.5)

    def work(sample):
        pdf = sample.pdf()  # renders (and for scans, rasterizes) via pdfium
        for _ in range(3):
            assert read_text(pdf).pages == 1
            assert page_images(pdf)[0][:3] == b"\xff\xd8\xff"
        return True

    with ThreadPoolExecutor(6) as pool:
        assert all(pool.map(work, samples * 2))
