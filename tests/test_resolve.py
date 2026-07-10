from datetime import datetime
from unittest.mock import MagicMock

import pytest

from navargus import glue


class TestResolveArgusIncident:
    def test_when_an_open_incident_exists_then_it_should_resolve_it(self, argus_client):
        argus_client.get_my_incidents.return_value = iter(
            [_incident(end_time=glue.INFINITY)]
        )
        glue.resolve_argus_incident(_alert(), timestamp=None)
        argus_client.resolve_incident.assert_called_once()

    def test_when_the_incident_is_stateless_then_it_should_refuse_to_resolve(
        self, argus_client
    ):
        argus_client.get_my_incidents.return_value = iter(
            [_incident(end_time=datetime(2020, 1, 1))]
        )
        glue.resolve_argus_incident(_alert())
        argus_client.resolve_incident.assert_not_called()

    def test_when_no_matching_incident_is_found_then_it_should_do_nothing(
        self, argus_client
    ):
        argus_client.get_my_incidents.return_value = iter([])
        glue.resolve_argus_incident(_alert())
        argus_client.resolve_incident.assert_not_called()


@pytest.fixture
def argus_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(glue, "get_argus_client", lambda: client)
    return client


def _incident(end_time):
    incident = MagicMock()
    incident.end_time = end_time
    return incident


def _alert():
    alert = MagicMock()
    alert.messages.filter.return_value = []  # no end message -> empty description
    return alert
