from __future__ import annotations

from docutils import nodes
from docutils.parsers.rst import roles


def _make_role(css_class: str):
    def role(name, rawtext, text, lineno, inliner, options=None, content=None):
        del name, rawtext, lineno, inliner
        options = options or {}
        content = content or []
        node = nodes.inline(text, text, classes=[css_class])
        return [node], []

    return role


def setup(app):
    from . import __version__

    roles.register_local_role("nrd_changed", _make_role("nrd_changed"))
    roles.register_local_role("nrd_ins", _make_role("nrd_ins"))
    roles.register_local_role("nrd_del", _make_role("nrd_del"))
    roles.register_local_role("nrd_link", _make_role("nrd_link"))

    metadata: dict[str, str | bool] = {
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }

    if __version__ is not None:
        metadata["version"] = __version__
    return metadata
