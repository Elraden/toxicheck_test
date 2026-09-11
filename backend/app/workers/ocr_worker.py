"""
Future OCR/ML worker entrypoint.

The API already talks to OCR through OcrService. When the model is ready, this
worker can consume queued OCR jobs, write recognized text to ocr_jobs, and ask
the analysis module to build a verdict.
"""


def run() -> None:
    raise NotImplementedError("OCR worker queue is not connected yet.")
