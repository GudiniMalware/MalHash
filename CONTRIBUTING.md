# Contributing to MalHash

Discuss broad changes in an issue. For defects, include your MalHash and Python versions, platform and a reproduction with synthetic data. Use the [security policy](SECURITY.md) for vulnerabilities.

## Development setup

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -e '.[all,dev]'
python -m ruff check .
python -m ruff format --check .
python -m pytest --basetemp .pytest-tmp
```

For SSDEEP compatibility tests, install the original `ssdeep` CLI and put it on `PATH`. On Windows x64, `ssdeep-wheel` provides the original libfuzzy engine as a test-only reference. For headless CLI testing, create a separate environment with `.[dev]` and verify the PySide6 package is absent.

## Code and reviews

- Keep file discovery, hashing and report behavior shared by the CLI and GUI. Preserve the Python API and stable structured report keys.
- Use centralized translation keys. Update all supported languages for changed user-facing strings. Keep code, identifiers and development documentation in English.
- Add focused tests for changed behavior. Use generated files and include edge cases where relevant. Never add real malware, sensitive paths, sample binaries or suspicious archives to the repository.
- State the platforms you tested and the commands you ran. Mention changes that affect dependencies, potentially untrusted input or evidence handling.

Read the [build guide](docs/BUILDING.md) if you are changing installation or release automation. Contributions to the original project are licensed under the [MIT License](LICENSE).
