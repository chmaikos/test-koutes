from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware import PrivateApiResponses


def test_authenticated_json_and_errors_are_not_cacheable(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert "Authorization" in response.headers["vary"]
    error = client.get("/api/boxes/999999")
    assert error.status_code == 404
    assert error.headers["cache-control"] == "private, no-store, max-age=0"


def test_static_routes_preserve_their_cache_policy():
    async def asset(request):
        return PlainTextResponse("asset", headers={"Cache-Control": "public, max-age=3600"})

    app = Starlette(routes=[Route("/assets/sample.js", asset)])
    with TestClient(PrivateApiResponses(app)) as client:
        response = client.get("/assets/sample.js")
    assert response.headers["cache-control"] == "public, max-age=3600"
