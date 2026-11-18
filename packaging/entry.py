"""Entry point of the packaged app (PyInstaller). No arguments = `switchboard launch`."""
import multiprocessing

from switchboard.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
