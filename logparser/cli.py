import argparse
import os
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Parse Scrapyd logs for statistics.")
    parser.add_argument(
        "log_dir", type=str, help="Path to the directory containing Scrapyd logs"
    )
    return parser.parse_args()


def find_log_files(log_dir):
    """Recursively find all files in the given directory."""
    log_files = []
    path = Path(log_dir)

    if not path.exists():
        print(f"Error: The directory '{log_dir}' does not exist.")
        return []

    if not path.is_dir():
        print(f"Error: The path '{log_dir}' is not a directory.")
        return []

    print(f"Scanning directory: {path.absolute()}")

    for root, _, files in os.walk(path):
        for file in files:
            # Assuming logs are files. We might want to filter by .log extension later
            # but usually scrapyd logs end in .log so let's check for likely candidates
            # or just take everything for now as requested "read all logs".
            if file.endswith(".log") or file.endswith(".txt"):
                file_path = Path(root) / file
                log_files.append(file_path)

    return log_files


from itertools import islice


def read_log_preview(file_path, num_lines=5):
    """Read the first few lines of a log file to verify we can read it."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in islice(f, num_lines)]
        return lines
    except Exception as e:
        return [f"Error reading file: {e}"]


def main():
    args = parse_args()
    log_files = find_log_files(args.log_dir)

    if not log_files:
        print("No log files found.")
        return

    print(f"Found {len(log_files)} log file(s).")

    for log_file in log_files:
        print(f"\n--- Reading: {log_file} ---")
        preview = read_log_preview(log_file)
        for line in preview:
            print(line)
        if len(preview) == 0:
            print("(File is empty)")


if __name__ == "__main__":
    main()
