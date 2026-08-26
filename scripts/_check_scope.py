#!/usr/bin/env python3
"""Catch the class of bug that killed chapters 15-17: a name read before the
line that assigns it, inside the same function.

A syntax check cannot see it — the name is valid Python until that path runs —
and it cost three chapters. This walks every function and flags any local that
is LOADED on a line before its first STORE.
"""
import ast, sys
from pathlib import Path

def _comprehension_names(fn):
    """Targets of comprehensions and generator expressions.

    In `[f(a) for a in xs]` the load of `a` sits textually before the `for`
    that binds it, which looks exactly like a read-before-assignment and is
    not one. Without excluding these the check is 90% noise and gets ignored,
    which is worse than not having it.
    """
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                for t in ast.walk(gen.target):
                    if isinstance(t, ast.Name):
                        names.add(t.id)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    for t in ast.walk(item.optional_vars):
                        if isinstance(t, ast.Name):
                            names.add(t.id)
    return names


def offenders(tree):
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        bound_elsewhere = _comprehension_names(fn)
        first_store, first_load = {}, {}
        for n in ast.walk(fn):
            if not isinstance(n, ast.Name):
                continue
            d = first_store if isinstance(n.ctx, ast.Store) else first_load
            d.setdefault(n.id, n.lineno)
            if isinstance(n.ctx, ast.Store):
                first_store[n.id] = min(first_store[n.id], n.lineno)
        args = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        for name, store_line in first_store.items():
            load_line = first_load.get(name)
            if name in args or name in bound_elsewhere or load_line is None:
                continue
            if load_line < store_line:
                out.append((fn.name, name, load_line, store_line))
    return out

def main() -> int:
    bad = 0
    for f in sorted(Path("scripts").glob("*.py")):
        try:
            found = offenders(ast.parse(f.read_text(encoding="utf-8")))
        except SyntaxError as e:
            print(f"  {f.name}: SYNTAX ERROR line {e.lineno}"); bad += 1; continue
        for fn, name, load, store in found:
            print(f"  {f.name}:{load} in {fn}(): reads {name!r} before it is "
                  f"assigned at line {store}")
            bad += 1
    print("  clean" if not bad else f"  {bad} issue(s)")
    return 1 if bad else 0

if __name__ == "__main__":
    raise SystemExit(main())
