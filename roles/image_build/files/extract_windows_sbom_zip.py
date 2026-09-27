"""Extract a Windows Packer SBOM archive into a Linux scan tree."""

import shutil
import stat
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def extract_windows_sbom_zip(archive: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError("unsafe SBOM destination")
    root = destination.resolve(strict=True)
    with ZipFile(archive) as source:
        members = []
        seen = set()
        for member in source.infolist():
            name = member.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            if (
                not name
                or name.startswith("/")
                or any(part in ("", ".", "..") for part in parts)
                or ":" in parts[0]
                or stat.S_ISLNK(member.external_attr >> 16)
                or tuple(parts) in seen
            ):
                raise ValueError("unsafe or duplicate Windows SBOM ZIP member")
            seen.add(tuple(parts))
            members.append((member, parts, name.endswith("/")))

        for member, parts, is_directory in members:
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(root) or target.is_symlink():
                raise ValueError("unsafe SBOM destination path")
            if is_directory:
                target.mkdir(exist_ok=True)
            else:
                with source.open(member) as stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: extract_windows_sbom_zip.py ARCHIVE DESTINATION")
    try:
        extract_windows_sbom_zip(Path(sys.argv[1]), Path(sys.argv[2]))
    except (BadZipFile, OSError, ValueError) as error:
        sys.exit(f"Windows SBOM ZIP extraction failed: {type(error).__name__}")
