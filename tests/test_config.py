import pytest


class TestConfiguration:
    def test_when_no_api_section_then_it_should_return_none_url_and_token(
        self, make_config
    ):
        config = make_config({})
        assert config.get_api_url() is None
        assert config.get_api_token() is None

    def test_when_api_section_present_then_it_should_return_url_and_token(
        self, make_config
    ):
        config = make_config(
            {"api": {"url": "https://argus.example.org/api/v1", "token": "secret"}}
        )
        assert config.get_api_url() == "https://argus.example.org/api/v1"
        assert config.get_api_token() == "secret"

    def test_when_timeout_unset_then_it_should_default_to_two_seconds(
        self, make_config
    ):
        assert make_config({}).get_api_timeout() == 2.0

    def test_when_sync_interval_unset_then_it_should_default_to_one(self, make_config):
        assert make_config({}).get_sync_interval() == 1

    def test_when_sync_interval_is_falsy_then_it_should_return_none(self, make_config):
        assert make_config({"api": {"sync-interval": 0}}).get_sync_interval() is None
        assert make_config({"api": {"sync-interval": None}}).get_sync_interval() is None

    def test_when_sync_interval_is_negative_then_it_should_raise(self, make_config):
        with pytest.raises(ValueError):
            make_config({"api": {"sync-interval": -5}}).get_sync_interval()

    def test_when_sync_interval_is_not_an_integer_then_it_should_raise(
        self, make_config
    ):
        with pytest.raises(ValueError):
            make_config({"api": {"sync-interval": "soon"}}).get_sync_interval()

    def test_when_default_level_unset_then_it_should_default_to_three(
        self, make_config
    ):
        assert make_config({}).get_default_level() == 3

    def test_when_filters_unset_then_it_should_ignore_maintenance_only(
        self, make_config
    ):
        config = make_config({})
        assert config.get_ignore_maintenance() is True
        assert config.get_ignore_stateless() is False

    def test_when_always_add_tags_set_then_it_should_return_them(self, make_config):
        config = make_config({"tags": {"always-add": {"customer": "example.org"}}})
        assert config.get_always_add_tags() == {"customer": "example.org"}
