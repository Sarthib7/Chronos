#!/usr/bin/env python3
"""Chronos as a Masumi agentic service.

Run:
    uv run masumi run main.py --standalone   # execute a job, no payment
    uv run masumi run main.py                # MIP-003 API server
"""

from dotenv import load_dotenv

load_dotenv()

from masumi import run  # noqa: E402

from agent import DEFAULT_TIMEFRAME, LIMIT_DEFAULT, LIMIT_MAX, LIMIT_MIN, TIMEFRAMES, process_job  # noqa: E402

INPUT_SCHEMA = {
    "input_data": [
        {
            "id": "topic",
            "type": "text",
            "name": "Topic",
            "data": {
                "placeholder": "agentic payments",
                "description": (
                    "What to track. Words from your topic must appear in an "
                    "article for it to be included, so prefer the terms the "
                    "press actually uses."
                ),
            },
            "validations": [
                {"validation": "min", "value": "2"},
                {"validation": "max", "value": "120"},
            ],
        },
        {
            "id": "timeframe",
            "type": "option",
            "name": "Lookback window",
            "data": {
                "description": "How far back to search.",
                "default": DEFAULT_TIMEFRAME,
                "values": TIMEFRAMES,
            },
            "validations": [
                {"validation": "min", "value": "1"},
                {"validation": "max", "value": "1"},
            ],
        },
        {
            "id": "limit",
            "type": "number",
            "name": "Articles",
            "data": {
                "description": f"How many articles to return ({LIMIT_MIN}-{LIMIT_MAX}).",
                "default": str(LIMIT_DEFAULT),
            },
            "validations": [
                {"validation": "min", "value": str(LIMIT_MIN)},
                {"validation": "max", "value": str(LIMIT_MAX)},
                {"validation": "optional", "value": "true"},
            ],
        },
    ]
}


if __name__ == "__main__":
    run(start_job_handler=process_job, input_schema_handler=INPUT_SCHEMA)
