# Recorded validation

The maintainer downloaded the public [Unmix v0.1.3 ZIP](https://github.com/light338/unmix/releases/download/v0.1.3/unmix-v0.1.3.zip) and supplied `examples/unmix.json` from outside the archive. No source checkout was copied into the trial. Node.js 24.19.0 and Git were available on PATH; ZipTrial ran under macOS Python 3.9.6.

| Input | SHA-256 | Verdict | Evidence |
|---|---|---|---|
| Original public ZIP | `3c6252fd176d6cf12bc61b5f0653c9a366ca14b2267573c5e3ebacd739e7ae83` | passed | CLI help and all 18 Unmix tests passed |
| Same ZIP with src/core.mjs removed | `31a651b0f275768f2f5fc852955a562d8c7b8a297319bc90384b4057ddcb5123` | failed | Missing required file; commands not executed |

The missing-file archive is an authored negative control, not a discovered defect in Unmix's published release. Both projects have the same maintainer. This does not claim independent users, general release correctness or a new AI-agent run. The release evidence archive contains both complete reports and the command manifest.

The ZipTrial test suite covers archive path/type collisions, links, metadata bounds, checksum mismatch, actual command failures, missing modules, environment filtering, output limits, timeouts, POSIX process cleanup and CLI exit/report semantics. CI additionally tests a generated ZIP of this project's own exact source commit.
