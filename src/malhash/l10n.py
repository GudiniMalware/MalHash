"""Shared message catalogs for the CLI, reports and GUI."""

import json
from functools import cache
from importlib.resources import files

LANGUAGES = ("en", "ru", "es")


@cache
def _catalog(language: str) -> dict[str, str]:
    base = files("malhash").joinpath("locales")
    messages = json.loads(base.joinpath(f"{language}.json").read_text(encoding="utf-8"))
    gui = base.joinpath(f"gui_{language}.json")
    if gui.is_file():
        messages.update(json.loads(gui.read_text(encoding="utf-8")))
    return messages


def translate(language: str, key: str, **values: object) -> str:
    """Return a localized message, falling back to English."""
    if language not in LANGUAGES:
        language = "en"
    message = _catalog(language).get(key)
    if message is None:
        message = _catalog("en").get(key, key)
    return message.format(**values) if values else message


def error_text(language: str, code: str, detail: str = "") -> str:
    message = translate(language, f"issue.{code}")
    return f"{message} ({detail})" if detail else message
