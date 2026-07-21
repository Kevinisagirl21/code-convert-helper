import requests


def clamp(value: int, lo: int, hi: int) -> int:
    # keep value within [lo, hi]
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def sum_up_to(n: int) -> int:
    total: int = 0
    for i in range(n):
        total: int = total + i
    return total


class Counter:
    """A simple counter with a running total."""

    def __init__(self, start: int):
        self.value = start
        self.history = [start]

    def increment(self, amount: int) -> None:
        self.value = self.value + amount
        # keep a record of every value we've held
        for h in self.history:
            print(h)

    def report(self) -> int:
        if self.value > 100:
            raise ValueError("counter overflowed")
        return self.value


def fetch_data(url: str) -> str:
    response: str = requests.get(url)
    return response
