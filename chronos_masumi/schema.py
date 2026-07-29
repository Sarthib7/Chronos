"""The input schema buyers see, in MIP-003 Attachment 01 form."""

from __future__ import annotations

from agent import DEFAULT_TIMEFRAME, LIMIT_DEFAULT, LIMIT_MAX, LIMIT_MIN, TIMEFRAMES


def normalize_input_data(raw: dict | list) -> dict:
    """Accept both shapes a buyer may send.

    MIP-003 documents `input_data` as an array of {key, value} pairs, and that is
    what marketplace clients send. A plain object is the more natural thing to
    hand-write and is what our own CLI-shaped tests use. Rejecting either one
    would break real callers, so both normalise to a flat dict.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        fields = {}
        for item in raw:
            if isinstance(item, dict) and "key" in item:
                fields[str(item["key"])] = item.get("value")
        return fields
    raise TypeError("input_data must be an object or a list of {key, value} pairs")

INPUT_SCHEMA = {
    "input_data": [
        {
            "id": "topic",
            "type": "text",
            "name": "Topic",
            "data": {
                "placeholder": "agentic payments",
                "description": (
                    "What to track. Words from your topic must appear in an article "
                    "for it to be included, so prefer the terms the press actually "
                    "uses. Chronos covers AI and machine-learning news."
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
