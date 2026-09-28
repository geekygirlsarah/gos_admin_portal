"""Normalize compose-page HTML into email-safe markup.

The messaging compose pages submit rich-text HTML straight from the browser.
Whatever arrives there is untrusted (a crafted POST never touches the editor)
and arrives in whatever shape the sender's word processor used. This module
turns that into markup an email client can render predictably:

* strips scripting, event handlers and unsafe URL schemes
* discards Microsoft Office cruft (``mso-*`` properties, ``Mso*`` classes,
  ``<o:p>``/``<w:*>`` XML islands, ``mso-list`` marker spans)
* rebuilds Word's paragraph-encoded bullets and numbers into real
  ``<ul>``/``<ol>`` elements, nested by their level
* drops blank spacer paragraphs that add phantom vertical space
* gives tables borders, collapsed borders and sensible widths
* produces a readable plain-text alternative

``EMAIL_BASE_CSS`` is the single source of truth for how a message body looks
in an email. ``programs/static/css/email_editor.css`` mirrors these rules so
the WYSIWYG canvas matches what the recipient sees. Change one, change both.
"""

from __future__ import annotations

import re
from typing import Any, Final

import nh3
from lxml import etree
from lxml import html as lxml_html
from premailer import transform

# --------------------------------------------------------------------------
# Sanitizer allowlist
# --------------------------------------------------------------------------

EMAIL_ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "caption",
        "center",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "font",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

# "class" and "mso-list" are deliberately allowed through the sanitizer even
# though they are stripped later: the Word-list rebuild has to *see* them.
EMAIL_ALLOWED_ATTRIBUTES: Final[dict[str, set[str]]] = {
    "*": {"align", "class", "dir", "id", "style", "title"},
    "a": {"href", "name", "rel", "target"},
    "font": {"color", "face", "size"},
    "img": {"align", "alt", "height", "src", "width"},
    "li": {"style", "type", "value"},
    "ol": {"start", "style", "type"},
    "p": {"align", "style"},
    "table": {
        "align",
        "bgcolor",
        "border",
        "cellpadding",
        "cellspacing",
        "role",
        "style",
        "width",
    },
    "td": {
        "align",
        "bgcolor",
        "colspan",
        "height",
        "nowrap",
        "rowspan",
        "style",
        "valign",
        "width",
    },
    "th": {
        "align",
        "bgcolor",
        "colspan",
        "height",
        "nowrap",
        "rowspan",
        "style",
        "valign",
        "width",
    },
}

EMAIL_ALLOWED_CSS_PROPERTIES: Final[frozenset[str]] = frozenset(
    {
        "background",
        "background-color",
        "border",
        "border-collapse",
        "border-color",
        "border-style",
        "border-width",
        "clear",
        "color",
        "display",
        "float",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "line-height",
        "list-style-position",
        "list-style-type",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-width",
        "mso-list",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration",
        "text-indent",
        "vertical-align",
        "white-space",
        "width",
    }
)

EMAIL_URL_SCHEMES: Final[set[str]] = {"http", "https", "mailto", "tel"}

# --------------------------------------------------------------------------
# Email base styles
# --------------------------------------------------------------------------

EMAIL_BASE_CSS: Final[str] = (
    "body{margin:0;padding:16px;background:#ffffff;color:#212529;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
    "Arial,sans-serif;font-size:16px;line-height:1.5;}"
    ".email-container{max-width:820px;margin:0 auto;}"
    "p{margin:0 0 12px 0;}"
    "h1,h2,h3,h4,h5,h6{margin:0 0 12px 0;line-height:1.25;}"
    "table{border-collapse:collapse;max-width:100%;}"
    "td,th{padding:6px;border:1px solid #dee2e6;vertical-align:top;}"
    "img{max-width:100%;height:auto;}"
    "a{color:#0d6efd;}"
)

_EMAIL_DOCUMENT: Final[str] = (
    "<!DOCTYPE html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<style>{css}</style>\n"
    "</head>\n"
    "<body>\n"
    '<div class="email-container">\n{content}\n</div>\n'
    "</body>\n"
    "</html>\n"
)

_DEFAULT_BORDER: Final[str] = "1px solid #dee2e6"
_DEFAULT_CELL_PADDING: Final[str] = "6px"

# --------------------------------------------------------------------------
# Style helpers
# --------------------------------------------------------------------------


def _parse_style(el: etree._Element) -> dict[str, str]:
    """Split a ``style`` attribute into a ``{property: value}`` dict."""
    raw = el.get("style") or ""
    props: dict[str, str] = {}
    for declaration in raw.split(";"):
        name, sep, value = declaration.partition(":")
        if not sep:
            continue
        name = name.strip().lower()
        value = value.strip()
        if name and value:
            props[name] = value
    return props


def _write_style(el: etree._Element, props: dict[str, str]) -> None:
    """Serialize ``props`` back onto ``el``, removing the attribute if empty."""
    if not props:
        el.attrib.pop("style", None)
        return
    el.set("style", "".join(f"{name}:{value};" for name, value in props.items()))


def _detach(el: etree._Element) -> None:
    """Remove ``el`` from its parent, preserving the text that followed it.

    ``lxml``'s ``remove()`` throws away an element's tail, which silently
    deletes real content when the removed element is a Word marker span.
    """
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail or ""
    if tail:
        previous = el.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


# --------------------------------------------------------------------------
# Word list reconstruction
# --------------------------------------------------------------------------

_LIST_LEVEL_RE: Final[re.Pattern[str]] = re.compile(r"level(\d+)", re.IGNORECASE)
_LIST_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\d+[.)]")


def _list_level(el: etree._Element) -> int | None:
    """Return the 1-based ``levelN`` of a Word list paragraph, else ``None``."""
    if el.tag != "p":
        return None
    style = el.get("style") or ""
    if "mso-list" not in style.lower():
        return None
    match = _LIST_LEVEL_RE.search(style)
    return int(match.group(1)) if match else 1


def _strip_marker(el: etree._Element) -> str:
    """Remove Word's ``mso-list:Ignore`` marker span; return the marker text."""
    marker = ""
    for span in list(el.iter("span")):
        if "mso-list:ignore" not in (span.get("style") or "").lower():
            continue
        marker += "".join(span.itertext())
        _detach(span)
    return marker


def _build_list(items: list[tuple[int, etree._Element]]) -> etree._Element:
    """Build a (possibly nested) list from ``(level, paragraph)`` pairs.

    Word encodes every list item as a flat ``<p>`` tagged with its depth, so
    nesting has to be reconstructed from those level numbers. The list type is
    inferred from Word's own marker glyph: a leading number means ordered.
    """
    markers = [_strip_marker(paragraph) for _, paragraph in items]
    tag = "ol" if any(_LIST_NUMBER_RE.match(m) for m in markers) else "ul"

    root = etree.Element(tag)
    # The shallowest level present maps onto the root list itself, so anchor
    # the stack there rather than at a synthetic level below it.
    base = min(level for level, _ in items)
    stack: list[tuple[int, etree._Element]] = [(base, root)]
    last_item: etree._Element | None = None

    for level, paragraph in items:
        while len(stack) > 1 and level <= stack[-1][0]:
            stack.pop()
        if level > stack[-1][0]:
            nested = etree.Element(tag)
            if last_item is not None:
                last_item.append(nested)
            else:
                root.append(nested)
            stack.append((level, nested))
        last_item = etree.SubElement(stack[-1][1], "li")
        for child in list(paragraph):
            _detach(child)
            last_item.append(child)
        text = (paragraph.text or "").strip()
        if text:
            if len(last_item):
                last_item[-1].tail = (last_item[-1].tail or "") + text
            else:
                last_item.text = text
    return root


def _rebuild_word_lists(root: etree._Element) -> None:
    """Replace each run of ``mso-list`` paragraphs with a real list element."""
    for parent in list(root.iter()):
        if not isinstance(parent.tag, str):
            continue
        children = list(parent)
        index = 0
        while index < len(children):
            level = _list_level(children[index])
            if level is None:
                index += 1
                continue
            start = index
            run: list[tuple[int, etree._Element]] = []
            while index < len(children):
                item_level = _list_level(children[index])
                if item_level is None:
                    break
                run.append((item_level, children[index]))
                index += 1
            replacement = _build_list(run)
            parent.insert(start, replacement)
            replacement.tail = run[-1][1].tail
            for _, paragraph in run:
                _detach(paragraph)


# --------------------------------------------------------------------------
# Cleanup passes
# --------------------------------------------------------------------------


def _drop_meaningless_attributes(root: etree._Element) -> None:
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        el.attrib.pop("class", None)
        # Email clients ignore `class` styling and `nowrap` on cells reliably
        # enough that leaving it in usually just causes overflow.
        el.attrib.pop("nowrap", None)
        props = _parse_style(el)
        for name in [key for key in props if key.startswith("mso-")]:
            del props[name]
        _write_style(el, props)


def _has_visible_content(el: etree._Element) -> bool:
    if el.text and el.text.strip().replace("\xa0", ""):
        return True
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.tag in {"img", "table", "hr"}:
            return True
        if _has_visible_content(child):
            return True
    return False


def _remove_spacer_blocks(root: etree._Element) -> None:
    """Delete empty ``<p>``/``<div>`` blocks that only add vertical space.

    These arrive from Word and from the editor's line-break handling, and are
    the main reason the delivered email is taller than the compose screen.
    """
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.tag not in {"p", "div"}:
            continue
        if _has_visible_content(el):
            continue
        # A spacer carries no text worth keeping, only the gap it leaves behind.
        _detach(el)


def _normalize_tables(root: etree._Element) -> None:
    for table in root.iter("table"):
        props = _parse_style(table)
        props.setdefault("border-collapse", "collapse")
        props.setdefault("width", "100%")
        _write_style(table, props)
        for cell in table.iter():
            if not isinstance(cell.tag, str) or cell.tag not in {"td", "th"}:
                continue
            cell_props = _parse_style(cell)
            if "border" not in cell_props:
                cell_props["border"] = _DEFAULT_BORDER
            if not any(name.startswith("padding") for name in cell_props):
                cell_props["padding"] = _DEFAULT_CELL_PADDING
            cell_props.setdefault("vertical-align", "top")
            _write_style(cell, cell_props)


def _inner_html(el: etree._Element) -> str:
    """Serialize an element's leading text and children as an HTML fragment."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(lxml_html.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


# --------------------------------------------------------------------------
# List-item cleanup
# --------------------------------------------------------------------------

_INLINE_TAGS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "abbr",
        "b",
        "br",
        "code",
        "em",
        "font",
        "i",
        "img",
        "mark",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "u",
    }
)
# Block tags whose presence marks an element as *not* inline-only content.
_BLOCK_TAGS: Final[frozenset[str]] = frozenset(
    {
        "blockquote",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)


def _inline_content(el: etree._Element) -> list[str | etree._Element]:
    """Return ``el``'s content as ordered ``text``/element items."""
    items: list[str | etree._Element] = []
    if el.text:
        items.append(el.text)
    for child in el:
        items.append(child)
        if child.tail:
            items.append(child.tail)
            # The element is re-parented during flattening; stop it from
            # carrying a copy of the same tail alongside the captured text.
            child.tail = None
    return items


def _is_inline_only_block(el: etree._Element) -> bool:
    """True when ``el`` is a simple block wrapping only inline content."""
    if not isinstance(el.tag, str) or el.tag not in {"p", "div"}:
        return False
    return not any(
        isinstance(desc.tag, str) and desc.tag in _BLOCK_TAGS
        for desc in el.iter()
        if desc is not el
    )


def _flatten_list_items(root: etree._Element) -> None:
    """Unwrap simple blocks inside ``<li>`` so items stay lean in email.

    The Tiptap editor serializes list items as ``<li><p>Item</p></li>``
    (ProseMirror's schema forces a block inside each item). Emails render
    ``<li>Item</li>`` far more predictably, so peel that layer back here --
    an item that holds only inline content loses its wrapping block.
    """
    for li in root.iter("li"):
        if not isinstance(li.tag, str):
            continue
        rebuilt: list[str | etree._Element] = []
        changed = False
        for child in list(li):
            if _is_inline_only_block(child):
                changed = True
                rebuilt.extend(_inline_content(child))
                if child.tail:
                    rebuilt.append(child.tail)
            else:
                rebuilt.append(child)

        if not changed:
            continue

        buffer = li.text or ""
        new_elements: list[etree._Element] = []
        for item in rebuilt:
            if isinstance(item, str):
                if buffer and not item.startswith("\xa0"):
                    buffer += " " if buffer and not buffer.endswith(" ") else ""
                    buffer += item
                else:
                    buffer += item
                continue
            if new_elements:
                new_elements[-1].tail = (new_elements[-1].tail or "") + buffer
            else:
                li.text = buffer
            buffer = ""
            new_elements.append(item)
        if new_elements:
            new_elements[-1].tail = (new_elements[-1].tail or "") + buffer
        else:
            li.text = buffer

        for old in list(li):
            li.remove(old)
        for element in new_elements:
            li.append(element)


def normalize_email_html(fragment: str) -> str:
    """Return sanitized, email-normalized HTML for a message body fragment."""
    if not fragment or not fragment.strip():
        return ""
    cleaned = nh3.clean(
        fragment,
        tags=set(EMAIL_ALLOWED_TAGS),
        attributes=EMAIL_ALLOWED_ATTRIBUTES,
        filter_style_properties=set(EMAIL_ALLOWED_CSS_PROPERTIES),
        url_schemes=EMAIL_URL_SCHEMES,
        url_relative="pass_through",
        link_rel=None,
        strip_comments=True,
    )
    if not cleaned.strip():
        return ""
    wrapper = lxml_html.fragment_fromstring(cleaned, create_parent="div")
    _rebuild_word_lists(wrapper)
    _drop_meaningless_attributes(wrapper)
    _remove_spacer_blocks(wrapper)
    _flatten_list_items(wrapper)
    _normalize_tables(wrapper)
    return _inner_html(wrapper)


def wrap_email_document(fragment: str) -> str:
    """Normalize ``fragment`` and wrap it in a complete HTML email document."""
    content = normalize_email_html(fragment)
    return _EMAIL_DOCUMENT.format(css=EMAIL_BASE_CSS, content=content)


def build_email_parts(fragment: str) -> tuple[str, str]:
    """Build the ``(html, text)`` bodies for a message sent from a fragment.

    Wraps the fragment in an email document, inlines CSS with premailer and
    derives a readable plain-text alternative.
    """
    document = wrap_email_document(fragment)
    try:
        inlined = transform(document)
    except Exception:  # pragma: no cover - premailer is already defensive
        inlined = document
    return inlined, html_to_text(inlined)


# --------------------------------------------------------------------------
# Plain-text alternative
# --------------------------------------------------------------------------

_TEXT_SKIP: Final[frozenset[str]] = frozenset({"script", "style", "head", "title"})
_TEXT_BLOCKS: Final[frozenset[str]] = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "caption",
        "center",
        "div",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "p",
        "pre",
        "section",
    }
)
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_BULLET: Final[str] = "\u2022"


def _append_collapsed(value: str | None, parts: list[str]) -> None:
    if value:
        parts.append(_WHITESPACE_RE.sub(" ", value))


def _walk_text(el: etree._Element, parts: list[str], state: dict[str, Any]) -> None:
    """Append an element's *content* to ``parts`` (its own text is handled here)."""
    tag = el.tag
    if not isinstance(tag, str) or tag in _TEXT_SKIP:
        return
    if tag == "br":
        parts.append("\n")
        return
    if tag == "img":
        _append_collapsed((el.get("alt") or "").strip(), parts)
        return

    opens = ""
    closes = ""
    if tag in {"ul", "ol"}:
        state["lists"].append({"tag": tag, "index": 0})
        opens = "\n"
    elif tag == "li":
        depth = max(len(state["lists"]) - 1, 0)
        current = state["lists"][-1] if state["lists"] else None
        if current and current["tag"] == "ol":
            current["index"] += 1
            marker = f"{current['index']}."
        else:
            marker = _BULLET
        parts.append("\n" + "  " * depth + marker + " ")
    elif tag == "tr":
        opens = "\n"
        state["cell"] = 0
    elif tag in {"td", "th"}:
        if state["cell"]:
            parts.append("\t")
        state["cell"] += 1
        # A cell is a single text column, so block breaks inside it must not
        # split the row apart.
        state["in_cell"] += 1
    elif tag in _TEXT_BLOCKS and not state["in_cell"]:
        opens = closes = "\n\n"

    parts.append(opens)
    _append_collapsed(el.text, parts)
    for child in el:
        _walk_text(child, parts, state)
        _append_collapsed(child.tail, parts)
    if tag in {"td", "th"}:
        state["in_cell"] -= 1
    parts.append(closes)

    if tag in {"ul", "ol"}:
        state["lists"].pop()
        parts.append("\n")


def html_to_text(html: str) -> str:
    """Render an HTML fragment as a readable plain-text alternative.

    Lists keep their markers, table cells are tab separated and blocks are
    separated by blank lines -- ``django.utils.html.strip_tags`` did none of
    this, which ran bulleted and tabular messages together into one line.
    """
    if not html or not html.strip():
        return ""
    wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    parts: list[str] = []
    state: dict[str, Any] = {"lists": [], "cell": 0, "in_cell": 0}
    _walk_text(wrapper, parts, state)
    text = "".join(parts)
    # Leading indentation inside a table row is a layout artifact, not content.
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
