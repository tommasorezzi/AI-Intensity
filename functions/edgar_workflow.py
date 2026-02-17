"""EDGAR download and processing workflow.

"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional
import configparser
import re
import os
import shutil
import time
import multiprocessing as mp
from functools import partial

import pandas as pd
from tqdm import tqdm

from . import core_nlp

try:
    # sec-edgar-downloader >= 5.x
    from sec_edgar_downloader import Downloader  # type: ignore
except Exception:  # pragma: no cover - import-time fallback
    Downloader = None  # type: ignore

__all__ = [
    "process_company",
    "sequential_test_run",
    "run_workflow",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(maybe_path: str) -> Path:
    p = Path(maybe_path)
    if not p.is_absolute():
        p = (_project_root() / p).resolve()
    return p


def _is_debug() -> bool:
    """Return True if debug output should be printed for worker processes.

    Controlled via environment variable AI_INTENSITY_DEBUG, which is set
    in run_workflow() based on config["Logging"].level.
    """
    try:
        v = os.getenv("AI_INTENSITY_DEBUG", "").strip().lower()
        return v in ("1", "true", "yes", "on", "debug")
    except Exception:
        return False


# ── Retry settings for SEC rate-limiting / 503 errors ────────────────
_MAX_RETRIES = 3
_RETRY_BASE_WAIT = 5  # seconds; actual wait = base * 2^attempt (5, 10, 20)


def _download_with_retry(dl, filing_type: str, ticker: str,
                         after: str, before: str) -> None:
    """Download filings with exponential-backoff retry on transient errors.

    Handles HTTP 503 (Service Unavailable) and 429 (Too Many Requests) from
    the SEC server, which are common under parallel load.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            try:
                dl.get(filing_type, ticker, after=after, before=before)
            except TypeError:
                dl.get(filing_type, ticker)
            return  # success
        except Exception as e:
            err_str = str(e)
            is_transient = any(code in err_str for code in ("503", "429", "502", "504"))
            if is_transient and attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_WAIT * (2 ** attempt)
                if _is_debug():
                    print(f"[DEBUG] {ticker}/{filing_type}: HTTP error, retry {attempt + 1}/{_MAX_RETRIES} in {wait}s")
                time.sleep(wait)
                last_exc = e
            else:
                # Non-transient error or out of retries — let caller handle it
                if attempt > 0 and _is_debug():
                    print(f"[DEBUG] {ticker}/{filing_type}: giving up after {attempt + 1} attempts")
                last_exc = e
                break
    # If we exhausted retries, just log; process_company will still try to
    # use any previously-cached files on disk.
    if last_exc and _is_debug():
        print(f"[DEBUG] {ticker}/{filing_type}: download failed: {last_exc}")


def _make_downloader(download_dir: Path, email: str):
    """Create a Downloader instance accommodating API variations.

    Different versions of sec_edgar_downloader use different __init__ signatures.
    We try several safe permutations, always ensuring an email is provided when
    required by the library.
    """
    if Downloader is None:
        raise RuntimeError(
            "sec_edgar_downloader is not available. Ensure it is installed via requirements.txt."
        )

    if not email:
        raise ValueError("[EDGAR].email is required in config.ini for SEC access")

    # Try common signatures in order of likelihood
    attempts = []
    try:
        # Newer versions (kwargs with explicit email_address)
        return Downloader(download_folder=str(download_dir), email_address=email)
    except TypeError as e:
        attempts.append(str(e))
    try:
        # Positional: (download_folder, email_address)
        return Downloader(str(download_dir), email)
    except TypeError as e:
        attempts.append(str(e))
    try:
        # Alt kw ordering
        return Downloader(email_address=email, download_folder=str(download_dir))
    except TypeError as e:
        attempts.append(str(e))
    try:
        # Some versions accept user_agent
        return Downloader(download_folder=str(download_dir), user_agent=email)
    except TypeError as e:
        attempts.append(str(e))
    try:
        # Last resort (not ideal; may fail due to missing email)
        return Downloader(str(download_dir))
    except Exception as e:  # pragma: no cover
        attempts.append(str(e))
        raise RuntimeError(
            "Failed to instantiate Downloader with available signatures. Errors: "
            + " | ".join(attempts)
        )


def _date_bounds_from_years(start_year: int, end_year: int) -> Tuple[str, str]:
    after = f"{start_year}-01-01"
    before = f"{end_year}-12-31"
    return after, before


def _extract_year_from_text(text: str) -> Optional[int]:
    """Try multiple strategies to extract filing year from submission text."""
    # Common headers in full-submission.txt
    patterns = [
        r"FILED\s+AS\s+OF\s+DATE:\s*(\d{8})",
        r"CONFORMED\s+PERIOD\s+OF\s+REPORT:\s*(\d{8})",
        r"FILING\s+DATE:\s*(\d{8})",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1)[:4])
            except Exception:
                pass
    return None


def _extract_year_from_path(path: Path) -> Optional[int]:
    """Heuristic fallback: look for a 4-digit year in parent folder names."""
    s = str(path)
    m = re.search(r"(20\d{2})", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


# Regex for parsing DOCUMENT blocks in SEC full-submission files (compiled once)
_RE_DOCUMENT = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_RE_DOC_TYPE = re.compile(r"<TYPE>\s*([^\n\r<]+)", re.IGNORECASE)

# DOCUMENT types that are binary/non-textual and should be skipped for NLP
_BINARY_DOC_TYPES = frozenset({
    "GRAPHIC", "ZIP", "EXCEL", "PDF", "XBRL", "JSON",
    "COVER", "JPG", "JPEG", "PNG", "GIF", "BMP", "TIFF",
})


def _extract_text_content(raw: str) -> str:
    """Extract only textual DOCUMENT sections from a SEC full-submission file.

    SEC full-submission.txt files bundle many ``<DOCUMENT>`` blocks: the main
    filing (10-K, 10-Q, etc.), plus binary attachments (graphics, PDFs, XBRL
    data).  Binary blocks often account for 60-80 % of the file size yet are
    useless for keyword matching.  This function keeps only textual blocks,
    dramatically reducing the input to the NLP pipeline.

    The SEC header (before the first ``<DOCUMENT>``) is always preserved
    because it contains metadata used for year extraction.
    """
    # Fast path: if the file has no DOCUMENT structure, return as-is
    if "<DOCUMENT>" not in raw and "<document>" not in raw:
        return raw

    # Preserve everything before the first <DOCUMENT> (SEC header, etc.)
    first_doc_pos = re.search(r"<DOCUMENT>", raw, re.IGNORECASE)
    header = raw[: first_doc_pos.start()] if first_doc_pos else ""

    text_parts = [header]
    for doc_match in _RE_DOCUMENT.finditer(raw):
        doc_body = doc_match.group(1)
        type_match = _RE_DOC_TYPE.search(doc_body[:200])  # TYPE is near the top
        if type_match:
            doc_type = type_match.group(1).strip().upper()
            # Skip if the type itself is binary or contains a binary keyword
            if doc_type in _BINARY_DOC_TYPES:
                continue
            if any(bt in doc_type for bt in _BINARY_DOC_TYPES):
                continue
        text_parts.append(doc_body)

    return "\n".join(text_parts)


def process_company(ticker: str, config: configparser.ConfigParser, keyword_index: Dict[str, List[List[str]]]) -> List[Dict[str, int]]:
    """Download and process all filings for a single ticker.

    Returns a list of result dictionaries, each corresponding to one filing with
    keys: 'Ticker', 'Year', 'AI_Intensity_Score', and per-keyword counts using
    normalized keyword phrases as keys.
    """

    edgar = config["EDGAR"]
    email = edgar.get("email", "")
    # Support comma-separated filing types (e.g. "10-K, 20-F, 40-F")
    filing_types_raw = edgar.get("filing_type", "10-K")
    filing_types = [ft.strip() for ft in filing_types_raw.split(",") if ft.strip()]
    download_dir = _resolve_path(edgar.get("download_dir", "./sec-edgar-filings"))

    try:
        start_year = int(edgar.get("start_year", "2020"))
        end_year = int(edgar.get("end_year", "2024"))
    except ValueError:
        raise ValueError("start_year and end_year in config[EDGAR] must be integers")

    download_dir.mkdir(parents=True, exist_ok=True)
    dl = _make_downloader(download_dir, email)

    after, before = _date_bounds_from_years(start_year, end_year)

    # Download and locate submissions for each filing type
    submissions: List[Path] = []
    candidate_roots: List[Path] = []
    candidate_filenames = ["full-submission.txt", "submission.txt"]

    for filing_type in filing_types:
        _download_with_retry(dl, filing_type, ticker, after, before)

        # Locate full-submission files for this ticker across common layouts
        roots = [
            download_dir / filing_type / ticker,
            download_dir / ticker / filing_type,
            download_dir / "filings" / ticker / filing_type,
            download_dir / "filings" / filing_type / ticker,
        ]
        candidate_roots.extend(roots)
        for root in roots:
            if root.exists():
                for fname in candidate_filenames:
                    submissions.extend(root.rglob(fname))

    # Fallback: scan entire download_dir for this ticker
    if not submissions:
        for fname in candidate_filenames:
            for p in download_dir.rglob(fname):
                sp = str(p).lower()
                if ticker.lower() in sp:
                    submissions.append(p)

    # Deduplicate (same file may appear via multiple search paths)
    submissions = list(dict.fromkeys(submissions))

    # Debug logging for discovered submissions
    try:
        if submissions:
            if _is_debug():
                print(f"[DEBUG] {ticker}: found {len(submissions)} submission file(s) in {download_dir}")
        else:
            print(
                f"[WARN] {ticker}: no submission files found in {download_dir} for filing types '{filing_types_raw}'. "
                f"Check date range and network/SEC access."
            )
    except Exception:
        pass

    # Build a set of keyword first-tokens for the early-exit check
    _first_tokens = set(keyword_index.keys())

    results: List[Dict[str, int]] = []
    for sub in submissions:
        try:
            text = sub.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        filtered_text = _extract_text_content(text)

        # Early-exit: skip expensive tokenization if no keyword first-token
        # appears anywhere in the lowered text
        lowered = filtered_text.lower()
        if not any(ft in lowered for ft in _first_tokens):
            total, per_kw = 0, {}
        else:
            tokens = core_nlp.normalize_and_tokenize(filtered_text)
            total, per_kw = core_nlp.count_ai_intensity(tokens, keyword_index)

        year = _extract_year_from_text(text)
        if year is None:
            year = _extract_year_from_path(sub) or 0

        row: Dict[str, int] = {
            "Ticker": ticker,
            "Year": year,
            "AI_Intensity_Score": total,
        }
        # Merge per-keyword counts (keys are normalized phrases)
        row.update(per_kw)
        results.append(row)

    # Cleanup: delete the ticker's filing folder(s) if requested
    cleanup = edgar.get("cleanup_filings", "manual").strip().lower()
    if cleanup == "auto" and submissions:
        freed = 0
        for root in candidate_roots:
            if root.exists():
                try:
                    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
                    shutil.rmtree(root, ignore_errors=True)
                    freed += size
                except Exception:
                    pass
        if freed and _is_debug():
            print(f"[DEBUG] {ticker}: cleaned up {freed / 1_048_576:.1f} MB")

    return results


def sequential_test_run(tickers: Iterable[str], config: configparser.ConfigParser, keyword_index: Dict[str, List[List[str]]]) -> List[Dict[str, int]]:
    """Temporary sequential runner to validate process_company logic.

    Returns the flattened list of result dictionaries across all tickers.
    """
    all_results: List[Dict[str, int]] = []
    for t in tickers:
        print(f"[seq] Processing {t} ...")
        try:
            res = process_company(t, config, keyword_index)
            print(f"[seq] {t}: {len(res)} filings processed")
            all_results.extend(res)
        except Exception as e:
            print(f"[seq] {t}: ERROR {e}")
    return all_results


def _process_company_catch(
    ticker: str,
    edgar_conf: Dict[str, str],
    keyword_index: Dict[str, List[List[str]]],
) -> List[Dict[str, int]]:
    """Wrapper for parallel execution that catches exceptions per ticker.

    Returns an empty list on error to allow the overall workflow to continue.
    """
    try:
        # Reconstruct a minimal ConfigParser for process_company
        cfg = configparser.ConfigParser()
        cfg["EDGAR"] = edgar_conf
        return process_company(ticker, cfg, keyword_index)
    except Exception as e:
        print(f"[parallel] {ticker}: ERROR {e}")
        return []


def run_workflow(
    tickers: Iterable[str],
    config: configparser.ConfigParser,
) -> "pd.DataFrame":
    """Run the full EDGAR workflow in parallel and return a DataFrame.

    """

    # Prepare keywords once
    general = config["General"]
    kw_file = general.get("keywords_file", "").strip()
    if kw_file:
        kw_path = _resolve_path(kw_file)
        keywords = core_nlp.load_keywords(str(kw_path))
    else:
        keywords = core_nlp.load_keywords()
    keyword_index = core_nlp.prepare_keywords(keywords)

    # Determine number of processes (0 means use all cores)
    try:
        processes_cfg = int(config.get("Performance", "processes", fallback="0"))
    except ValueError:
        processes_cfg = 0
    processes = None if processes_cfg == 0 else max(1, processes_cfg)

    tickers_list = [str(t).strip().upper() for t in tickers if str(t).strip()]
    if not tickers_list:
        return pd.DataFrame()

    # Build a simple, serializable EDGAR config for workers
    edgar = config["EDGAR"]
    edgar_conf: Dict[str, str] = {
        "email": edgar.get("email", ""),
        "filing_type": edgar.get("filing_type", "10-K"),
        "download_dir": edgar.get("download_dir", "./sec-edgar-filings"),
        "start_year": edgar.get("start_year", "2020"),
        "end_year": edgar.get("end_year", "2024"),
        "cleanup_filings": edgar.get("cleanup_filings", "manual"),
    }

    # Parallel execution
    # Use 'spawn' context explicitly for Windows compatibility
    # Set debug environment variable for child processes based on Logging.level
    try:
        if config.get("Logging", "level", fallback="INFO").upper() == "DEBUG":
            os.environ["AI_INTENSITY_DEBUG"] = "1"
        else:
            os.environ.pop("AI_INTENSITY_DEBUG", None)
    except Exception:
        pass

    ctx = mp.get_context("spawn")
    partial_func = partial(_process_company_catch, edgar_conf=edgar_conf, keyword_index=keyword_index)

    results_nested: List[List[Dict[str, int]]] = []
    with ctx.Pool(processes=processes) as pool:
        iterator = pool.imap_unordered(partial_func, tickers_list)
        for res in tqdm(iterator, total=len(tickers_list), desc="Processing companies"):
            results_nested.append(res)

    # Flatten and build DataFrame
    flat_results: List[Dict[str, int]] = [row for sub in results_nested for row in sub]
    df = pd.DataFrame(flat_results)
    return df
