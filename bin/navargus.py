#!/usr/bin/env python
#
# Copyright (C) 2020 UNINETT
#
# This file is part of Network Administration Visualized (NAV).
#
# NAV is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.  You should have received a copy of the GNU General Public License
# along with NAV. If not, see <http://www.gnu.org/licenses/>.
#
"""NAV Event Engine -> Argus Exporter

Exports events from NAV's Event Engine streaming interface into an Argus server.

JSON parsing inspired by https://stackoverflow.com/a/58442063
"""
import sys
import os
import fcntl
import re
import logging
from json import JSONDecoder, JSONDecodeError

import requests


_logger = logging.getLogger("navae")
ARGUS_API_URL = ""
ARGUS_API_TOKEN = ""
ARGUS_HEADERS = {
    "Authorization": "Token " + ARGUS_API_TOKEN,
    "Content-Type": "text/plain",
}
NOT_WHITESPACE = re.compile(r"[^\s]")


def main():
    """Main execution point"""
    logging.basicConfig(level=logging.DEBUG)

    # Ensure we do non-blocking reads from stdin, as we don't wont to get stuck when
    # we receive blobs that are smaller than the set buffer size
    fd = sys.stdin.fileno()
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

    for alert in emit_json_objects_from(sys.stdin):
        dispatch_alert_to_argus(alert)


def emit_json_objects_from(stream, buf_size=1024, decoder=JSONDecoder()):
    """Generates a sequence of objects based on a stream of stacked JSON blobs.

    BUGS: If the stream ever emits anything that is not valid JSON in between the
    emitted whitespace, this entire code breaks down, since it always tries to decode
    the whole concatenated buffer for every block received.

    :param stream: Any file-like object.
    :param buf_size: The buffer size to use when reading from the stream.
    :param decoder: The decoder object to use for decoding data read from the stream.
    :type decoder: JSONDecoder
    """
    buffer = ""
    error = None
    while True:
        block = stream.read(buf_size)
        if not block:
            continue
        buffer += block
        pos = 0
        while True:
            match = NOT_WHITESPACE.search(buffer, pos)
            if not match:
                break
            pos = match.start()
            try:
                obj, pos = decoder.raw_decode(buffer, pos)
            except JSONDecodeError as err:
                error = err
                break
            else:
                error = None
                yield obj
        buffer = buffer[pos:]
    if error is not None:
        raise error


def dispatch_alert_to_argus(alert):
    """Dispatches an alert structure to an Argus instance via its REST API"""
    requests.post(url=ARGUS_API_URL + "/alerts/", headers=ARGUS_HEADERS, json=alert)


if __name__ == "__main__":
    main()
