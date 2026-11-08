"""Research-only resource observer. The OCR engine is not modified.

Child CPU: delta of Linux RUSAGE_CHILDREN (process-wide, may include unrelated
children). Child RSS: sampled Tesseract processes (not exact high-water mark).
Python CPU: calling worker thread only, so other FastAPI threads are excluded.
"""
import os
import resource
import threading
import time
from pathlib import Path

import psutil


def process_rss():
    try:
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class OCRResourceMonitor:
    """Measure one embedded OCR operation under the service's OCR lock."""

    def __init__(self, interval=0.05):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._parent = psutil.Process(os.getpid())
        self._before_children = None
        self._before_thread = None
        self._before_process = None
        self.parent_samples = []
        self.tesseract_rss_peak = None
        self.process_tree_rss_peak = None
        self.tesseract_observed = False
        self.samples = 0

    def _sample(self):
        parent_rss = process_rss()
        if parent_rss is not None:
            self.parent_samples.append(parent_rss)
        tess_rss = 0
        found = False
        try:
            children = self._parent.children(recursive=True)
        except (psutil.Error, OSError):
            children = []
        for child in children:
            try:
                if 'tesseract' in child.name().lower():
                    tess_rss += child.memory_info().rss
                    found = True
            except (psutil.Error, OSError):
                continue
        if found:
            self.tesseract_observed = True
            self.tesseract_rss_peak = max(self.tesseract_rss_peak or 0, tess_rss)
        if parent_rss is not None:
            tree_rss = parent_rss + tess_rss
            self.process_tree_rss_peak = max(self.process_tree_rss_peak or 0, tree_rss)
        self.samples += 1

    def _collect(self):
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def start(self):
        self._before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
        self._before_thread = time.thread_time()
        self._before_process = time.process_time()
        self._thread = threading.Thread(target=self._collect, daemon=True)
        self._thread.start()

    def finish(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        child_cpu = max(0, (
            after.ru_utime + after.ru_stime
            - self._before_children.ru_utime - self._before_children.ru_stime
        ) * 1000)
        python_thread_cpu = max(0, (time.thread_time() - self._before_thread) * 1000)
        parent_cpu = max(0, (time.process_time() - self._before_process) * 1000)
        self._sample()
        # RUSAGE_CHILDREN is process-global; do not represent it as a precise
        # isolated Tesseract measurement without a dedicated worker process.
        observed_child_cpu = child_cpu if self.tesseract_observed else None
        return {
            'ocr_python_thread_cpu_ms': python_thread_cpu,
            'tesseract_child_cpu_time_ms': observed_child_cpu,
            'ocr_cpu_time_ms': python_thread_cpu + observed_child_cpu if observed_child_cpu is not None else None,
            'tesseract_child_rss_peak_bytes': self.tesseract_rss_peak,
            'ocr_process_tree_rss_peak_bytes': self.process_tree_rss_peak if self.tesseract_observed else None,
            'shared_process_cpu_delta_ms': parent_cpu,
            'shared_process_rss_peak_bytes': max(self.parent_samples) if self.parent_samples else None,
            'rss_start_bytes': self.parent_samples[0] if self.parent_samples else None,
            'rss_end_bytes': process_rss(),
            'tesseract_process_observed': self.tesseract_observed,
            'resource_sample_count': self.samples,
            'resource_sample_interval_ms': round(self.interval * 1000),
            'measurement_quality': 'sampled_process_tree' if self.tesseract_observed else 'child_not_observed',
            'limitation': 'Child CPU is RUSAGE_CHILDREN delta shared by the API process; possible unrelated child work. RSS peaks are sampled every 50 ms, not exact. Browser CPU/RAM unavailable.',
        }
