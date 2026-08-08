from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from neural_mesh import (
    CallableProvider,
    ComparisonPolicy,
    ConsensusEngine,
    ConsensusReachedScorer,
    ContainsScorer,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationPolicy,
    EvaluationReport,
    EvaluationRunConfig,
    EvaluationRunner,
    EvaluationScore,
    EvaluationSummary,
    ExactMatchScorer,
    ExcludesScorer,
    JsonObjectScorer,
    ProviderResponse,
    ReplaySuite,
    ScoreStatus,
    compare_reports,
    load_evaluation_report,
    load_replay_suite,
)
from neural_mesh.cli import main


def replay_document(*, failing: bool = False, complete_cost: bool = True) -> dict[str, object]:
    cost = "0.01" if complete_cost else None

    def recorded(content: str, *, model: str | None = None) -> dict[str, object]:
        return {
            "content": content,
            "model": model,
            "input_tokens": 5,
            "output_tokens": 6,
            "cost_usd": cost,
        }

    return {
        "schema_version": "neural-mesh-replay-suite/v1",
        "name": "support-policy",
        "consensus_config": {
            "max_providers": 3,
            "max_concurrency": 2,
            "minimum_successful_responses": 2,
            "minimum_cluster_size": 2,
            "minimum_agreement_ratio": 0.6,
            "similarity_threshold": 0.8,
        },
        "policy": {
            "minimum_case_pass_rate": 1.0,
            "minimum_mean_agreement_ratio": 0.6,
            "maximum_provider_failure_rate": 0.0,
            "maximum_total_cost_usd": "0.06",
            "maximum_p95_duration_ms": 1000,
            "require_complete_cost_reporting": complete_cost,
        },
        "run_config": {
            "max_cases": 10,
            "max_case_concurrency": 2,
            "max_total_provider_calls": 30,
        },
        "cases": [
            {
                "id": "retry",
                "task": "support/retry",
                "prompt": "How should this request retry?",
                "expected_text": "Use bounded backoff with jitter.",
                "required_substrings": ["jitter"],
                "tags": ["reliability"],
                "responses": {
                    "alpha": recorded("Use bounded backoff with jitter.", model="alpha-1"),
                    "beta": recorded(
                        "Retry forever." if failing else "Use bounded backoff with jitter."
                    ),
                    "gamma": recorded("Use a fixed retry delay."),
                },
            },
            {
                "id": "escalate",
                "task": "support/escalation",
                "prompt": "What should happen after repeated failures?",
                "expected_text": "Escalate to support.",
                "responses": {
                    "alpha": recorded("Escalate to support."),
                    "beta": recorded("Escalate to support."),
                    "gamma": recorded("Ignore the failures."),
                },
            },
        ],
    }


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


async def passing_report() -> EvaluationReport:
    suite = _suite_from_document(replay_document())
    return await suite.build_runner().run(suite.name, suite.cases)


def _suite_from_document(document: Mapping[str, object]) -> ReplaySuite:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "suite.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_replay_suite(path)


@pytest.mark.asyncio
async def test_replay_suite_produces_privacy_minimized_passing_report(tmp_path: Path) -> None:
    suite = load_replay_suite(write_json(tmp_path / "suite.json", replay_document()))
    report = await suite.build_runner().run(suite.name, suite.cases)

    assert report.passed is True
    assert report.summary.cases_total == 2
    assert report.summary.cases_passed == 2
    assert report.summary.case_pass_rate == 1.0
    assert report.summary.mean_agreement_ratio == pytest.approx(2 / 3)
    assert report.summary.provider_failure_rate == 0.0
    assert report.provider_names == ("alpha", "beta", "gamma")
    assert report.case_results[0].consensus_sha256 is not None
    assert report.case_results[0].scores[:3] == (
        EvaluationScore("consensus_reached", ScoreStatus.PASSED, 2 / 3, "consensus_selected"),
        EvaluationScore("exact_match", ScoreStatus.PASSED, 1.0, "matched"),
        EvaluationScore("contains_required", ScoreStatus.PASSED, 1.0, "all_present"),
    )
    assert all(score.status is ScoreStatus.SKIPPED for score in report.case_results[0].scores[3:])

    artifact = json.dumps(report.to_dict())
    assert "How should this request retry?" not in artifact
    assert "Use bounded backoff with jitter." not in artifact
    assert "private" not in artifact


@pytest.mark.asyncio
async def test_report_round_trip_is_validated(tmp_path: Path) -> None:
    report = await passing_report()
    path = write_json(tmp_path / "report.json", report.to_dict())
    loaded = load_evaluation_report(path)
    assert loaded.to_dict() == report.to_dict()

    tampered = report.to_dict()
    summary_value = tampered["summary"]
    assert isinstance(summary_value, dict)
    summary = dict(summary_value)
    summary["cases_passed"] = 0
    tampered["summary"] = summary
    write_json(path, tampered)
    with pytest.raises(ValueError, match="summary does not match"):
        load_evaluation_report(path)


@pytest.mark.asyncio
async def test_policy_reports_quality_reliability_cost_and_latency_failures() -> None:
    summary = EvaluationSummary(
        cases_total=2,
        cases_passed=1,
        case_pass_rate=0.5,
        mean_agreement_ratio=0.4,
        provider_failure_rate=0.5,
        p95_duration_ms=200,
        total_reported_cost_usd=Decimal("2"),
        cost_reporting_complete=True,
    )
    policy = EvaluationPolicy(
        minimum_case_pass_rate=0.9,
        minimum_mean_agreement_ratio=0.7,
        maximum_provider_failure_rate=0.1,
        maximum_total_cost_usd=Decimal("1"),
        maximum_p95_duration_ms=100,
        require_complete_cost_reporting=True,
    )
    assert {violation.reason for violation in policy.violations(summary)} == {
        "case_pass_rate_below_minimum",
        "mean_agreement_ratio_below_minimum",
        "provider_failure_rate_above_maximum",
        "latency_budget_exceeded",
        "cost_budget_exceeded",
    }

    incomplete = replace(summary, cost_reporting_complete=False)
    reasons = {violation.reason for violation in policy.violations(incomplete)}
    assert "cost_reporting_incomplete" in reasons
    assert "cost_budget_unverifiable" in reasons


def test_builtin_scorers_cover_skip_failure_and_normalization() -> None:
    case = EvaluationCase(
        "case",
        "task",
        "prompt",
        expected_text="Expected  answer",
        required_substrings=("ANSWER", "expected"),
    )
    result = _result(" expected answer ")
    assert ExactMatchScorer().score(case, result).passed
    assert ContainsScorer().score(case, result).passed
    assert not ExactMatchScorer(case_sensitive=True).score(case, result).passed
    assert not ContainsScorer(case_sensitive=True).score(case, result).passed

    no_reference = EvaluationCase("none", "task", "prompt")
    assert ExactMatchScorer().score(no_reference, result).status is ScoreStatus.SKIPPED
    assert ContainsScorer().score(no_reference, result).status is ScoreStatus.SKIPPED
    no_consensus = replace(result, consensus_text=None)
    assert not ConsensusReachedScorer().score(case, no_consensus).passed
    assert not ExactMatchScorer().score(case, no_consensus).passed
    assert not ContainsScorer().score(case, no_consensus).passed


def test_forbidden_and_json_scorers_cover_structured_contracts() -> None:
    case = EvaluationCase(
        "structured",
        "task",
        "prompt",
        forbidden_substrings=("secret", "password"),
        required_json_keys=("decision", "reason"),
    )
    valid = _result('{"decision": "allow", "reason": "bounded"}')
    assert ExcludesScorer().score(case, valid).passed
    assert JsonObjectScorer().score(case, valid).passed

    forbidden = _result('{"decision": "allow", "reason": "contains SECRET"}')
    assert not ExcludesScorer().score(case, forbidden).passed
    missing = _result('{"decision": "allow"}')
    assert not JsonObjectScorer().score(case, missing).passed
    invalid = _result("not json")
    assert JsonObjectScorer().score(case, invalid).reason == "invalid_json"
    array = _result("[]")
    assert JsonObjectScorer().score(case, array).reason == "not_json_object"

    no_requirements = EvaluationCase("none", "task", "prompt")
    assert ExcludesScorer().score(no_requirements, valid).status is ScoreStatus.SKIPPED
    assert JsonObjectScorer().score(no_requirements, valid).status is ScoreStatus.SKIPPED
    no_consensus = replace(valid, consensus_text=None)
    assert not ExcludesScorer().score(case, no_consensus).passed
    assert not JsonObjectScorer().score(case, no_consensus).passed


@pytest.mark.asyncio
async def test_custom_scorer_failure_is_redacted() -> None:
    class BrokenScorer:
        name = "broken"

        def score(self, case: EvaluationCase, result: Any) -> EvaluationScore:
            del case, result
            raise RuntimeError("private scorer detail")

    async def response(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        return "same"

    engine = ConsensusEngine([CallableProvider("one", response), CallableProvider("two", response)])
    runner = EvaluationRunner(engine, scorers=[BrokenScorer()])
    report = await runner.run("suite", [EvaluationCase("case", "task", "prompt")])
    score = report.case_results[0].scores[0]
    assert score.status is ScoreStatus.ERROR
    assert score.reason == "scorer_error"
    assert report.passed is False
    assert "private scorer detail" not in json.dumps(report.to_dict())


@pytest.mark.asyncio
async def test_custom_scorer_contract_failure_is_isolated() -> None:
    class MismatchedScorer:
        name = "expected"

        def score(self, case: EvaluationCase, result: Any) -> EvaluationScore:
            del case, result
            return EvaluationScore("unexpected", ScoreStatus.PASSED, 1.0, "passed")

    async def response(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        return "same"

    engine = ConsensusEngine([CallableProvider("one", response), CallableProvider("two", response)])
    report = await EvaluationRunner(engine, scorers=[MismatchedScorer()]).run(
        "suite", [EvaluationCase("case", "task", "prompt")]
    )
    assert report.case_results[0].scores[0].status is ScoreStatus.ERROR


@pytest.mark.asyncio
async def test_runner_enforces_case_and_provider_call_bounds() -> None:
    async def response(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        return "same"

    engine = ConsensusEngine([CallableProvider("one", response), CallableProvider("two", response)])
    case = EvaluationCase("case", "task", "prompt")
    with pytest.raises(ValueError, match="must not be empty"):
        await EvaluationRunner(engine).run("suite", [])
    with pytest.raises(ValueError, match="ids must be unique"):
        await EvaluationRunner(engine).run("suite", [case, case])
    with pytest.raises(ValueError, match="max_cases"):
        await EvaluationRunner(
            engine,
            config=EvaluationRunConfig(max_cases=1),
        ).run("suite", [case, replace(case, case_id="two", prompt="two")])
    with pytest.raises(ValueError, match="max_total_provider_calls"):
        await EvaluationRunner(
            engine,
            config=EvaluationRunConfig(max_total_provider_calls=3),
        ).run("suite", [case, replace(case, case_id="two", prompt="two")])
    with pytest.raises(TypeError, match="only EvaluationCase"):
        await EvaluationRunner(engine).run("suite", [case, "not-a-case"])  # type: ignore[list-item]


def test_runner_requires_unique_nonempty_scorers() -> None:
    async def response(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        return "same"

    engine = ConsensusEngine([CallableProvider("one", response), CallableProvider("two", response)])
    with pytest.raises(ValueError, match="at least one"):
        EvaluationRunner(engine, scorers=[])
    with pytest.raises(ValueError, match="must be unique"):
        EvaluationRunner(engine, scorers=[ExactMatchScorer(), ExactMatchScorer()])


@pytest.mark.asyncio
async def test_runner_cancellation_reaches_provider() -> None:
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def slow(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
        return "late"

    engine = ConsensusEngine([CallableProvider("one", slow), CallableProvider("two", slow)])
    task = asyncio.create_task(
        EvaluationRunner(engine).run("suite", [EvaluationCase("case", "task", "prompt")])
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_compare_reports_detects_case_and_metric_regressions() -> None:
    baseline = await passing_report()
    candidate_case = replace(
        baseline.case_results[0],
        passed=False,
        duration_ms=baseline.case_results[0].duration_ms + 10,
        agreement_ratio=baseline.case_results[0].agreement_ratio - 0.2,
        providers_succeeded=2,
        provider_errors=1,
        reported_cost_usd=baseline.case_results[0].reported_cost_usd + Decimal("1"),
    )
    second_case = replace(
        baseline.case_results[1],
        duration_ms=baseline.case_results[1].duration_ms + 10,
    )
    candidate_cases = (candidate_case, second_case)
    candidate_summary = EvaluationSummary(
        cases_total=2,
        cases_passed=1,
        case_pass_rate=0.5,
        mean_agreement_ratio=baseline.summary.mean_agreement_ratio - 0.1,
        provider_failure_rate=1 / 6,
        p95_duration_ms=baseline.summary.p95_duration_ms + 10,
        total_reported_cost_usd=baseline.summary.total_reported_cost_usd + Decimal("1"),
        cost_reporting_complete=True,
    )
    candidate = replace(
        baseline,
        suite_name="candidate",
        case_results=candidate_cases,
        summary=candidate_summary,
    )
    comparison = compare_reports(
        baseline,
        candidate,
        ComparisonPolicy(
            maximum_cost_increase_usd=Decimal("0"),
            maximum_p95_duration_increase_ms=0,
        ),
    )
    assert comparison.passed is False
    assert comparison.regressed_cases == ("retry",)
    assert {violation.reason for violation in comparison.violations} == {
        "pass_rate_regressed",
        "agreement_regressed",
        "provider_failures_increased",
        "case_regression_budget_exceeded",
        "latency_regressed",
        "cost_regressed",
    }

    allowed = compare_reports(
        baseline,
        candidate,
        ComparisonPolicy(
            maximum_pass_rate_drop=0.5,
            maximum_agreement_drop=0.1,
            maximum_failure_rate_increase=1 / 6,
            maximum_cost_increase_usd=Decimal("1"),
            maximum_p95_duration_increase_ms=10,
            maximum_regressed_cases=1,
        ),
    )
    assert allowed.passed is True


@pytest.mark.asyncio
async def test_compare_reports_requires_same_cases_and_complete_cost() -> None:
    baseline = await passing_report()
    mismatched = replace(
        baseline,
        case_results=(
            replace(baseline.case_results[0], case_id="different"),
            baseline.case_results[1],
        ),
    )
    with pytest.raises(ValueError, match="same case ids"):
        compare_reports(baseline, mismatched)

    incomplete_cases = tuple(
        replace(case, cost_reporting_complete=False) for case in baseline.case_results
    )
    incomplete = replace(
        baseline,
        case_results=incomplete_cases,
        summary=replace(baseline.summary, cost_reporting_complete=False),
    )
    comparison = compare_reports(
        baseline,
        incomplete,
        ComparisonPolicy(maximum_cost_increase_usd=Decimal("0")),
    )
    assert comparison.cost_delta_usd is None
    assert comparison.violations[0].reason == "cost_regression_unverifiable"


def test_cli_evaluate_and_compare_support_ci_exit_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite_path = write_json(tmp_path / "suite.json", replay_document())
    baseline_path = tmp_path / "baseline.json"
    assert main(["evaluate", str(suite_path), "--output", str(baseline_path)]) == 0
    assert json.loads(baseline_path.read_text())["passed"] is True

    candidate_path = tmp_path / "candidate.json"
    assert main(["evaluate", str(suite_path), "--output", str(candidate_path), "--compact"]) == 0
    assert "\n" not in candidate_path.read_text().strip()
    assert main(["compare", str(baseline_path), str(candidate_path)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    failed_suite = write_json(tmp_path / "failed-suite.json", replay_document(failing=True))
    failed_report = tmp_path / "failed-report.json"
    assert main(["evaluate", str(failed_suite), "--output", str(failed_report)]) == 1
    assert json.loads(failed_report.read_text())["passed"] is False
    assert main(["compare", str(baseline_path), str(failed_report)]) == 1


def test_cli_returns_bounded_error_for_invalid_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = write_json(tmp_path / "bad.json", {"schema_version": "unknown"})
    assert main(["evaluate", str(bad)]) == 2
    error = capsys.readouterr().err
    assert error.startswith("neural-mesh: error:")
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": "wrong"}, "unsupported replay suite schema"),
        ({"cases": []}, "cases must not be empty"),
    ],
)
def test_replay_loader_rejects_invalid_contract(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    document = replay_document()
    document.update(change)
    with pytest.raises(ValueError, match=message):
        load_replay_suite(write_json(tmp_path / "suite.json", document))


def test_replay_loader_requires_unique_prompts(tmp_path: Path) -> None:
    document = replay_document()
    cases = document["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    second = cases[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    second["prompt"] = first["prompt"]
    with pytest.raises(ValueError, match="prompts must be unique"):
        load_replay_suite(write_json(tmp_path / "suite.json", document))


def test_replay_loader_requires_complete_provider_matrix(tmp_path: Path) -> None:
    document = replay_document()
    cases = document["cases"]
    assert isinstance(cases, list)
    responses = cases[1]["responses"]
    assert isinstance(responses, dict)
    del responses["gamma"]
    with pytest.raises(ValueError, match="every replay provider"):
        load_replay_suite(write_json(tmp_path / "suite.json", document))


def test_loaders_reject_invalid_json_and_oversized_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        load_replay_suite(invalid)
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        load_evaluation_report(invalid)

    import neural_mesh.evaluation as evaluation

    valid = write_json(tmp_path / "valid.json", replay_document())
    monkeypatch.setattr(evaluation, "_MAX_REPLAY_BYTES", 1)
    with pytest.raises(ValueError, match="50 MB"):
        load_replay_suite(valid)
    monkeypatch.setattr(evaluation, "_MAX_REPORT_BYTES", 1)
    with pytest.raises(ValueError, match="100 MB"):
        load_evaluation_report(valid)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda doc: doc["cases"].__setitem__(slice(None), doc["cases"] * 5), "hard case limit"),
        (lambda doc: doc["cases"][0].update(responses={"one": "x"}), "at least two"),
        (
            lambda doc: doc["cases"][0].update(required_substrings=[1]),
            "must contain only strings",
        ),
        (lambda doc: doc["cases"][0].update(expected_text=1), "string or null"),
        (lambda doc: doc["cases"][0].update(responses=[]), "responses must be an object"),
        (lambda doc: doc.update(consensus_config=[]), "consensus_config must be an object"),
    ],
)
def test_replay_loader_rejects_malformed_nested_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    message: str,
) -> None:
    document = replay_document()
    if message == "hard case limit":
        import neural_mesh.evaluation as evaluation

        monkeypatch.setattr(evaluation, "_MAX_CASES_HARD_LIMIT", 3)
    mutate(document)
    with pytest.raises((TypeError, ValueError), match=message):
        load_replay_suite(write_json(tmp_path / "suite.json", document))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EvaluationCase("bad id", "task", "prompt"),
        lambda: EvaluationCase("id", "", "prompt"),
        lambda: EvaluationCase("id", "task", ""),
        lambda: EvaluationCase("id", "task", "prompt", required_substrings=("x", "x")),
        lambda: EvaluationCase("id", "task", "prompt", required_substrings=("x",) * 101),
        lambda: EvaluationCase("id", "task", "prompt", tags=("x", "x")),
        lambda: EvaluationCase("id", "task", "prompt", forbidden_substrings=("x", "x")),
        lambda: EvaluationCase("id", "task", "prompt", required_json_keys=("x", "x")),
        lambda: EvaluationCase("id", "task", "prompt", tags=tuple(f"x{i}" for i in range(51))),
        lambda: EvaluationCase("id", "task", "prompt", expected_text=1),  # type: ignore[arg-type]
        lambda: EvaluationCase("id", "task", "x" * 1_000_001),
        lambda: EvaluationScore("score", ScoreStatus.PASSED, "1", "reason"),  # type: ignore[arg-type]
        lambda: EvaluationScore("score", ScoreStatus.PASSED, 2.0, "reason"),
        lambda: EvaluationScore("score", ScoreStatus.PASSED, float("nan"), "reason"),
        lambda: EvaluationRunConfig(max_cases=0),
        lambda: EvaluationRunConfig(max_case_concurrency=33),
        lambda: EvaluationPolicy(minimum_case_pass_rate=2.0),
        lambda: EvaluationPolicy(maximum_total_cost_usd=1),  # type: ignore[arg-type]
        lambda: EvaluationPolicy(maximum_total_cost_usd=Decimal("-1")),
        lambda: EvaluationPolicy(maximum_p95_duration_ms=True),
        lambda: ComparisonPolicy(maximum_cost_increase_usd=1),  # type: ignore[arg-type]
        lambda: ComparisonPolicy(maximum_cost_increase_usd=Decimal("NaN")),
        lambda: ComparisonPolicy(maximum_regressed_cases=-1),
    ],
)
def test_public_models_reject_invalid_values(factory: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_report_models_reject_inconsistent_and_untrusted_values() -> None:
    result = EvaluationCaseResult(
        case_id="case",
        task="task",
        tags=(),
        passed=True,
        scores=(EvaluationScore("score", ScoreStatus.PASSED, 1.0, "passed"),),
        duration_ms=1,
        agreement_level="unanimous",
        agreement_ratio=1.0,
        providers_queried=2,
        providers_succeeded=2,
        provider_errors=0,
        provider_timeouts=0,
        invalid_responses=0,
        reported_cost_usd=Decimal("0"),
        cost_reporting_complete=True,
        consensus_sha256="a" * 64,
    )
    summary = EvaluationSummary(1, 1, 1.0, 1.0, 0.0, 1, Decimal("0"), True)
    EvaluationReport(
        "suite",
        datetime.now(timezone.utc),
        1,
        ("one", "two"),
        (result,),
        summary,
        (),
    )
    with pytest.raises(ValueError, match="outcome counts"):
        replace(result, provider_errors=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        replace(result, providers_queried=1)
    with pytest.raises(ValueError, match="reported_cost_usd"):
        replace(result, reported_cost_usd=Decimal("-1"))
    with pytest.raises(ValueError, match="SHA-256"):
        replace(result, consensus_sha256="bad")
    with pytest.raises(ValueError, match="summary does not match"):
        EvaluationReport(
            "suite",
            datetime.now(timezone.utc),
            1,
            ("one", "two"),
            (result,),
            replace(summary, case_pass_rate=0.5),
            (),
        )
    with pytest.raises(ValueError, match="timezone"):
        EvaluationReport("suite", datetime.now(), 1, ("one",), (result,), summary, ())
    with pytest.raises(ValueError, match="between 1 and 32"):
        EvaluationReport("suite", datetime.now(timezone.utc), 1, (), (result,), summary, ())
    with pytest.raises(ValueError, match="must be unique"):
        EvaluationReport(
            "suite", datetime.now(timezone.utc), 1, ("one", "one"), (result,), summary, ()
        )
    with pytest.raises(ValueError, match="unique ids"):
        EvaluationReport(
            "suite", datetime.now(timezone.utc), 1, ("one",), (result, result), summary, ()
        )
    with pytest.raises(ValueError, match="total_reported_cost_usd"):
        replace(summary, total_reported_cost_usd=Decimal("-1"))


@pytest.mark.asyncio
async def test_report_loader_rejects_tampered_envelope(tmp_path: Path) -> None:
    report = (await passing_report()).to_dict()
    for key, value, message in (
        ("schema_version", "old", "unsupported report schema"),
        ("created_at", "2026-01-01T00:00:00", "timezone"),
        ("passed", False, "passed flag"),
    ):
        tampered = dict(report)
        tampered[key] = value
        with pytest.raises(ValueError, match=message):
            load_evaluation_report(write_json(tmp_path / f"{key}.json", tampered))


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": "neural-mesh-evaluation/v1"},
        [],
    ],
)
def test_report_loader_rejects_wrong_shapes(tmp_path: Path, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_evaluation_report(write_json(tmp_path / "report.json", value))


def _result(content: str) -> Any:
    from neural_mesh import AgreementLevel, ConsensusResult, ProviderOutcome, ProviderStatus

    response = ProviderResponse(content)
    return ConsensusResult(
        task="task",
        created_at=datetime.now(timezone.utc),
        duration_ms=1,
        requested_max_tokens=10,
        outcomes=(
            ProviderOutcome("one", ProviderStatus.SUCCESS, 1, response),
            ProviderOutcome("two", ProviderStatus.SUCCESS, 1, response),
        ),
        agreement_level=AgreementLevel.UNANIMOUS,
        agreement_ratio=1.0,
        average_similarity=1.0,
        consensus_text=content,
        winning_providers=("one", "two"),
        reported_input_tokens=0,
        reported_output_tokens=0,
        token_reporting_complete=False,
        reported_cost_usd=Decimal("0"),
        cost_reporting_complete=False,
    )
