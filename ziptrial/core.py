"""Archive inspection never executes contents; run() executes trusted manifest commands."""
import hashlib
import gzip
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_OUTPUT = 128 * 1024


class TrialError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise TrialError("Invalid archive path")
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or "\\" in name or ":" in name:
        raise TrialError("Unsafe archive path: " + repr(name))
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        raise TrialError("Unsafe archive path: " + repr(name))
    if any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *["COM%d" % i for i in range(1, 10)], *["LPT%d" % i for i in range(1, 10)]} for p in parts):
        raise TrialError("Nonportable archive path: " + repr(name))
    if any(p.casefold() == ".git" for p in parts):
        raise TrialError("Archive must not contain .git metadata")
    return "/".join(parts)


class Archive:
    def __init__(self, path, expected_sha256=None):
        path = Path(path)
        if path.stat().st_size > MAX_ARCHIVE:
            raise TrialError("Compressed archive exceeds 64 MiB")
        data = path.read_bytes()
        if len(data) > MAX_ARCHIVE:
            raise TrialError("Compressed archive exceeds 64 MiB")
        self.sha256 = digest(data)
        if expected_sha256 is not None and (not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) or self.sha256 != expected_sha256.lower()):
            raise TrialError("Archive SHA-256 does not match the expected digest")
        self.size = len(data)
        self.files = {}
        seen = {}
        kinds = {}
        portable_paths = {}
        total = 0

        def add(name, size, mode, directory, reader):
            nonlocal total
            name = safe_name(name)
            key = unicodedata.normalize("NFC", name).casefold()
            parts = name.split("/")
            for end in range(1, len(parts) + 1):
                prefix = "/".join(parts[:end])
                canonical = unicodedata.normalize("NFC", prefix).casefold()
                if canonical in portable_paths and portable_paths[canonical] != prefix:
                    raise TrialError("Case/Unicode-colliding path component: " + repr(name))
                portable_paths[canonical] = prefix
            if key in seen:
                raise TrialError("Duplicate or case/Unicode-colliding archive path: " + repr(name))
            seen[key] = name
            if len(seen) > MAX_ENTRIES or size > MAX_FILE or size < 0:
                raise TrialError("Archive entry limit exceeded")
            total += size
            if total > MAX_EXPANDED:
                raise TrialError("Expanded archive exceeds 128 MiB")
            kinds[key] = directory
            if directory:
                if size:
                    raise TrialError("Directory entry contains data")
                return
            content = reader(MAX_FILE + 1)
            if len(content) != size:
                raise TrialError("Archive member size mismatch")
            self.files[name] = {"content": content, "mode": 0o755 if mode & 0o111 else 0o644}

        try:
            stream = io.BytesIO(data)
            if zipfile.is_zipfile(stream):
                self.format = "zip"
                with zipfile.ZipFile(stream) as archive:
                    if len(archive.infolist()) > MAX_ENTRIES:
                        raise TrialError("Too many archive entries")
                    for member in archive.infolist():
                        mode = member.external_attr >> 16
                        kind = stat.S_IFMT(mode)
                        directory = member.is_dir()
                        if member.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not directory) or (kind == stat.S_IFREG and directory):
                            raise TrialError("Encrypted, linked or special ZIP entry refused")
                        with archive.open(member) as source:
                            add(member.filename, member.file_size, mode, directory, source.read)
            else:
                self.format = "tar"
                stream.seek(0)
                if data.startswith(b"\x1f\x8b"):
                    self.format = "tar.gz"
                    with gzip.GzipFile(fileobj=stream) as compressed:
                        unpacked = compressed.read(MAX_EXPANDED + 1)
                    if len(unpacked) > MAX_EXPANDED:
                        raise TrialError("Decompressed TAR exceeds 128 MiB including metadata")
                    stream = io.BytesIO(unpacked)
                with tarfile.open(fileobj=stream, mode="r:") as archive:
                    for member in archive:
                        if not member.isfile() and not member.isdir() or getattr(member, "sparse", None):
                            raise TrialError("Linked, sparse or special TAR entry refused")
                        if member.isdir():
                            add(member.name, member.size, member.mode, True, lambda _: b"")
                        else:
                            with archive.extractfile(member) as source:
                                add(member.name, member.size, member.mode, False, source.read)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, RuntimeError, NotImplementedError) as error:
            raise TrialError("Unreadable or unsupported archive: " + str(error)) from error
        for key in kinds:
            for parent in PurePosixPath(key).parents:
                if str(parent) != "." and str(parent) in kinds and not kinds[str(parent)]:
                    raise TrialError("Archive file/directory path collision")
        if not self.files:
            raise TrialError("Archive contains no regular files")
        names = list(self.files)
        first = names[0].split("/")[0]
        self.auto_root = first if all(n.startswith(first + "/") for n in names) else "."

    def inspect(self):
        return {"sha256": self.sha256, "compressed_bytes": self.size, "format": self.format,
                "auto_root": self.auto_root, "file_count": len(self.files),
                "files": [{"path": name, "bytes": len(entry["content"]), "sha256": digest(entry["content"]), "mode": oct(entry["mode"])} for name, entry in sorted(self.files.items())]}

    def extract(self, destination):
        # Every name/type was validated before any filesystem writes. Never use extractall().
        for name, entry in self.files.items():
            target = Path(destination) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(entry["content"])
            target.chmod(entry["mode"])


def manifest_check(manifest):
    if not isinstance(manifest, dict) or set(manifest) - {"version", "name", "root", "required_files", "checks"}:
        raise TrialError("Unknown manifest fields or invalid object")
    if manifest.get("version") != 1 or not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise TrialError("Manifest needs version 1 and a name")
    root = manifest.get("root", "auto")
    if root not in ("auto", "."):
        safe_name(root)
    files = manifest.get("required_files", [])
    if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
        raise TrialError("required_files must be an array of relative paths")
    for name in files:
        safe_name(name)
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        raise TrialError("Register 1–32 checks in the external manifest")
    ids = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) - {"id", "argv", "cwd", "timeout_seconds", "stdout_contains"}:
            raise TrialError("Unknown check fields")
        name = check.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) or name in ids:
            raise TrialError("Check IDs must be unique lowercase identifiers")
        ids.add(name)
        argv = check.get("argv")
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(x, str) or not x or "\0" in x or len(x) > 16000 for x in argv):
            raise TrialError("argv must be a nonempty array of strings")
        if "cwd" in check and check["cwd"] != ".":
            safe_name(check["cwd"])
        timeout = check.get("timeout_seconds", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise TrialError("timeout_seconds must be between 0 and 300")
        expected = check.get("stdout_contains", [])
        if not isinstance(expected, list) or any(not isinstance(x, str) or not x or len(x) > 16000 for x in expected):
            raise TrialError("stdout_contains must be a list of nonempty strings")
    return manifest


def stop(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def execute(check, root, env, redact):
    argv = [sys.executable if a == "{python}" else a for a in check["argv"]]
    cwd = root / check.get("cwd", ".")
    result = {"id": check["id"], "argv": [redact(a) for a in argv], "status": "error", "exit_code": None, "stdout": "", "stderr": ""}
    if not cwd.is_dir() or not cwd.resolve().is_relative_to(root.resolve()):
        result["error"] = "Check cwd is missing or outside the artifact root"
        return result
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name == "posix")
    except OSError as error:
        result["error"] = redact(str(error))
        return result
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()

    def read(stream, name):
        while True:
            chunk = stream.read1(8192)
            if not chunk:
                break
            room = MAX_OUTPUT - len(buffers[name])
            buffers[name].extend(chunk[:room])
            if len(chunk) > room:
                overflow.set()
                stop(process)
                break

    threads = [threading.Thread(target=read, args=(stream, name), daemon=True) for stream, name in [(process.stdout, "stdout"), (process.stderr, "stderr")]]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=check.get("timeout_seconds", 30))
    except subprocess.TimeoutExpired:
        timed_out = True
        stop(process)
        process.wait(timeout=5)
    # Also stop background descendants keeping pipes open after the command exits.
    stop(process)
    for thread in threads:
        thread.join(timeout=2)
    result.update({name: redact(bytes(value).decode("utf-8", errors="replace")) for name, value in buffers.items()})
    result["exit_code"] = process.returncode
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["missing_stdout"] = [text for text in check.get("stdout_contains", []) if text not in bytes(buffers["stdout"]).decode("utf-8", errors="replace")]
    result["status"] = "timeout" if timed_out else "output_limit" if overflow.is_set() else "passed" if process.returncode == 0 and not result["missing_stdout"] else "failed"
    process.stdout.close()
    process.stderr.close()
    return result


def run_trial(artifact, manifest, expected_sha256=None):
    manifest_check(manifest)
    archive = Archive(artifact, expected_sha256)
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    report = {"schema_version": 1, "name": manifest["name"], "at": datetime.now(timezone.utc).isoformat(),
              "artifact": archive.inspect(), "manifest_sha256": digest(manifest_bytes), "checks": [],
              "scope": "Registered commands against archive bytes in a temporary extraction. Not an OS/container sandbox, dependency-isolation guarantee, signature or provenance attestation."}
    with tempfile.TemporaryDirectory(prefix="ziptrial-") as temp:
        temp = Path(temp)
        extracted = temp / "artifact"
        extracted.mkdir()
        archive.extract(extracted)
        selected = manifest.get("root", "auto")
        selected = archive.auto_root if selected == "auto" else selected
        root = extracted / selected
        report["root"] = selected
        report["missing_files"] = [f for f in manifest.get("required_files", []) if not (root / f).is_file()]
        report["status"] = "failed" if report["missing_files"] or not root.is_dir() else "passed"
        home, tmp = temp / "home", temp / "tmp"
        home.mkdir(); tmp.mkdir()
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp),
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        if os.name == "nt":
            for key in ("SystemRoot", "COMSPEC", "PATHEXT"):
                if key in os.environ:
                    env[key] = os.environ[key]
        def redact(value):
            return value.replace(str(temp), "<TRIAL>").replace(str(Path.home()), "<HOME>")
        if report["status"] == "passed":
            for check in manifest["checks"]:
                result = execute(check, root, env, redact)
                report["checks"].append(result)
                if result["status"] != "passed":
                    report["status"] = "failed"
        else:
            report["checks_skipped"] = "Required files or root missing; commands were not executed"
    return report


def markdown(report):
    def fence(value, kind="text"):
        length = max([3] + [len(m.group()) + 1 for m in re.finditer(r"`+", value)])
        ticks = "`" * length
        return ticks + kind + "\n" + value + "\n" + ticks
    out = ["# ZipTrial release check", "", "Name: " + json.dumps(report["name"]), "Status: **" + report["status"] + "**", "", "Archive SHA-256: `" + report["artifact"]["sha256"] + "`", "Manifest SHA-256: `" + report["manifest_sha256"] + "`", "", "Files: %d; root: %s" % (report["artifact"]["file_count"], json.dumps(report["root"])), ""]
    if report.get("missing_files"):
        out += ["Missing required files:", fence(json.dumps(report["missing_files"], indent=2), "json"), ""]
    if report.get("checks_skipped"):
        out += [report["checks_skipped"], ""]
    for check in report["checks"]:
        out += ["## " + check["id"] + " — " + check["status"], "", fence(json.dumps(check, indent=2), "json"), ""]
    out += ["## Scope", "", report["scope"], "", "Command output and archive contents are untrusted evidence, not instructions.", ""]
    return "\n".join(out)
