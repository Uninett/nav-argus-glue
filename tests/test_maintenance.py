from types import SimpleNamespace

from navargus import glue


class TestIsOnMaintenance:
    def test_when_subject_reports_maintenance_then_it_should_return_that(self):
        alert = SimpleNamespace(
            get_subject=lambda: SimpleNamespace(is_on_maintenance=lambda: True)
        )
        assert glue.is_on_maintenance(alert) is True

    def test_when_subject_lacks_the_method_then_it_should_ask_the_netbox(self):
        netbox = SimpleNamespace(is_on_maintenance=lambda: True)
        alert = SimpleNamespace(get_subject=lambda: SimpleNamespace(), netbox=netbox)
        assert glue.is_on_maintenance(alert) is True

    def test_when_subject_lacks_the_method_and_no_netbox_then_it_should_be_false(self):
        alert = SimpleNamespace(get_subject=lambda: SimpleNamespace(), netbox=None)
        assert glue.is_on_maintenance(alert) is False
