"""
Knowledge base for LLM-generated Manim code.

Layer 1 (this package so far): an API reference read from the INSTALLED ManimCE
package (so it always matches the version we render with) and a static checker
that uses it to catch code that would certainly crash, before anything runs.
"""
