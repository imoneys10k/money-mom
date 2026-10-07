"""ULID-style identifiers: 48-bit millisecond time + 80 random bits, Crockford base32."""

from __future__ import annotations

import os
import time
from typing import Callable

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_BITS = 80


def _encode(n: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(chars))


class IdGenerator:
    """Monotonic within one process: ids never sort backwards, even if the clock does."""

    def __init__(
        self,
        clock_ms: Callable[[], int] | None = None,
        randbytes: Callable[[int], bytes] | None = None,
    ) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._randbytes = randbytes or os.urandom
        self._last_ms = -1
        self._last_rand = 0

    def new(self) -> str:
        ms = self._clock_ms()
        if ms <= self._last_ms:
            ms = self._last_ms
            self._last_rand += 1
            if self._last_rand >= 1 << _RANDOM_BITS:
                raise OverflowError("id counter overflow within one millisecond")
        else:
            self._last_ms = ms
            self._last_rand = int.from_bytes(self._randbytes(10), "big")
        return _encode(ms, 10) + _encode(self._last_rand, 16)
