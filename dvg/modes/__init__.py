"""Opt-in freeform pipelines added alongside sequential `--mode freeform`.

Each module exposes `run(args)` and is dispatched from dvg.generate. They render
only through dvg.parallel_render (never dvg.freeform._run) so a section or an
animation slice can be rendered in isolation with its own media dir.
"""
