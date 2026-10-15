import io
import gzip
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from ziptrial.core import Archive, TrialError, digest, manifest_check, markdown, run_trial


class ZipTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def archive(self, files, name="artifact.zip"):
        target = self.root / name
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, data in files.items():
                archive.writestr(path, data)
        return target

    def manifest(self, code="print('artifact works')", **kwargs):
        return {"version": 1, "name": "fixture", "required_files": ["app.py"], "checks": [{"id": "smoke", "argv": ["{python}", "-I", "app.py"], "stdout_contains": ["artifact works"], **kwargs}]}

    def test_archive_inspection_hashes_inventory_without_execution(self):
        marker = self.root / "executed"
        artifact = self.archive({"pkg/app.py": "from pathlib import Path;Path(%r).touch()" % str(marker)})
        archive = Archive(artifact)
        self.assertFalse(marker.exists())
        self.assertEqual(archive.sha256, digest(artifact.read_bytes()))
        self.assertEqual(archive.inspect()["files"][0]["path"], "pkg/app.py")
        self.assertEqual(archive.auto_root, "pkg")

    def test_success_runs_from_extraction_with_empty_home_and_without_caller_secret(self):
        code = "import os;from pathlib import Path;assert 'ZIPTRIAL_TEST_SECRET' not in os.environ;assert not Path('.git').exists();assert not list(Path.home().iterdir());print('artifact works')"
        artifact = self.archive({"pkg/app.py": code})
        with mock.patch.dict(os.environ, {"ZIPTRIAL_TEST_SECRET": "do-not-copy"}):
            report = run_trial(artifact, self.manifest())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["checks"][0]["exit_code"], 0)
        self.assertNotIn(self.temp.name, json.dumps(report))
        self.assertIn("Status: **passed**", markdown(report))

    def test_broken_download_fails_even_when_checkout_file_exists(self):
        (self.root / "missing.py").write_text("value=7")
        artifact = self.archive({"pkg/app.py": "from missing import value\nprint('artifact works')"})
        report = run_trial(artifact, self.manifest())
        self.assertEqual(report["status"], "failed")
        self.assertIn("ModuleNotFoundError", report["checks"][0]["stderr"])

    def test_required_file_failure_never_runs_commands(self):
        marker = self.root / "should-not-exist"
        artifact = self.archive({"pkg/README.md": "missing app"})
        manifest = self.manifest()
        manifest["checks"][0]["argv"] = ["{python}", "-c", "open(%r,'w').close()" % str(marker)]
        report = run_trial(artifact, manifest)
        self.assertEqual(report["missing_files"], ["app.py"])
        self.assertEqual(report["checks"], [])
        self.assertFalse(marker.exists())

    def test_unexpected_stdout_and_failed_exit_do_not_pass(self):
        artifact = self.archive({"app.py": "print('wrong result')"})
        report = run_trial(artifact, self.manifest())
        self.assertEqual(report["checks"][0]["status"], "failed")
        self.assertEqual(report["checks"][0]["missing_stdout"], ["artifact works"])
        artifact = self.archive({"app.py": "print('artifact works');raise SystemExit(9)"}, "failed.zip")
        self.assertEqual(run_trial(artifact, self.manifest())["checks"][0]["exit_code"], 9)

    def test_timeouts_and_output_floods_are_bounded(self):
        artifact = self.archive({"app.py": "import time;time.sleep(30)"})
        start = time.monotonic()
        self.assertEqual(run_trial(artifact, self.manifest(timeout_seconds=0.1))["checks"][0]["status"], "timeout")
        self.assertLess(time.monotonic() - start, 6)
        artifact = self.archive({"app.py": "import sys;sys.stdout.write('a'*1000000)"}, "flood.zip")
        check = run_trial(artifact, self.manifest())["checks"][0]
        self.assertEqual(check["status"], "output_limit")
        self.assertLessEqual(len(check["stdout"]), 128*1024)

    def test_missing_executable_is_error_not_success(self):
        artifact = self.archive({"app.py": "print('artifact works')"})
        manifest = self.manifest()
        manifest["checks"][0]["argv"] = ["ziptrial-deliberately-nonexistent-command"]
        check = run_trial(artifact, manifest)["checks"][0]
        self.assertEqual(check["status"], "error")
        self.assertIsNone(check["exit_code"])

    @unittest.skipUnless(os.name == "posix", "Process-group cleanup is supported on POSIX")
    def test_background_descendants_do_not_outlive_successful_command(self):
        marker = self.root / "background-survived"
        child = "import time;from pathlib import Path;time.sleep(0.8);Path(%r).touch()" % str(marker)
        code = "import subprocess,sys;subprocess.Popen([sys.executable,'-c',%r]);print('artifact works')" % child
        artifact = self.archive({"app.py": code})
        self.assertEqual(run_trial(artifact, self.manifest())["status"], "passed")
        time.sleep(1)
        self.assertFalse(marker.exists())

    def test_sha256_mismatch_refuses_execution(self):
        artifact = self.archive({"app.py": "print('artifact works')"})
        with self.assertRaisesRegex(TrialError, "SHA-256"):
            run_trial(artifact, self.manifest(), "0"*64)
        self.assertEqual(run_trial(artifact, self.manifest(), digest(artifact.read_bytes()))["status"], "passed")

    def test_traversal_absolute_windows_and_git_paths_refused(self):
        for name in ["../escape", "/absolute", "pkg/../../escape", "pkg\\escape", "C:/escape", "pkg/./app", "pkg//app", "pkg/.git/config", "pkg/CON.txt", "pkg/trailing.", "pkg/control\nfile"]:
            with self.subTest(name=name):
                artifact = self.archive({name: "x"})
                with self.assertRaises(TrialError):
                    Archive(artifact)

    def test_case_unicode_duplicate_and_prefix_collisions_refused(self):
        for files in [{"a.py": "x", "A.py": "x"}, {"Pkg/a": "x", "pkg/b": "x"}, {"caf\u00e9/a": "x", "cafe\u0301/b": "x"}, {"a": "file", "a/b": "child"}]:
            with self.subTest(files=files):
                with self.assertRaises(TrialError):
                    Archive(self.archive(files))
        artifact = self.root / "duplicate.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("app.py", "one")
            with self.assertWarns(UserWarning):
                archive.writestr("app.py", "two")
        with self.assertRaises(TrialError):
            Archive(artifact)

    def test_zip_symlink_refused_and_executable_mode_retained(self):
        artifact = self.root / "link.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        with self.assertRaises(TrialError):
            Archive(artifact)
        with zipfile.ZipFile(artifact, "w") as archive:
            info = zipfile.ZipInfo("tool.sh")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(info, "#!/bin/sh\n")
        output = self.root / "extracted"; output.mkdir()
        Archive(artifact).extract(output)
        self.assertTrue((output / "tool.sh").stat().st_mode & stat.S_IXUSR)

    def test_tar_gzip_and_hardlink_rejection(self):
        artifact = self.root / "source.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            content = b"print('artifact works')"
            info = tarfile.TarInfo("package/app.py"); info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        self.assertEqual(run_trial(artifact, self.manifest())["status"], "passed")
        with tarfile.open(artifact, "w:gz") as archive:
            info = tarfile.TarInfo("link"); info.type = tarfile.LNKTYPE; info.linkname = "/outside"
            archive.addfile(info)
        with self.assertRaises(TrialError):
            Archive(artifact)

    def test_size_limits_and_invalid_archives(self):
        artifact = self.archive({"app.py": "x" * 100})
        with mock.patch("ziptrial.core.MAX_FILE", 10):
            with self.assertRaises(TrialError):
                Archive(artifact)
        with mock.patch("ziptrial.core.MAX_EXPANDED", 10):
            with self.assertRaises(TrialError):
                Archive(artifact)
        artifact.write_bytes(gzip.compress(b"x" * 1000))
        with mock.patch("ziptrial.core.MAX_EXPANDED", 100):
            with self.assertRaisesRegex(TrialError, "Decompressed TAR"):
                Archive(artifact)
        artifact.write_bytes(b"not an archive")
        with self.assertRaises(TrialError):
            Archive(artifact)

    def test_manifest_rejects_unknown_fields_cwd_traversal_and_ambiguous_checks(self):
        for manifest in [dict(self.manifest(), unknown=True), dict(self.manifest(), checks=[]), dict(self.manifest(), root="../outside")]:
            with self.assertRaises(TrialError):
                manifest_check(manifest)
        for change in [{"cwd": "../outside"}, {"timeout_seconds": 0}, {"argv": "python app.py"}, {"stdout_contains": "text"}]:
            manifest = self.manifest(); manifest["checks"][0].update(change)
            with self.assertRaises(TrialError):
                manifest_check(manifest)

    def test_cli_outputs_reports_and_refuses_overwrite(self):
        artifact = self.archive({"app.py": "print('artifact works')"})
        manifest = self.root / "manifest.json"; manifest.write_text(json.dumps(self.manifest()))
        output = self.root / "result"
        argv = [sys.executable, "-m", "ziptrial", "run", str(artifact), "--manifest", str(manifest), "--output", str(output)]
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((output / "report.json").is_file())
        self.assertEqual(json.loads((output / "report.json").read_text())["status"], "passed")
        self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 2)
        artifact = self.archive({"app.py": "raise SystemExit(1)"})
        argv[-1] = str(self.root / "failed")
        self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
