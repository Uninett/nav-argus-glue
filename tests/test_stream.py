import io
from unittest.mock import MagicMock

import pytest

from navargus import glue


class TestEmitJsonObjectsFrom:
    def test_when_stream_has_stacked_objects_then_it_should_yield_each(
        self, no_sync, always_readable
    ):
        stream = io.StringIO('{"a": 1}\n{"b": 2}\n')
        assert list(glue.emit_json_objects_from(stream)) == [{"a": 1}, {"b": 2}]

    def test_when_objects_are_separated_by_whitespace_then_it_should_parse_them(
        self, no_sync, always_readable
    ):
        stream = io.StringIO('  {"a": 1}   {"b": 2}  ')
        assert list(glue.emit_json_objects_from(stream)) == [{"a": 1}, {"b": 2}]

    def test_when_stream_is_empty_then_it_should_yield_nothing_and_return(
        self, no_sync, always_readable
    ):
        stream = io.StringIO("")
        assert list(glue.emit_json_objects_from(stream)) == []


@pytest.fixture
def no_sync(monkeypatch):
    """Disable the periodic re-sync so the parser can be exercised in isolation."""
    config = MagicMock()
    config.get_sync_interval.return_value = None
    monkeypatch.setattr(glue, "_config", config)


@pytest.fixture
def always_readable(monkeypatch):
    """StringIO is not selectable, so pretend the stream is always readable."""
    monkeypatch.setattr(glue.select, "select", lambda r, w, x, timeout: (r, [], []))
