#!/usr/bin/env python3
"""Migrate tasks_source.json → tasks.db (SQLite). 幂等。"""
from __future__ import annotations

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                      # scripts/ (file_lock)
sys.path.insert(0, str(_HERE.parent / "dashboard"))  # dashboard/ (store)

from store.json_store import JsonTaskStore   # noqa: E402
from store.sqlite_store import SqliteTaskStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(_HERE.parent / "data"))
    args = ap.parse_args()

    data_dir = pathlib.Path(args.data_dir)
    src = JsonTaskStore(data_dir / "tasks_source.json")
    dst = SqliteTaskStore(data_dir / "tasks.db")
    tasks = src.load_tasks()
    dst.save_tasks(tasks)
    print(f"migrated {len(tasks)} tasks → {data_dir / 'tasks.db'} (now {dst.count()})")


if __name__ == "__main__":
    main()
