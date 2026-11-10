from __future__ import annotations

import pytest

from ollama_workstation.cli import build_parser


def test_benchmark_cli_defaults_to_repeatable_core_suite() -> None:
    args = build_parser().parse_args(["benchmark", "qwen3:8b"])
    assert args.command == "benchmark"
    assert args.model == "qwen3:8b"
    assert args.suite == "core"
    assert args.repeats == 3
    assert args.output is None


def test_benchmark_cli_accepts_custom_report_path_and_repeats(tmp_path) -> None:
    destination = tmp_path / "reports" / "model.json"
    args = build_parser().parse_args([
        "benchmark", "local:latest", "--suite", "11vated-core",
        "--repeats", "5", "--output", str(destination),
    ])
    assert args.suite == "11vated-core"
    assert args.repeats == 5
    assert args.output == destination


def test_benchmark_cli_rejects_out_of_range_repeats() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["benchmark", "local:latest", "--repeats", "6"])
