from __future__ import annotations

import json

from docutils import nodes
from docutils.parsers.rst import roles
from sphinx.util.docutils import SphinxDirective


class DiffTable(SphinxDirective):
    has_content = True

    def run(self) -> list[nodes.Node]:
        self.assert_has_content()
        try:
            data = json.loads("\n".join(self.content))
            field, old_label, new_label = data["headers"]
            header = nodes.row()
            for label in (field, old_label, new_label):
                header += nodes.entry("", nodes.paragraph("", label))

            body = nodes.tbody()
            for field, old_segments, new_segments in data["rows"]:
                row = nodes.row()
                row += nodes.entry("", nodes.paragraph("", field))
                row += self._source_cell(old_segments)
                row += self._source_cell(new_segments)
                body += row
        except (KeyError, TypeError, ValueError) as error:
            raise self.error(f"Invalid nrd-diff-table data: {error}") from error

        table = nodes.table(classes=["need_rd_table", "colwidths-auto"])
        self.set_source_info(table)
        group = nodes.tgroup(cols=3)
        table += group
        for width in (16, 42, 42):
            group += nodes.colspec(colwidth=width)
        group += nodes.thead("", header)
        group += body
        return [table]

    @staticmethod
    def _source_cell(segments: list[list[str | None]]) -> nodes.entry:
        entry = nodes.entry()
        # Sphinx uses the raw source to distinguish parsed literal blocks from code.
        block = nodes.literal_block(json.dumps(segments))
        for text, css_class in segments:
            if not isinstance(text, str):
                raise ValueError("each diff segment must contain text")
            if css_class not in (None, "nrd_del", "nrd_ins", "nrd_changed"):
                raise ValueError(f"unknown diff class {css_class!r}")
            # The writer records the original newline style in the inline marker.
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if css_class:
                block += nodes.inline("", text, classes=[css_class])
            elif text:
                block += nodes.Text(text)
        if len(block):
            entry += block
        return entry


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

    app.add_directive("nrd-diff-table", DiffTable)
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
