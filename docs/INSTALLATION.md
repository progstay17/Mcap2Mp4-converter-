# Installation guide

## Windows installer (recommended)

1. Download **MCAP2MP4-Setup-0.3.2.exe** from the [installer link](https://github.com/progstay17/Mcap2Mp4-converter-/raw/refs/heads/main/MCAP2MP4-Setup-0.3.2.exe).
2. Open the downloaded installer and follow the setup wizard.
3. Launch MCAP2MP4 from the Start Menu or the shortcut created during setup.
4. Select an MCAP recording, review the available options, and start conversion.
5. Open the output folder and verify the resulting MP4.

Only download the installer from this project's repository. If Windows displays a security prompt, verify the file and source before choosing whether to continue.

## Command-line use

Run the CLI executable from its folder:

```powershell
.\mcap2mp4-cli.exe --version
.\mcap2mp4-cli.exe --help
```

The `--help` output from your installed version is the source of truth for supported command-line options.

## Build from source

Use Python 3.12 and install the development requirements:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m unittest discover tests
python -m mcap2mp4 --help
```

Packaging scripts and the Windows build workflow are available in the repository's `build/` and `.github/workflows/` directories.