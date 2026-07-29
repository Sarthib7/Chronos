import chronos.config as config
from chronos.config import clean_value, load_settings, read_env

VALID_KEY = "sk-or-" + "b" * 40


class TestCleanValue:
    def test_strips_whitespace(self):
        assert clean_value("  value  ") == "value"

    def test_strips_control_characters(self):
        # A shell profile exporting a stray escape character produced exactly this.
        assert clean_value("\x1b") == ""

    def test_control_characters_inside_a_value_are_removed(self):
        assert clean_value("sk-or\x00-abc\x7f") == "sk-or-abc"

    def test_none_is_blank(self):
        assert clean_value(None) == ""


class TestReadEnv:
    def test_real_shell_value_wins_over_file(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "shell-key-value")
        monkeypatch.setattr(config, "dotenv_values", lambda: {"OPENROUTER_API_KEY": "file"})
        assert read_env("OPENROUTER_API_KEY") == "shell-key-value"

    def test_control_only_shell_value_falls_through_to_file(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "\x1b")
        monkeypatch.setattr(config, "dotenv_values", lambda: {"OPENROUTER_API_KEY": "file-key"})
        assert read_env("OPENROUTER_API_KEY") == "file-key"

    def test_blank_shell_value_falls_through_to_file(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
        monkeypatch.setattr(config, "dotenv_values", lambda: {"OPENROUTER_API_KEY": "file-key"})
        assert read_env("OPENROUTER_API_KEY") == "file-key"

    def test_missing_everywhere_is_blank(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(config, "dotenv_values", dict)
        assert read_env("OPENROUTER_API_KEY") == ""


class TestLoadSettings:
    def test_real_key_enables_llm(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", VALID_KEY)
        monkeypatch.setattr(config, "dotenv_values", dict)
        assert load_settings().llm_enabled is True

    def test_key_in_file_survives_a_junk_shell_export(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "\x1b")
        monkeypatch.setattr(config, "dotenv_values", lambda: {"OPENROUTER_API_KEY": VALID_KEY})
        settings = load_settings()
        assert settings.llm_enabled is True
        assert settings.openrouter_api_key == VALID_KEY

    def test_stub_key_is_treated_as_absent(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "too-short")
        monkeypatch.setattr(config, "dotenv_values", dict)
        assert load_settings().llm_enabled is False

    def test_defaults_apply_when_unset(self, monkeypatch):
        for name in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "HTTP_TIMEOUT"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(config, "dotenv_values", dict)
        settings = load_settings()
        assert settings.openrouter_model == config.DEFAULT_MODEL
        assert settings.http_timeout == 15
        assert settings.llm_enabled is False
