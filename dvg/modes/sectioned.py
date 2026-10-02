"""Approach A — sectioned single call (stub; implemented on feat/freeform-sectioned).

One LLM call writes a scene that follows the section contract (dvg.sections); it
is scanned, API-checked, contract-checked, then rendered through the parallel
engine (dvg.parallel_render) with the chosen --render-strategy.
"""

from __future__ import annotations


def run(args) -> None:
    raise NotImplementedError(
        "freeform-sectioned (approach A) is implemented on branch feat/freeform-sectioned")
