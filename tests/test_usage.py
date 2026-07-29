from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from neural_mesh import CallableProvider, ConsensusEngine, JsonlUsageStore, ProviderResponse
from neural_mesh.usage import UsageRecord


def usage_record(*, cost_complete: bool = True) -> UsageRecord:
    return UsageRecord(
        timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
        task_sha256="a" * 64,
        agreement_level="unanimous",
        providers_queried=2,
        providers_succeeded=2,
        requested_max_tokens=100,
        reported_input_tokens=20,
        reported_output_tokens=30,
        token_reporting_complete=True,
        reported_cost_usd=Decimal("0.12"),
        cost_reporting_complete=cost_complete,
        duration_ms=15,
    )


@pytest.mark.asyncio
async def test_jsonl_store_persists_privacy_minimized_record_and_statistics(
    tmp_path: Path,
) -> None:
    usage_path = tmp_path / "state" / "usage.jsonl"
    store = JsonlUsageStore(usage_path)

    async def complete(_prompt: str, _max_tokens: int) -> ProviderResponse:
        return ProviderResponse(
            "private response",
            input_tokens=5,
            output_tokens=7,
            cost_usd=Decimal("0.02"),
        )

    result = await ConsensusEngine(
        [CallableProvider("provider", complete)],
        usage_store=store,
    ).run("private task", "private prompt")

    assert result.usage_recorded is True
    persisted = usage_path.read_text(encoding="utf-8")
    assert "private task" not in persisted
    assert "private prompt" not in persisted
    assert "private response" not in persisted
    item = json.loads(persisted)
    assert len(item["task_sha256"]) == 64

    stats = await store.statistics()
    assert stats.records == 1
    assert stats.invalid_records == 0
    assert stats.agreement_distribution == {"insufficient": 1}
    assert stats.providers_queried == 1
    assert stats.providers_succeeded == 1
    assert stats.reported_input_tokens == 5
    assert stats.reported_output_tokens == 7
    assert stats.reported_cost_usd == Decimal("0.02")
    assert stats.incomplete_cost_records == 0
    assert stats.to_dict()["reported_cost_usd"] == "0.02"


@pytest.mark.asyncio
async def test_statistics_are_streaming_bounded_and_tolerate_invalid_lines(
    tmp_path: Path,
) -> None:
    usage_path = tmp_path / "usage.jsonl"
    store = JsonlUsageStore(usage_path, durable=False)
    await store.append(usage_record())
    with usage_path.open("ab") as stream:
        stream.write(b"not-json\n")
        stream.write(b"x" * 16_385 + b"\n")
        stream.write(json.dumps(usage_record(cost_complete=False).to_dict()).encode() + b"\n")
        stream.write(b"\xff\xfe\n")

    bounded = await store.statistics(max_records=3)
    assert bounded.records == 1
    assert bounded.invalid_records == 2
    assert bounded.truncated is True
    assert bounded.providers_queried == 2

    complete = await store.statistics(max_records=5)
    assert complete.records == 2
    assert complete.invalid_records == 3
    assert complete.truncated is False
    assert complete.providers_queried == 4
    assert complete.reported_cost_usd == Decimal("0.24")
    assert complete.incomplete_cost_records == 1


@pytest.mark.asyncio
async def test_empty_store_and_argument_validation(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "missing.jsonl")
    stats = await store.statistics()
    assert stats.records == 0
    assert stats.to_dict()["agreement_distribution"] == {}

    with pytest.raises(TypeError):
        await store.statistics(max_records=True)
    with pytest.raises(ValueError):
        await store.statistics(max_records=0)
    with pytest.raises(TypeError):
        JsonlUsageStore(tmp_path / "usage", max_file_bytes=True)
    with pytest.raises(ValueError):
        JsonlUsageStore(tmp_path / "usage", max_file_bytes=100)
    with pytest.raises(TypeError):
        JsonlUsageStore(tmp_path / "usage", durable=cast(bool, "yes"))
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError):
        JsonlUsageStore(directory)


def test_usage_record_validates_privacy_metadata() -> None:
    valid = usage_record()
    values = valid.to_dict()
    assert values["task_sha256"] == "a" * 64

    with pytest.raises(ValueError):
        replace(valid, task_sha256="invalid")
    with pytest.raises(ValueError):
        replace(valid, timestamp=datetime(2026, 7, 28))
    with pytest.raises(ValueError):
        replace(valid, agreement_level="")
    with pytest.raises(ValueError):
        replace(valid, providers_queried=-1)
    with pytest.raises(TypeError):
        replace(valid, reported_cost_usd=cast(Decimal, 1))
    with pytest.raises(ValueError):
        replace(valid, reported_cost_usd=Decimal("NaN"))


@pytest.mark.asyncio
async def test_store_rejects_oversized_serialized_record(tmp_path: Path) -> None:
    store = JsonlUsageStore(tmp_path / "usage.jsonl")
    record = replace(usage_record(), agreement_level="x" * 20_000)

    with pytest.raises(ValueError, match="record-size"):
        await store.append(record)


@pytest.mark.asyncio
async def test_statistics_reject_semantically_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    base = usage_record().to_dict()
    rows: list[object] = [
        [],
        {**base, "agreement_level": ""},
        {**base, "providers_queried": -1},
        {**base, "reported_cost_usd": "-1"},
        {**base, "reported_cost_usd": "NaN"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    statistics = await JsonlUsageStore(path).statistics()

    assert statistics.records == 0
    assert statistics.invalid_records == len(rows)


@pytest.mark.asyncio
async def test_store_size_limit_raises_without_partial_append(tmp_path: Path) -> None:
    usage_path = tmp_path / "full.jsonl"
    usage_path.write_bytes(b"x" * 16_300)
    store = JsonlUsageStore(usage_path, max_file_bytes=16_384)

    with pytest.raises(OSError):
        await store.append(usage_record())
    assert usage_path.stat().st_size == 16_300


def test_symbolic_link_usage_path_is_rejected_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symbolic links are not available in this environment")

    with pytest.raises(ValueError, match="symbolic link"):
        JsonlUsageStore(link)


@pytest.mark.asyncio
async def test_usage_path_replacement_with_symlink_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    store = JsonlUsageStore(path)
    try:
        os.symlink(target, path)
    except OSError:
        pytest.skip("symbolic links are not available in this environment")

    with pytest.raises(OSError):
        await store.append(usage_record())
    with pytest.raises(OSError):
        await store.statistics()
    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not enforced on Windows")
async def test_new_usage_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"

    await JsonlUsageStore(path).append(usage_record())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
