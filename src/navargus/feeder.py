#!/usr/bin/env python3
"""This program just tests submitting some empty data to a navargus process and exiting.

It serves to verify that navargus is able to identify that its input has gone away,
so it can exit rather than fall into a tight loop.
"""
import subprocess
import os
import sys


def main():
    environ = {**os.environ, "NAV_LOGGING_CONF": "logging.conf"}
    proc = subprocess.Popen(
        ["navargus"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, env=environ
    )
    proc.stdin.write(b"{}\n\n")
    proc.stdin.flush()
    sys.exit()


if __name__ == "__main__":
    main()
