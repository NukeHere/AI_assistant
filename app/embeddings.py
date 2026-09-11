from __future__ import annotations

import hashlib
import math
import re

TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]{3,}")
EMBEDDING_DIMS = 96


def embed_text(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
    vector = [0.0] * dims
    for token in tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dims
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    return normalize(vector)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def normalize(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if not length:
        return vector
    return [value / length for value in vector]


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
