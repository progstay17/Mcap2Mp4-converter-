import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from mcap2mp4.cli import main
    raise SystemExit(main())
