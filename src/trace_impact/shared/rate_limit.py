"""Simple per-instance request pacing; account-wide token/day quotas remain provider enforced."""

import time
from threading import Lock


class RequestPacer:
    def __init__(self, requests_per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60 / requests_per_minute if requests_per_minute else 0
        self.clock, self.sleep = clock, sleep
        self.next_request = 0.0
        self.lock = Lock()

    def wait(self) -> None:
        with self.lock:
            delay = max(0.0, self.next_request - self.clock())
            if delay:
                self.sleep(delay)
            self.next_request = self.clock() + self.interval
