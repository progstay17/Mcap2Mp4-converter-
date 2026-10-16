# Contributing to MCAP2MP4

Thanks for helping improve MCAP2MP4.

## Development setup

Use Python 3.12. On Windows, create and activate a virtual environment, then install the development requirements:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

## Run tests

Run the automated test suite before opening a pull request:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

The same test command runs in GitHub Actions on pushes to `main` and pull requests targeting `main`.

## Pull requests

- Keep changes focused and explain the user-facing reason for the change.
- Add or update tests when behavior changes.
- Update `docs/CHANGELOG.md` for user-visible changes.
- Do not commit generated installers, build output, recordings, credentials, or machine-specific configuration.
- Do not remove bundled VLC files or other runtime assets without verifying the packaged application still works.
- Preserve original MCAP recordings when testing conversion workflows.

## Reporting a problem

Include the application version, Windows/Python details where relevant, steps to reproduce, expected behavior, actual behavior, and sanitized logs. Do not attach private or sensitive recordings unless you have permission to share them.
