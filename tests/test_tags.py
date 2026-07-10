from types import SimpleNamespace

import pytest

from navargus import glue


class TestBuildTagsFrom:
    def test_when_alert_has_a_netbox_then_it_should_tag_host_room_and_location(
        self, empty_always_add
    ):
        tags = dict(glue.build_tags_from(_netbox_alert()))
        assert tags["host"] == "sw1.example.org"
        assert tags["room"] == "server-room"
        assert tags["location"] == "hq"

    def test_when_organization_has_parents_then_it_should_tag_each_ancestor(
        self, empty_always_add
    ):
        tags = list(glue.build_tags_from(_netbox_alert()))
        organizations = [value for key, value in tags if key == "organization"]
        assert organizations == ["team", "dept", "org"]

    def test_when_subject_is_a_netbox_then_it_should_add_a_host_url_tag(
        self, empty_always_add
    ):
        tags = dict(glue.build_tags_from(_netbox_alert()))
        assert tags["host_url"] == "/netbox/sw1/"

    def test_when_subject_is_an_interface_then_it_should_tag_the_interface_name(
        self, empty_always_add
    ):
        tags = dict(glue.build_tags_from(_interface_alert()))
        assert tags["interface"] == "GigabitEthernet0/1"

    def test_when_config_has_always_add_tags_then_it_should_append_them(
        self, monkeypatch, make_config
    ):
        monkeypatch.setattr(
            glue, "_config", make_config({"tags": {"always-add": {"customer": "x"}}})
        )
        tags = dict(glue.build_tags_from(_netbox_alert()))
        assert tags["customer"] == "x"


@pytest.fixture
def empty_always_add(monkeypatch, make_config):
    monkeypatch.setattr(glue, "_config", make_config({"tags": {"always-add": {}}}))


def _netbox_alert():
    org = SimpleNamespace(id="org", parent=None)
    dept = SimpleNamespace(id="dept", parent=org)
    team = SimpleNamespace(id="team", parent=dept)
    room = SimpleNamespace(id="server-room", location=SimpleNamespace(id="hq"))
    netbox = SimpleNamespace(sysname="sw1.example.org", room=room, organization=team)
    subject = glue.Netbox()
    subject.get_absolute_url = lambda: "/netbox/sw1/"
    return SimpleNamespace(
        event_type_id="boxState",
        alert_type=SimpleNamespace(name="boxDown"),
        netbox=netbox,
        get_subject=lambda: subject,
    )


def _interface_alert():
    subject = glue.Interface()
    subject.ifname = "GigabitEthernet0/1"
    return SimpleNamespace(
        event_type_id="linkState",
        alert_type=SimpleNamespace(name="linkDown"),
        netbox=None,
        get_subject=lambda: subject,
    )
