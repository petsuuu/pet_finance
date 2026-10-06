from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient


def test_recovery_readonly_fees_validation_and_existing_dashboard(client: TestClient) -> None:
    client.post("/api/v1/accounts", json={"name": "Negative bank", "opening_balance": "-100"})
    before = client.get("/api/v1/audit/transactions").json()
    r = client.get("/api/v1/dashboard/recovery-plan")
    assert r.status_code == 200
    data = r.json()
    assert data["recovery_date_known_obligations"] is None
    assert data["recorded_deficit"] == "100.00"
    assert data["estimated_bank_charges"] is None
    charged = client.get("/api/v1/dashboard/recovery-plan", params={"estimated_bank_charges": "20"})
    assert charged.status_code == 200
    assert charged.json()["forecast_after_planned_spending"] == "-120.00"
    assert (
        client.get(
            "/api/v1/dashboard/recovery-plan", params={"estimated_bank_charges": "-1"}
        ).status_code
        == 422
    )
    day = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    dash = client.get(
        "/api/v1/dashboard/monthly", params={"year": day.year, "month": day.month}
    ).json()
    assert dash["recovery_plan"]["recorded_deficit"] == "100.00"
    assert client.get("/api/v1/audit/transactions").json() == before
    assert client.get("/api/v1/transactions").json() == []
