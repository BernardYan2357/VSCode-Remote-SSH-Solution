import platform
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def judge_system() -> str:
    system = platform.system()
    if system in {"Linux", "Windows"}:
        return system
    raise EnvironmentError(f"Unsupported operating system: {system}")


def validate_commit_id(commit_id: str) -> None:
    if not commit_id or not re.fullmatch(r"[A-Za-z0-9._-]+", commit_id):
        raise ValueError("Commit ID contains invalid characters.")


def get_windows_commit_id() -> str:
    try:
        result = subprocess.run(
            ["code", "--version"],
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


def wait_for_download(
    download_directory: Path,
    existing_files: set[Path],
    timeout: int = 1800,
) -> Path:
    temporary_suffixes = {".crdownload", ".part", ".tmp"}
    deadline = time.monotonic() + timeout
    last_sizes: dict[Path, int] = {}
    stable_counts: dict[Path, int] = {}

    while time.monotonic() < deadline:
        for path in download_directory.iterdir():
            if (
                path in existing_files
                or not path.is_file()
                or path.suffix.lower() in temporary_suffixes
                or not path.name.lower().endswith((".tar.gz", ".tgz"))
            ):
                continue
            size = path.stat().st_size
            if size == last_sizes.get(path):
                stable_counts[path] = stable_counts.get(path, 0) + 1
                if size > 0 and stable_counts[path] >= 2:
                    return path
            else:
                last_sizes[path] = size
                stable_counts[path] = 0
        time.sleep(1)

    raise TimeoutError(
        f"No completed download found in {download_directory} within {timeout} seconds."
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
