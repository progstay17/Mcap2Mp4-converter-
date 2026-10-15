import multiprocessing

from mcap2mp4.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()          # wajib untuk .exe PyInstaller + spawn
    raise SystemExit(main())
