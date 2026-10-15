# ZipTrial

**Your checkout passes. Does the ZIP your users download pass too?**

ZipTrial inspects a ZIP/TAR release and runs your explicit smoke checks from a
fresh temporary extraction. It catches missing packaged files, failed entry
points, unexpected output and commands that time out. It records the exact
archive hash, file inventory, command results and a Markdown review.

Python 3.9+; standard library only. No account, hosted service or AI model required.
Experimental v0.1.0, tested locally on macOS and through CI on Linux/macOS.
No PyPI package publication is claimed; use this source checkout.

## Quick start

```sh
git clone https://github.com/light338/ziptrial.git
cd ziptrial
python3 -m unittest discover -s tests -v
python3 -m examples.demo /absolute/new-demo-directory
```

The demo creates working and deliberately incomplete archives. It requires the
working archive to pass and the broken archive to fail, then saves both reports.
It does not download or run third-party software.

Inspect an archive without executing its contents:

```sh
python3 -m ziptrial inspect /path/project.zip
```

Run commands from an **external manifest you trust**:

```sh
python3 -m ziptrial run /path/project.zip --manifest smoke.json --output /path/new-report
```

A minimal `smoke.json`:

```json
{
  "version": 1,
  "name": "My Python release",
  "root": "auto",
  "required_files": ["mytool/__main__.py"],
  "checks": [
    {
      "id": "cli-help",
      "argv": ["{python}", "-m", "mytool", "--help"],
      "stdout_contains": ["Usage"],
      "timeout_seconds": 30
    }
  ]
}
```

`{python}` selects the interpreter running ZipTrial. Commands are argument arrays,
never implicitly passed to a shell. `cwd` may select a relative directory inside
the archive root. `root: "auto"` selects a single enclosing directory when every
regular file is inside it, otherwise the extraction root. Set `root: "."` or an
explicit relative directory to override. A successful check needs exit code zero
and every configured `stdout_contains` string. Missing required files prevent all
command execution. All registered checks run if that preflight passes.

The output directory must be new. It contains `report.json` and `review.md`.
Exit codes: **0** checks passed, **1** artifact/check failed, **2** invalid input,
refused archive, checksum mismatch or another setup error.

To bind inspection/execution to a digest obtained from a trusted source:

```sh
python3 -m ziptrial run project.zip --sha256 EXPECTED_64_HEX_CHARACTERS --manifest smoke.json --output /path/new-report
```

A digest identifies bytes. It does not authenticate the publisher by itself.

## A real downloaded release

We downloaded [Unmix v0.1.3](https://github.com/light338/unmix/releases/tag/v0.1.3),
then ran its CLI and all 18 regression tests from the archive. Both checks passed.
Removing `src/core.mjs` from a copy caused the same manifest to fail preflight.
These are two projects maintained by the same author, not independent adoption.
See [recorded validation](docs/validation.md) and [the manifest](examples/unmix.json).
Node.js 22+ and Git on PATH are needed only for that Unmix example.

## Use in CI

ZipTrial's own CI creates a ZIP with `git archive` and tests **that ZIP**, as well
as the working tree. To use the same pattern for your trusted release:

```sh
python3 -m ziptrial run "$RUNNER_TEMP/project.zip" --manifest smoke.json --output "$RUNNER_TEMP/ziptrial-report"
```

Provision your runtime dependencies before the command. A manifest can explicitly
run installation/build commands, but they execute with the same permissions and
network access as other checks. Never run an untrusted pull request archive on a
runner containing credentials. [Execution boundaries](docs/boundaries.md) explain
what the temporary extraction does and does not isolate.

## Boundaries

- ZIP, uncompressed TAR and gzip-compressed TAR. Encrypted archives,
  symlinks, hardlinks, special/sparse entries, `.git`, traversal, duplicate paths
  and case/Unicode path collisions are refused before extraction.
- Limits: 64 MiB compressed, 128 MiB expanded, 16 MiB per file, 10,000 entries.
- Per-check timeout up to 300 seconds; stdout/stderr capped at 128 KiB each.
- HOME/temp are fresh, common caller secrets and language-path environment
  variables are not forwarded. PATH and system-installed dependencies remain.
- This is **not an OS or container sandbox**. Trusted commands can access the
  filesystem/network with the current user's permissions, or deliberately escape
  ordinary process-group cleanup. Use a disposable container/VM for untrusted code.
- Reports redact the temporary root and home path, but cannot remove every secret
  a program might print. Review reports before publishing them.
- No automatic dependency installation, vulnerability scan, signature verification,
  reproducible-build proof or guarantee that every feature works.
- POSIX process-group cleanup is tested; Windows is not validated in v0.1.0.

## Contribute

A useful report includes an archive/reproduction you may share, a minimal external
manifest, expected behavior and the actual verdict. Do not upload secrets or private
source. Tests run with `python3 -m unittest discover -s tests -v`. Development used
AI assistance; synthetic fixtures and maintainer-owned trials are labelled as such.

MIT licensed. Packaging smoke tests are established practice; ZipTrial packages
the archive boundary, explicit checks and inspectable evidence into one small CLI.
