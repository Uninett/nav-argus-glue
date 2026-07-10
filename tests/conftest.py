"""Shared test configuration for navargus.

``navargus.glue`` bootstraps Django and imports NAV's ORM at *import time*
(``bootstrap_django("navargus")`` followed by ``from nav.models... import ...``).
NAV is an end-user application that cannot reasonably be installed in a unit-test
environment, so before anything imports ``navargus.glue`` we register just enough
fake ``nav`` (and ``django``) surface in ``sys.modules`` for that import to
succeed. Tests then exercise the pure logic and pass mock ORM objects explicitly.
"""

import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _register(name, is_package=False, **attrs):
    module = types.ModuleType(name)
    if is_package:
        module.__path__ = []
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _raise_missing_config(*_args, **_kwargs):
    # Configuration.load_config() treats a missing config file as "no config".
    raise OSError("navargus.yml is not available under test")


class _StubAlertHistory:
    class DoesNotExist(Exception):
        pass

    objects = MagicMock()


class _StubNetbox:
    pass


class _StubInterface:
    pass


def _install_nav_and_django_stubs():
    _register("nav", is_package=True)
    _register("nav.bootstrap", bootstrap_django=lambda *a, **k: None)
    _register("nav.logs", init_stderr_logging=lambda *a, **k: None)
    _register("nav.buildconf", VERSION="5.19.0")
    _register("nav.config", open_configfile=_raise_missing_config)
    _register("nav.models", is_package=True)
    _register("nav.models.fields", INFINITY=datetime.max)
    _register(
        "nav.models.event",
        AlertHistory=_StubAlertHistory,
        STATE_START="s",
        STATE_STATELESS="x",
        STATE_END="e",
    )
    _register("nav.models.manage", Netbox=_StubNetbox, Interface=_StubInterface)

    _register("django", is_package=True)
    _register("django.urls", reverse=lambda *a, **k: "/event/details/")


_install_nav_and_django_stubs()


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Keep the cached client and config from leaking between tests."""
    from navargus import glue

    glue._client = None
    glue._config = None
    yield
    glue._client = None
    glue._config = None


@pytest.fixture
def make_config():
    """Returns a factory building a Configuration straight from a dict.

    The ``open_configfile`` stub raises ``OSError``, so a fresh Configuration
    starts empty and we just layer the test's data on top.
    """
    from navargus.glue import Configuration

    def _make(data=None):
        config = Configuration()
        config.update(data or {})
        return config

    return _make
