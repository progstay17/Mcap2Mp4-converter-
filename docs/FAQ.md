# FAQ & troubleshooting

## The installer will not start
- Confirm the download completed and the file name is **MCAP2MP4-Setup-0.3.2.exe**.
- Download it again from the project's official GitHub repository if the file may be incomplete.
- Follow your organization's Windows software-installation policy.

## A recording does not convert
- Keep the original `.mcap` file unchanged.
- Confirm the recording uses a supported H.265 video stream and a supported MCAP message layout.
- Try a small known-good sample to distinguish a file-specific issue from an installation issue.
- Save the error message and application logs when asking for help.

## The MP4 has no video or the duration looks wrong
MCAP recordings can contain multiple topics, timestamps, and sensor streams. Check that the intended camera topic was selected and compare the output with the source recording. If the problem persists, report the app version and a minimal reproducible example.

## The built-in player or two-camera mode is unavailable
Playback support can depend on the packaged build and libVLC availability. See the version notes in [CHANGELOG.md](CHANGELOG.md); some optional media components may need separate preparation.

## Where do I find command-line options?
Run `mcap2mp4-cli.exe --help` from your installed version. For a version check, run `mcap2mp4-cli.exe --version`.

## What should I include in a bug report?
- MCAP2MP4 version and Windows version
- The command or steps used
- The exact error message and relevant log
- Whether the source file plays elsewhere
- A small sample with sensitive data removed, if sharing is permitted

Do not share confidential recordings or sensitive data publicly.