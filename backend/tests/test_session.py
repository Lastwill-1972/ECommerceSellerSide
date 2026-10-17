import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    # SQLite only checks Session lifecycle, never MySQL business compatibility.
    engine = create_engine(f"sqlite:///{(tmp_path / 'session.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE lifecycle_probe (id INTEGER PRIMARY KEY)"))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(deps, "get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def test_same_request_shares_session_and_requests_are_isolated(session_factory):
    app = FastAPI()
    observed = []

    def child(db: Session = Depends(deps.get_db)):
        return db

    @app.get("/probe")
    def probe(db: Session = Depends(deps.get_db), child_db: Session = Depends(child)):
        assert db is child_db
        observed.append(db)
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200
        assert client.get("/probe").status_code == 200
    assert observed[0] is not observed[1]


@pytest.mark.parametrize("raise_error", [False, True])
def test_dependency_rolls_back_uncommitted_work(session_factory, raise_error):
    dependency = deps.get_db()
    session = next(dependency)
    session.execute(text("INSERT INTO lifecycle_probe (id) VALUES (1)"))
    if raise_error:
        with pytest.raises(RuntimeError, match="service failed"):
            dependency.throw(RuntimeError("service failed"))
    else:
        dependency.close()
    with session_factory() as verification:
        assert verification.scalar(text("SELECT COUNT(*) FROM lifecycle_probe")) == 0


def test_top_level_service_can_commit(session_factory):
    dependency = deps.get_db()
    session = next(dependency)
    with session.begin():
        session.execute(text("INSERT INTO lifecycle_probe (id) VALUES (1)"))
    dependency.close()
    with session_factory() as verification:
        assert verification.scalar(text("SELECT COUNT(*) FROM lifecycle_probe")) == 1
