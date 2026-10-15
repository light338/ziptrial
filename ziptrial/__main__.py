import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .core import Archive, TrialError, manifest_check, markdown, run_trial


def main():
    parser = argparse.ArgumentParser(description="Inspect release archives or run trusted smoke checks against the downloaded bytes.")
    parser.add_argument("--version", action="version", version="ZipTrial " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "run"):
        p = sub.add_parser(command)
        p.add_argument("archive")
        p.add_argument("--sha256", help="Expected SHA-256 from a trusted source (optional)")
        if command == "run":
            p.add_argument("--manifest", required=True, help="External JSON command manifest you trust")
            p.add_argument("--output", required=True, help="New directory for report.json and review.md")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            print(json.dumps(Archive(args.archive, args.sha256).inspect(), indent=2))
            return 0
        output = Path(args.output).resolve()
        if output.exists():
            raise TrialError("Output directory must not already exist")
        manifest = manifest_check(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
        report = run_trial(args.archive, manifest, args.sha256)
        output.mkdir(parents=True, exist_ok=False)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (output / "review.md").write_text(markdown(report), encoding="utf-8")
        print(json.dumps({"status": report["status"], "checks": [{"id": c["id"], "status": c["status"]} for c in report["checks"]], "missing_files": report["missing_files"], "report": str(output / "review.md")}, indent=2))
        return 0 if report["status"] == "passed" else 1
    except (TrialError, OSError, ValueError, TypeError) as error:
        print("ZipTrial: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
