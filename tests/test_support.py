"""Support behaviour. The supplied messages are inputs, not a lookup table."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from commerce_mock import default_seed_dir
from commerce_mock.store import CommerceStore
from support.api import create_app
from support.model import MockModel, ModelRequest, ModelResponse, OpenAICompatibleClient
from support.policy import eligibility
from support.service import SupportService
from support.slots import extract_amount, extract_item_query, extract_quantity

ROOT = Path(__file__).resolve().parents[1]
REQUESTS = json.loads((ROOT / "scenarios" / "requests.json").read_text(encoding="utf-8"))
BY_ID = {item["scenario_id"]: item for item in REQUESTS}


def _reply_of(service: SupportService, scenario_id: str) -> dict:
    item = BY_ID[scenario_id]
    return service.handle(item["authenticated_customer_id"], item["message"])


def test_slots_from_the_supplied_wording() -> None:
    glass = BY_ID["scenario-02"]["message"]
    mug = BY_ID["scenario-04"]["message"]
    assert extract_item_query(glass) == "wine glasses"
    assert extract_quantity(glass) == 1
    assert extract_amount(glass) is None
    assert extract_item_query(mug) == "Travel Mug"
    assert extract_amount(mug) == "34.90"


def test_where_is_my_in_transit_order(service: SupportService, store: CommerceStore) -> None:
    result = _reply_of(service, "scenario-01")
    assert result["action"] == "order_status"
    assert result["grounding"] == "model"
    assert "ORD-100" in result["reply"]
    assert "in transit" in result["reply"]
    assert "DHL-ORD100-TEST" in result["reply"]
    assert "I have submitted a refund" not in result["reply"]
    assert store.runtime_records("refunds") == []
    assert store.runtime_records("proposals") == []


def test_paraphrased_tracking_question_is_not_hard_coded(store: CommerceStore) -> None:
    result = SupportService(store).handle("CUST-001", "Has order ORD-100 shipped?")
    assert result["action"] == "order_status"
    assert "DHL-ORD100-TEST" in result["reply"]
    assert store.runtime_records("refunds") == []


def test_partial_damage_quote_does_not_move_money(service: SupportService, store: CommerceStore) -> None:
    result = _reply_of(service, "scenario-02")
    assert result["action"] == "refund_proposed"
    assert result["amount"] == "14.90"
    assert result["explanation_code"] == "awaiting_confirmation"
    reply = result["reply"]
    assert "14.90" in reply
    assert "Wine Glass" in reply
    assert "ORD-200" in reply
    assert "Decanter" not in reply
    assert "54.80" not in reply
    assert "25.00" not in reply
    assert "I have submitted a refund" not in reply
    assert store.runtime_records("refunds") == []
    proposals = store.runtime_records("proposals")
    assert len(proposals) == 1
    assert proposals[0]["lines"][0]["item_id"] == "ITEM-201"
    assert proposals[0]["lines"][0]["quantity"] == 1
    assert proposals[0]["status"] == "open"


def test_paraphrased_damage_request_quotes_one_glass(store: CommerceStore) -> None:
    result = SupportService(store).handle(
        "CUST-001",
        "I want a refund for the broken wine glass on ORD-200",
    )
    assert result["action"] == "refund_proposed"
    assert result["amount"] == "14.90"
    assert store.runtime_records("refunds") == []


def test_other_customers_order_is_not_disclosed(service: SupportService, store: CommerceStore) -> None:
    result = _reply_of(service, "scenario-03")
    assert result["action"] == "not_found"
    assert result["explanation_code"] == "order_not_on_account"
    reply = result["reply"].lower()
    assert "ord-204" in reply
    for leaked in ("martin", "ivanov", "headphone", "129", "dhl-ord100-test", "anna.petrova"):
        assert leaked not in reply
    trace = json.dumps(result["debug"]["tool_trace"]).lower()
    for leaked in ("martin", "headphone", "129.00", "item-401"):
        assert leaked not in trace
    assert store.runtime_records("refunds") == []


def test_wrong_expected_amount_is_not_paid(service: SupportService, store: CommerceStore) -> None:
    result = _reply_of(service, "scenario-04")
    assert result["action"] == "refund_proposed"
    assert result["amount"] == "29.90"
    assert result["explanation_code"] == "claim_does_not_match_ledger"
    reply = result["reply"]
    assert "29.90" in reply
    assert "34.90" in reply
    assert "Travel Mug" in reply
    assert "I have submitted a refund" not in reply
    assert store.runtime_records("refunds") == []
    assert len(store.runtime_records("proposals")) == 1


def test_clear_process_request_uses_the_ledger_amount(store: CommerceStore) -> None:
    service = SupportService(store)
    result = service.handle(
        "CUST-001",
        "Please process the refund for the Travel Mug from order ORD-300. The return was received.",
    )
    assert result["action"] == "refund_executed"
    assert result["amount"] == "29.90"
    refunds = store.runtime_records("refunds")
    assert len(refunds) == 1
    assert refunds[0]["amount"] == "29.90"
    assert "I have submitted a refund" in result["reply"]


def test_bare_yes_does_not_pick_between_open_proposals(service: SupportService, store: CommerceStore) -> None:
    _reply_of(service, "scenario-02")
    _reply_of(service, "scenario-04")
    result = service.handle("CUST-001", "yes")
    assert result["action"] == "clarify"
    assert result["explanation_code"] == "ambiguous_proposal"
    assert store.runtime_records("refunds") == []


def test_confirming_the_ledger_amount_pays_once(service: SupportService, store: CommerceStore) -> None:
    _reply_of(service, "scenario-02")
    _reply_of(service, "scenario-04")
    first = service.handle("CUST-001", "yes, confirm €29.90")
    second = service.handle("CUST-001", "yes, confirm €29.90")
    assert first["action"] == "refund_executed"
    assert first["amount"] == "29.90"
    assert "Travel Mug" in first["reply"]
    assert second["action"] == "already_refunded"
    refunds = [row for row in store.runtime_records("refunds") if row["status"] == "executed"]
    assert len(refunds) == 1
    assert refunds[0]["lines"][0]["name"] == "Travel Mug"
    open_proposals = [row for row in store.runtime_records("proposals") if row["status"] == "open"]
    assert [row["amount"] for row in open_proposals] == ["14.90"]


def test_in_transit_damage_is_not_refunded(store: CommerceStore) -> None:
    result = SupportService(store).handle(
        "CUST-001",
        "Please process the refund for the Ceramic Dinner Set on order ORD-100. It arrived broken.",
    )
    assert result["action"] == "refund_declined"
    assert result["explanation_code"] == "not_eligible_in_transit"
    assert "DHL-ORD100-TEST" in result["reply"]
    assert store.runtime_records("refunds") == []
    assert store.runtime_records("proposals") == []


def test_poisoned_tool_arguments_cannot_widen_the_refund(store: CommerceStore) -> None:
    class PoisonModel(MockModel):
        def complete(self, request: ModelRequest) -> ModelResponse:
            response = super().complete(request)
            if request.stage != "plan":
                return response
            for call in response.tool_calls:
                if call.name == "quote_refund":
                    call.arguments.update(
                        {
                            "customer_id": "CUST-002",
                            "order_id": "ORD-204",
                            "item_query": "Wireless Headphones",
                            "quantity": 2,
                            "claimed_amount": "129.00",
                            "execute": True,
                        }
                    )
            return response

    message = BY_ID["scenario-02"]["message"]
    result = SupportService(store, PoisonModel()).handle("CUST-001", message)
    assert result["action"] == "refund_proposed"
    assert result["amount"] == "14.90"
    assert "Wine Glass" in result["reply"]
    assert "headphone" not in result["reply"].lower()
    assert "129" not in result["reply"]
    assert store.runtime_records("refunds") == []


def test_ungrounded_draft_is_replaced_and_not_paid(store: CommerceStore) -> None:
    class LyingDraftModel(MockModel):
        def complete(self, request: ModelRequest) -> ModelResponse:
            if request.stage == "draft":
                return ModelResponse(
                    content={
                        "reply": (
                            "I have submitted a refund of €999.00 for Wireless Headphones "
                            "on order ORD-204. Email martin.ivanov@example.test."
                        )
                    }
                )
            return super().complete(request)

    service = SupportService(store, LyingDraftModel())
    result = _reply_of(service, "scenario-01")
    assert result["grounding"] == "template"
    assert result["action"] == "order_status"
    assert "DHL-ORD100-TEST" in result["reply"]
    reply = result["reply"].lower()
    assert "999.00" not in reply
    assert "headphone" not in reply
    assert "martin" not in reply
    assert "ord-204" not in reply
    assert store.runtime_records("refunds") == []
    reviews = store.runtime_records("reviews")
    assert len(reviews) == 1
    assert "999.00" in reviews[0]["rejected_draft"]


def test_seed_files_survive_the_agent(seed_bytes: dict[str, bytes], service: SupportService, store: CommerceStore) -> None:
    for item in REQUESTS:
        service.handle(item["authenticated_customer_id"], item["message"])
    service.handle("CUST-001", "yes, confirm €29.90")
    service.clear()
    assert store.runtime_records("refunds") == []
    assert store.runtime_records("proposals") == []
    on_disk = {path.name: path.read_bytes() for path in Path(default_seed_dir()).iterdir() if path.is_file()}
    assert on_disk == seed_bytes


def test_http_path_hides_the_other_customers_order(store: CommerceStore) -> None:
    client = TestClient(create_app(store))
    response = client.post(
        "/support/messages",
        json={
            "authenticated_customer_id": "CUST-001",
            "message": BY_ID["scenario-03"]["message"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "not_found"
    assert "headphone" not in body["reply"].lower()
    cleared = client.post("/runtime/clear")
    assert cleared.status_code == 200
    assert client.get("/runtime").json()["refunds"] == []


def test_live_model_client_builds_a_tool_payload_and_does_not_call_out() -> None:
    client = OpenAICompatibleClient(base_url="https://api.openai.com/v1", model_name="gpt-4.1")
    payload = client.payload(
        ModelRequest(stage="plan", messages=[{"role": "system", "content": "plan"}, {"role": "user", "content": "{}"}])
    )
    assert payload["tools"]
    assert "api_key" not in payload
    assert payload["model"] == "gpt-4.1"
    try:
        client.complete(ModelRequest(stage="draft", messages=[{"role": "user", "content": "{}"}]))
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("live client should not call a provider")


def test_eligibility_rules() -> None:
    assert eligibility("in_transit", None, True) == "not_eligible_in_transit"
    assert eligibility("delivered", None, False) == "need_defect_reason"
    assert eligibility("delivered", None, True) is None
    assert eligibility("returned", "received", False) is None
    assert eligibility("returned", "in_progress", False) == "return_not_received"


def test_missing_order_id_asks_instead_of_guessing(service: SupportService, store: CommerceStore) -> None:
    result = service.handle("CUST-001", "Where is my order?")
    assert result["action"] == "clarify"
    assert result["explanation_code"] == "missing_order_reference"
    assert store.runtime_records("refunds") == []
    assert "ORD-" not in result["reply"]
