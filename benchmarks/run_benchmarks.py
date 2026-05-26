from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "benchmarks" / "results"


@dataclass(frozen=True)
class BenchmarkCommand:
    name: str
    command: list[str]


def _run_command(command: BenchmarkCommand) -> dict[str, object]:
    started_at = time.perf_counter()
    completed = subprocess.run(
        command.command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started_at
    return {
        "name": command.name,
        "command": command.command,
        "returncode": completed.returncode,
        "elapsed": elapsed,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _write_reports(results: list[dict[str, object]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    text_path = output_dir / f"benchmark-{stamp}.txt"
    json_path = output_dir / f"benchmark-{stamp}.json"

    lines: list[str] = [f"Benchmark run: {stamp}", ""]
    for result in results:
        lines.append(f"== {result['name']} ==")
        lines.append("Command: " + " ".join(str(part) for part in result["command"]))
        lines.append(f"Exit code: {result['returncode']} | Runner elapsed: {float(result['elapsed']):.2f}s")
        if result["stdout"]:
            lines.append(str(result["stdout"]))
        if result["stderr"]:
            lines.append("stderr:")
            lines.append(str(result["stderr"]))
        lines.append("")

    text_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return text_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the benchmark suite and store reports.")
    parser.add_argument("--provider", choices=["arbeitnow", "adzuna"], default="arbeitnow")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--offers", type=int, default=500)
    parser.add_argument("--query", default="c++ simulation")
    parser.add_argument("--country", default="fr")
    parser.add_argument("--where", default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if args.pages < 1 or args.repeats < 1 or args.offers < 1:
        raise SystemExit("--pages, --repeats, and --offers must be positive.")

    python = sys.executable
    commands: list[BenchmarkCommand] = [
        BenchmarkCommand(
            "fetch",
            [
                python,
                "benchmarks/benchmark_fetch.py",
                "--provider",
                args.provider,
                "--pages",
                str(args.pages),
                "--repeats",
                str(args.repeats),
                "--stage",
                "all",
                "--query",
                args.query,
                "--country",
                args.country,
                *(["--where", args.where] if args.where else []),
            ],
        ),
        BenchmarkCommand(
            "scoring",
            [
                python,
                "benchmarks/benchmark_scoring.py",
                "--provider",
                "synthetic",
                "--offers",
                str(args.offers),
                "--repeats",
                str(args.repeats),
            ],
        ),
        BenchmarkCommand(
            "storage",
            [
                python,
                "benchmarks/benchmark_storage.py",
                "--offers",
                str(args.offers),
                "--repeats",
                str(args.repeats),
            ],
        )
    ]

    results = [_run_command(command) for command in commands]
    text_path, json_path = _write_reports(results, args.output_dir)

    for result in results:
        if result["stdout"]:
            print(result["stdout"])
    print(f"\nSaved benchmark reports:")
    print(f"- {text_path}")
    print(f"- {json_path}")

    if any(result["returncode"] != 0 for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
