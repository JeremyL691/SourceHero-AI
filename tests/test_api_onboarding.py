from tests.conftest import TEST_USER_ID


def test_health_reports_ready_and_empty_stats(auth_client):
    client, _ = auth_client
    response = client.get("/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ready"
    assert payload["stats"]["sources"] == 0
    assert payload["stats"]["documents"] == 0
    assert payload["stats"]["chunks"] == 0


def test_demo_seed_is_idempotent(auth_client):
    import pytest
    pytest.skip("/demo/seed endpoint was removed in the cloud migration; re-add when seeding is ported to multi-tenant DB.")


def test_save_conversation_indexes_markdown(auth_client):
    client, _ = auth_client
    response = client.post(
        "/conversations/save",
        json={
            "title": "Research chat about retrieval",
            "markdown": "# Conversation Summary\n\nThe user asked about retrieval and citations.",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["documents_inserted"] == 1
    assert payload["chunks_inserted"] >= 1

    search = client.post("/search", json={"query": "retrieval citations", "source_type": "conversation"})
    assert search.status_code == 200
    assert search.json()["hits"]
