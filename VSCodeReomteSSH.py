import argparse
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
from pathlib import Path
from typing import Optional
from utils import (
    download_with_progress,
    get_windows_commit_id,
    judge_system,
    open_download_url,
    users_root,
    validate_commit_id,
    wait_for_download,
)


def verify_vscode_server(path: Path) -> bool:
    """
    校验解压后的 vscode-server 目录结构是否符合 VS Code Remote SSH 的要求：文件必须直接位于 path 下
    """
    if not path.is_dir():
        print(f"VSCode server directory does not exist: {path}")
        return False

    required_files = (
        "bin/code-server",
        "bin/helpers/check-requirements.sh",
        "node",
        "product.json",
    )
    missing = [name for name in required_files if not (path / name).exists()]
    if not missing:
        return True

    nested = sorted(p.name for p in path.iterdir() if p.is_dir() and p.name.startswith("vscode-server-linux"))
    hint = f" (files were extracted into {path / nested[0]} instead)" if nested else ""
    print(f"VSCode server layout is invalid in {path}: missing {', '.join(missing)}{hint}")
    return False


def search_vscode_server(commit_id: str) -> Optional[Path]:
    """
    从各用户目录里面找寻是否有同 commit_id 的 vscode-server，如果有则返回该路径，否则返回 None
    """
    validate_commit_id(commit_id)

    for p in users_root().iterdir():
        vscode_server_path = p / ".vscode-server" / "bin"
        if p.is_dir() and vscode_server_path.exists():
            for path in vscode_server_path.iterdir():
                if path.is_dir() and path.name == commit_id:
                    return path
    return None


def mv_vscode_server(source_path: Path):
    """
    将指定路径的 vscode-server_path / commit_id 移动到当前用户的 .vscode-server/bin/ 目录下
    """
    if not source_path or not source_path.is_dir():
        raise ValueError(f"Source path does not exist or is not a directory: {source_path}")

    target_path = Path.home() / ".vscode-server" / "bin" / source_path.name
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if source_path.resolve() == target_path.resolve():
        print(f"VSCode server is already in {target_path}.")
        return target_path
    if target_path.exists():
        raise FileExistsError(f"Target path already exists: {target_path}")

    try:
        shutil.move(str(source_path), str(target_path))
        print(f"Moved VSCode server from {source_path} to {target_path}.")
        return target_path
    except OSError as error:
        print(f"Failed to move VSCode server: {error}")
        raise


def download_vscode_server(commit_id: str):
    """
    下载指定 commit_id 的 vscode-server 到当前用户的 .vscode-server/bin/ 目录下
    """
    validate_commit_id(commit_id)

    target_path = Path.home() / ".vscode-server" / "bin" / commit_id
    target_path.parent.mkdir(parents=True, exist_ok=True)
    download_url = (
        f"https://update.code.visualstudio.com/commit:{commit_id}/"
        "server-linux-x64/stable"
    )
    archive_path = target_path.parent / f"{commit_id}.tar.gz"

    try:
        print(f"Downloading VSCode server from {download_url}...")
        download_with_progress(download_url, archive_path)

        staging_path = Path(tempfile.mkdtemp(prefix=f".{commit_id}.", dir=target_path.parent))
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                for member in archive.getmembers():
                    member_path = (staging_path / member.name).resolve()
                    if not member_path.is_relative_to(staging_path.resolve()):
                        raise ValueError(f"Unsafe archive member: {member.name}")
                    if member.issym() or member.islnk():
                        raise ValueError(f"Links are not allowed in archive: {member.name}")
                archive.extractall(staging_path)
            entries = list(staging_path.iterdir())
            extracted_path = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging_path
            target_path.mkdir(exist_ok=False)
            for entry in extracted_path.iterdir():
                shutil.move(str(entry), str(target_path / entry.name))
        finally:
            shutil.rmtree(staging_path, ignore_errors=True)

        if not verify_vscode_server(target_path):
            raise ValueError(f"VSCode server layout is invalid: {target_path}")
        print(f"VSCode server downloaded to {target_path}.")
        return target_path
    except (OSError, urllib.error.URLError, tarfile.TarError, ValueError) as error:
        print(f"Failed to download VSCode server: {error}")
        raise
    finally:
        archive_path.unlink(missing_ok=True)


def mv_from_windows_to_linux(commit_id: str, ssh_host: str):
    """
    从 Windows 下载 Linux 版 VSCode Server，并通过 SSH 传输到 Linux
    """
    validate_commit_id(commit_id)
    if not ssh_host:
        raise ValueError("SSH host is required on Windows.")

    download_url = (
        f"https://update.code.visualstudio.com/commit:{commit_id}/"
        "server-linux-x64/stable"
    )
    remote_archive = f"/tmp/vscode-server-{commit_id}.tar.gz"
    remote_target = f"$HOME/.vscode-server/bin/{commit_id}"

    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / f"{commit_id}.tar.gz"
        try:
            download_directory = Path.home() / "Downloads"
            download_directory.mkdir(parents=True, exist_ok=True)
            existing_files = set(download_directory.iterdir())
            print("Select download method:")
            print("1. Download through browser and monitor Downloads")
            print("2. Monitor an existing/new download in Downloads")
            print("3. Download through command line")
            choice = input("Enter 1, 2, or 3 [3]: ").strip() or "3"

            if choice == "1":
                print("Opening the browser. Please keep the download in Downloads.")
                open_download_url(download_url)
                downloaded_file = wait_for_download(
                    download_directory,
                    existing_files,
                )
                shutil.copy2(downloaded_file, archive_path)
            elif choice == "2":
                print("Waiting for a completed download in Downloads...")
                downloaded_file = wait_for_download(
                    download_directory,
                    existing_files,
                )
                shutil.copy2(downloaded_file, archive_path)
            elif choice == "3":
                print(f"Downloading VSCode server from {download_url}...")
                download_with_progress(download_url, archive_path)
            else:
                raise ValueError("Invalid choice. Please enter 1, 2, or 3.")

            print(f"Copying VSCode server to {ssh_host}...")
            subprocess.run(
                ["scp", str(archive_path), f"{ssh_host}:{remote_archive}"],
                check=True,
            )

            remote_command = (
                f"mkdir -p \"{remote_target}\" && "
                f"tar -xzf {shlex.quote(remote_archive)} "
                f"--strip-components=1 -C \"{remote_target}\" && "
                f"rm -f {shlex.quote(remote_archive)} && "
                f"test -f \"{remote_target}/bin/code-server\" && "
                f"test -f \"{remote_target}/bin/helpers/check-requirements.sh\" && "
                f"test -f \"{remote_target}/node\" && "
                f"test -f \"{remote_target}/product.json\""
            )
            subprocess.run(
                ["ssh", ssh_host, remote_command],
                check=True,
            )
            print(f"VSCode server was installed on {ssh_host}:{remote_target}.")
        except (
            OSError,
            urllib.error.URLError,
            subprocess.CalledProcessError,
            TimeoutError,
            ValueError,
        ) as error:
            print(f"Failed to transfer VSCode server: {error}")
            raise


def main():
    parser = argparse.ArgumentParser(description="VSCode Remote SSH Solution.")
    parser.add_argument("--commitId", type=str, help="Commit ID of the VSCode server")
    parser.add_argument(
        "--sshHost",
        type=str,
        help="SSH config Host alias or user@host used when running on Windows",
    )
    args = parser.parse_args()

    system = judge_system()
    if not args.commitId and system == "Windows":
        try:
            args.commitId = get_windows_commit_id()
            print(f"Detected VS Code commit ID: {args.commitId}")
        except (EnvironmentError, ValueError) as error:
            parser.error(str(error))

    if not args.commitId:
        parser.error("Commit ID cannot be None.")

    if system != "Linux":
        print("The script is going to download the VSCode server for the specified commit ID from Windows to Linux.")
        mv_from_windows_to_linux(args.commitId, args.sshHost)
        return

    search_vscode_server_path = search_vscode_server(args.commitId)
    if search_vscode_server_path:
        print(f"Found VSCode server for commit ID {args.commitId} at {search_vscode_server_path}.")
        print(f"Moving VSCode server for commit ID {args.commitId} to current user...")
        mv_vscode_server(search_vscode_server_path)
    else:
        print(f"No VSCode server found for commit ID {args.commitId}.")
        print(f"Starting to download VSCode server for commit ID {args.commitId}...")
        download_vscode_server(args.commitId)


if __name__ == "__main__":
    main()
