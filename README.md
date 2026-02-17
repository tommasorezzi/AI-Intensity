## Prerequisites

- Python 3.10+ installed and available on PATH.
- Windows 10/11 is fully supported (one-click `run.bat`).
- macOS/Linux are supported via the Python CLI (use a virtual environment and `pip`).

## Setup

1. Create and activate a virtual environment (optional but recommended).
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Edit `config.ini` in the project root. Important sections:

- `[General]`
  - `companies_csv_path`: path to the CSV with a `Ticker` column (e.g., `./companies.csv`).
  - `output_dir`: directory where the Excel report will be written (e.g., `./output`).
  - `keywords_file`: optional path to a custom keyword list (`.txt`, one per line). Leave blank to use the 36 built-in defaults.
  - `report_filename`: name of the output Excel file (e.g., `AI_Intensity_Report.xlsx`).

- `[EDGAR]`
  - `email`: your email address (required by the SEC for the User-Agent).
  - `download_dir`: where SEC filings are stored locally (e.g., `./sec-edgar-filings`).
  - `filing_type`: comma-separated filing types to download (e.g., `10-K, 20-F, 40-F`). Useful for including both domestic US companies (10-K) and foreign filers (20-F, 40-F).
  - `cleanup_filings`: `auto` to delete downloaded filings after processing each ticker (saves disk space), `manual` to keep them for later inspection.
  - `start_year`, `end_year`: year range for filings.

- `[Performance]`
  - `processes`: number of worker processes (0 uses all available cores).

- `[Logging]`
  - `level`: verbosity level (`INFO`, `DEBUG`, `WARNING`, `ERROR`). `DEBUG` enables diagnostic logs in worker processes as well.

The `companies.csv` file must contain at least a `Ticker` column. Values can be either EDGAR tickers (e.g., `AAPL`, `MSFT`) or Refinitiv RICs (e.g., `AAPL.OQ`, `MSFT.O`). RICs will be used in the report; they are automatically mapped to EDGAR tickers for SEC downloads. Slash-style share-class tickers (e.g., `BF/B` → `BF-B`) are also handled.

## Usage

Run the main entry point from the project directory:

```bash
python main.py
```

This will:

- Load configuration and tickers.
- Download SEC filings for each type specified in `filing_type` (with automatic retry and exponential backoff on HTTP 429/503 errors).
- Filter binary content (images, PDFs, XBRL, etc.) from SEC files to reduce NLP processing time.
- Compute the AI Intensity scores per filing using the keyword matcher.
- Generate an Excel report in the `output_dir`.

## One-click run (Windows only)

For a terminal-free experience, double-click `run.bat` in the project root:

What it does:

- Creates the local virtual environment `.venv` if missing
- Upgrades `pip` and installs `requirements.txt`
- Runs `main.py` with unbuffered output to show progress and logs

Notes:

- Close the Excel report before re-running, otherwise the file can be locked by Excel. If the file is locked, the app automatically saves to a timestamped copy (e.g., `AI_Intensity_Report_YYYYMMDD_HHMMSS.xlsx`).
- `config.ini` controls paths, date range, and performance settings.
- You can reduce parallelism by setting `[Performance] processes = 1` if you experience SEC rate limiting.

## Logging

- The verbosity is controlled by `config.ini` → `[Logging].level`.
- Default is `INFO` (clean output for end users).
- Set to `DEBUG` to print diagnostic details (sample DataFrame head, workbook inspection) and worker-level debug (SEC retry attempts, file cleanup, etc.).

## Troubleshooting

- Excel report locked by Excel:
  - Close the workbook before re-running. If locked, the app saves a timestamped copy (e.g., `AI_Intensity_Report_YYYYMMDD_HHMMSS.xlsx`).
- SEC throttling (rate limiting):
  - The program automatically retries up to 3 times with exponential backoff (5s, 10s, 20s) on transient HTTP errors (429, 502, 503, 504).
  - Reduce parallelism: set `[Performance] processes = 1`.
  - Narrow date range: `[EDGAR] start_year = 2024`, `end_year = 2024` for a quick test.
  - Test with few tickers (e.g., only `AAPL` in `companies.csv`).
- Custom keywords:
  - Provide a `.txt` file path in `[General].keywords_file` (one keyword per line; lines starting with `#` are ignored).

## Output

The Excel report contains three sheets:

- "Riepilogo Generale": sums of numeric metrics by `Ticker` (excluding `Year`), sorted by `AI_Intensity_Score_Total`. Includes a `Filing_Count` column showing how many filings contribute to each ticker's totals.
- "Dati Dettagliati": detailed rows for each filing, sorted by `Ticker` and `Year`. Contains both the `Ticker` column (with the RIC or original ticker provided by the user) and the `EDGAR_Ticker` column (the ticker used internally for SEC downloads).
- "Analisi Trend": pivot table of `AI_Intensity_Score` with `Ticker` as rows and `Year` as columns.

Columns auto-fit, headers are bold, and autofilters are enabled to improve readability.
