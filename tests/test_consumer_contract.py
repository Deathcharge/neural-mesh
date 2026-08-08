from __future__ import annotations

import json
from pathlib import Path

import neural_mesh
from neural_mesh import ConsensusEvent, EvaluationReport, ReplaySuite, ReportComparison


def test_samsarix_consumer_contract_fixture_matches_public_api() -> None:
    path = Path(__file__).parents[1] / "contracts" / "consumer_contract_v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    assert contract["contract"] == "samsarix-neural-mesh-consumer/v1"
    for symbol in contract["public_symbols"]:
        assert symbol in neural_mesh.__all__
        assert hasattr(neural_mesh, symbol)
    assert set(contract["artifact_schemas"]) == {
        ReportComparison.SCHEMA_VERSION,
        ConsensusEvent.SCHEMA_VERSION,
        EvaluationReport.SCHEMA_VERSION,
        ReplaySuite.SCHEMA_VERSION,
    }
