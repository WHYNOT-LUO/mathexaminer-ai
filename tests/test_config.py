import pytest

from mathexaminer import config


def test_missing_required_settings_names_them(monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(config.ConfigError, match="SUPABASE_URL, SUPABASE_KEY, DEEPSEEK_API_KEY"):
        config.load_settings()


def test_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    s = config.load_settings()
    assert s.deepseek_model == config.DEFAULT_DEEPSEEK_MODEL
    assert s.deepseek_base_url == config.DEFAULT_DEEPSEEK_BASE_URL
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://p.example.com/deepseek/")
    monkeypatch.setenv("MAX_GRADINGS_PER_DAY", "5")
    s = config.load_settings()
    assert (s.deepseek_model, s.deepseek_base_url, s.max_gradings_per_day) == (
        "deepseek-v4-pro", "https://p.example.com/deepseek", 5,
    )


@pytest.mark.parametrize("name,value", [("DEEPSEEK_MODEL", "../evil?x=1"), ("DEEPSEEK_BASE_URL", "ftp://x"), ("MAX_GRADINGS_PER_DAY", "many")])
def test_bad_values_rejected(monkeypatch, name, value):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv(name, value)
    with pytest.raises(config.ConfigError):
        config.load_settings()
