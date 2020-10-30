#!/usr/bin/env python3
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
"""NAV Event Engine -> Argus Exporter - AKA Argus glue service for NAV.

Exports events from NAV's Event Engine streaming interface into an Argus server.

JSON parsing inspired by https://stackoverflow.com/a/58442063
"""
import select
import sys
import os
import fcntl
import re
import logging
import argparse
from datetime import datetime
from json import JSONDecoder, JSONDecodeError
from typing import Generator, Any

from django.urls import reverse
from pyargus.client import Client
from pyargus.models import Incident

from nav.bootstrap import bootstrap_django

bootstrap_django("navargus")

from nav.models.manage import Netbox, Interface
from nav.logs import init_stderr_logging
from nav.config import NAVConfigParser


_logger = logging.getLogger("navargus")
_client = None
_config = None
NOT_WHITESPACE = re.compile(r"[^\s]")
STATE_START = "s"
STATE_END = "e"
STATE_STATELESS = "x"
INFINITY = datetime.max


def main():
    """Main execution point"""
    global _config
    init_stderr_logging()
    _config = NAVArgusConfig()

    parser = parse_args()
    read_eventengine_stream()


def parse_args():
    """Builds an ArgumentParser and returns parsed program arguments"""
    parser = argparse.ArgumentParser(
        description="Synchronizes NAV alerts with an Argus alert aggregating server",
        usage="%(prog)s [options]",
        epilog="This program is designed to be run as an export script by NAV's event "
        "engine. See eventengine.conf for details.",
    )
    return parser.parse_args()


def read_eventengine_stream():
    """Reads a continuous stream of eventengine JSON blobs on stdin and updates the
    connected Argus server based on this.
    """
    # Ensure we do non-blocking reads from stdin, as we don't wont to get stuck when
    # we receive blobs that are smaller than the set buffer size
    _logger.info("Accepting eventengine stream data on stdin (pid=%s)", os.getpid())
    fd = sys.stdin.fileno()
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

    try:
        for alert in emit_json_objects_from(sys.stdin):
            dispatch_alert_to_argus(alert)
    except KeyboardInterrupt:
        _logger.info("Keyboard interrupt received, exiting")
        pass


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
        select.select([stream], [], [])
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


def dispatch_alert_to_argus(alert: dict):
    """Dispatches an alert structure to an Argus instance via its REST API"""
    state = alert.get("state")
    if state in (STATE_START, STATE_STATELESS):
        incident = convert_to_argus_incident(alert)
        post_incident_to_argus(incident)
    else:
        resolve_argus_incident(alert)


def convert_to_argus_incident(alert: dict) -> Incident:
    """
    :param alert: A JSON-serialized AlertQueue instance from NAV, in the form of a dict
    :returns: An object describing an Argus Incident, suitable for POSTing to its API.
    """
    state = alert.get("state", STATE_STATELESS)
    url = (
        reverse("event-details", args=(alert.get("history"),))
        if alert.get("history")
        else None
    )

    incident = Incident(
        start_time=alert.get("time"),
        end_time=INFINITY if state == STATE_START else None,
        source_incident_id=alert.get("history"),
        details_url=url if url else alert.get("alert_details_url"),
        description=alert.get("message"),
        tags=dict(build_tags_from(alert)),
    )

    return incident


def build_tags_from(alert: dict) -> Generator:
    """
    Generates a series of tag tuples
    :param alert: A JSON-serialized AlertQueue instance from NAV, in the form of a dict
    :returns: A generator of (tag_name, tag_value) tuples, suitable to make a tag
              dictionary for an Argus incident.
    """
    yield "event_type", alert.get("event_type", {}).get("id")
    yield "alert_type", alert.get("alert_type", {}).get("name")
    subject_type = alert.get("subject_type")

    # The JSON blob provided by eventengine does not drill deep into the data model,
    # so we will need to look up certain data from NAV itself to produce an accurate
    # set of tags:
    # TODO: Find a sane convention for translating various event subjects to tags, such
    #       as power supplies, modules etc.

    if alert.get("netbox"):
        netbox = Netbox.objects.get(pk=alert.get("netbox"))
        yield "host", netbox.sysname
        yield "room", netbox.room.id
        yield "location", netbox.room.location.id
    if subject_type == "Netbox":
        yield "host_url", alert.get("subject_url")
    elif subject_type == "Interface":
        interface = Interface.objects.get(pk=alert.get("subid"))
        yield "interface", interface.ifname


def post_incident_to_argus(incident: Incident) -> int:
    """Posts an incident payload to an Argus API instance"""
    client = get_argus_client()
    incident_response = client.post_incident(incident)
    if incident_response:
        return incident_response.pk


def resolve_argus_incident(alert: dict):
    """Looks up and resolves an existing incident in Argus based on the supplied
    end-state alert.
    """
    nav_alert_id = alert.get("history")
    if not nav_alert_id:
        return

    client = get_argus_client()
    incident = next(
        client.get_my_incidents(open=True, source_incident_id=nav_alert_id), None,
    )
    if incident:
        if incident.end_time != INFINITY:
            _logger.error("Cannot resolve a stateless incident")
            return

        client.resolve_incident(
            incident, description=alert.get("message"), timestamp=alert.get("time")
        )
    else:
        _logger.warning("Couldn't find corresponding Argus Incident to resolve")


def get_argus_client():
    """Returns a (cached) API client object"""
    global _client
    if not _client:
        _client = Client(
            api_root_url=_config.get_api_url(), token=_config.get_api_token()
        )
    return _client


class NAVArgusConfig(NAVConfigParser):
    """Config file definition for NAVArgus glue service"""

    DEFAULT_CONFIG_FILES = ("navargus.conf",)
    DEFAULT_CONFIG = """
[api]
"""

    def get_api_url(self):
        """Returns the configured Argus API base URL"""
        return self.get("api", "url")

    def get_api_token(self):
        """Returns the configured Argus API access token"""
        return self.get("api", "token")


if __name__ == "__main__":
    main()
