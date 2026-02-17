## Prerequisiti

- Python 3.10+ installato e aggiunto al PATH.
- Windows 10/11 è pienamente supportato (esecuzione con un clic su `run.bat`).
- macOS/Linux sono supportati tramite la CLI di Python (usa un ambiente virtuale e `pip`).

## Installazione

1. Crea e attiva un ambiente virtuale (opzionale ma consigliato).
2. Installa le dipendenze:

   ```bash
   pip install -r requirements.txt
   ```

## Configurazione

Modifica `config.ini` nella radice del progetto. Sezioni importanti:

- `[General]`
  - `companies_csv_path`: percorso del CSV con una colonna `Ticker` (ad es., `./companies.csv`).
  - `output_dir`: directory in cui verrà scritto il report Excel (ad es., `./output`).
  - `keywords_file`: percorso facoltativo a un elenco personalizzato di parole chiave (`.txt`, una per riga). Lascia vuoto per usare i 36 valori predefiniti.
  - `report_filename`: nome del file Excel di output (ad es., `AI_Intensity_Report.xlsx`).

- `[EDGAR]`
  - `email`: il tuo indirizzo email (richiesto dalla SEC per l'User-Agent).
  - `download_dir`: dove vengono archiviati localmente i documenti SEC (ad es., `./sec-edgar-filings`).
  - `filing_type`: tipi di documento da scaricare, separati da virgola (ad es., `10-K, 20-F, 40-F`). Utile per includere sia aziende domestiche USA (10-K) che estere (20-F, 40-F).
  - `cleanup_filings`: `auto` per eliminare i file scaricati dopo l'elaborazione di ciascun ticker (risparmia spazio disco), `manual` per conservarli per ispezione.
  - `start_year`, `end_year`: intervallo di anni per i documenti.

- `[Performance]`
  - `processes`: numero di processi 'worker' (0 usa tutti i core disponibili).

- `[Logging]`
  - `level`: livello di verbosità (`INFO`, `DEBUG`, `WARNING`, `ERROR`). `DEBUG` abilita log diagnostici anche nei processi worker.

Il file `companies.csv` deve contenere almeno una colonna `Ticker`. I valori possono essere sia ticker EDGAR (ad es., `AAPL`, `MSFT`) sia RIC di Refinitiv (ad es., `AAPL.OQ`, `MSFT.O`). I RIC verranno mantenuti nel report, mentre il programma li mappa automaticamente ai ticker EDGAR per scaricare i documenti dalla SEC. Vengono gestiti anche i ticker con barre (ad es., `BF/B` → `BF-B`).

## Utilizzo

Esegui il punto di ingresso principale dalla directory del progetto:

```bash
python main.py
```

Questo farà:

- Caricare la configurazione e i ticker.
- Scaricare i documenti SEC per ciascun tipo specificato in `filing_type` (con retry automatico e backoff esponenziale in caso di errori HTTP 429/503).
- Filtrare il contenuto binario (immagini, PDF, XBRL, ecc.) dai file SEC per ridurre il tempo di elaborazione NLP.
- Calcolare i punteggi di AI Intensity per ciascun documento utilizzando il motore di corrispondenza delle parole chiave.
- Generare un report Excel in `output_dir`.

## Esecuzione con un clic (solo su Windows)

Per un'esperienza senza terminale, fai doppio clic su `run.bat` nella radice del progetto:

Cosa fa:

- Crea l'ambiente virtuale locale `.venv` se mancante
- Aggiorna `pip` e installa i pacchetti da `requirements.txt`
- Esegue `main.py` con output non bufferizzato per mostrare l'avanzamento e i log

Note:

- Chiudi il report Excel prima di rieseguire, altrimenti il file può essere bloccato da Excel. Se il file è bloccato, l'app salva automaticamente una copia con un 'timestamp' (ad es., `AI_Intensity_Report_YYYYMMDD_HHMMSS.xlsx`).
- `config.ini` controlla i percorsi, l'intervallo di date e le impostazioni delle prestazioni.
- Puoi ridurre il parallelismo impostando `[Performance] processes = 1` se riscontri limitazioni di velocità da parte della SEC.

## Logging

- La verbosità è controllata da `config.ini` → `[Logging].level`.
- Il valore predefinito è `INFO` (output pulito per gli utenti finali).
- Imposta `DEBUG` per stampare dettagli diagnostici (intestazione "head" di un DataFrame di esempio, ispezione del workbook) e log a livello di worker (tentativi di retry SEC, cleanup dei file, ecc.).

## Troubleshooting

- Report Excel bloccato da Excel:
  - Chiudi la cartella di lavoro prima di rieseguire. Se bloccata, l'app salva una copia con timestamp (ad es., `AI_Intensity_Report_YYYYMMDD_HHMMSS.xlsx`).
- Throttling della SEC (rate limiting):
  - Il programma effettua automaticamente fino a 3 tentativi con backoff esponenziale (5s, 10s, 20s) per errori HTTP transitori (429, 502, 503, 504).
  - Riduci i processi in parallelo: imposta `[Performance] processes = 1`.
  - Restringi l'intervallo di date: `[EDGAR] start_year = 2024`, `end_year = 2024` per un test rapido.
  - Prova con pochi ticker (ad es., solo `AAPL` in `companies.csv`).
- Parole chiave personalizzate:
  - Fornisci un percorso di file `.txt` in `[General].keywords_file` (una parola chiave per riga; le righe che iniziano con `#` vengono ignorate).

## Output

Il report Excel contiene tre fogli:

- "Riepilogo Generale": somme delle metriche numeriche per `Ticker` (escludendo `Year`), ordinate per `AI_Intensity_Score_Total`. Include anche la colonna `Filing_Count` con il numero di documenti per ciascun ticker.
- "Dati Dettagliati": righe dettagliate per ciascun documento, ordinate per `Ticker` e `Year`. Contiene sia la colonna `Ticker` (con il RIC o ticker originale fornito dall'utente) sia la colonna `EDGAR_Ticker` (il ticker usato internamente per scaricare dal SEC).
- "Analisi Trend": tabella pivot di `AI_Intensity_Score` con `Ticker` come righe e `Year` come colonne.

Le colonne si adattano automaticamente, le intestazioni sono in grassetto e i filtri automatici sono abilitati per migliorare la leggibilità.