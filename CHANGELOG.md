# Changelog

## 0.1.0 — 2026-10-15

- Standard-library-only ZIP/TAR inventory and SHA-256 binding.
- Bounded extraction rejecting traversal, links, special files and ambiguous paths.
- Trusted external manifests: required files, argv checks, expected output and timeouts.
- Fresh extraction/HOME/temp, bounded process output and POSIX descendant cleanup.
- JSON and Markdown results tied to exact archive bytes and interpreted manifest.
- Authored positive/negative controls and downloaded Unmix v0.1.3 trial.
- Checkout tests plus actual archive tests in CI. No PyPI publication or external adoption claimed.
