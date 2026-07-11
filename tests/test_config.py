import pytest

from bot.core.config import DEFAULT_MUTED_LOG_EVENTS, Config, ConfigError


def _required_env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "123456789")
    monkeypatch.setenv("LOG_CHANNEL_ID", "987654321")


def test_nothing_is_muted_by_default(monkeypatch):
    # The log channel mirrors every event unless the admin explicitly trims it back.
    _required_env(monkeypatch)
    monkeypatch.delenv("LOG_MUTED_EVENTS", raising=False)

    cfg = Config()
    assert cfg.log_muted_events == set(DEFAULT_MUTED_LOG_EVENTS)
    assert cfg.log_muted_events == set()


def test_muted_log_events_can_be_overridden(monkeypatch):
    _required_env(monkeypatch)
    monkeypatch.setenv("LOG_MUTED_EVENTS", "member_joined, Role_Added")

    cfg = Config()
    assert cfg.log_muted_events == {"member_joined", "role_added"}


def test_empty_muted_log_events_means_log_everything(monkeypatch):
    _required_env(monkeypatch)
    monkeypatch.setenv("LOG_MUTED_EVENTS", "")

    cfg = Config()
    assert cfg.log_muted_events == set()


def test_config_loads_all_vars(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "123456789")
    monkeypatch.setenv("LOG_CHANNEL_ID", "987654321")

    cfg = Config()
    assert cfg.discord_token == "token123"
    assert cfg.database_url == "postgresql://localhost/test"
    assert cfg.guild_id == 123456789
    assert cfg.log_channel_id == 987654321


def test_config_raises_on_missing_var(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "123456789")
    monkeypatch.setenv("LOG_CHANNEL_ID", "987654321")

    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        Config()


def test_config_raises_on_invalid_int(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "not-an-int")
    monkeypatch.setenv("LOG_CHANNEL_ID", "987654321")

    with pytest.raises(ConfigError, match="GUILD_ID"):
        Config()


def test_lavalink_uri_default(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("LOG_CHANNEL_ID", "2")
    monkeypatch.delenv("LAVALINK_URI", raising=False)

    cfg = Config()
    assert cfg.lavalink_uri == "http://lavalink:2333"


def test_lavalink_uri_override(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("LOG_CHANNEL_ID", "2")
    monkeypatch.setenv("LAVALINK_URI", "http://myhost:2333")

    cfg = Config()
    assert cfg.lavalink_uri == "http://myhost:2333"


def test_lavalink_password_default(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("LOG_CHANNEL_ID", "2")
    monkeypatch.delenv("LAVALINK_PASSWORD", raising=False)

    cfg = Config()
    assert cfg.lavalink_password == "youshallnotpass"


def _set_required(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("LOG_CHANNEL_ID", "2")


def test_provider_api_keys_default_none(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.delenv("PANDASCORE_API_KEY", raising=False)

    cfg = Config()
    assert cfg.football_data_api_key is None
    assert cfg.pandascore_api_key is None


def test_provider_api_keys_override(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "fd-key")
    monkeypatch.setenv("PANDASCORE_API_KEY", "ps-key")

    cfg = Config()
    assert cfg.football_data_api_key == "fd-key"
    assert cfg.pandascore_api_key == "ps-key"
