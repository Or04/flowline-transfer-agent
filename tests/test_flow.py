import asyncio
import json
from pathlib import Path
import pytest
from transfer_flow.mock import MockServer
from transfer_flow.client import TransferClient
from transfer_flow.ingestion import parse_file
from transfer_flow.agent import AgentHarness, AgentError, LocalTools, ScriptedLLM, build_report

ROOT = Path(__file__).resolve().parents[1]
DATA = parse_file(ROOT / "transfers.json")

@pytest.fixture
def system():
    with MockServer() as server:
        client = TransferClient(server.url)
        client.reset(DATA["accounts"])
        yield server, client

EXPECTED = ["ACCEPTED", "ACCEPTED", "ACCEPTED", "UNKNOWN_ACCOUNT", "INVALID_AMOUNT", "MISSING_FIELD", "DUPLICATE", "INSUFFICIENT_FUNDS", "SAME_ACCOUNT", "UNSUPPORTED_CURRENCY", "EXCEEDS_LIMIT", "ACCEPTED"]

def test_json_batch_returns_correct_row_decisions_and_database_balances(system):
    server, client = system
    batch = client.submit(ROOT / "transfers.json")
    result = client.status(batch["batchId"])
    assert result["status"] == "DONE"
    assert [r["code"] or r["status"] for r in result["results"]] == EXPECTED
    assert server.state.ledger.balances() == {"ACC-1001": "7350.50", "ACC-1002": "2074.75", "ACC-1003": "7174.75", "ACC-2001": "300.00", "ACC-3001": "400.00"}
    assert client.summary() == {"openingTotal": "17300.00", "currentTotal": "17300.00", "preserved": True}
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 8

def test_repeated_submission_is_idempotent_in_api_and_database(system):
    server, client = system
    first = client.submit(ROOT / "transfers.json")
    balances = server.state.ledger.balances()
    second = client.submit(ROOT / "transfers.json")
    assert second == first
    assert client.status(second["batchId"])["status"] == "DONE"
    assert server.state.ledger.balances() == balances
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4

def test_injected_ledger_failure_rolls_back_every_balance_and_all_writes(system):
    server, client = system
    client.request("POST", "/__faults", {"failOnWrite": 2})
    before = server.state.ledger.balances()
    batch = client.submit(ROOT / "transfers.json")
    assert client.status(batch["batchId"])["status"] == "FAILED"
    assert server.state.ledger.balances() == before
    assert client.summary()["preserved"] is True
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 0
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    # A failed batch can be resubmitted with a different explicit attempt key.
    import hashlib
    payload = {"transfers": DATA["transfers"]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _, again = client.request("POST", "/batches", payload, {"Idempotency-Key": "retry:" + digest})
    assert client.status(again["batchId"])["status"] == "DONE"
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4

def test_lost_http_500_response_retries_without_duplicate_batch(system):
    server, client = system
    client.request("POST", "/__faults", {"fail500Next": 1})
    batch = client.submit(ROOT / "transfers.json")
    assert client.status(batch["batchId"])["status"] == "DONE"
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 8
    assert client.summary()["preserved"] is True

def test_xlsx_and_json_produce_same_decisions_and_balances(system):
    server, client = system
    json_result = client.status(client.submit(ROOT / "transfers.json")["batchId"])
    expected_balances = server.state.ledger.balances()
    client.reset(DATA["accounts"])
    xlsx_result = client.status(client.submit(ROOT / "transfers.xlsx")["batchId"])
    assert [(r["status"], r["code"]) for r in json_result["results"]] == [(r["status"], r["code"]) for r in xlsx_result["results"]]
    assert server.state.ledger.balances() == expected_balances

def test_agent_happy_path_report_matches_ledger_and_trace(system):
    server, client = system
    agent = AgentHarness(LocalTools(client, ROOT))
    report = asyncio.run(agent.run(f"Process {ROOT / 'transfers.json'} and give me a reconciliation report"))
    assert len(report["accepted"]) == 4
    assert len(report["rejected"]) == 7
    assert len(report["duplicates"]) == 1
    assert report["moneyPreserved"] is True
    assert report["currentTotal"] == client.summary()["currentTotal"]
    assert [r["code"] or r["status"] for r in client.status(report["batchId"])["results"]] == EXPECTED
    assert len(agent.trace) == 4 and agent.trace[-1]["validated"]
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4

def test_agent_tool_failure_is_explicit_and_does_not_change_ledger(system):
    server, client = system
    class BrokenTools(LocalTools):
        async def call(self, name, arguments):
            if name == "get_batch_status":
                raise RuntimeError("Temporary tool outage")
            return await super().call(name, arguments)
    agent = AgentHarness(BrokenTools(client, ROOT))
    with pytest.raises(AgentError, match="Tool failure"):
        asyncio.run(agent.run(f"Process {ROOT / 'transfers.json'} and give me a reconciliation report"))
    assert agent.trace[-1]["error"] == "Temporary tool outage"
    assert client.summary()["preserved"] is True
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4

def test_malicious_agent_report_cannot_override_authoritative_outcomes(system):
    server, client = system
    class CompromisedLLM(ScriptedLLM):
        def next(self, goal, history):
            decision = super().next(goal, history)
            if isinstance(decision, dict):
                decision["accepted"] = [r.get("reference") for r in DATA["transfers"]]
                decision["rejected"] = []
                return decision
            return decision
    agent = AgentHarness(LocalTools(client, ROOT), CompromisedLLM())
    with pytest.raises(AgentError, match="contradicts"):
        asyncio.run(agent.run(f"Process {ROOT / 'transfers.json'} and give me a reconciliation report"))
    assert server.state.ledger.db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 4
    assert [r["code"] or r["status"] for r in client.status(agent.trace[0]["result"]["batchId"])["results"]] == EXPECTED
