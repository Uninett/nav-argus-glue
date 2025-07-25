#!/usr/bin/env python3
#
# Copyright (C) 2020, 2021 UNINETT
# Copyright (C) 2022 Sikt
#
# This file is part of nav-argus-glue.
#
# nav-argus-glue is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.  You should have received a copy of the GNU General Public License
# along with nav-argus-glue. If not, see <http://www.gnu.org/licenses/>.
#
"""NAV Event Engine -> Argus Exporter - AKA Argus glue service for NAV.

Exports events from NAV's Event Engine streaming interface into an Argus server.

JSON parsing inspired by https://stackoverflow.com/a/58442063
"""

import select
import sys
import os
import re
import logging
import argparse
import time
from datetime import datetime, timedelta
from json import JSONDecoder, JSONDecodeError
from typing import Generator, List, Tuple, Optional

import yaml

from pyargus.client import Client
from pyargus.models import Incident

from nav.bootstrap import bootstrap_django
from nav.models.fields import INFINITY

bootstrap_django("navargus")

from nav.models.manage import Netbox, Interface
from nav.models.event import AlertHistory, STATE_START, STATE_STATELESS, STATE_END
from nav.logs import init_stderr_logging
from nav.config import open_configfile
from nav.buildconf import VERSION as _NAV_VERSION

from django.urls import reverse


_logger = logging.getLogger("navargus")
_client = None
_config: "Configuration" = None
NOT_WHITESPACE = re.compile(r"[^\s]")
NAV_SERIES = tuple(int(i) for i in _NAV_VERSION.split(".")[:2])
NAV_VERSION_WITH_SEVERITY = (5, 2)
SELECT_TIMEOUT = 30.0  # seconds


def main():
    """Main execution point"""
    global _config
    init_stderr_logging()
    _config = Configuration()

    parser = parse_args()
    if parser.test_api:
        test_argus_api()
    elif parser.sync_report:
        sync_report()
    elif parser.sync:
        do_sync()
    else:
        read_eventengine_stream()


def parse_args():
    """Builds an ArgumentParser and returns parsed program arguments"""
    parser = argparse.ArgumentParser(
        description="Synchronizes NAV alerts with an Argus alert aggregating server",
        usage="%(prog)s [options]",
        epilog="This program is designed to be run as an export script by NAV's event "
        "engine. See eventengine.conf for details.",
    )
    runtime_modes = parser.add_mutually_exclusive_group()
    runtime_modes.add_argument(
        "--test-api", action="store_true", help="Tests Argus API access"
    )
    runtime_modes.add_argument(
        "--sync-report",
        action="store_true",
        help="Prints a short report on NAV Alerts and Argus Incidents that aren't "
        "properly synced",
    )
    runtime_modes.add_argument(
        "--sync",
        action="store_true",
        help="Synchronizes existing NAV Alerts and Argus Incidents",
    )
    return parser.parse_args()


def read_eventengine_stream():
    """Reads a continuous stream of eventengine JSON blobs on stdin and updates the
    connected Argus server based on this.
    """
    # Ensure we do non-blocking reads from stdin, as we don't wont to get stuck when
    # we receive blobs that are smaller than the set buffer size
    from navargus import __version__ as version

    _logger.info(
        "Accepting eventengine stream data on stdin (pid=%s, version=%s)",
        os.getpid(),
        version,
    )
    os.set_blocking(sys.stdin.fileno(), False)

    try:
        for alert in emit_json_objects_from(sys.stdin):
            _logger.debug("got alert to dispatch: %r", alert.get("message"))
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
    last_block_size = 0
    error = None
    last_full_sync: Optional[datetime] = None
    while True:
        sync_interval = _config.get_sync_interval()
        if sync_interval and (
            not last_full_sync
            or last_full_sync + timedelta(minutes=sync_interval) < datetime.now()
        ):
            do_sync()
            last_full_sync = datetime.now()

        if last_block_size < buf_size:
            readable, _, _ = select.select([stream], [], [], SELECT_TIMEOUT)
        if last_block_size >= buf_size or stream in readable:
            _logger.debug("reading data from %r", stream)
            block = stream.read(buf_size)
            if not block:
                if not last_block_size:
                    # select() will keep claiming that the input handle has data
                    # available when the input pipe is actually gone. If we read two
                    # empty blocks in a row, we take it as a sign that our input is
                    # gone, and we should exit.
                    _logger.info(
                        "read multiple empty blocks in a row, maybe input went away. "
                        "aborting..."
                    )
                    return
                last_block_size = 0
                continue
            last_block_size = len(block)
        else:
            _logger.debug("select timed out")
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
    """Dispatches an alert structure to an Argus instance via its REST API

    :param alert: A deserialized JSON blob received from event engine
    """
    alerthistid = alert.get("history")
    on_maintenance = (
        bool(alert.get("on_maintenance"))
        or alert.get("event_type", {}).get("id") == "maintenanceState"
    )
    if not alerthistid:
        return

    if _config.get_ignore_maintenance() and on_maintenance:
        _logger.info(
            "Not posting incident as alert subject is on maintenance: %s",
            alert.get("message"),
        )
        return
    # We don't care about most of the contents of the JSON blob we received,
    # actually, since we can fetch what we want and more directly from the NAV
    # database
    try:
        alerthist = AlertHistory.objects.get(pk=alerthistid)
    except AlertHistory.DoesNotExist:
        # Workaround for eventengine bug: Its transaction is potentially not
        # committed yet, so we wait just a little bit:
        time.sleep(1)
        try:
            alerthist = AlertHistory.objects.get(pk=alerthistid)
        except AlertHistory.DoesNotExist:
            _logger.error(
                "Ignoring invalid alerthist PK received from event engine: %r",
                alerthistid,
            )
            return

    state = alert.get("state")
    if state in (STATE_START, STATE_STATELESS):
        if state == STATE_STATELESS and _config.get_ignore_stateless():
            _logger.info(
                "Ignoring stateless alert as configured to: %s", alert.get("message")
            )
            return
        incident = convert_alerthistory_object_to_argus_incident(alerthist)
        post_incident_to_argus(incident)
    else:
        # when resolving, the AlertHistory timestamp may not have been updated yet
        timestamp = alert.get("time")
        resolve_argus_incident(alerthist, timestamp)


def convert_alerthistory_object_to_argus_incident(alert: AlertHistory) -> Incident:
    """Converts an unresolved AlertHistory object from NAV to a Argus Incident.

    :param alert: A NAV AlertHistory object
    :returns: An object describing an Argus Incident, suitable for POSTing to its API.
    """
    url = reverse("event-details", args=(alert.pk,))

    incident = Incident(
        start_time=alert.start_time,
        end_time=alert.end_time,
        source_incident_id=alert.pk,
        details_url=url if url else "",
        description=get_short_start_description(alert),
        level=convert_severity_to_level(alert.severity),
        tags=dict(build_tags_from(alert)),
    )
    return incident


def convert_severity_to_level(severity: int) -> int:
    """Converts a NAV severity level into an Argus Incident level"""
    if NAV_SERIES >= NAV_VERSION_WITH_SEVERITY:
        return severity  # NAV severity levels match Argus levels from this version on
    else:
        return _config.get_default_level()


def get_short_start_description(alerthist: AlertHistory):
    """Describes an AlertHistory object via its shortest, english-language start
    message (or stateless message, in the case of stateless alerts)
    """
    msgs = alerthist.messages.filter(
        type="sms", state__in=(STATE_START, STATE_STATELESS), language="en"
    )
    return msgs[0].message if msgs else ""


def get_short_end_description(alerthist: AlertHistory):
    """Describes an AlertHistory object via its shortest, english-language end
    message.
    """
    msgs = alerthist.messages.filter(type="sms", state=STATE_END, language="en")
    return msgs[0].message if msgs else ""


def build_tags_from(alert: AlertHistory) -> Generator:
    """
    Generates a series of tag tuples
    :param alert: An AlertHistory object from NAV
    :returns: A generator of (tag_name, tag_value) tuples, suitable to make a tag
              dictionary for an Argus incident.
    """
    yield "event_type", alert.event_type_id
    if alert.alert_type:
        yield "alert_type", alert.alert_type.name
    subject = alert.get_subject()
    # TODO: Find a sane convention for translating various event subjects to tags, such
    #       as power supplies, modules etc.

    if alert.netbox:
        yield "host", alert.netbox.sysname
        yield "room", alert.netbox.room.id
        yield "location", alert.netbox.room.location.id
        yield "organization", alert.netbox.organization.id
    if isinstance(subject, Netbox):
        yield "host_url", subject.get_absolute_url()
    elif isinstance(subject, Interface):
        yield "interface", subject.ifname

    for tag, value in _config.get_always_add_tags().items():
        yield tag, value


def post_incident_to_argus(incident: Incident) -> int:
    """Posts an incident payload to an Argus API instance"""
    client = get_argus_client()
    incident_response = client.post_incident(incident)
    if incident_response:
        return incident_response.pk


def resolve_argus_incident(alert: AlertHistory, timestamp=None):
    """Looks up the mirror Incident of alert in Argus and marks it as resolved.

    :param alert: The NAV AlertHistory object used to find the Argus Incident.
    :param timestamp: The optional timestamp of the ending event. Because of the way
                      event engine works, the AlertHistory record may actually not have
                      been updated yet at the time the ending event is exported into
                      this program.
    """
    client = get_argus_client()
    incident = next(
        client.get_my_incidents(open=True, source_incident_id=alert.pk), None
    )
    if incident:
        if incident.end_time != INFINITY:
            _logger.error("Cannot resolve a stateless incident")
            return
        _logger.debug("Resolving with an end_time of %r", timestamp or alert.end_time)
        client.resolve_incident(
            incident,
            description=get_short_end_description(alert),
            timestamp=timestamp or alert.end_time,
        )
    else:
        _logger.warning("Couldn't find corresponding Argus Incident to resolve")


def get_argus_client():
    """Returns a (cached) API client object"""
    global _client
    if not _client:
        _client = Client(
            api_root_url=_config.get_api_url(),
            token=_config.get_api_token(),
            timeout=_config.get_api_timeout(),
        )
    return _client


def test_argus_api():
    """Tests access to the Argus API by fetching all open incidents"""
    client = get_argus_client()
    incidents = client.get_incidents(open=True)
    next(incidents, None)
    print(
        "Argus API is accessible at {}".format(client.api.api_root_url), file=sys.stderr
    )


def do_sync():
    """Synchronizes Argus with NAV alerts.

    Unresolved NAV alerts that don't exist as Incidents in Argus are created there,
    unresolved Argus Incidents that are resolved in NAV will be resolved in Argus.
    """
    unresolved_argus_incidents, new_nav_alerts = get_unsynced_report()

    for alert in new_nav_alerts:
        description = describe_alerthist(alert).replace("\t", " ")
        incident = verify_incident_exists(alert.pk)
        if incident:
            _logger.warning(
                "Argus incident %s already exists for this NAV alert, with end_time "
                "set to %r, ignoring: %s",
                incident.pk,
                incident.end_time,
                description,
            )
            continue
        incident = convert_alerthistory_object_to_argus_incident(alert)
        _logger.debug("Posting to Argus: %s", description)
        post_incident_to_argus(incident)

    client = get_argus_client()
    for incident in unresolved_argus_incidents:
        try:
            alert = AlertHistory.objects.get(pk=incident.source_incident_id)
        except AlertHistory.DoesNotExist:
            _logger.error(
                "Argus incident %r refers to non-existent NAV Alert: %s",
                incident,
                incident.source_incident_id,
            )
            continue
        _logger.debug(
            "Resolving Argus Incident: %s",
            describe_incident(incident).replace("\t", " "),
        )
        has_resolved_time = alert.end_time < INFINITY if alert.end_time else False
        resolve_time = alert.end_time if has_resolved_time else datetime.now()
        client.resolve_incident(
            incident,
            description=get_short_end_description(alert),
            timestamp=resolve_time,
        )


def verify_incident_exists(alerthistid: int) -> [Incident, None]:
    """Verifies whether a given NAV Alert has a corresponding Argus Incident,
    regardless of whether its resolved or not.  If an Incident is found, and Incident
    object is returned for inspection.
    """
    client = get_argus_client()
    try:
        incident = next(client.get_my_incidents(source_incident_id=alerthistid))
        return incident
    except StopIteration:
        return None


def sync_report():
    """Prints a short report on which alerts and incidents aren't synced"""
    missed_resolve, missed_open = get_unsynced_report()

    if missed_resolve:
        caption = "These incidents are resolved in NAV, but not in Argus"
        print(caption + "\n" + "=" * len(caption))
        for incident in missed_resolve:
            print(describe_incident(incident))
        if missed_open:
            print()

    if missed_open:
        caption = "These incidents are open in NAV, but are missing from Argus"
        print(caption + "\n" + "=" * len(caption))
        for alert in missed_open:
            print(describe_alerthist(alert))


def get_unsynced_report() -> Tuple[List[Incident], List[AlertHistory]]:
    """Returns a report of which NAV AlertHistory objects and Argus Incidents objects
    are unsynced.

    :returns: A two-tuple (incidents, alerts). The first list identifies Argus
              incidents that should have been resolved, but aren't. The second list
              identifies unresolved NAV AlertHistory objects that have no corresponding
              Incident in Argus at all.
    """
    client = get_argus_client()
    nav_alerts = AlertHistory.objects.unresolved().prefetch_related("messages")
    if _config.get_ignore_maintenance():
        nav_alerts = (a for a in nav_alerts if not a.get_subject().is_on_maintenance())
    nav_alerts = {a.pk: a for a in nav_alerts}
    argus_incidents = {
        int(i.source_incident_id): i for i in client.get_my_incidents(open=True)
    }

    missed_resolve = set(argus_incidents).difference(nav_alerts)
    missed_open = set(nav_alerts).difference(argus_incidents)

    return (
        [argus_incidents[i] for i in missed_resolve],
        [nav_alerts[i] for i in missed_open],
    )


def describe_alerthist(alerthist: AlertHistory):
    """Describes an alerthist object for tabulated output to stdout"""
    return "{pk}\t{timestamp}\t{msg}".format(
        pk=alerthist.pk,
        timestamp=alerthist.start_time,
        msg=get_short_start_description(alerthist) or "N/A",
    )


def describe_incident(incident: Incident):
    """Describes an Argus Incident object for tabulated output to stdout"""
    return "{pk}\t{timestamp}\t{msg}".format(
        pk=incident.source_incident_id,
        timestamp=incident.start_time,
        msg=incident.description,
    )


class Configuration(dict):
    CONFIG_FILE = "navargus.yml"

    def __init__(self):
        super().__init__()
        self.load_config()

    def load_config(self):
        try:
            with open_configfile(self.CONFIG_FILE) as ymldata:
                cfg = yaml.safe_load(ymldata)
                self.update(cfg)
        except OSError:
            _logger.info("No configuration file found: %s", self.CONFIG_FILE)

    def get_api_url(self):
        """Returns the configured Argus API base URL"""
        return self.get("api", {}).get("url")

    def get_api_token(self):
        """Returns the configured Argus API access token"""
        return self.get("api", {}).get("token")

    def get_api_timeout(self) -> float:
        """Returns the configured API request timeout value"""
        return float(self.get("api", {}).get("timeout", 2.0))

    def get_sync_interval(self):
        """Returns the configured sync interval in minutes.

        The sync interval determines how often a full re-sync against the Argus API
        should take place. A value of `None` means that periodic re-sync should not
        take place.

        """
        sync_interval = self.get("api", {}).get("sync-interval", 1)
        if not sync_interval:
            return None

        try:
            sync_interval = int(sync_interval)
        except ValueError:
            raise ValueError(
                "The setting for sync-interval must be a positive integer. Current value: %r",
                sync_interval,
            )
        if sync_interval < 0:
            raise ValueError(
                "The setting for sync-interval must be a positive integer. Current value: %i",
                sync_interval,
            )
        return sync_interval

    def get_default_level(self) -> int:
        return int(self.get("api", {}).get("default-level", 3))

    def get_always_add_tags(self):
        """Returns a set of tags to add to all incidents"""
        return self.get("tags", {}).get("always-add", {})

    def get_ignore_maintenance(self):
        """Returns the value of the maintenance filter option"""
        return self.get("filters", {}).get("ignore-maintenance", True)

    def get_ignore_stateless(self):
        """Returns the value of the stateless alert filter option"""
        return self.get("filters", {}).get("ignore-stateless", False)


if __name__ == "__main__":
    main()
