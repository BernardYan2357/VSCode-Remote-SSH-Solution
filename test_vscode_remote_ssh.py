import contextlib
import io
import itertools
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import utils
import VSCodeReomteSSH as remote


COMMIT = "69047191fc427f2f92828279aae1d90d8eeda7ab"
OTHER_COMMIT = "a" * 40


def write_archive(path, commit=COMMIT, architecture="x64"):
    content = json.dumps({"commit": commit}).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(f"vscode-server-linux-{architecture}/product.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
        for name, data in (
            ("node", b"node"),
            ("bin/code-server", b"#!/bin/sh\n"),
            ("bin/helpers/check-requirements.sh", b"#!/bin/sh\n"),
        ):
            member = tarfile.TarInfo(f"vscode-server-linux-{architecture}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


class DownloadTestCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.downloads = self.home / "Downloads"
        self.downloads.mkdir()
        self.sleep = self.enterContext(patch("utils.time.sleep"))
        self.enterContext(
            patch("utils.time.monotonic", side_effect=itertools.count())
        )

    def wait(self, excluded=None):
        return utils.wait_for_download(
            self.downloads, excluded or set(), timeout=20, commit_id=COMMIT
        )


class DownloadTests(DownloadTestCase):
    def test_existing_matching_archive_is_detected(self):
        archive = self.downloads / "vscode-server-linux-x64.tar.gz"
        write_archive(archive)
        self.assertEqual(self.wait(), archive)
        self.assertEqual(self.sleep.call_count, 2)

    def test_new_matching_archive_is_detected(self):
        archive = self.downloads / "server.tar.gz"
        self.sleep.side_effect = lambda _: (
            write_archive(archive) if not archive.exists() else None
        )
        self.assertEqual(self.wait(), archive)

    def test_wrong_commit_and_invalid_archive_are_skipped(self):
        write_archive(self.downloads / "old.tar.gz", OTHER_COMMIT)
        (self.downloads / "invalid.tar.gz").write_bytes(b"incomplete archive")
        (self.downloads / "browser.tar.gz.crdownload").write_bytes(b"downloading")
        matching = self.downloads / "matching.tgz"

        def download_matching_archive(_):
            if self.sleep.call_count == 3:
                write_archive(matching)

        self.sleep.side_effect = download_matching_archive
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.wait(), matching)
        self.assertIn(OTHER_COMMIT, output.getvalue())
        self.assertIn("invalid.tar.gz", output.getvalue())

    def test_changed_archive_is_checked_again(self):
        archive = self.downloads / "server.tar.gz"
        write_archive(archive, OTHER_COMMIT)

        def finish_download(_):
            if self.sleep.call_count == 3:
                write_archive(archive)

        self.sleep.side_effect = finish_download
        self.assertEqual(self.wait(), archive)

    def test_browser_partial_file_prevents_early_selection(self):
        archive = self.downloads / "server.tar.gz"
        write_archive(archive)
        partial = self.downloads / "server.tar.gz.part"
        partial.touch()

        def finish_download(_):
            if self.sleep.call_count == 3:
                partial.unlink()

        self.sleep.side_effect = finish_download
        self.assertEqual(self.wait(), archive)
        self.assertGreaterEqual(self.sleep.call_count, 3)

    def test_only_wrong_commit_times_out(self):
        write_archive(self.downloads / "old.tar.gz", OTHER_COMMIT)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(TimeoutError, COMMIT):
                self.wait()
        self.assertEqual(output.getvalue().count("Skipping"), 1)
        self.assertEqual(output.getvalue().count("WARNING:"), 1)
        self.assertIn("old.tar.gz", output.getvalue())
        self.assertIn(f"found {OTHER_COMMIT}", output.getvalue())
        self.assertIn(f"expected {COMMIT}", output.getvalue())

    def transfer(self, choice, expected_download, open_browser=None):
        uploaded_commits = []

        def run(command, **kwargs):
            if command[0] == "scp":
                uploaded_commits.append(
                    utils.get_vscode_server_commit_id(Path(command[1]))
                )

        def wait(directory, excluded, **kwargs):
            downloaded = utils.wait_for_download(directory, excluded, timeout=20, **kwargs)
            self.assertEqual(downloaded, expected_download)
            return downloaded

        with (
            patch.object(Path, "home", return_value=self.home),
            patch("builtins.input", return_value=choice),
            patch.object(remote, "open_download_url", side_effect=open_browser),
            patch.object(remote, "wait_for_download", side_effect=wait),
            patch.object(remote.subprocess, "run", side_effect=run) as run_mock,
        ):
            remote.mv_from_windows_to_linux(COMMIT, "test-host", "x64")
        self.assertEqual(uploaded_commits, [COMMIT])
        self.assertEqual([call.args[0][0] for call in run_mock.call_args_list], ["scp", "ssh"])

    def test_option_two_uploads_existing_matching_archive(self):
        archive = self.downloads / "server.tar.gz"
        write_archive(archive)
        self.transfer("2", archive)

    def test_option_one_uses_new_download(self):
        old = self.downloads / "old.tar.gz"
        write_archive(old)
        new = self.downloads / "new.tar.gz"
        self.transfer("1", new, open_browser=lambda _: write_archive(new))

    def test_missing_commit_is_rejected(self):
        archive = self.downloads / "server.tar.gz"
        write_archive(archive, None)
        with self.assertRaisesRegex(ValueError, "no valid commit ID"):
            utils.get_vscode_server_commit_id(archive)

    def test_option_three_rejects_wrong_commit_before_upload(self):
        with (
            patch.object(Path, "home", return_value=self.home),
            patch("builtins.input", return_value="3"),
            patch.object(
                remote, "download_with_progress",
                side_effect=lambda url, path: write_archive(path, OTHER_COMMIT),
            ),
            patch.object(remote.subprocess, "run") as run_mock,
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                remote.mv_from_windows_to_linux(COMMIT, "test-host", "x64")
        run_mock.assert_not_called()


class ArchitectureTests(DownloadTestCase):
    def test_automatic_detection_uses_remote_instead_of_windows_architecture(self):
        with (
            patch("utils.platform.machine", return_value="AMD64") as local_machine,
            patch("utils.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 0, stdout="aarch64\n",
            )) as run,
        ):
            self.assertEqual(utils.detect_server_architecture("rdk_s100"), "arm64")
        local_machine.assert_not_called()
        run.assert_called_once_with(
            ["ssh", "rdk_s100", "uname -m"], check=True, capture_output=True, text=True,
        )

    def test_automatic_local_detection(self):
        for machine, expected in (("x86_64", "x64"), ("aarch64", "arm64")):
            with self.subTest(machine=machine), patch("utils.platform.machine", return_value=machine):
                self.assertEqual(utils.detect_server_architecture(), expected)
        with patch("utils.platform.machine", return_value="riscv64"):
            with self.assertRaisesRegex(ValueError, "Unsupported architecture"):
                utils.detect_server_architecture()

    def test_remote_detection_failure_does_not_fall_back_to_x64(self):
        with patch("utils.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ssh")):
            with self.assertRaises(subprocess.CalledProcessError):
                utils.detect_server_architecture("rdk_s100")

    def test_windows_automatic_download_and_explicit_override(self):
        for explicit, expected in ((None, "arm64"), ("arm64", "arm64"), ("x64", "x64")):
            with (
                self.subTest(explicit=explicit),
                patch.object(Path, "home", return_value=self.home),
                patch("builtins.input", return_value="3"),
                patch.object(remote, "detect_server_architecture", return_value="arm64") as detect,
                patch.object(remote, "download_with_progress", side_effect=lambda url, path:
                    write_archive(path, architecture=expected)) as download,
                patch.object(remote.subprocess, "run") as run,
            ):
                remote.mv_from_windows_to_linux(COMMIT, "rdk_s100", explicit)
                self.assertIn(f"/server-linux-{expected}/stable", download.call_args.args[0])
                self.assertEqual([call.args[0][0] for call in run.call_args_list], ["scp", "ssh"])
                if explicit is None:
                    detect.assert_called_once_with("rdk_s100")
                else:
                    detect.assert_not_called()

    def test_linux_download_uses_local_arm64_architecture(self):
        with (
            patch.object(Path, "home", return_value=self.home),
            patch("utils.platform.machine", return_value="aarch64"),
            patch.object(remote, "download_with_progress", side_effect=lambda url, path:
                write_archive(path, architecture="arm64")) as download,
        ):
            installed = remote.download_vscode_server(COMMIT)
        self.assertIn("/server-linux-arm64/stable", download.call_args.args[0])
        self.assertTrue(remote.verify_vscode_server(installed))

    def test_cli_accepts_only_supported_architectures(self):
        for architecture in ("x64", "arm64"):
            with (
                self.subTest(architecture=architecture),
                patch.object(sys, "argv", ["script", "--commitId", COMMIT,
                                          "--sshHost", "rdk_s100", "--arch", architecture]),
                patch.object(remote, "judge_system", return_value="Windows"),
                patch.object(remote, "mv_from_windows_to_linux") as transfer,
            ):
                remote.main()
                transfer.assert_called_once_with(COMMIT, "rdk_s100", architecture)
        for architecture in ("aarch64", "x86_64", "armhf"):
            with (
                self.subTest(architecture=architecture),
                patch.object(sys, "argv", ["script", "--arch", architecture]),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(remote, "mv_from_windows_to_linux") as transfer,
            ):
                with self.assertRaises(SystemExit) as error:
                    remote.main()
                self.assertEqual(error.exception.code, 2)
                transfer.assert_not_called()


class InterruptTests(unittest.TestCase):
    def test_interrupt_exits_without_traceback(self):
        script = Path(remote.__file__).resolve()
        for stage in ("menu", "monitor"):
            with self.subTest(stage=stage):
                code = f"""
import pathlib
import runpy
import sys
import tempfile
from unittest.mock import patch
sys.argv = [{str(script)!r}, '--commitId', {COMMIT!r}, '--sshHost', 'test-host', '--arch', 'x64']
with tempfile.TemporaryDirectory() as home, \\
     patch('utils.judge_system', return_value='Windows'), \\
     patch.object(pathlib.Path, 'home', return_value=pathlib.Path(home)), \\
     patch('builtins.input', side_effect=KeyboardInterrupt if {stage!r} == 'menu' else None, return_value='2'), \\
     patch('utils.wait_for_download', side_effect=KeyboardInterrupt):
    runpy.run_path({str(script)!r}, run_name='__main__')
"""
                result = subprocess.run(
                    [sys.executable, "-c", code], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 130, result.stderr)
                self.assertIn("Cancelled by user.", result.stdout)
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
