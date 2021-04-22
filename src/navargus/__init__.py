#!/usr/bin/env python3
#
# Copyright (C) 2020, 2021 UNINETT
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
import fcntl
import re
import logging
import argparse
import time
from datetime import datetime
from json import JSONDecoder, JSONDecodeError
from typing import Generator, Tuple, List

import yaml

from pyargus.client import Client
from pyargus.models import Incident

from nav.bootstrap import bootstrap_django
from nav.models.fields import INFINITY

bootstrap_django("navargus")

from nav.models.manage import Netbox, Interface
from nav.models.service import Service
from nav.models.event import AlertHistory, STATE_START, STATE_STATELESS, STATE_END
from nav.logs import init_stderr_logging
from nav.config import open_configfile

from django.urls import reverse
from django.db.models import Q


_logger = logging.getLogger("navargus")
_client = None
_config = None  # type: Configuration
NOT_WHITESPACE = re.compile(r"[^\s]")


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
        if _config.get_sync_on_startup():
            do_sync()
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
    """Dispatches an alert structure to an Argus instance via its REST API

    :param alert: A deserialized JSON blob received from event engine
    """
    alerthistid = alert.get("history")
    on_maintenance = (
        bool(alert.get("on_maintenance"))
        or alert.get("event_type", {}).get("id") == "maintenanceState"
    )
    if alerthistid:
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
    """Converts a NAV severity level into an Argus Incident level.

    ATM, NAV severities are poorly defined, so this just falls back to the default
    level as set in the config file
    """
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
            api_root_url=_config.get_api_url(), token=_config.get_api_token()
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
        resolve_time = alert.end_time if alert.end_time < INFINITY else datetime.now()
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
        nav_alerts = (a for a in nav_alerts if not was_on_maintenance(a))
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


def was_on_maintenance(alert: AlertHistory):
    """Returns True if the subject of the alert appeared to be on maintenance at the
    time the alert was issued.

    The NAV libraries contain API calls to evaluate whether an alert subject is
    currently on maintenance. However, it doesn't currently have the ability to
    easily evaluate whether something was on maintenance at a particular point in
    time. This becomes important to nav-argus-glue when syncing potentially old
    alerts from the NAV alert history to Argus (NAV 5.1 at the time of this writing)
    - hence, this function exists.
    """
    on_maintenance = False
    maintenances = AlertHistory.objects.filter(
        event_type="maintenanceState",
        netbox=alert.netbox,
        start_time__lte=alert.start_time,
        end_time__gte=alert.start_time,
    )
    subject = alert.get_subject()
    if isinstance(subject, Service):
        on_maintenance = maintenances.filter(subid=str(subject.id)).count() > 0
    if not on_maintenance:
        # if service wasn't explicitly on service, check whether the netbox itself was
        on_maintenance = maintenances.filter(subid="").count() > 0

    if on_maintenance:
        _logger.debug(
            "%s was on maintenance when the alert took place: %s",
            subject,
            describe_alerthist(alert),
        )
        return True
    else:
        return False


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

    def get_sync_on_startup(self):
        """Returns True if this program should always sync the Argus API on startup"""
        return bool(self.get("api", {}).get("sync-on-startup"))

    def get_default_level(self) -> int:
        return int(self.get("api", {}).get("default-level", 3))

    def get_always_add_tags(self):
        """Returns a set of tags to add to all incidents"""
        return self.get("tags", {}).get("always-add", {})

    def get_ignore_maintenance(self):
        """Returns the value of the maintenance filter option"""
        return self.get("filters", {}).get("ignore-maintenance", True)


if __name__ == "__main__":
    main()
