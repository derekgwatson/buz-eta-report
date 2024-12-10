def test_home_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome to Buz Reports" in response.data  # Update to match your app


def test_edit_user_route_unauthorized(client):
    response = client.get("/edit_user/1")
    assert response.status_code == 302  # Redirect to login if not authenticated
    assert b"login" in response.data.lower()  # Ensure redirect to login
