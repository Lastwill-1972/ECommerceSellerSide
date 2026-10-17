import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=From file\nMYSQL_ROOT_PASSWORD=ignored\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "From environment")
    assert Settings(_env_file=env_file).app_name == "From environment"


def test_database_url_preserves_special_characters():
    settings = Settings(db_user="demo", db_password="p@ss:/%word", db_name="demo")
    assert settings.database_url.drivername == "mysql+pymysql"
    assert settings.database_url.password == "p@ss:/%word"
    assert "p@ss:/%word" not in repr(settings)
    assert settings.database_url.query["charset"] == "utf8mb4"


def test_missing_database_credentials_fail_clearly():
    with pytest.raises(ValueError, match="DB_NAME"):
        _ = Settings(db_name=None, db_user=None, db_password=None).database_url


def test_invalid_database_port():
    with pytest.raises(ValidationError):
        Settings(db_port=0)
