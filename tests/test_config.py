import pytest

from bot.core.config import Config, ConfigError


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
