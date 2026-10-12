import pytest
from fastapi.testclient import TestClient

from refi_radar.api import create_app
from refi_radar.contacts import write_contacts
from refi_radar.ingest import REQUIRED_COLUMNS, write_tape
from refi_radar.synthetic import generate_contacts


@pytest.fixture(scope="module")
def client(tmp_path_factory, synthetic_loans):
    path = tmp_path_factory.mktemp("data") / "tape.csv"
    write_tape(synthetic_loans, path)
    write_contacts(generate_contacts(), path.with_name("crm_contacts.csv"))
    return TestClient(create_app(tape_path=str(path)))


def test_health_reports_data_quality(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["loans_loaded"] == 250
    assert body["data_quality"]["rows_rejected"] == 0
    assert body["horizon_months"] == 36
    assert body["window_end"] == "2029-10-01"


def test_app_falls_back_to_synthetic_tape_when_none_configured(monkeypatch):
    monkeypatch.delenv("REFI_RADAR_TAPE", raising=False)
    body = TestClient(create_app()).get("/api/health").json()
    assert body["tape_source"].startswith("synthetic")


def test_scenarios_and_market_reference_data(client):
    scenarios = client.get("/api/scenarios").json()
    assert {"base", "office_vacancy_shock", "severe"} <= {s["name"] for s in scenarios}
    assert {s["label"] for s in scenarios} == {
        "Base Case",
        "Higher for Longer",
        "Office Vacancy Shock",
        "Severe Downturn",
    }
    assert "office" in client.get("/api/market").json()["property_types"]


def test_analyze_preset_returns_ranked_list_and_comparison(client):
    body = client.post("/api/analyze", json={"scenario": "office_vacancy_shock", "top_n": 10}).json()
    assert len(body["assessments"]) == 10
    scores = [a["priority"]["score"] for a in body["assessments"]]
    assert scores == sorted(scores, reverse=True)
    assert body["comparison"]["baseline"] == "base"
    assert body["summary"]["loans_in_window"] > 10


def test_analyze_with_slider_overrides_creates_custom_scenario(client):
    base = client.post("/api/analyze", json={"scenario": "base"}).json()["summary"]
    custom = client.post(
        "/api/analyze", json={"scenario": "base", "overrides": {"rate_shock_bps": 200, "cap_rate_shock_bps": 100}}
    ).json()
    assert custom["scenario"]["name"] == "custom"
    assert custom["scenario"]["label"] == "Custom Scenario"
    assert custom["summary"]["total_refi_gap"] > base["total_refi_gap"]
    assert custom["comparison"]["loans_worsened"] > 0


def test_analyze_property_type_filter(client):
    body = client.post("/api/analyze", json={"property_types": ["hotel"], "top_n": 500}).json()
    assert {a["property_type"] for a in body["assessments"]} == {"hotel"}


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario": "does_not_exist"},
        {"overrides": {"rate_shock_bps": 10_000}},
        {"overrides": {"office_vacancy_shock_pp": -0.1}},
        {"property_types": ["spaceship"]},
        {"top_n": 0},
    ],
)
def test_analyze_rejects_invalid_input(client, payload):
    assert client.post("/api/analyze", json=payload).status_code == 422


def test_loan_detail_and_not_found(client):
    body = client.get("/api/loans/NMK-00001", params={"scenario": "severe"}).json()
    assert body["loan_id"] == "NMK-00001"
    assert body["reasons"]
    assert client.get("/api/loans/NOPE").status_code == 404


def test_tape_validation_endpoint(client):
    header = ",".join(REQUIRED_COLUMNS)
    row = 'L-1,X,warehouse,"Austin, TX",S,Bank,2020-01-01,2027-01-01,1000000,0.05,30,2000000,150000,0.9'
    resp = client.post("/api/tape/validate", content=f"{header}\n{row}", headers={"Content-Type": "text/csv"})
    assert resp.status_code == 200
    assert resp.json()["data_quality"]["rows_rejected"] == 1
    assert resp.json()["errors"][0]["field"] == "property_type"

    missing = client.post("/api/tape/validate", content="loan_id\nL-1", headers={"Content-Type": "text/csv"})
    assert missing.status_code == 422


def test_dashboard_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Refi Radar" in resp.text
    assert client.get("/static/app.js").status_code == 200
    assert 'rel="icon"' in resp.text
    assert client.get("/static/favicon.svg").status_code == 200
    logo = client.get("/static/logo.svg")
    assert logo.status_code == 200 and "<svg" in logo.text


def test_contacts_cover_every_sponsor_in_the_book(client):
    contacts = {c["sponsor"]: c for c in client.get("/api/contacts").json()}
    sponsors = {a["sponsor"] for a in client.post("/api/analyze", json={"top_n": 5000}).json()["assessments"]}
    assert sponsors <= set(contacts)
    assert all(c["phone"].startswith("(212) 555-01") for c in contacts.values())


def test_assessments_carry_next_step_and_talking_points(client):
    a = client.post("/api/analyze", json={"top_n": 1}).json()["assessments"][0]
    assert a["next_step"].startswith("Pitch")
    assert len(a["talking_points"]) == 3


def test_call_list_filter_narrows_list_but_not_summary(client):
    full = client.post("/api/analyze", json={"top_n": 5000}).json()
    office_distressed = [
        a for a in full["assessments"] if a["property_type"] == "office" and a["bucket"] == "distressed"
    ]
    body = client.post(
        "/api/analyze",
        json={"top_n": 5, "call_list_filter": {"property_type": "office", "bucket": "distressed"}},
    ).json()
    assert body["assessments_total"] == len(office_distressed) > 5
    assert [a["loan_id"] for a in body["assessments"]] == [a["loan_id"] for a in office_distressed[:5]]
    assert body["summary"] == full["summary"]


def test_call_list_filter_by_maturity_quarter(client):
    body = client.post("/api/analyze", json={"top_n": 5000, "call_list_filter": {"period": "Past due"}}).json()
    assert body["assessments"] and all(a["past_maturity"] for a in body["assessments"])
    wall = {p["period"]: p for p in body["summary"]["maturity_wall"]}
    assert sum(a["balloon_balance"] for a in body["assessments"]) == pytest.approx(
        sum(wall["Past due"]["balance_by_bucket"].values())
    )


def test_call_list_filter_rejects_unknown_bucket(client):
    resp = client.post("/api/analyze", json={"call_list_filter": {"bucket": "on_fire"}})
    assert resp.status_code == 422
