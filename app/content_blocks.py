"""Structured text content for a module item.

A text item is stored as an ordered list of blocks:

    heading        -> {"type": "heading",       "text": "..."}
    subheading     -> {"type": "subheading",    "text": "..."}
    paragraph      -> {"type": "paragraph",     "text": "..."}
    bullet_list    -> {"type": "bullet_list",   "items": ["...", "..."]}
    numbered_list  -> {"type": "numbered_list", "items": ["...", "..."]}

The teacher can send either:
  * `blocks`  - the list above (validated by Pydantic), or
  * `body`    - plain text / markdown-style text ("# Heading", "## Sub heading",
                "- bullet", "1. numbered"), which the backend parses into blocks.

Bullets typed inside a paragraph ("Intro:\\n- a\\n- b") are split out into a real
bullet_list block, so the frontend never has to guess.
"""
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PARAGRAPH = 5000
MAX_ITEM = 1000

_BULLET_RE = re.compile(r"^\s*[-*•–]\s+(\S.*?)\s*$")
_NUMBER_RE = re.compile(r"^\s*\d{1,3}[.)]\s+(\S.*?)\s*$")
_H1_RE = re.compile(r"^\s*#\s+(\S.*?)\s*$")
_H2_RE = re.compile(r"^\s*#{2,6}\s+(\S.*?)\s*$")
_MARKER_RE = re.compile(r"^\s*(?:[-*•–]|\d{1,3}[.)])\s+")


# ------------------------------------------------------------------ models
class _Block(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class HeadingBlock(_Block):
    type: Literal["heading"]
    text: str = Field(min_length=1, max_length=255)


class SubheadingBlock(_Block):
    type: Literal["subheading"]
    text: str = Field(min_length=1, max_length=255)


class ParagraphBlock(_Block):
    type: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=MAX_PARAGRAPH)


class _ListBlock(_Block):
    items: list[str] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def _clean_items(cls, items: list[str]) -> list[str]:
        cleaned = []
        for i, raw in enumerate(items, 1):
            text = _MARKER_RE.sub("", raw).strip()  # drop a stray "- " / "1. "
            if not text:
                raise ValueError(f"list item {i} is empty")
            if len(text) > MAX_ITEM:
                raise ValueError(f"list item {i} is longer than {MAX_ITEM} characters")
            cleaned.append(text)
        return cleaned


class BulletListBlock(_ListBlock):
    type: Literal["bullet_list"]


class NumberedListBlock(_ListBlock):
    type: Literal["numbered_list"]


Block = Annotated[
    Union[
        HeadingBlock,
        SubheadingBlock,
        ParagraphBlock,
        BulletListBlock,
        NumberedListBlock,
    ],
    Field(discriminator="type"),
]

_CONTENT_TYPES = {"paragraph", "bullet_list", "numbered_list"}


# ------------------------------------------------------------------ parsing
def parse_text(text: str, allow_headings: bool = True) -> list[dict]:
    """Turn plain/markdown-style text into a list of block dicts."""
    blocks: list[dict] = []
    para: list[str] = []
    items: list[str] = []
    list_kind: str | None = None

    def flush_para():
        if para:
            blocks.append({"type": "paragraph", "text": " ".join(para)})
            para.clear()

    def flush_list():
        nonlocal list_kind
        if items:
            blocks.append({"type": list_kind, "items": list(items)})
            items.clear()
        list_kind = None

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            flush_para()
            flush_list()
            continue

        if allow_headings:
            if m := _H1_RE.match(line):
                flush_para(); flush_list()
                blocks.append({"type": "heading", "text": m.group(1)})
                continue
            if m := _H2_RE.match(line):
                flush_para(); flush_list()
                blocks.append({"type": "subheading", "text": m.group(1)})
                continue

        bullet, number = _BULLET_RE.match(line), _NUMBER_RE.match(line)
        if bullet or number:
            kind = "bullet_list" if bullet else "numbered_list"
            flush_para()
            if list_kind != kind:
                flush_list()
                list_kind = kind
            items.append((bullet or number).group(1))
            continue

        if list_kind and line[0].isspace() and items:
            items[-1] += " " + line.strip()  # wrapped continuation of a list item
            continue

        flush_list()
        para.append(line.strip())

    flush_para()
    flush_list()
    return blocks


def normalize_blocks(blocks: list) -> list[dict]:
    """Dump validated blocks; split bullets typed inside a paragraph into lists."""
    out: list[dict] = []
    for block in blocks:
        data = block.model_dump()
        if data["type"] == "paragraph":
            out.extend(parse_text(data["text"], allow_headings=False))
        else:
            out.append(data)
    return out


# --------------------------------------------------------------- validation
def validate_structure(blocks: list[dict]) -> None:
    """Rules: heading -> content -> subheading -> content ...

    * a subheading needs a heading somewhere before it
    * every heading / subheading must be followed directly by content
      (a paragraph or a list), not by nothing or by another heading
    """
    errors: list[str] = []
    seen_heading = False
    for i, block in enumerate(blocks, 1):
        kind = block["type"]
        if kind == "heading":
            seen_heading = True
        elif kind == "subheading" and not seen_heading:
            errors.append(f"Block {i}: a subheading must come after a heading.")

        if kind in ("heading", "subheading"):
            nxt = blocks[i]["type"] if i < len(blocks) else None
            if nxt not in _CONTENT_TYPES:
                errors.append(
                    f"Block {i}: {kind} '{block['text']}' must be followed by content "
                    "(a paragraph or a list)."
                )
    if errors:
        raise ValueError(" ".join(errors))


def to_markdown(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        kind = b["type"]
        if kind == "heading":
            parts.append(f"# {b['text']}")
        elif kind == "subheading":
            parts.append(f"## {b['text']}")
        elif kind == "paragraph":
            parts.append(b["text"])
        elif kind == "bullet_list":
            parts.append("\n".join(f"- {t}" for t in b["items"]))
        else:
            parts.append("\n".join(f"{n}. {t}" for n, t in enumerate(b["items"], 1)))
    return "\n\n".join(parts)


def build_blocks(body: str | None, blocks: list | None):
    """Single entry point used by create/update.

    Returns (body_text, block_dicts). Raises ValueError with a readable message
    if the structure is invalid. `blocks` wins over `body` when both are sent.
    """
    if blocks is not None:
        data = normalize_blocks(blocks)
    elif body:
        data = parse_text(body)
    else:
        return None, None
    validate_structure(data)
    return (to_markdown(data) or None), data