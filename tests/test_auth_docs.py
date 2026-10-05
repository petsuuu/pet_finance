from app.core.config import Settings
from app.main import create_app


def test_swagger_declares_bearer_security() -> None:
    api = create_app(Settings(database_url="postgresql+psycopg://localhost/test", api_token="test"))
    schema = api.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    operation = schema["paths"]["/api/v1/imports/clofin/preview"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert not any(p["name"] == "authorization" for p in operation.get("parameters", []))
