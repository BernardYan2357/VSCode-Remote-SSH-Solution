import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import webbrowser
from pathlib import Path, PurePosixPath
from typing import Optional


SERVER_ARCHITECTURES = ("x64", "arm64")


def detect_server_architecture(ssh_host: Optional[str] = None) -> str:
    if ssh_host is None:
        machine = platform.machine()
    else:
        result = subprocess.run(
            ["ssh", ssh_host, "uname -m"], check=True, capture_output=True, text=True,
        )
        machine = result.stdout.strip()
    architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(machine)
    if architecture is None:
        raise ValueError(
            f"Unsupported architecture: {machine!r}. "
            "Specify --arch x64 or --arch arm64."
        )
    return architecture


def judge_system() -> str:
    system = platform.system()
    if system in {"Linux", "Windows"}:
        return system
    raise EnvironmentError(f"Unsupported operating system: {system}")


def validate_commit_id(commit_id: str) -> None:
    if not commit_id or not re.fullmatch(r"[A-Za-z0-9._-]+", commit_id):
        raise ValueError("Commit ID contains invalid characters.")


def get_windows_commit_id() -> str:
    code = shutil.which("code.cmd") or "code"
    try:
        result = subprocess.run(
            [code, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise EnvironmentError(
            "The 'code' command was not found. Add VS Code to PATH or provide --commitId."
        ) from error
    except subprocess.CalledProcessError as error:
        raise EnvironmentError(
            f"Failed to execute 'code --version': {error}"
        ) from error

    match = re.search(r"\b[0-9a-fA-F]{40}\b", result.stdout)
    if not match:
        raise ValueError(
            "Could not find a VS Code commit ID in the output of 'code --version'."
        )
    return match.group(0)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def get_vscode_server_commit_id(archive_path: Path) -> str:
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not member.isfile() or not (
                parts == ("product.json",)
                or (
                    len(parts) == 2
                    and parts[0].startswith("vscode-server-linux-")
                    and parts[1] == "product.json"
                )
            ):
                continue
            with archive.extractfile(member) as source:
                product = json.load(source)
            commit_id = product.get("commit") if isinstance(product, dict) else None
            if not isinstance(commit_id, str) or not commit_id:
                raise ValueError("VSCode server product.json has no valid commit ID.")
            return commit_id
    raise ValueError("VSCode server product.json was not found in the archive.")


def wait_for_download(
    download_directory: Path,
    existing_files: set[Path],
    timeout: int = 1800,
    *,
    commit_id: Optional[str] = None,
) -> Path:
    temporary_suffixes = {".crdownload", ".part", ".tmp"}
    deadline = time.monotonic() + timeout
    last_states: dict[Path, tuple[int, int]] = {}
    stable_counts: dict[Path, int] = {}
    checked_states: dict[Path, tuple[int, int]] = {}

    while time.monotonic() < deadline:
        for path in download_directory.iterdir():
            if (
                path in existing_files
                or not path.is_file()
                or path.suffix.lower() in temporary_suffixes
                or not path.name.lower().endswith((".tar.gz", ".tgz"))
            ):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            state = (stat.st_size, stat.st_mtime_ns)
            if state == last_states.get(path):
                stable_counts[path] = stable_counts.get(path, 0) + 1
                if stat.st_size > 0 and stable_counts[path] >= 2:
                    if any(
                        path.with_name(path.name + suffix).exists()
                        for suffix in temporary_suffixes
                    ):
                        continue
                    if checked_states.get(path) == state:
                        continue
                    checked_states[path] = state
                    if commit_id is not None:
                        try:
                            archive_commit = get_vscode_server_commit_id(path)
                        except (OSError, EOFError, tarfile.TarError, ValueError) as error:
                            print(f"Skipping {path.name}: {error}")
                            continue
                        if archive_commit != commit_id:
                            print(
                                f"WARNING: {path.name}: VSCode server commit ID mismatch "
                                f"(found {archive_commit}, expected {commit_id}). "
                                "Skipping this file; waiting for a matching download.",
                                flush=True,
                            )
                            continue
                        print(f"Found VSCode server for commit ID {archive_commit}: {path}")
                    return path
            else:
                last_states[path] = state
                stable_counts[path] = 0
        time.sleep(1)

    commit_hint = f" for commit ID {commit_id}" if commit_id else ""
    raise TimeoutError(
        f"No completed download{commit_hint} "
        f"found in {download_directory} within {timeout} seconds."
    )


def download_with_progress(url: str, output_path: Path) -> None:
    chunk_size = 1024 * 1024
    downloaded = 0
    start_time = time.monotonic()

    with urllib.request.urlopen(url, timeout=60) as response:
        content_length = response.headers.get("Content-Length")
        total_size = int(content_length) if content_length else None
        with output_path.open("wb") as output:
            while chunk := response.read(chunk_size):
                output.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.monotonic() - start_time, 0.001)
                speed = downloaded / elapsed / 1024 / 1024
                if total_size:
                    progress = downloaded / total_size * 100
                    remaining_size = total_size - downloaded
                    eta = format_duration(
                        remaining_size / (downloaded / elapsed)
                    )
                    message = (
                        f"\rDownloaded {progress:6.2f}% "
                        f"({downloaded / 1024 / 1024:.2f}/"
                        f"{total_size / 1024 / 1024:.2f} MB) "
                        f"at {speed:.2f} MB/s, ETA: {eta}"
                    )
                else:
                    message = (
                        f"\rDownloaded {downloaded / 1024 / 1024:.2f} MB "
                        f"at {speed:.2f} MB/s, ETA: unknown"
                    )
                print(message, end="", flush=True)
                sys.stdout.flush()
    print()


def open_download_url(url: str) -> None:
    if not webbrowser.open(url):
        raise OSError("Could not open the download URL in a browser.")


def users_root() -> Path:
    linux_users = Path("/home")
    return linux_users if linux_users.is_dir() else Path.home().parent
