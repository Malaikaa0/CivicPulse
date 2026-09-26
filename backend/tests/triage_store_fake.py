"""An in-memory TriageStore for tests, with the same semantics the Redis one gives."""


class FakeStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        # Everything the code under test asked of the store, for "no PII anywhere" assertions.
        self.seen_keys: list[str] = []
        self.seen_values: list[str] = []
        self.fail_get = False
        self.fail_set = False
        self.fail_incr = False
        self.fail_lists = False

    def get(self, key: str) -> str | None:
        self.seen_keys.append(key)
        if self.fail_get:
            raise ConnectionError("redis down")
        return self.values.get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.seen_keys.append(key)
        self.seen_values.append(value)
        if self.fail_set:
            raise ConnectionError("redis down")
        self.values[key] = value
        self.ttls[key] = ttl_seconds

    def incr(self, key: str) -> int:
        self.seen_keys.append(key)
        if self.fail_incr:
            raise ConnectionError("redis down")
        count = int(self.values.get(key, "0")) + 1
        self.values[key] = str(count)
        return count

    def push_capped(self, key: str, value: str, max_length: int) -> None:
        self.seen_keys.append(key)
        self.seen_values.append(value)
        if self.fail_lists:
            raise ConnectionError("redis down")
        self.lists[key] = [value, *self.lists.get(key, [])][:max_length]

    def list_range(self, key: str, start: int, stop: int) -> list[str]:
        self.seen_keys.append(key)
        if self.fail_lists:
            raise ConnectionError("redis down")
        return self.lists.get(key, [])[start : stop + 1]

    def cache_entries(self) -> dict[str, str]:
        return {k: v for k, v in self.values.items() if k.startswith("triage:v1:")}
