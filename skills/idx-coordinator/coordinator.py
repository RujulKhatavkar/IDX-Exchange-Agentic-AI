"""Dispatch an OpenClaw routing plan to the IDX specialists."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys


SKILLS = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 45
ROUTES = {"search", "market", "recommend", "knowledge", "email_draft"}


def required_text(request, field):
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{request['agent']} requires {field}")
    return value.strip()


def positive_int(request, field, default, maximum):
    value = request.get(field, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


def command_for(request):
    route = request["agent"]
    if route == "search":
        mode = request.get("mode", "active")
        if mode == "comps":
            city = required_text(request, "city")
            months = positive_int(request, "months", 12, 120)
            return "idx-property-search/search_properties.py", [
                "comps", f"--city={city}", f"--months={months}",
            ]
        query = required_text(request, "query")
        if mode == "semantic":
            return "idx-semantic-search/semantic_search.py", ["search", "--", query]
        if mode != "active":
            raise ValueError("search mode must be active, comps, or semantic")
        page = positive_int(request, "page", 1, 10000)
        known = request.get("known", {})
        if not isinstance(known, dict) or any(
            type(value) not in (str, int, float, type(None)) for value in known.values()
        ):
            raise ValueError("known must be an object of prior search filters")
        city = required_text(request, "city")
        return "idx-property-search/search_properties.py", [
            "search", f"--page={page}", "--known=" + json.dumps({**known, "city": city}),
            "--", query,
        ]
    if route == "market":
        city = required_text(request, "city")
        months = positive_int(request, "months", 12, 120)
        args = ["overview", f"--city={city}", f"--months={months}"]
        for option in ("zip", "type"):
            if option in request:
                args.append(f"--{option}={required_text(request, option)}")
        return "idx-market-stats/market_stats.py", args
    if route == "recommend":
        listing = required_text(request, "listing")
        args = ["similar"]
        if "city" in request:
            args.append(f"--city={required_text(request, 'city')}")
        return "idx-recommendations/recommend.py", [*args, "--", listing]
    return "idx-rag/rag.py", ["retrieve", "--", required_text(request, "query")]


def run_specialist(command):
    script, args = command
    project_python = SKILLS.parent / "venv" / "bin" / "python"
    interpreter = str(project_python) if project_python.is_file() else sys.executable
    try:
        completed = subprocess.run(
            [interpreter, str(SKILLS / script), *args],
            capture_output=True, text=True, check=True, timeout=TIMEOUT_SECONDS,
        )
        data = json.loads(completed.stdout)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object from specialist")
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Specialist timed out"}
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {"status": "error", "error": "Specialist failed to return usable data"}
    return {"status": "ok", "data": data}


def orchestrate(plan, runner=run_specialist):
    if not isinstance(plan, list) or len(plan) > len(ROUTES):
        raise ValueError("Expected an array with at most five agent requests")
    commands = {}
    draft = None
    for request in plan:
        if not isinstance(request, dict):
            raise ValueError("Each request must be an object")
        route = request.get("agent")
        if not isinstance(route, str) or route not in ROUTES:
            raise ValueError("Unknown agent")
        if route in commands or (route == "email_draft" and draft is not None):
            raise ValueError(f"Duplicate agent: {route}")
        if route == "email_draft":
            draft = {"brief": required_text(request, "brief"), "status": "draft_only"}
        else:
            commands[route] = command_for(request)

    if not plan:
        return {"status": "unsupported", "results": {}}

    # Keep each specialist in its own process, including its database pool.
    results = {}
    if commands:
        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            tasks = {route: executor.submit(runner, cmd) for route, cmd in commands.items()}
            results = {route: task.result() for route, task in tasks.items()}
    if draft is not None:
        results["email_draft"] = draft
    return {"status": "ok", "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="JSON array of specialist requests")
    args = parser.parse_args()
    try:
        output = orchestrate(json.loads(args.plan))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
