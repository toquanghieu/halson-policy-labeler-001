"""Verify that every quote the model returns actually appears in the posting.

The model is asked for verbatim quotes, but "asked" is not "guaranteed". Matching
is whitespace- and case-insensitive (postings in EMSCAD often have lost
whitespace between sentences), and the span returned is always cut from the
original posting text, so what the UI highlights is real text, not the model's
version of it.
"""

from __future__ import annotations

import html
import re

from labeler.labeling.schemas import TEXT_FIELDS, Evidence, Posting

MIN_QUOTE_CHARS = 8
# Free-text fields plus salary, which is where §4-style claims sometimes live.
EVIDENCE_FIELDS = (*TEXT_FIELDS, "salary_range")


_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")
_CHAR_MAP = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "\xa0": " "}


def _normalise_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """Lowercase, collapse whitespace, decode HTML entities and unify quote/dash characters.

    Returns the normalised string and, for each of its characters, the start and
    end of the source span it came from (a decoded entity spans the whole `&amp;`).
    EMSCAD keeps entities such as `&amp;`; models quote them decoded.
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    prev_space = True
    i = 0
    while i < len(text):
        chunk, src, step = text[i], i, 1
        if chunk == "&" and (m := _ENTITY.match(text, i)):
            decoded = html.unescape(m.group())
            if decoded != m.group():
                chunk, step = decoded, len(m.group())
        for ch in chunk:
            ch = _CHAR_MAP.get(ch, ch)
            if ch.isspace():
                if prev_space:
                    continue
                ch = " "
                prev_space = True
            else:
                prev_space = False
            out.append(ch.lower())
            starts.append(src)
            ends.append(src + step)
        i += step
    while out and out[-1] == " ":
        out.pop()
        starts.pop()
        ends.pop()
    return "".join(out), starts, ends


_TRIM = re.compile(r"^[\s\"'.,;:…-]+|[\s\"'.,;:…-]+$")


def locate(posting: Posting, quote: str, section: str) -> Evidence | None:
    q = _TRIM.sub("", quote or "")
    if len(q) < MIN_QUOTE_CHARS:
        return None
    nq = _normalise_with_map(q)[0]
    for field in EVIDENCE_FIELDS:
        source = getattr(posting, field) or ""
        ns, starts, ends = _normalise_with_map(source)
        pos = ns.find(nq)
        if pos >= 0:
            start_n, end_n = pos, pos + len(nq)
        else:
            # Common EMSCAD artefact: sentences glued together without a space
            # ("place.We have"). Retry with spaces removed on both sides.
            span = _find_ignoring_spaces(ns, nq)
            if span is None:
                continue
            start_n, end_n = span
        start, end = starts[start_n], ends[end_n - 1]
        return Evidence(section=section, quote=source[start:end], field=field, start=start, end=end)
    return None


def _find_ignoring_spaces(haystack: str, needle: str) -> tuple[int, int] | None:
    compact_needle = needle.replace(" ", "")
    if not compact_needle:
        return None
    kept = [i for i, c in enumerate(haystack) if c != " "]
    compact_hay = "".join(haystack[i] for i in kept)
    p = compact_hay.find(compact_needle)
    if p < 0:
        return None
    return kept[p], kept[p + len(compact_needle) - 1] + 1


def verify(posting: Posting, section: str, quotes: list[str]) -> tuple[list[Evidence], list[str]]:
    """Split quotes into verified evidence and rejected strings."""
    ok: list[Evidence] = []
    rejected: list[str] = []
    for q in quotes:
        ev = locate(posting, q, section)
        if ev is None:
            rejected.append(q)
        elif not any(e.field == ev.field and e.start == ev.start for e in ok):
            ok.append(ev)
    return ok, rejected
