# Scrapyd Log Parser

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/Status-Beta-orange.svg)]()

A high-performance tool designed to parse **Scrapyd** logs and generate detailed statistics, providing deep insights that Scrapyd doesn't provide natively.

## Features

- **High Performance**: Leverages `ProcessPoolExecutor` for parallel log parsing, maximizing CPU utilization.
- **Organized Output**: Centralizes all parsed JSON data into a dedicated `scrapydlogparser` directory, mirroring your project's structure.
- **Loop Mode**: Automatically monitors and re-parses logs at configurable intervals.
- **Smart Cleanup**: Automatically removes orphaned JSON files when their corresponding log files are deleted.
- **Incremental Parsing**: Only processes new or modified logs by checking file size against existing data.
- **Error Detection**: Specifically detects critical unhandled errors and crashes, labeling them for easy identification.

## Quick Start

### Installation

Install in editable mode for development:

```bash
pip install -e .
```

### Basic Usage

Run the parser by pointing it to your Scrapyd logs directory:

```bash
scrapyd-logparser /path/to/scrapyd/logs
```

By default, this will:

1. Create a `scrapydlogparser/` directory inside your logs folder.
2. Generate individual `.json` files for every log, mirroring the project/spider structure.
3. Save a global `scrapydlogparser.json` summary in the same directory.

### CLI Options

| Option       | Shorthand | Description                                                | Default                                       |
| :----------- | :-------- | :--------------------------------------------------------- | :-------------------------------------------- |
| `--interval` | `-i`      | Interval in seconds for continuous monitoring (loop mode). | `5`                                           |
| `--output`   | `-o`      | Path to the summary JSON file.                             | `logs/scrapydlogparser/scrapydlogparser.json` |
| `--force`    | `-f`      | Forces a full re-parse of all log files.                   | `Disabled`                                    |
| `--json-dir` |           | Custom directory to store individual JSON files.           | `logs/scrapydlogparser/`                      |

## Advanced Usage

### Continuous Monitoring (Loop Mode)

To keep your statistics updated in real-time (e.g., every 60 seconds):

```bash
scrapyd-logparser ./logs --interval 60
```

## Data Structure

The tool transforms your standard Scrapyd logs into a clean, queryable JSON structure:

```text
logs/
├── project/
│   └── spider/
│       └── job.log
└── scrapydlogparser/
    ├── scrapydlogparser.json (Global Summary)
    └── project/
        └── spider/
            └── job.json (Detailed stats)
```
