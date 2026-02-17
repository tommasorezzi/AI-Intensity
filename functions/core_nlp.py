"""Core NLP engine for keyword loading, normalization, and counting.

Design notes:
- Normalization targets robustness: lowercase, strip HTML tags, standardize variants
  of "A.I." to "ai", replace hyphens with spaces, remove punctuation, collapse
  whitespace, and split into tokens.
- Matching is non-overlapping. If a multi-word keyword matches, its constituent
  tokens are marked as used so shorter keywords cannot overlap that span.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import re

__all__ = [
    "load_keywords",
    "normalize_and_tokenize",
    "prepare_keywords",
    "count_ai_intensity",
]

# ── Pre-compiled regex patterns (compiled once at module load) ───────────
_RE_HTML_TAGS = re.compile(r"<[^>]+>")
_RE_AI_DOTS = re.compile(r"\ba\.\s*i\.?\b")
_RE_DASHES = re.compile(r"[\-\u2012-\u2015]")
_RE_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_RE_MULTISPACE = re.compile(r"\s+")


def load_keywords(filepath: Optional[str] = None) -> List[str]:
    """Load the list of AI-related keywords.

    If ``filepath`` is provided, a UTF-8 text file is read with one keyword per
    line (empty lines and lines starting with '#' are ignored). Otherwise, a
    default list of 25 common AI-related keywords/phrases is returned.

    Parameters
    ----------
    filepath: Optional[str]
        Optional path to a text file containing one keyword per line.

    Returns
    -------
    List[str]
        The list of keywords.
    """

    default_keywords: List[str] = [
        # ── Core AI / ML ──
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "data science",
        "cognitive computing",
        # ── Architectures & paradigms ──
        "neural network",
        "neural networks",
        "transformer",
        "transformers",
        "large language model",
        "large language models",
        "foundation model",
        "foundation models",
        "generative ai",
        # ── Learning approaches ──
        "supervised learning",
        "unsupervised learning",
        "semi-supervised learning",
        "reinforcement learning",
        "model training",
        "model inference",
        "training data",
        # ── NLP & language ──
        "natural language processing",
        "natural language understanding",
        "chatbot",
        "chatbots",
        "speech recognition",
        "voice recognition",
        # ── Vision ──
        "computer vision",
        "machine vision",
        "image recognition",
        "facial recognition",
        # ── Applications ──
        "predictive analytics",
        "predictive modeling",
        "anomaly detection",
        "recommendation system",
        "recommendation systems",
        "robotic process automation",
    ]

    if not filepath:
        return default_keywords

    try:
        seen = set()
        loaded: List[str] = []
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                kw = line.strip()
                if not kw or kw.startswith("#"):
                    continue
                # Preserve order but avoid duplicates
                if kw not in seen:
                    seen.add(kw)
                    loaded.append(kw)
        # If file was empty or had no valid lines, fall back to default
        return loaded if loaded else default_keywords
    except OSError:
        # If reading fails, fall back to default for robustness
        return default_keywords


def normalize_and_tokenize(text: str) -> List[str]:
    """Normalize raw text and split into tokens.

    Parameters
    ----------
    text: str
        Raw input text

    Returns
    -------
    List[str]
        Normalized tokens
    """

    if not text:
        return []

    s = text.lower()
    s = _RE_HTML_TAGS.sub(" ", s)
    s = _RE_AI_DOTS.sub("ai", s)
    s = _RE_DASHES.sub(" ", s)
    s = _RE_NON_ALNUM.sub(" ", s)
    s = _RE_MULTISPACE.sub(" ", s).strip()
    return s.split()


def prepare_keywords(keywords: List[str]) -> Dict[str, List[List[str]]]:
    """Prepare and index tokenized keywords for efficient matching.

    Each keyword is tokenized using the exact same logic as
    :func:`normalize_and_tokenize`. The resulting index maps the first token to a
    list of tokenized keywords that start with that token, sorted by length
    descending (so the longest candidate is tried first).

    Parameters
    ----------
    keywords: List[str]
        Raw keyword phrases.

    Returns
    -------
    Dict[str, List[List[str]]]
        Mapping from first token -> list of tokenized keywords (longest-first)
    """

    index: Dict[str, List[List[str]]] = {}
    for kw in keywords:
        tokens = normalize_and_tokenize(kw)
        if not tokens:
            continue
        first = tokens[0]
        index.setdefault(first, []).append(tokens)

    # Sort candidate lists longest-first, then lexicographically for stability
    for first, lst in index.items():
        lst.sort(key=lambda t: (-len(t), " ".join(t)))
    return index


def count_ai_intensity(tokens: List[str], keyword_index: Dict[str, List[List[str]]]) -> Tuple[int, Dict[str, int]]:
    """Count non-overlapping keyword matches in a token stream.

    The algorithm scans tokens left-to-right. Whenever the current token is a
    possible start of any keyword (per ``keyword_index``), we attempt matches in
    longest-first order. On a match, we increment the total score, update per-
    keyword counts, mark the matched token span as used so no overlaps can occur,
    advance the pointer by the length of the match, and continue.

    Parameters
    ----------
    tokens: List[str]
        Normalized tokens of the source text.
    keyword_index: Dict[str, List[List[str]]]
        Prepared keyword index from :func:`prepare_keywords`.

    Returns
    -------
    Tuple[int, Dict[str, int]]
        A pair of (total_score, per_keyword_counts). The per-keyword dictionary
        uses the normalized phrase (space-joined tokens) as the key.
    """

    n = len(tokens)
    if n == 0:
        return 0, {}

    used = [False] * n
    total_score = 0
    per_keyword: Dict[str, int] = {}

    i = 0
    while i < n:
        if used[i]:
            i += 1
            continue

        start = tokens[i]
        candidates = keyword_index.get(start)
        matched = False

        if candidates:
            for cand in candidates:
                L = len(cand)
                if i + L <= n and tokens[i : i + L] == cand and all(not used[j] for j in range(i, i + L)):
                    total_score += 1
                    phrase = " ".join(cand)
                    per_keyword[phrase] = per_keyword.get(phrase, 0) + 1
                    for j in range(i, i + L):
                        used[j] = True
                    i += L
                    matched = True
                    break

        if not matched:
            i += 1

    return total_score, per_keyword
