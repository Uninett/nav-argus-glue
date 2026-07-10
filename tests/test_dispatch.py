from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from navargus import glue


class TestDispatchAlertToArgus:
    def test_when_alert_has_no_history_then_it_should_do_nothing(self, dispatch_env):
        glue.dispatch_alert_to_argus({})
        dispatch_env.post.assert_not_called()
        dispatch_env.resolve.assert_not_called()

    def test_when_subject_is_on_maintenance_and_ignored_then_it_should_not_post(
        self, dispatch_env
    ):
        dispatch_env.config.get_ignore_maintenance.return_value = True
        glue.dispatch_alert_to_argus({"history": 1, "on_maintenance": True})
        dispatch_env.post.assert_not_called()

    def test_when_event_type_is_maintenance_state_and_ignored_then_it_should_not_post(
        self, dispatch_env
    ):
        dispatch_env.config.get_ignore_maintenance.return_value = True
        alert = {"history": 1, "event_type": {"id": "maintenanceState"}}
        glue.dispatch_alert_to_argus(alert)
        dispatch_env.post.assert_not_called()

    def test_when_start_alert_arrives_then_it_should_post_the_converted_incident(
        self, dispatch_env
    ):
        glue.dispatch_alert_to_argus({"history": 1, "state": glue.STATE_START})
        dispatch_env.post.assert_called_once_with(dispatch_env.convert.return_value)

    def test_when_stateless_alert_is_ignored_then_it_should_not_post(
        self, dispatch_env
    ):
        dispatch_env.config.get_ignore_stateless.return_value = True
        glue.dispatch_alert_to_argus({"history": 1, "state": glue.STATE_STATELESS})
        dispatch_env.post.assert_not_called()

    def test_when_stateless_alert_is_not_ignored_then_it_should_post(
        self, dispatch_env
    ):
        dispatch_env.config.get_ignore_stateless.return_value = False
        glue.dispatch_alert_to_argus({"history": 1, "state": glue.STATE_STATELESS})
        dispatch_env.post.assert_called_once_with(dispatch_env.convert.return_value)

    def test_when_end_alert_arrives_then_it_should_resolve_with_the_event_time(
        self, dispatch_env
    ):
        alert = {"history": 1, "state": glue.STATE_END, "time": None}
        glue.dispatch_alert_to_argus(alert)
        dispatch_env.resolve.assert_called_once_with(dispatch_env.alerthist, None)

    def test_when_alerthist_appears_after_a_retry_then_it_should_still_post(
        self, dispatch_env, monkeypatch
    ):
        monkeypatch.setattr(glue.time, "sleep", lambda _seconds: None)
        dispatch_env.objects_get.side_effect = [
            glue.AlertHistory.DoesNotExist(),
            dispatch_env.alerthist,
        ]
        glue.dispatch_alert_to_argus({"history": 1, "state": glue.STATE_START})
        dispatch_env.post.assert_called_once_with(dispatch_env.convert.return_value)

    def test_when_alerthist_never_appears_then_it_should_give_up_without_posting(
        self, dispatch_env, monkeypatch
    ):
        monkeypatch.setattr(glue.time, "sleep", lambda _seconds: None)
        dispatch_env.objects_get.side_effect = glue.AlertHistory.DoesNotExist
        glue.dispatch_alert_to_argus({"history": 1, "state": glue.STATE_START})
        dispatch_env.post.assert_not_called()


@pytest.fixture
def dispatch_env(monkeypatch):
    """Patches the module boundary so dispatch *decisions* can be asserted in
    isolation from incident conversion and the Argus client."""
    config = MagicMock()
    config.get_ignore_maintenance.return_value = False
    config.get_ignore_stateless.return_value = False
    monkeypatch.setattr(glue, "_config", config)

    alerthist = MagicMock()
    objects_get = MagicMock(return_value=alerthist)
    monkeypatch.setattr(glue.AlertHistory.objects, "get", objects_get)

    convert = MagicMock(return_value=MagicMock(name="incident"))
    monkeypatch.setattr(glue, "convert_alerthistory_object_to_argus_incident", convert)
    post = MagicMock()
    monkeypatch.setattr(glue, "post_incident_to_argus", post)
    resolve = MagicMock()
    monkeypatch.setattr(glue, "resolve_argus_incident", resolve)

    return SimpleNamespace(
        config=config,
        alerthist=alerthist,
        objects_get=objects_get,
        convert=convert,
        post=post,
        resolve=resolve,
    )
