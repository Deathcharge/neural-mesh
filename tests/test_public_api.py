from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path

import neural_mesh
from neural_mesh import ConsensusEngine, ConsensusResult
from neural_mesh.multi_ai_consensus import MultiAIConsensus


def test_public_version_and_compatibility_alias() -> None:
    assert neural_mesh.__version__ == "0.2.0"
    assert MultiAIConsensus is ConsensusEngine
    assert "ConsensusResult" in neural_mesh.__all__
    assert ConsensusResult.__module__ == "neural_mesh.consensus"


def test_offline_example_runs(capsys: object) -> None:
    del capsys
    example = Path(__file__).parents[1] / "examples" / "basic_consensus.py"
    namespace = runpy.run_path(str(example), run_name="not_main")
    result = asyncio.run(namespace["main"]())
    assert result is None


def test_result_module_is_json_serializable_via_example(capsys: object) -> None:
    del capsys
    assert json.dumps({"version": neural_mesh.__version__}) == '{"version": "0.2.0"}'
