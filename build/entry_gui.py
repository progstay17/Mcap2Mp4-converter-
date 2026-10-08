import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()          # wajib: worker konversi memakai spawn
    from mcap2mp4.gui import main
    raise SystemExit(main())
