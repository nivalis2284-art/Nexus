"""Deterministic lexical candidate retrieval for Chinese and Latin text."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

_HAN = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-z0-9_]+(?:[.+#-][a-z0-9_]+)*")
_STOP = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "的", "了", "是", "在", "和", "与", "也", "就", "都", "而", "及", "或",
})


def terms(text: str) -> Counter[str]:
    lowered = text.lower()
    found: list[str] = []
    for run in _HAN.findall(lowered):
        if len(run) == 1:
            found.append(run)
        else:
            found.extend(run[index:index + 2] for index in range(len(run) - 1))
    found.extend(word for word in _WORD.findall(lowered) if word not in _STOP)
    return Counter(term for term in found if term not in _STOP)


def lexical_score(query: str, document: str) -> float:
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    document_terms = terms(document)
    shared = set(query_terms) & set(document_terms)
    weighted = sum(query_terms[term] * math.log1p(document_terms[term]) for term in shared)
    return weighted / math.sqrt(sum(value * value for value in query_terms.values()) + 1)


def rank(query: str, candidates: Iterable[tuple[str, str]]) -> list[tuple[str, float]]:
    scored = [(identity, lexical_score(query, document)) for identity, document in candidates]
    return sorted((item for item in scored if item[1] > 0), key=lambda item: (-item[1], item[0]))


def tokens_for_budget(value: str, budget: int) -> int:
    """Conservative budget estimate: CJK characters count individually."""
    cjk = sum(1 for char in value if "\u3400" <= char <= "\u9fff")
    return cjk + (len(value) - cjk + 3) // 4
