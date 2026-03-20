"""
Generate resume-safe metrics from Testing CSV data.

Usage (PowerShell):
    python .\\Testing\\resume_metrics_summary.py
    python .\\Testing\\resume_metrics_summary.py --csv .\\Testing\\test_results_main.csv
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


def safe_float(value: str) -> float | None:
    try:
        number = float(str(value).strip())
        if math.isnan(number):
            return None
        return number
    except (ValueError, TypeError):
        return None


def parse_ratio(value: str) -> tuple[int, int] | None:
    raw = str(value).strip()
    if "/" not in raw:
        return None
    left, right = raw.split("/", 1)
    try:
        filled = int(left.strip())
        total = int(right.strip())
        return filled, total
    except ValueError:
        return None


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def summarize(rows: list[dict[str, str]]) -> dict:
    if not rows:
        return {}

    times = []
    accuracies = []
    field_counts = []
    board_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
    ratios = []
    complexity_times: defaultdict[str, list[float]] = defaultdict(list)
    complexity_counts: Counter[str] = Counter()

    for row in rows:
        board = (row.get("Job Board/Site Type") or "Unknown").strip()
        status = (row.get("Final Status") or "Unknown").strip()
        failure = (row.get("Failure Point") or "").strip()
        complexity = (row.get("Form Type") or "Unknown").strip()

        board_counter[board] += 1
        status_counter[status] += 1
        if failure:
            failure_counter[failure] += 1
        complexity_counts[complexity] += 1

        time_value = safe_float(row.get("Total Time Taken (seconds)", ""))
        if time_value is not None and time_value > 0:
            times.append(time_value)
            complexity_times[complexity].append(time_value)

        accuracy_value = safe_float(row.get("Accuracy Score (1-10)", ""))
        if accuracy_value is not None and accuracy_value > 0:
            accuracies.append(accuracy_value)

        field_value = safe_float(row.get("Total Form Fields Detected", ""))
        if field_value is not None and field_value >= 0:
            field_counts.append(int(field_value))

        ratio = parse_ratio(row.get("Fields Filled/Total Available", ""))
        if ratio:
            ratios.append(ratio)

    success_count = sum(
        count for status, count in status_counter.items() if "success" in status.lower()
    )
    total_count = len(rows)
    success_rate = (100.0 * success_count / total_count) if total_count else 0.0

    filled_total = sum(filled for filled, _ in ratios)
    available_total = sum(total for _, total in ratios)
    fill_rate = (100.0 * filled_total / available_total) if available_total > 0 else None

    complexity_summary = {}
    for key in sorted(complexity_counts.keys()):
        values = complexity_times.get(key, [])
        complexity_summary[key] = {
            "count": complexity_counts[key],
            "median_seconds": round(median(values), 1) if values else None,
        }

    return {
        "rows": total_count,
        "boards": board_counter,
        "status": status_counter,
        "failures": failure_counter,
        "success_rate": round(success_rate, 1),
        "time_median_seconds": round(median(times), 1) if times else None,
        "time_min_seconds": round(min(times), 1) if times else None,
        "time_max_seconds": round(max(times), 1) if times else None,
        "fields_median": int(median(field_counts)) if field_counts else None,
        "fields_min": min(field_counts) if field_counts else None,
        "fields_max": max(field_counts) if field_counts else None,
        "accuracy_avg": round(sum(accuracies) / len(accuracies), 1) if accuracies else None,
        "fill_rate": round(fill_rate, 1) if fill_rate is not None else None,
        "filled_total": filled_total,
        "available_total": available_total,
        "complexity": complexity_summary,
    }


def print_summary(summary: dict, csv_path: Path) -> None:
    if not summary:
        print("No data rows found.")
        return

    print("=" * 72)
    print("Resume Metrics Summary")
    print("=" * 72)
    print(f"Source CSV: {csv_path}")
    print(f"Total evaluated applications: {summary['rows']}")
    print(f"ATS/job-board categories covered: {len(summary['boards'])}")
    print(
        "Status mix: "
        + ", ".join(f"{k}={v}" for k, v in summary["status"].most_common())
    )
    print(f"Success rate: {summary['success_rate']}%")
    print(
        f"Run time (seconds): median={summary['time_median_seconds']}, "
        f"range={summary['time_min_seconds']}-{summary['time_max_seconds']}"
    )
    print(
        f"Form size (fields): median={summary['fields_median']}, "
        f"range={summary['fields_min']}-{summary['fields_max']}"
    )
    if summary["accuracy_avg"] is not None:
        print(f"Manual accuracy score average: {summary['accuracy_avg']}/10")
    if summary["fill_rate"] is not None:
        print(
            f"Recorded field fill rate: {summary['fill_rate']}% "
            f"({summary['filled_total']}/{summary['available_total']})"
        )
    if summary["failures"]:
        print(
            "Most frequent failure points: "
            + ", ".join(f"{k}={v}" for k, v in summary["failures"].most_common(3))
        )

    print("\nComplexity breakdown:")
    for complexity, data in summary["complexity"].items():
        print(
            f"- {complexity}: n={data['count']}, median time={data['median_seconds']}s"
        )

    print("\nResume-safe metric snippets:")
    print(
        f"- Evaluated {summary['rows']} live applications across "
        f"{len(summary['boards'])} ATS/job-board categories "
        f"with standardized run-level telemetry."
    )
    print(
        f"- Instrumented each run with 41 tracked metrics and a median execution "
        f"time of {summary['time_median_seconds']} seconds "
        f"(range: {summary['time_min_seconds']}-{summary['time_max_seconds']} seconds)."
    )
    if summary["accuracy_avg"] is not None:
        print(
            f"- Maintained human-reviewed field accuracy at "
            f"{summary['accuracy_avg']}/10 across recorded runs."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize resume-safe test metrics.")
    parser.add_argument(
        "--csv",
        default="Testing/test_results_main.csv",
        help="Path to CSV metrics file (default: Testing/test_results_main.csv)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    rows = load_rows(csv_path)
    summary = summarize(rows)
    print_summary(summary, csv_path)


if __name__ == "__main__":
    main()
