"""Conservative English boundary rules and reconciliation of cumulative ASR text."""

import re
from difflib import SequenceMatcher

_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"}


def boundary_strength(word: str) -> int:
    token = word.rstrip('\"\u201d\u2019\')]}')
    if token.endswith(("!", "?", ";")):
        return 2
    if token.endswith("."):
        stem = token[:-1].lower()
        if stem in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", stem):
            return 0
        if re.fullmatch(r"\d+(?:\.\d+)*", stem):
            return 0
        return 2
    return 1 if token.endswith(",") else 0


def stable_prefix(previous: list[str], current: list[str]) -> int:
    for index, (old, new) in enumerate(zip(previous, current)):
        if old != new:
            return index
    return min(len(previous), len(current))


def choose_boundary(words: list[str], target: int, grace: int = 10,
                    stable: int = 0, force: bool = False) -> int | None:
    """Return words to commit. 0 target disables early splitting entirely.

    Prefer stable punctuation near the soft target; commas require substantial
    clauses on both sides. Length/time fallback may still split an unfinished clause.
    """
    if target <= 0 or not words or (len(words) < target and not force):
        return None
    limit = min(len(words), target + grace)
    safe = limit if force else min(limit, stable)
    minimum = max(3, target // 2)
    for strength in (2, 1):
        candidates = [i for i in range(minimum, safe + 1)
                      if boundary_strength(words[i - 1]) == strength
                      and (strength == 2 or len(words) - i >= 3)]
        if candidates:
            return min(candidates, key=lambda i: (abs(i - target), -i))
    if force or len(words) >= target + grace:
        cut = min(target, len(words))
        # Avoid ending immediately after a negation when there is a nearby alternative.
        while cut > 1 and words[cut - 1].lower().strip(",.;") in {"not", "no", "never"}:
            cut -= 1
        return cut
    return None


def remap_cuts(previous: list[str], current: list[str], cuts: list[int]) -> list[int]:
    """Move committed boundaries through ASR insertions/deletions/replacements.

    Ranges always partition the latest text, including when a range becomes empty.
    """
    opcodes = SequenceMatcher(None, previous, current, autojunk=False).get_opcodes()
    result = []
    for cut in cuts:
        mapped = min(cut, len(current))
        for tag, a, b, c, d in opcodes:
            if a <= cut <= b:
                if tag == "equal":
                    mapped = c + cut - a
                elif a == b:
                    mapped = d
                else:
                    mapped = c + round((cut - a) * (d - c) / (b - a))
                # An insertion exactly at a boundary belongs to the following range.
                break
        result.append(max(result[-1] if result else 0, min(mapped, len(current))))
    return result
