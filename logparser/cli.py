import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from logparser.parser import LogParser

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_runtime_to_seconds(runtime_str):
    """Parse runtime string (HH:MM:SS or 'X day(s), HH:MM:SS') into total seconds."""
    if not runtime_str or runtime_str == "0:00:00":
        return 0

    try:
        total_seconds = 0

        # Handle format like "1 day, 20:22:00" or "2 days, 1:23:45"
        if "day" in runtime_str:
            day_part, time_part = runtime_str.split(",", 1)
            # Extract days number
            days = int(day_part.split()[0])
            total_seconds += days * 86400  # 86400 seconds in a day
            runtime_str = time_part.strip()

        # Handle format like "1:23:45" or "0:12:34"
        parts = runtime_str.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = map(int, parts)
            total_seconds += hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes, seconds = map(int, parts)
            total_seconds += minutes * 60 + seconds

        return total_seconds
    except (ValueError, AttributeError):
        return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Parse Scrapyd logs for statistics.")
    parser.add_argument("log_dir", type=str, help="Path to the directory containing Scrapyd logs")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output summary JSON file path",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force re-parsing of all files, ignoring existing summary",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=0,
        help="Interval in seconds to run the parser in a loop. Default 0 (run once).",
    )
    parser.add_argument(
        "--json-dir",
        type=str,
        default=None,
        help="Directory to store parsed JSON files. Follows project/spider structure. If not set, saves alongside logs.",
    )
    return parser.parse_args()


def find_log_files(log_dir):
    """Recursively find all files in the given directory."""
    log_files = []
    path = Path(log_dir)

    if not path.exists():
        logger.error(f"The directory '{log_dir}' does not exist.")
        return []

    if not path.is_dir():
        logger.error(f"The path '{log_dir}' is not a directory.")
        return []

    logger.info(f"Scanning directory: {path.absolute()}")

    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(".log") or file.endswith(".txt"):
                file_path = Path(root) / file
                log_files.append(file_path)

    return log_files


def cleanup_orphans(log_dir, exclude_files=None):
    """Remove valid JSON stats files that no longer have a corresponding log file."""
    if exclude_files is None:
        exclude_files = set()

    logger.info(f"Scanning for orphaned JSON files in: {log_dir}")
    removed_count = 0
    log_dir_path = Path(log_dir).absolute()
    scrapydlogparser_dir = log_dir_path / "scrapydlogparser"

    if not log_dir_path.exists():
        return

    # Walk the directory
    for root, dirs, files in os.walk(log_dir_path):
        root_path = Path(root)

        for file in files:
            if not file.endswith(".json"):
                continue

            json_path = root_path / file

            # Skip excluded files
            if str(json_path.absolute()) in exclude_files:
                continue

            # Determine potential log path
            log_candidates = []

            # Check if inside scapydlogparser directory (New Structure)
            # We ONLY cleanup JSONs in scrapydlogparser_dir
            try:
                if (
                    scrapydlogparser_dir in json_path.parents
                    or scrapydlogparser_dir == json_path.parent
                ):
                    rel_path = json_path.relative_to(scrapydlogparser_dir)
                    # Map back to source structure: logs/scrapydlogparser/project/spider/job.json -> logs/project/spider/job.log
                    cand_log = log_dir_path / rel_path.with_suffix(".log")
                    log_candidates.append(cand_log)
                    log_candidates.append(log_dir_path / rel_path.with_suffix(".txt"))
                else:
                    # Ignore JSONs outside scrapydlogparser to avoid deleting other library files
                    continue
            except ValueError:
                # Fallback if path manipulation fails
                continue

            # Check if any candidate exists
            found = False
            for cand in log_candidates:
                if cand.exists():
                    found = True
                    break

            if not found:
                try:
                    json_path.unlink()
                    removed_count += 1
                    # logger.debug(f"Removed orphan: {json_path}")
                except Exception as e:
                    logger.error(f"Error removing orphan {json_path}: {e}")

    if removed_count > 0:
        logger.info(f"Removed {removed_count} orphaned JSON files.")
    else:
        logger.info("No orphaned JSON files found.")


def process_single_file(log_file):
    """Helper function to run in a separate process."""
    parser = LogParser(log_file)
    stats = parser.parse()
    # It's more efficient to return the object and process saving in the main thread
    # to avoid file locking issues or complex multiprocessing logic for simple writes,
    # but for individual files, we can write them here to parallelize I/O.
    return stats


def run_analysis(args):
    start_time = time.perf_counter()

    # Define output files strictly to avoid deleting them during cleanup
    if args.output:
        output_path = Path(args.output).absolute()
    else:
        # Default: logs/scrapydlogparser/scrapydlogparser.json
        log_dir_path = Path(args.log_dir).absolute()
        output_path = log_dir_path / "scrapydlogparser" / "scrapydlogparser.json"

    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Clean up orphaned JSON files first
    cleanup_orphans(args.log_dir, exclude_files={str(output_path)})

    log_files = find_log_files(args.log_dir)

    if not log_files:
        logger.warning("No log files found.")
        return

    summary_results = {}
    # output_path is already set above

    # Incremental parsing logic
    # Read from jobs.jsonl instead of scrapydlogparser.json
    existing_data_map = {}  # Map log_path -> stats_dict for O(1) lookup
    existing_project_structure = {}  # Keep track of existing structure

    # Use jobs.jsonl for incremental parsing
    jobs_jsonl_path = output_path.parent / "jobs.jsonl"

    if jobs_jsonl_path.exists() and not args.force:
        try:
            with open(jobs_jsonl_path, "r", encoding="utf-8") as f:
                line_count = 0
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        log_path = entry.get("log_path")
                        if log_path:
                            existing_data_map[log_path] = entry

                            # Build project structure
                            project = entry.get("project", "unknown")
                            if project not in existing_project_structure:
                                existing_project_structure[project] = []
                            existing_project_structure[project].append(entry)
                        line_count += 1

            logger.info(f"Loaded {len(existing_data_map)} existing entries from jobs.jsonl.")
        except Exception as e:
            logger.warning(f"Could not read existing jobs.jsonl ({e}). Starting fresh.")

    # Initialize summary_results with existing structure or empty dict
    # We will rebuild it to ensure clean state but reusing unchanged entries
    summary_results = {}

    files_to_process = []
    skipped_count = 0

    if args.force:
        files_to_process = log_files
        logger.info("Force mode enabled: reprocessing all files.")
        summary_results = {}  # Clear everything on force
    else:
        for log_file in log_files:
            abs_path = str(log_file.absolute())
            if abs_path in existing_data_map:
                # Check if file size has changed
                try:
                    current_size = log_file.stat().st_size
                    existing_entry = existing_data_map[abs_path]

                    if current_size == existing_entry.get("size", -1):
                        skipped_count += 1

                        # Add unchanged entry to summary_results
                        project = existing_entry.get("project", "unknown")
                        if project not in summary_results:
                            summary_results[project] = []
                        summary_results[project].append(existing_entry)

                        continue
                except OSError:
                    pass

            files_to_process.append(log_file)

        if skipped_count > 0:
            logger.info(f"Skipping {skipped_count} unchanged files.")

    if not files_to_process:
        logger.info("No new or modified files to process.")
    else:
        logger.info(f"Processing {len(files_to_process)} files with ProcessPoolExecutor...")

    # Use ProcessPoolExecutor to utilize all CPU cores
    with ProcessPoolExecutor() as executor:
        # Submit all tasks
        future_to_file = {executor.submit(process_single_file, f): f for f in files_to_process}

        # Collect results as they complete
        for i, future in enumerate(as_completed(future_to_file)):
            try:
                stats = future.result()

                # Append to summary grouped by project
                project = stats.project
                if project not in summary_results:
                    summary_results[project] = []
                summary_results[project].append(stats.to_summary_dict())

                # Save individual detail file
                if args.json_dir:
                    base_dir = Path(args.json_dir)
                    # Use project/spider structure from stats or path
                    # We can use stats.project and stats.spider which are extracted reliably

                    target_dir = base_dir / stats.project / stats.spider
                    target_dir.mkdir(parents=True, exist_ok=True)
                    json_path = target_dir / f"{stats.job}.json"

                    # Update json_path in stats object so the summary points to the correct location?
                    # The summary usually contains "log_path". Should we add "json_path"?
                    # The current to_summary_dict() doesn't verify json path, backend assumes it relative to log?
                    # Actually, scrapyd-view constructs URL based on project/spider/runKey.json.
                    # If we change location, we might break scrapyd-view unless it knows the new prefix.
                else:
                    log_path = Path(stats.log_path)
                    # Resolve root_log_dir relative to the execution or passed arg
                    root_log_dir = Path(args.log_dir).absolute()

                    try:
                        rel_path = log_path.relative_to(root_log_dir)
                    except ValueError:
                        # Fallback: construct path using project/spider/job.json if available
                        if stats.project != "unknown" and stats.spider != "unknown":
                            rel_path = Path(stats.project) / stats.spider / log_path.name
                        else:
                            # Last resort: just the filename
                            rel_path = Path(log_path.name)

                    target_dir = root_log_dir / "scrapydlogparser" / rel_path.parent
                    target_dir.mkdir(parents=True, exist_ok=True)
                    json_path = target_dir / log_path.with_suffix(".json").name

                # Actualizamos stats.json_path si queremos rastrearlo (opcional, LogStats no tiene ese campo explicito en init pero to_dict lo usa?)
                # LogStats no tiene json_path en __init__, pero podemos agregarlo dinámicamente si fuera necesario.

                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(stats.to_dict(), f, indent=2)

            except Exception as e:
                logger.error(f"Error processing file: {e}")

            # Simple progress indicator
            if (i + 1) % 100 == 0:
                logger.info(f"Processed {i + 1}/{len(files_to_process)} files...")

    # Calculate statistics per project and globally
    stats_output = {
        "projects": {},
        "global": {
            "total_items": 0,
            "total_pages": 0,
            "active_spiders": 0,
            "completed_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "total_runtime": 0,
            "runtime_count": 0,
        },
    }

    for project, runs in summary_results.items():
        p_stats = {
            "total_items": 0,
            "total_pages": 0,
            "active_spiders": 0,
            "completed_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "total_runtime": 0,
            "runtime_count": 0,
        }

        for r in runs:
            items = r.get("items", 0)
            pages = r.get("pages", 0)
            status = r.get("status")
            crashed = r.get("crashed", False)
            finish_reason = r.get("finish_reason", "N/A")
            runtime_str = r.get("runtime", "0:00:00")
            runtime_sec = parse_runtime_to_seconds(runtime_str)

            # Global accumulation
            stats_output["global"]["total_items"] += items
            stats_output["global"]["total_pages"] += pages

            # Project accumulation
            p_stats["total_items"] += items
            p_stats["total_pages"] += pages

            if status == "running":
                stats_output["global"]["active_spiders"] += 1
                p_stats["active_spiders"] += 1
            else:
                # Completed runs
                stats_output["global"]["completed_runs"] += 1
                p_stats["completed_runs"] += 1

                if not crashed and items > 0:
                    stats_output["global"]["successful_runs"] += 1
                    p_stats["successful_runs"] += 1
                else:
                    stats_output["global"]["failed_runs"] += 1
                    p_stats["failed_runs"] += 1

                if runtime_sec > 0:
                    stats_output["global"]["total_runtime"] += runtime_sec
                    stats_output["global"]["runtime_count"] += 1
                    p_stats["total_runtime"] += runtime_sec
                    p_stats["runtime_count"] += 1

        # Calculate rates and averages for project
        p_final = {
            "total_items": p_stats["total_items"],
            "total_pages": p_stats["total_pages"],
            "active_spiders": p_stats["active_spiders"],
            "failed_runs": p_stats["failed_runs"],
            "success_rate": (
                round((p_stats["successful_runs"] / p_stats["completed_runs"] * 100), 2)
                if p_stats["completed_runs"] > 0
                else 0
            ),
            "avg_runtime": (
                round(p_stats["total_runtime"] / p_stats["runtime_count"], 2)
                if p_stats["runtime_count"] > 0
                else 0
            ),
        }
        stats_output["projects"][project] = p_final

    # Calculate global rates and averages
    g = stats_output["global"]
    global_final = {
        "total_items": g["total_items"],
        "total_pages": g["total_pages"],
        "active_spiders": g["active_spiders"],
        "failed_runs": g["failed_runs"],
        "success_rate": (
            round((g["successful_runs"] / g["completed_runs"] * 100), 2)
            if g["completed_runs"] > 0
            else 0
        ),
        "avg_runtime": (
            round(g["total_runtime"] / g["runtime_count"], 2) if g["runtime_count"] > 0 else 0
        ),
    }
    stats_output["global"] = global_final

    # Save optimized JSONL output
    output_dir = output_path.parent

    # 1. Save stats.json (metadata and statistics)
    stats_file = output_dir / "stats.json"
    total_jobs = sum(len(runs) for runs in summary_results.values())
    active_count = sum(
        1
        for runs in summary_results.values()
        for r in runs
        if r.get("status") == "running" or r.get("finish_reason") == "N/A"
    )

    stats_data = {
        "status": "ok",
        "stats": stats_output,
        "total_jobs": total_jobs,
        "active_count": active_count,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats_data, f, indent=2)

    # 2. Save active.jsonl (only running jobs)
    active_file = output_dir / "active.jsonl"
    with open(active_file, "w", encoding="utf-8") as f:
        for project, runs in summary_results.items():
            for run in runs:
                if run.get("status") == "running" or run.get("finish_reason") == "N/A":
                    f.write(json.dumps(run) + "\n")

    # 3. Update jobs.jsonl (all jobs, sorted by latest_log_time)
    jobs_file = output_dir / "jobs.jsonl"

    # Read existing jobs to preserve history
    existing_jobs = {}
    if jobs_file.exists():
        try:
            with open(jobs_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        job = json.loads(line)
                        job_key = f"{job.get('project', 'unknown')}-{job.get('spider', 'unknown')}-{job.get('job', 'unknown')}"
                        existing_jobs[job_key] = job
        except Exception as e:
            logger.warning(f"Could not read existing jobs.jsonl: {e}")

    # Update with current data
    for project, runs in summary_results.items():
        for run in runs:
            job_key = f"{run.get('project', 'unknown')}-{run.get('spider', 'unknown')}-{run.get('job', 'unknown')}"
            existing_jobs[job_key] = run

    # Sort by latest_log_time (newest first)
    all_jobs = sorted(
        existing_jobs.values(),
        key=lambda x: x.get("latest_log_time") or x.get("last_update_time") or "",
        reverse=True,
    )

    # Write sorted jobs
    with open(jobs_file, "w", encoding="utf-8") as f:
        for job in all_jobs:
            f.write(json.dumps(job) + "\n")

    # Also keep the old scrapydlogparser.json for backward compatibility (optional)
    # with open(output_path, "w", encoding="utf-8") as f:
    #     json.dump({"status": "ok", "stats": stats_output, "projects": summary_results}, f, indent=4)

    end_time = time.perf_counter()
    duration = end_time - start_time

    # Calculate total count from values lists
    total_parsed = sum(len(items) for items in summary_results.values())

    logger.info(f"\nSuccessfully parsed {total_parsed} logs in {duration:.4f} seconds.")
    logger.info(f"\nOptimized output saved:")
    logger.info(f"  - stats.json: {stats_file.stat().st_size / 1024:.1f} KB")
    logger.info(
        f"  - active.jsonl: {active_file.stat().st_size / 1024:.1f} KB ({active_count} jobs)"
    )
    logger.info(
        f"  - jobs.jsonl: {jobs_file.stat().st_size / 1024:.1f} KB ({len(all_jobs)} total jobs)"
    )


def main():
    args = parse_args()

    if args.interval > 0:
        logger.info(f"Running in loop mode. Interval: {args.interval} seconds.")
        while True:
            try:
                run_analysis(args)
            except Exception as e:
                logger.error(f"Error in analysis loop: {e}")

            logger.info(f"Sleeping for {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_analysis(args)


if __name__ == "__main__":
    main()
