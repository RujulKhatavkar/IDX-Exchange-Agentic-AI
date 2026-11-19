import subprocess
import sys
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-coordinator"))
import coordinator  # noqa: E402


def test_mixed_search_and_market_start_concurrently():
    rendezvous = Barrier(2)

    def run(command):
        rendezvous.wait(timeout=5)
        return {"status": "ok", "data": command}

    result = coordinator.orchestrate([
        {"agent": "search", "query": "3 beds under $900k in Pasadena", "city": "Pasadena"},
        {"agent": "market", "city": "Pasadena"},
    ], runner=run)
    search = result["results"]["search"]["data"]
    market = result["results"]["market"]["data"]
    assert search[0] == "idx-property-search/search_properties.py"
    assert search[1][-1] == "3 beds under $900k in Pasadena"
    assert market == ("idx-market-stats/market_stats.py",
                      ["overview", "--city=Pasadena", "--months=12"])


@pytest.mark.parametrize(("task", "script"), [
    ({"agent": "search", "mode": "comps", "city": "Irvine"},
     "idx-property-search/search_properties.py"),
    ({"agent": "search", "mode": "semantic", "query": "Quiet craftsman"},
     "idx-semantic-search/semantic_search.py"),
    ({"agent": "recommend", "listing": "MLS123"},
     "idx-recommendations/recommend.py"),
    ({"agent": "knowledge", "query": "What's DOM?"},
     "idx-rag/rag.py"),
])
def test_specialist_dispatch(task, script):
    result = coordinator.orchestrate([task], runner=lambda command: command)
    assert result["results"][task["agent"]][0] == script


def test_refinement_carries_prior_filters_and_page():
    _, args = coordinator.command_for({
        "agent": "search", "query": "actually 4 beds", "city": "Pasadena",
        "known": {"city": "Irvine", "maxPrice": 900000}, "page": 2,
    })
    assert args == [
        "search", "--page=2", '--known={"city": "Pasadena", "maxPrice": 900000}',
        "--", "actually 4 beds",
    ]


def test_draft_with_successful_and_failed_specialists():
    def run(command):
        if command[0] == "idx-rag/rag.py":
            return {"status": "error", "error": "Specialist timed out"}
        return {"status": "ok", "data": {"summary": {"sold_count": 30}}}

    result = coordinator.orchestrate([
        {"agent": "email_draft", "brief": "Send Maya a Pasadena market summary with a DOM definition"},
        {"agent": "market", "city": "Pasadena"},
        {"agent": "knowledge", "query": "What is DOM?"},
    ], runner=run)["results"]
    assert result["market"]["data"]["summary"]["sold_count"] == 30
    assert result["knowledge"]["status"] == "error"
    assert result["email_draft"]["status"] == "draft_only"


def test_draft_only_and_unsupported_never_start_specialist():
    def unexpected(command):
        pytest.fail(f"Unexpected execution: {command}")

    assert coordinator.orchestrate([], runner=unexpected)["status"] == "unsupported"
    draft = {"agent": "email_draft", "brief": "Draft a follow-up"}
    assert coordinator.orchestrate([draft], runner=unexpected)["results"]["email_draft"] == {
        "status": "draft_only", "brief": "Draft a follow-up",
    }


@pytest.mark.parametrize("invalid", [
    {"agent": "send_email"},
    {"agent": "market", "city": "Irvine"},
    {"agent": "search", "query": "Find homes"},
    {"agent": "recommend"},
    {"agent": "knowledge", "query": ""},
])
def test_invalid_plan_is_rejected_before_any_dispatch(invalid):
    def unexpected(command):
        pytest.fail(f"Unexpected execution: {command}")

    with pytest.raises(ValueError):
        coordinator.orchestrate([{"agent": "market", "city": "Pasadena"}, invalid], unexpected)


def test_specialist_arguments_are_passed_literally(tmp_path, monkeypatch):
    script = tmp_path / "echo.py"
    script.write_text("import json, sys\nprint(json.dumps({'args': sys.argv[1:]}))\n")
    monkeypatch.setattr(coordinator, "SKILLS", tmp_path)
    args = ["--", "--help; $(echo injected)"]
    assert coordinator.run_specialist(("echo.py", args)) == {
        "status": "ok", "data": {"args": args},
    }


def test_timeout_returns_an_error_without_child_output(monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs["timeout"], output="private")

    monkeypatch.setattr(coordinator.subprocess, "run", time_out)
    assert coordinator.run_specialist(("echo.py", [])) == {
        "status": "error", "error": "Specialist timed out",
    }
