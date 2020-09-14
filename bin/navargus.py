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
import select
import sys
import os
import fcntl
import re
import logging
from json import JSONDecoder, JSONDecodeError
from typing import Generator, Any

from django.urls import reverse

from nav.bootstrap import bootstrap_django
bootstrap_django("navargus")

from nav.models.manage import Netbox, Interface

import requests


_logger = logging.getLogger("navargus")
ARGUS_API_URL = ""
ARGUS_API_TOKEN = ""
ARGUS_HEADERS = {
    "Authorization": "Token " + ARGUS_API_TOKEN,
}
POST_HEADERS = {
    "Content-Type": "application/json",
}
NOT_WHITESPACE = re.compile(r"[^\s]")
STATE_START = "s"
STATE_END = "e"
STATE_STATELESS = "x"


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


def convert_to_argus_incident(alert: dict) -> dict:
    """
    :param alert: A JSON-serialized AlertQueue instance from NAV, in the form of a dict
    :returns: A dict describing an Argus Incident, suitable for POSTing to its API.
    """
    state = alert.get("state", STATE_STATELESS)
    incident = {
        "start_time": alert.get("time"),
        "end_time": "infinity" if state == STATE_START else None,
        "source_incident_id": alert.get("history"),
        "details_url": alert.get("alert_details_url"),
        "description": alert.get("message"),
        "tags": list(build_tags_from(alert)),
    }

    return incident


def build_tags_from(alert: dict) -> Generator:
    """
    Generates a series of tag objects
    :param alert: A JSON-serialized AlertQueue instance from NAV, in the form of a dict
    :returns: A list of tag dicts for posting with an Argus Incident
    """
    yield tag("event_type", alert.get("event_type", {}).get("id"))
    yield tag("alert_type", alert.get("alert_type", {}).get("name"))
    subject_type = alert.get("subject_type")

    # The JSON blob provided by eventengine does not drill deep into the data model,
    # so we will need to look up certain data from NAV itself to produce an accurate
    # set of tags:
    # TODO: Find a sane convention for translating various event subjects to tags, such
    #       as power supplies, modules etc.

    if alert.get("netbox"):
        netbox = Netbox.objects.get(pk=alert.get("netbox"))
        yield tag("host", netbox.sysname)
        yield tag("room", netbox.room.id)
        yield tag("location", netbox.room.location.id)
    if subject_type == "Netbox":
        yield tag("host_url", alert.get("subject_url"))
    elif subject_type == "Interface":
        interface = Interface.objects.get(pk=alert.get("subid"))
        yield tag("interface", interface.ifname)


def tag(key: str, value: Any) -> dict:
    """Returns a Argus-compliant tag object that can be converted to JSON for the API"""
    return {"tag": "{}={}".format(key, value)}


def post_incident_to_argus(incident):
    """Posts an incident payload to an Argus API instance"""
    response = requests.post(
        url=ARGUS_API_URL + "/incidents/",
        headers={**ARGUS_HEADERS, **POST_HEADERS},
        json=incident,
    )
    if response.status_code in (200, 201):
        payload = response.json()
        pk = payload.get("pk")
        return pk
    else:
        _logger.error(
            "Failed posting alert to Argus (%r): %r",
            response.status_code,
            response.content,
        )


def resolve_argus_incident(alert: dict):
    """Looks up and resolves an existing incident in Argus based on the supplied
    end-state alert.
    """
    nav_alert_id = alert.get("history")
    if not nav_alert_id:
        return

    incident = find_existing_argus_incident(nav_alert_id)
    if incident:
        if incident.get("end_time") != "infinity":
            _logger.error("Cannot resolve a stateless incident")
            return
        post_incident_resolve_event(incident, alert)

    else:
        _logger.warning("Couldn't find corresponding Argus Incident to resolve")


def find_existing_argus_incident(nav_alert_id: int) -> dict:
    """Retrieves an existing Incident from Argus based on a NAV alert ID"""
    endpoint = "/incidents/mine/?source_incident_id={}".format(nav_alert_id)
    response = requests.get(url=ARGUS_API_URL + endpoint, headers=ARGUS_HEADERS)
    if response.status_code == 200:
        payload = response.json()
        if payload:
            return payload[0]


def post_incident_resolve_event(incident: dict, alert: dict):
    incident_id = incident.get("pk")
    endpoint = "/incidents/{}/events/".format(incident_id)
    event = {
        "timestamp": alert.get("time"),
        "type": "END",
        "description": alert.get("message"),
    }
    response = requests.post(
        url=ARGUS_API_URL + endpoint,
        headers={**ARGUS_HEADERS, **POST_HEADERS},
        json=event,
    )
    if response.status_code in (200, 201):
        payload = response.json()
        pk = payload.get("pk")
        return pk
    else:
        _logger.error(
            "Failed posting incident end event to Argus (%r): %r",
            response.status_code,
            response.content,
        )


if __name__ == "__main__":
    main()
