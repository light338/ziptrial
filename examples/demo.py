"""Authored positive/negative packaging controls; no model or network calls."""
import json
import sys
import zipfile
from pathlib import Path
from ziptrial.core import markdown, run_trial


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest = {"version": 1, "name": "Small CLI distribution", "required_files": ["app.py", "messages.json"], "checks": [{"id": "packaged-cli", "argv": ["{python}", "app.py"], "stdout_contains": ["Hello from the release"]}]}
    (root / "smoke.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for label in ("good", "missing-data"):
        artifact = root / (label + ".zip")
        with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("package/app.py", "import json\nfrom pathlib import Path\nprint(json.loads(Path('messages.json').read_text())['hello'])\n")
            if label == "good":
                archive.writestr("package/messages.json", json.dumps({"hello": "Hello from the release"}))
        report = run_trial(artifact, manifest)
        (root / (label + ".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
        (root / (label + ".md")).write_text(markdown(report), encoding="utf-8")
        expected = "passed" if label == "good" else "failed"
        if report["status"] != expected:
            raise RuntimeError("Control verdict differs from expected: " + label)
        print(label + ": " + report["status"])


if __name__ == "__main__":
    main()
