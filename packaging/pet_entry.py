"""Dedicated native companion process; its own macOS application identity."""
import argparse
import multiprocessing
from pathlib import Path
from switchboard.config import Config
from switchboard.desktop_pet import run

if __name__=='__main__':
    multiprocessing.freeze_support()
    parser=argparse.ArgumentParser(prog='Laith')
    parser.add_argument('--home',type=Path)
    parser.add_argument('--panel',action='store_true')
    args=parser.parse_args()
    run(Config(args.home),args.panel)
