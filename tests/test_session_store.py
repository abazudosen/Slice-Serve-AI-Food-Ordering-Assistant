from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

import webapp
from app_models import PizzaState, Session
from session_store import SessionRepository


def test_session_repository_round_trips_state_and_trace(tmp_path):
    database_path = tmp_path / "orders.sqlite3"
    repository = SessionRepository(database_path)
    session = Session()
    session.state = PizzaState(size="medium", crust="thin", toppings=["olives"])
    session.trace.append({"user_message": "medium thin with olives"})

    assert repository.save("session-123", session, "owner-a")
    restored = SessionRepository(database_path).get("session-123", "owner-a")

    assert restored is not None
    assert restored.flow == "pizza"
    assert restored.state == session.state
    assert restored.trace == session.trace
    listed = repository.list_sessions("owner-a", flow="pizza")
    assert listed[0]["session_id"] == "session-123"
    assert listed[0]["summary"] == "Pizza: medium thin"


def test_session_repository_restores_bagel_state(tmp_path):
    repository = SessionRepository(tmp_path / "orders.sqlite3")
    session = Session(flow="bagel")
    session.state.bread_type = "everything"
    session.state.filling = "salmon"

    assert repository.save("bagel-123", session, "owner-a")
    restored = repository.get("bagel-123", "owner-a")

    assert restored is not None
    assert restored.state == session.state
    assert repository.list_sessions("owner-a", flow="bagel")[0]["summary"] == "Bagel: everything salmon"


def test_session_repository_isolates_owners(tmp_path):
    repository = SessionRepository(tmp_path / "orders.sqlite3")
    session = Session()
    session.state = PizzaState(size="large")

    assert repository.save("shared-id", session, "owner-a")
    assert repository.get("shared-id", "owner-b") is None
    assert repository.list_sessions("owner-b") == []
    assert repository.delete("shared-id", "owner-b") is False
    assert repository.save("shared-id", Session(flow="bagel"), "owner-b") is False
    assert repository.get("shared-id", "owner-a").state.size == "large"


def test_chat_saves_session_for_later_resume(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "orders.sqlite3")
    monkeypatch.setattr(webapp, "SESSION_REPOSITORY", repository)

    class StubAgent:
        def run_turn(self, session, user_message, cancellation_event=None):
            session.state.size = "large"
            session.trace.append({"user_message": user_message})
            return "What crust would you like?"

    monkeypatch.setattr(webapp, "AGENT", StubAgent())

    with TestClient(webapp.app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "large", "flow": "pizza"},
        )
        assert response.status_code == 200
        assert response.headers.get("x-request-id")
        session_id = response.json()["session_id"]

        saved = client.get("/api/sessions?flow=pizza")
        assert saved.status_code == 200
        assert saved.json()[0]["session_id"] == session_id

        resumed = client.get(f"/api/session/{session_id}")
        assert resumed.status_code == 200
        assert resumed.json()["state"]["size"] == "large"
        assert resumed.json()["trace"] == [{"user_message": "large"}]
        deleted = client.delete(f"/api/session/{session_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/session/{session_id}").status_code == 404


def test_api_scopes_saved_sessions_to_signed_owner_cookie(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "orders.sqlite3")
    monkeypatch.setattr(webapp, "SESSION_REPOSITORY", repository)

    class StubAgent:
        def run_turn(self, session, user_message, cancellation_event=None):
            session.state.size = "large"
            return "What crust would you like?"

    monkeypatch.setattr(webapp, "AGENT", StubAgent())

    with TestClient(webapp.app) as first_client:
        response = first_client.post(
            "/api/chat",
            json={"message": "large", "flow": "pizza"},
        )
        session_id = response.json()["session_id"]
        assert first_client.get("/api/sessions").json()[0]["session_id"] == session_id

    with TestClient(webapp.app) as other_client:
        assert other_client.get("/api/sessions").json() == []
        assert other_client.get(f"/api/session/{session_id}").status_code == 404
        assert other_client.delete(f"/api/session/{session_id}").status_code == 404


def test_cancel_endpoint_prevents_in_flight_turn_from_being_saved(tmp_path, monkeypatch):
    repository = SessionRepository(tmp_path / "orders.sqlite3")
    monkeypatch.setattr(webapp, "SESSION_REPOSITORY", repository)
    entered_agent = Event()

    class SlowAgent:
        def run_turn(self, session, user_message, cancellation_event=None):
            entered_agent.set()
            cancellation_event.wait(timeout=5)
            session.state.size = "large"
            return "This stale reply must not be saved."

    monkeypatch.setattr(webapp, "AGENT", SlowAgent())

    with TestClient(webapp.app) as client:
        client.get("/api/sessions")
        session_id = "cancel-this-turn"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                client.post,
                "/api/chat",
                json={"session_id": session_id, "message": "large", "flow": "pizza"},
            )
            assert entered_agent.wait(timeout=2)
            canceled = client.post(f"/api/session/{session_id}/cancel")
            response = pending.result(timeout=2)

        assert canceled.status_code == 202
        assert canceled.json() == {"cancelled": True}
        assert "canceled" in response.json()["reply"].lower()
        assert repository.get(session_id, "nonexistent-owner") is None
        assert client.get("/api/sessions").json() == []
