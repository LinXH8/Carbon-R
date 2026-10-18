"""The agent: a tool-calling loop over the three tools, and the words around a figure.

The model's job here is selection, not computation. It chooses which document to read,
which factor to look up and when to calculate; it never carries a quantity between tools
(see `toolspec.py` on handles) and never states a number the tools did not return (see
`audit.py`). Those two properties are what make CLAUDE.md rule 1 a structural fact rather
than a prompt instruction.
"""
