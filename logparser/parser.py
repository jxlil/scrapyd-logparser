import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LogStats:
    log_path: str
    size: int
    project: str = "unknown"
    spider: str = "unknown"
    job: str = "unknown"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    pages: int = 0
    items: int = 0
    finish_reason: str = "N/A"

    # Extended stats
    timeline: List[Dict] = field(default_factory=list)
    log_categories: Dict[str, Dict] = field(
        default_factory=lambda: {
            "critical_logs": {"count": 0, "details": []},
            "error_logs": {"count": 0, "details": []},
            "warning_logs": {"count": 0, "details": []},
            "redirect_logs": {"count": 0, "details": []},
            "retry_logs": {"count": 0, "details": []},
            "ignore_logs": {"count": 0, "details": []},
        }
    )
    host_info: Dict[str, str] = field(default_factory=dict)
    crawler_stats: Dict[str, Any] = field(default_factory=dict)
    head: str = ""
    tail: str = ""

    @property
    def duration(self) -> str:
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            return str(delta)
        return "0:00:00"

    @property
    def status(self) -> str:
        if self.finish_reason == "N/A":
            return "running"
        return "finished"

    def to_dict(self):

        data = asdict(self)
        if self.start_time:
            data["start_time"] = self.start_time.isoformat(sep=" ")
            data["first_log_time"] = data["start_time"]
        if self.end_time:
            data["end_time"] = self.end_time.isoformat(sep=" ")
            data["latest_log_time"] = data["end_time"]
            data["last_update_time"] = data["end_time"]

        # Serialize datetime in timeline
        if self.timeline:
            new_timeline = []
            for entry in self.timeline:
                new_entry = entry.copy()
                if isinstance(new_entry.get("timestamp"), datetime):
                    new_entry["timestamp"] = new_entry["timestamp"].isoformat(sep=" ")
                new_timeline.append(new_entry)
            data["timeline"] = new_timeline

        data["duration"] = self.duration
        data["runtime"] = self.duration
        data["status"] = self.status
        data["shutdown_reason"] = "N/A"
        return data

    def to_summary_dict(self):
        """Returns a dictionary without detailed timeline or large text blocks."""
        data = self.to_dict()
        data.pop("timeline", None)
        data.pop("head", None)
        data.pop("tail", None)

        # Simplify log_categories in summary (keep counts, remove details)
        if "log_categories" in data:
            summary_cats = {}
            for cat, info in data["log_categories"].items():
                summary_cats[cat] = {"count": info.get("count", 0)}
            data["log_categories"] = summary_cats

        return data


class LogParser:
    # Regex patterns (compiled for performance)
    _TIMESTAMP_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    _PAGES_PATTERN = re.compile(r"Crawled (\d+) pages")
    _DUMP_STATS_ITEMS_PATTERN = re.compile(r"'item_scraped_count': (\d+)")
    _FINISH_REASON_PATTERN = re.compile(r"Closing spider \((.*?)\)")

    # Pattern for periodic stats with rates:
    # Crawled 298 pages (at 298 pages/min), scraped 27126 items (at 27126 items/min)
    _LOG_STATS_PATTERN = re.compile(
        r"Crawled (\d+) pages \(at (\d+) pages/min\), scraped (\d+) items \(at (\d+) items/min\)"
    )

    # Log Level Patterns
    _LOG_LEVEL_PATTERN = re.compile(
        r" \[(?:scrapy\.|casper\.|root|py\.|ubereats|.*?)\] (CRITICAL|ERROR|WARNING|INFO|DEBUG): (.*)"
    )
    _RETRY_PATTERN = re.compile(r"Retrying <GET")
    _UNHANDLED_ERROR_PATTERN = re.compile(r"CRITICAL: Unhandled error in Deferred")
    _TELNET_PATTERN = re.compile(r"Telnet console listening on (.*)")
    _SCRAPY_VERSION_PATTERN = re.compile(r"Scrapy (\d+\.\d+\.\d+) started")
    _DUMP_STATS_START_PATTERN = re.compile(r"Dumping Scrapy stats:")
    _DUMP_STATS_LINE_PATTERN = re.compile(r"^\s*\{?'([^']+)':\s*(.*?)[,}]?$")

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._in_stats_dump = False

    def parse(self) -> LogStats:
        # Assuming structure: .../{project}/{spider}/{job}.log
        try:
            job = self.file_path.stem
            spider = self.file_path.parent.name
            project = self.file_path.parent.parent.name
        except Exception:
            job, spider, project = "unknown", "unknown", "unknown"

        stats = LogStats(
            log_path=str(self.file_path.absolute()),
            size=self.file_path.stat().st_size,
            project=project,
            spider=spider,
            job=job,
        )

        head_lines = []
        tail_lines = deque(maxlen=50)  # Keep last 50 lines
        line_count = 0

        try:
            with open(self.file_path, mode="r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_count += 1
                    cleaned_line = line.strip()

                    # Capture head (first 50 lines)
                    if line_count <= 50:
                        head_lines.append(cleaned_line)

                    # Capture tail
                    tail_lines.append(cleaned_line)

                    self._process_line(line, stats)

        except Exception as e:
            stats.finish_reason = f"Error reading log: {str(e)}"

        stats.head = "\n".join(head_lines)
        stats.tail = "\n".join(tail_lines)

        return stats

    def _process_line(self, line: str, stats: LogStats):
        # Extract timestamp
        match = self._TIMESTAMP_PATTERN.match(line)
        timestamp = None
        if match:
            dt_str = match.group(1)
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                timestamp = dt
                if not stats.start_time:
                    stats.start_time = dt
                stats.end_time = dt
            except ValueError:
                pass

        if timestamp:
            # Check for periodic log stats (pages and items scraped per minute)
            log_stats_match = self._LOG_STATS_PATTERN.search(line)
            if log_stats_match:
                pages_count = int(log_stats_match.group(1))
                pages_rate = int(log_stats_match.group(2))
                items_count = int(log_stats_match.group(3))
                items_rate = int(log_stats_match.group(4))

                stats.timeline.append(
                    {
                        "timestamp": timestamp,
                        "pages": pages_count,
                        "pages_rate": pages_rate,
                        "items": items_count,
                        "items_rate": items_rate,
                    }
                )
                # Also update current totals for immediate feedback
                stats.pages = max(stats.pages, pages_count)
                stats.items = max(stats.items, items_count)

        # Match Log Levels and specific events
        level_match = self._LOG_LEVEL_PATTERN.search(line)
        if level_match:
            level = level_match.group(1)
            message = level_match.group(2)

            if level == "CRITICAL":
                self._add_log_category(stats, "critical_logs", line)
            elif level == "ERROR":
                self._add_log_category(stats, "error_logs", line)
            elif level == "WARNING":
                self._add_log_category(stats, "warning_logs", line)

        if self._RETRY_PATTERN.search(line):
            self._add_log_category(stats, "retry_logs", line)

        # Host Info Extraction
        telnet_match = self._TELNET_PATTERN.search(line)
        if telnet_match:
            stats.host_info["telnet_console"] = telnet_match.group(1)

        version_match = self._SCRAPY_VERSION_PATTERN.search(line)
        if version_match:
            stats.host_info["scrapy_version"] = version_match.group(1)

        # Check for finish reason
        finish_match = self._FINISH_REASON_PATTERN.search(line)
        if finish_match:
            stats.finish_reason = finish_match.group(1)

        # Check for unhandled critical errors
        if self._UNHANDLED_ERROR_PATTERN.search(line):
            stats.finish_reason = "critical_error"

        # Check for pages
        pages_match = self._PAGES_PATTERN.search(line)
        if pages_match:
            stats.pages = max(stats.pages, int(pages_match.group(1)))

        # Check for item count in stats dump (fallback if not in full dump parse)
        items_stats_match = self._DUMP_STATS_ITEMS_PATTERN.search(line)
        if items_stats_match:
            stats.items = max(stats.items, int(items_stats_match.group(1)))

        # Multi-line stats dump parsing
        if self._DUMP_STATS_START_PATTERN.search(line):
            self._in_stats_dump = True
            return

        if self._in_stats_dump:
            if "}" in line:
                # Handle potentially data on the same line as the closing brace
                if line.strip() != "}":
                    dump_match = self._DUMP_STATS_LINE_PATTERN.search(line)
                    if dump_match:
                        key = dump_match.group(1)
                        val_str = dump_match.group(2).strip().rstrip(",").rstrip("}")
                        # Inline type conversion for now to keep it small
                        try:
                            if val_str.startswith("'") or val_str.startswith('"'):
                                val = val_str.strip("'\"")
                            elif val_str.isdigit():
                                val = int(val_str)
                            elif val_str.replace(".", "", 1).isdigit():
                                val = float(val_str)
                            else:
                                val = val_str
                            stats.crawler_stats[key] = val
                        except Exception:
                            stats.crawler_stats[key] = val_str

                self._in_stats_dump = False
                # Final check for pages and items from the collected crawler_stats
                if "downloader/response_count" in stats.crawler_stats:
                    stats.pages = max(
                        stats.pages,
                        int(stats.crawler_stats["downloader/response_count"]),
                    )
                if "item_scraped_count" in stats.crawler_stats:
                    stats.items = max(stats.items, int(stats.crawler_stats["item_scraped_count"]))
                return
            else:
                dump_match = self._DUMP_STATS_LINE_PATTERN.search(line)
                if dump_match:
                    key = dump_match.group(1)
                    val_str = dump_match.group(2).strip().rstrip(",")

                    # Basic type conversion
                    try:
                        if val_str.startswith("'") or val_str.startswith('"'):
                            val = val_str.strip("'\"")
                        elif val_str.isdigit():
                            val = int(val_str)
                        elif val_str.replace(".", "", 1).isdigit():
                            val = float(val_str)
                        else:
                            val = val_str
                        stats.crawler_stats[key] = val
                    except Exception:
                        stats.crawler_stats[key] = val_str

    def _add_log_category(self, stats: LogStats, category: str, line: str):
        if category in stats.log_categories:
            stats.log_categories[category]["count"] += 1
            # Keep only first 10 details to save space
            if len(stats.log_categories[category]["details"]) < 10:
                stats.log_categories[category]["details"].append(line.strip())
