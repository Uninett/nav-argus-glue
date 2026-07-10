from navargus import glue


class TestConvertSeverityToLevel:
    def test_when_nav_version_carries_severity_then_it_should_pass_it_through(
        self, monkeypatch
    ):
        monkeypatch.setattr(glue, "NAV_SERIES", (5, 19))
        assert glue.convert_severity_to_level(2) == 2

    def test_when_nav_version_predates_severity_then_it_should_use_default_level(
        self, monkeypatch, make_config
    ):
        monkeypatch.setattr(glue, "NAV_SERIES", (5, 0))
        monkeypatch.setattr(glue, "_config", make_config({"api": {"default-level": 4}}))
        assert glue.convert_severity_to_level(2) == 4
