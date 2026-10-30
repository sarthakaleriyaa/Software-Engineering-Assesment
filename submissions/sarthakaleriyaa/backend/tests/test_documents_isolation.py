from app.models.document import Document


def create_user(client, email: str) -> dict:
    response = client.post(
        "/auth/signup",
        json={"email": email, "password": "CorrectPassword123"},
    )
    assert response.status_code == 201

    login_response = client.post(
        "/auth/login",
        json={"email": email, "password": "CorrectPassword123"},
    )
    assert login_response.status_code == 200

    return {
        "user": response.json(),
        "token": login_response.json()["access_token"],
    }


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def add_document(
    db_session,
    user_id: int,
    filename: str,
    stored_path: str,
) -> Document:
    document = Document(
        user_id=user_id,
        filename=filename,
        stored_path=stored_path,
        content_type="text/plain",
        size_bytes=10,
        status="ready",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    return document


def test_users_cannot_list_or_delete_each_others_documents(
    client,
    db_session,
    tmp_path,
):
    user_a = create_user(client, "a@example.com")
    user_b = create_user(client, "b@example.com")

    document_a = add_document(
        db_session,
        user_a["user"]["id"],
        "a.txt",
        str(tmp_path / "a.txt"),
    )
    document_b = add_document(
        db_session,
        user_b["user"]["id"],
        "b.txt",
        str(tmp_path / "b.txt"),
    )

    list_a = client.get(
        "/documents",
        headers=auth_headers(user_a["token"]),
    )
    list_b = client.get(
        "/documents",
        headers=auth_headers(user_b["token"]),
    )

    assert [item["id"] for item in list_a.json()] == [document_a.id]
    assert [item["id"] for item in list_b.json()] == [document_b.id]

    delete_other = client.delete(
        f"/documents/{document_b.id}",
        headers=auth_headers(user_a["token"]),
    )
    assert delete_other.status_code == 404

    list_b_after = client.get(
        "/documents",
        headers=auth_headers(user_b["token"]),
    )
    assert [item["id"] for item in list_b_after.json()] == [document_b.id]

    delete_own = client.delete(
        f"/documents/{document_a.id}",
        headers=auth_headers(user_a["token"]),
    )
    assert delete_own.status_code == 204

    list_a_after = client.get(
        "/documents",
        headers=auth_headers(user_a["token"]),
    )
    assert list_a_after.json() == []


def test_selected_document_validation_rejects_other_users_document(
    client,
    db_session,
    tmp_path,
):
    user_a = create_user(client, "a@example.com")
    user_b = create_user(client, "b@example.com")

    document_b = add_document(
        db_session,
        user_b["user"]["id"],
        "b.txt",
        str(tmp_path / "b.txt"),
    )

    response = client.post(
        "/chat",
        json={
            "question": "What is in the document?",
            "document_ids": [document_b.id],
        },
        headers=auth_headers(user_a["token"]),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "One or more selected documents are unavailable"
    )
