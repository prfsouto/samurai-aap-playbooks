"""Preserve Packer output bytes while rendering valid UTF-8 to Ansible."""

import argparse
import codecs
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_path")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a Packer command is required")

    # No creation: the role must first persist init/validate evidence to this file.
    with os.fdopen(os.open(args.log_path, os.O_WRONLY | os.O_APPEND), "wb") as raw_log:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        ) as process:
            assert process.stdout is not None
            while chunk := process.stdout.read(65536):
                raw_log.write(chunk)
                sys.stdout.buffer.write(decoder.decode(chunk).encode("utf-8"))
            sys.stdout.buffer.write(decoder.decode(b"", final=True).encode("utf-8"))
            sys.stdout.buffer.flush()
            return process.wait()


if __name__ == "__main__":
    sys.exit(main())
