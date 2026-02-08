# Scrapyd Log Parser (Beta)

This project aims to parse Scrapyd logs to generate detailed statistics, providing insights that Scrapyd does not natively offer.

## Current status: Beta 0.1.0

### Features

- **Project Structure**: Standard Python project structure.
- **High Performance**: Uses `ProcessPoolExecutor` to parse logs in parallel, utilizing all CPU cores.
- **Log Discovery**: Recursively finds all log files in a specified directory.
- **CLI**: Basic command-line interface to point to a log directory.

### Usage

1. **Installation** (dev mode):

   ```bash
   pip install -e .
   ```

2. **Run the parser**:

   ````bash
   ```bash
   scrapyd-logparser /path/to/scrapyd/logs --json-dir /path/to/json/output
   ````

   Or directly via python:

   ```bash
   python3 logparser/cli.py /path/to/scrapyd/logs [options]
   ```

   **Options:**
   - `--json-dir`: Directory to store parsed JSON files. Follows `project/spider/job.json` structure.
   - `--output`: Output summary JSON file path.
   - `--force`: Force re-parsing of all files.

3. **Code Formatting (Dev)**:
   This project uses `black` and `isort` via `pre-commit`.

   Install hooks:

   ```bash
   pre-commit install
   ```

   Run manually:

   ```bash
   pre-commit run --all-files
   ```
