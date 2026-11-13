"""Tiny todo list stored in todos.json.  python todo.py add "buy milk" | list | done N"""
import json, sys
from pathlib import Path

DB = Path(__file__).with_name("todos.json")


def load():
    return json.loads(DB.read_text()) if DB.exists() else []


def save(items):
    DB.write_text(json.dumps(items, indent=2))


def add(text):
    items = load()
    items.append({"text": text, "done": False})
    save(items)


def done(n):
    items = load()
    items[n]["done"] = True
    save(items)


def show():
    for i, t in enumerate(load(), 1):
        print(f"{i}. [{'x' if t['done'] else ' '}] {t['text']}")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["list"]
    if cmd == "add":
        add(" ".join(rest))
    elif cmd == "done":
        done(int(rest[0]))
    show()
