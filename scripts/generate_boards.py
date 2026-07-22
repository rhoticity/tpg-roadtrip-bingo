from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FREE_SPACE_TEXT = "any photo of your choice"
FREE_SPACE_CATEGORY = "Free"
GRID_SIZE = 5
CENTER_INDEX = 12
CATEGORY_REQUIREMENTS = {
    "Easy": 11,
    "Easy license plate letter/number game, only one per card from this category": 1,
    "Medium": 7,
    "Hard": 5,
}
ROOT = Path(__file__).resolve().parents[1]
PROMPTS_PATH = ROOT / "prompts.md"
USERS_PATH = ROOT / "users.md"
BOARDS_DIR = ROOT / "boards"


@dataclass(frozen=True)
class InlineLink:
    text: str
    url: str


@dataclass(frozen=True)
class PromptCell:
    index: int
    row: int
    col: int
    category: str
    text: str
    urls: tuple[str, ...] = ()
    inline_links: tuple[InlineLink, ...] = ()


@dataclass(frozen=True)
class PromptOption:
    text: str
    urls: tuple[str, ...] = ()
    inline_links: tuple[InlineLink, ...] = ()


@dataclass(frozen=True)
class IssueRequest:
    mode: str
    username: str | None = None
    prompts: tuple[str, ...] = ()


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def parse_prompts(path: Path = PROMPTS_PATH) -> dict[str, list[PromptOption]]:
    categories: dict[str, list[PromptOption]] = {}
    current_category: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            current_category = line[3:].strip()
            categories.setdefault(current_category, [])
            continue
        if line.startswith("- ") and current_category:
            prompt_text = line[2:].strip()
            categories[current_category].append(
                PromptOption(
                    text=clean_prompt_text(prompt_text),
                    urls=extract_prompt_links(prompt_text),
                    inline_links=extract_inline_links(prompt_text),
                )
            )

    missing = [category for category in CATEGORY_REQUIREMENTS if category not in categories]
    if missing:
        raise ValueError(f"Missing prompt categories: {', '.join(missing)}")

    return categories


def parse_users(path: Path = USERS_PATH) -> list[str]:
    users: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        users.append(re.sub(r"^[*-]\s*", "", line))
    return users


def clean_prompt_text(prompt: str) -> str:
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", prompt)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_prompt_links(prompt: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\[[^\]]+\]\(([^)]+)\)", prompt))


def extract_inline_links(prompt: str) -> tuple[InlineLink, ...]:
    return tuple(
        InlineLink(text=m.group(1), url=m.group(2))
        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", prompt)
    )


def sanitize_username(username: str) -> str:
    cleaned = username.strip()
    if not cleaned:
        raise ValueError("Username must not be empty")
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    if cleaned in {".", ".."}:
        raise ValueError("Username must not resolve to a filesystem path")
    return re.sub(r"\.(png|pdf)$", "", cleaned)


def board_paths(username: str) -> tuple[Path, Path, Path]:
    safe_username = sanitize_username(username)
    board_path = BOARDS_DIR / safe_username
    return board_path.with_suffix(".pdf"), board_path.with_suffix(".png"), board_path.with_suffix(".json")


def as_prompt_option(prompt: str | PromptOption) -> PromptOption:
    if isinstance(prompt, PromptOption):
        return prompt
    return PromptOption(text=prompt)


def build_randomized_cells(
    prompts_by_category: dict[str, list[str | PromptOption]], rng: random.Random
) -> list[PromptCell]:
    chosen_entries: list[tuple[str, PromptOption]] = []
    for category, count in CATEGORY_REQUIREMENTS.items():
        options = [as_prompt_option(option) for option in prompts_by_category[category]]
        if len(options) < count:
            raise ValueError(f"Category '{category}' does not have enough prompts")
        chosen_entries.extend((category, option) for option in rng.sample(options, count))

    rng.shuffle(chosen_entries)
    cells: list[PromptCell] = []
    entry_iter = iter(chosen_entries)

    for index in range(GRID_SIZE * GRID_SIZE):
        row, col = divmod(index, GRID_SIZE)
        if index == CENTER_INDEX:
            cells.append(
                PromptCell(
                    index=index,
                    row=row,
                    col=col,
                    category=FREE_SPACE_CATEGORY,
                    text=FREE_SPACE_TEXT,
                )
            )
            continue
        category, option = next(entry_iter)
        cells.append(PromptCell(index=index, row=row, col=col, category=category, text=option.text, urls=option.urls, inline_links=option.inline_links))

    return cells


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    starting_size: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    for size in range(starting_size, 11, -1):
        font = load_font(size)
        lines = wrap_text(draw, text, font, max_width)
        bbox = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=4, align="center")
        if (bbox[2] - bbox[0]) <= max_width and (bbox[3] - bbox[1]) <= max_height:
            return font, lines
    font = load_font(12)
    return font, wrap_text(draw, text, font, max_width)


def draw_text_with_inline_links(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    inline_links: tuple[InlineLink, ...],
    text_x: float,
    text_y: float,
    block_width: float,
    spacing: int = 4,
) -> list[tuple[str, float, float, float, float]]:
    """
    Render wrapped text lines with hyperlinked words drawn in blue with underlines.

    Linked words are matched in the order they appear in *inline_links* so that
    repeated anchor texts (e.g. two "list" links) are paired with the correct URL.

    Returns a list of ``(url, x1, y1, x2, y2)`` tuples in image-pixel coordinates,
    one entry per link occurrence found in the rendered text.
    """
    link_color = (17, 85, 204)  # blue, similar to a default browser link
    underline_gap = 1  # pixels between the bottom of the glyph box and the underline
    ascent, _ = font.getmetrics()
    line_advance = ascent + spacing

    # Find where each inline link sits within the joined line text.
    # We join lines with a single space to mirror how wrap_text works.
    full_text = " ".join(lines)
    ordered_spans: list[tuple[int, int, str]] = []
    search_from = 0
    for link in inline_links:
        idx = full_text.find(link.text, search_from)
        if idx != -1:
            ordered_spans.append((idx, idx + len(link.text), link.url))
            search_from = idx + len(link.text)

    # Map full_text positions to (line_index, char_index_within_line).
    # The full_text is  lines[0] + " " + lines[1] + " " + …
    pos_to_line: list[tuple[int, int] | None] = []
    for li, line in enumerate(lines):
        for ci in range(len(line)):
            pos_to_line.append((li, ci))
        if li < len(lines) - 1:
            pos_to_line.append(None)  # the joining space

    # Convert span positions to per-line spans.
    line_spans: dict[int, list[tuple[int, int, str]]] = {}
    for span_start, span_end, url in ordered_spans:
        if span_start >= len(pos_to_line) or pos_to_line[span_start] is None:
            continue
        li, ci_start = pos_to_line[span_start]
        last_char = span_end - 1
        if last_char >= len(pos_to_line) or pos_to_line[last_char] is None:
            continue
        li_end, ci_end_inclusive = pos_to_line[last_char]
        if li == li_end:
            line_spans.setdefault(li, []).append((ci_start, ci_end_inclusive + 1, url))

    image_link_rects: list[tuple[str, float, float, float, float]] = []

    for li, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        line_x = text_x + (block_width - line_width) / 2
        line_draw_y = text_y + li * line_advance

        line_bbox = draw.textbbox((0, 0), line or "A", font=font)
        visual_y1 = line_draw_y + line_bbox[1]
        visual_y2 = line_draw_y + line_bbox[3]

        spans = sorted(line_spans.get(li, []))

        if not spans:
            draw.text((line_x, line_draw_y), line, fill="black", font=font)
            continue

        # Build ordered segments for this line.
        segments: list[tuple[str, str | None]] = []
        pos = 0
        for cs, ce, url in spans:
            if cs > pos:
                segments.append((line[pos:cs], None))
            segments.append((line[cs:ce], url))
            pos = ce
        if pos < len(line):
            segments.append((line[pos:], None))

        current_x = line_x
        for seg_text, url in segments:
            seg_width = draw.textlength(seg_text, font=font)
            if url:
                draw.text((current_x, line_draw_y), seg_text, fill=link_color, font=font)
                underline_y = int(visual_y2) + underline_gap
                draw.line(
                    [(int(current_x), underline_y), (int(current_x + seg_width), underline_y)],
                    fill=link_color,
                    width=1,
                )
                image_link_rects.append((url, current_x, visual_y1, current_x + seg_width, visual_y2))
            else:
                draw.text((current_x, line_draw_y), seg_text, fill="black", font=font)
            current_x += seg_width

    return image_link_rects


def render_board(username: str, cells: Iterable[PromptCell], pdf_path: Path, png_path: Path) -> None:
    outer_margin = 36
    row_label_width = 72
    username_height = 52
    bingo_height = 64
    cell_size = 220
    cell_margin = 14
    grid_width = GRID_SIZE * cell_size
    image_width = outer_margin * 2 + row_label_width + grid_width
    image_height = outer_margin * 2 + username_height + bingo_height + grid_width

    image = Image.new("RGB", (image_width, image_height), "white")
    draw = ImageDraw.Draw(image)

    username_font = load_font(26)
    header_font = load_font(42)
    row_label_font = load_font(30)
    star_font = load_font(36)

    grid_left = outer_margin + row_label_width
    grid_top = outer_margin + username_height + bingo_height
    grid_right = grid_left + grid_width
    grid_bottom = grid_top + grid_width

    username_text = sanitize_username(username)
    username_bbox = draw.textbbox((0, 0), username_text, font=username_font)
    username_x = grid_left + (grid_width - (username_bbox[2] - username_bbox[0])) / 2
    username_y = outer_margin + (username_height - (username_bbox[3] - username_bbox[1])) / 2
    draw.text((username_x, username_y), username_text, fill="black", font=username_font)

    for column, letter in enumerate("BINGO"):
        cell_left = grid_left + column * cell_size
        bbox = draw.textbbox((0, 0), letter, font=header_font)
        letter_x = cell_left + (cell_size - (bbox[2] - bbox[0])) / 2
        letter_y = outer_margin + username_height + (bingo_height - (bbox[3] - bbox[1])) / 2
        draw.text((letter_x, letter_y), letter, fill="black", font=header_font)

    for row in range(GRID_SIZE):
        label = str(row + 1)
        row_top = grid_top + row * cell_size
        bbox = draw.textbbox((0, 0), label, font=row_label_font)
        label_x = outer_margin + (row_label_width - (bbox[2] - bbox[0])) / 2
        label_y = row_top + (cell_size - (bbox[3] - bbox[1])) / 2
        draw.text((label_x, label_y), label, fill="black", font=row_label_font)

    for offset in range(GRID_SIZE + 1):
        x = grid_left + offset * cell_size
        y = grid_top + offset * cell_size
        draw.line((x, grid_top, x, grid_bottom), fill="black", width=3)
        draw.line((grid_left, y, grid_right, y), fill="black", width=3)

    cell_list = list(cells)
    cell_image_link_rects: dict[int, list[tuple[str, float, float, float, float]]] = {}
    for cell in cell_list:
        cell_left = grid_left + cell.col * cell_size
        cell_top = grid_top + cell.row * cell_size
        box = (
            cell_left + cell_margin,
            cell_top + cell_margin,
            cell_left + cell_size - cell_margin,
            cell_top + cell_size - cell_margin,
        )
        if cell.index == CENTER_INDEX:
            star_bbox = draw.textbbox((0, 0), "★", font=star_font)
            star_x = box[0] + ((box[2] - box[0]) - (star_bbox[2] - star_bbox[0])) / 2
            star_y = box[1] + 8
            draw.text((star_x, star_y), "★", fill="black", font=star_font)
            free_font, free_lines = fit_text(draw, cell.text, box[2] - box[0], box[3] - box[1] - 64, 24)
            free_text = "\n".join(free_lines)
            free_bbox = draw.multiline_textbbox((0, 0), free_text, font=free_font, spacing=4, align="center")
            free_x = box[0] + ((box[2] - box[0]) - (free_bbox[2] - free_bbox[0])) / 2
            free_y = star_y + (star_bbox[3] - star_bbox[1]) + 12
            draw.multiline_text((free_x, free_y), free_text, fill="black", font=free_font, spacing=4, align="center")
            continue

        font, lines = fit_text(draw, cell.text, box[2] - box[0], box[3] - box[1], 24)
        wrapped_text = "\n".join(lines)
        bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=4, align="center")
        text_x = box[0] + ((box[2] - box[0]) - (bbox[2] - bbox[0])) / 2
        text_y = box[1] + ((box[3] - box[1]) - (bbox[3] - bbox[1])) / 2
        if cell.inline_links:
            block_width = bbox[2] - bbox[0]
            cell_image_link_rects[cell.index] = draw_text_with_inline_links(
                draw, lines, font, cell.inline_links, text_x, text_y, block_width
            )
        else:
            draw.multiline_text((text_x, text_y), wrapped_text, fill="black", font=font, spacing=4, align="center")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")
    png_path.write_bytes(image_bytes.getvalue())
    image_bytes.seek(0)

    page_width = float(image_width)
    page_height = float(image_height)
    pdf = canvas.Canvas(str(pdf_path), pagesize=(page_width, page_height))
    pdf.drawImage(ImageReader(image_bytes), 0, 0, width=page_width, height=page_height, mask="auto")

    for cell in cell_list:
        if cell.index in cell_image_link_rects:
            # Precise per-word link overlays derived from the rendered image positions.
            for url, x1, y1, x2, y2 in cell_image_link_rects[cell.index]:
                pdf.linkURL(url, (x1, page_height - y2, x2, page_height - y1), relative=0)
        elif cell.urls:
            # Fallback: cover the entire cell (backward-compatible with metadata that
            # lacks inline_links but still has URLs stored).
            cell_left = grid_left + cell.col * cell_size
            cell_top = grid_top + cell.row * cell_size
            link_left = cell_left + cell_margin
            link_right = cell_left + cell_size - cell_margin
            link_top = cell_top + cell_margin
            link_bottom = cell_top + cell_size - cell_margin
            segment_height = (link_bottom - link_top) / len(cell.urls)
            for index, url in enumerate(cell.urls):
                segment_top = link_top + index * segment_height
                segment_bottom = segment_top + segment_height
                pdf.linkURL(
                    url,
                    (link_left, page_height - segment_bottom, link_right, page_height - segment_top),
                    relative=0,
                )

    pdf.showPage()
    pdf.save()


def write_metadata(username: str, cells: Iterable[PromptCell], metadata_path: Path) -> None:
    payload = {
        "username": sanitize_username(username),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cells": [
            {
                "index": cell.index,
                "row": cell.row,
                "col": cell.col,
                "category": cell.category,
                "text": cell.text,
                "urls": list(cell.urls),
                "inline_links": [{"text": lk.text, "url": lk.url} for lk in cell.inline_links],
            }
            for cell in cells
        ],
    }
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_metadata(metadata_path: Path) -> list[PromptCell]:
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    cells = []
    for cell in payload["cells"]:
        inline_links = tuple(
            InlineLink(text=lk["text"], url=lk["url"])
            for lk in cell.get("inline_links", [])
        )
        cell_data = {
            **cell,
            "urls": tuple(cell.get("urls", ())),
            "inline_links": inline_links,
        }
        cells.append(PromptCell(**cell_data))
    return cells


def create_one(username: str, prompts_by_category: dict[str, list[str | PromptOption]], rng: random.Random) -> Path:
    pdf_path, png_path, metadata_path = board_paths(username)
    cells = build_randomized_cells(prompts_by_category, rng)
    render_board(username, cells, pdf_path, png_path)
    write_metadata(username, cells, metadata_path)
    return pdf_path


def create_all(prompts_by_category: dict[str, list[str | PromptOption]], rng: random.Random) -> list[Path]:
    users = parse_users()
    if not users:
        raise ValueError("users.md does not contain any usernames")
    return [create_one(username, prompts_by_category, rng) for username in users]


def update_existing(
    username: str,
    prompts_to_replace: Iterable[str],
    prompts_by_category: dict[str, list[str | PromptOption]],
    rng: random.Random,
) -> Path:
    pdf_path, png_path, metadata_path = board_paths(username)
    if not metadata_path.exists():
        raise FileNotFoundError(f"Board metadata not found for {sanitize_username(username)}")

    requested_prompts = [prompt.strip() for prompt in prompts_to_replace if prompt.strip()]
    if not requested_prompts:
        raise ValueError("At least one prompt must be provided for update-existing mode")

    cells = read_metadata(metadata_path)
    updated_cells = list(cells)
    occupied_texts = {cell.text for cell in updated_cells}

    for prompt in requested_prompts:
        match_index = next((i for i, cell in enumerate(updated_cells) if cell.text == prompt), None)
        if match_index is None:
            raise ValueError(f"Prompt not found on board: {prompt}")

        existing_cell = updated_cells[match_index]
        category = existing_cell.category
        if category == FREE_SPACE_CATEGORY:
            raise ValueError("The free space cannot be rerolled")

        occupied_texts.remove(existing_cell.text)
        candidates = [
            option
            for option in (as_prompt_option(candidate) for candidate in prompts_by_category[category])
            if option.text not in occupied_texts
        ]
        if not candidates:
            raise ValueError(f"No remaining prompts available to replace '{prompt}' in category '{category}'")

        replacement = rng.choice(candidates)
        occupied_texts.add(replacement.text)
        updated_cells[match_index] = PromptCell(
            index=existing_cell.index,
            row=existing_cell.row,
            col=existing_cell.col,
            category=category,
            text=replacement.text,
            urls=replacement.urls,
            inline_links=replacement.inline_links,
        )

    render_board(username, updated_cells, pdf_path, png_path)
    write_metadata(username, updated_cells, metadata_path)
    return pdf_path


def parse_issue_request(body: str) -> IssueRequest:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Issue body must not be empty")

    if len(lines) == 1 and lines[0].lower() == "create all":
        return IssueRequest(mode="create-all")
    if len(lines) == 1:
        return IssueRequest(mode="create-one", username=lines[0])
    return IssueRequest(mode="update-existing", username=lines[0], prompts=tuple(lines[1:]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate road trip bingo boards")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_all_parser = subparsers.add_parser("create-all", help="Create a board for every user in users.md")
    create_all_parser.add_argument("--seed", type=int, default=None)

    create_one_parser = subparsers.add_parser("create-one", help="Create one board for a username")
    create_one_parser.add_argument("username")
    create_one_parser.add_argument("--seed", type=int, default=None)

    update_parser = subparsers.add_parser("update-existing", help="Reroll prompts on an existing board")
    update_parser.add_argument("username")
    update_parser.add_argument("prompts", nargs="+")
    update_parser.add_argument("--seed", type=int, default=None)

    issue_parser = subparsers.add_parser("from-issue-body", help="Dispatch a board operation from an issue body file")
    issue_parser.add_argument("body_file", type=Path)
    issue_parser.add_argument("--seed", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    prompts_by_category = parse_prompts()

    try:
        if args.command == "create-all":
            created_paths = create_all(prompts_by_category, rng)
        elif args.command == "create-one":
            created_paths = [create_one(args.username, prompts_by_category, rng)]
        elif args.command == "update-existing":
            created_paths = [update_existing(args.username, args.prompts, prompts_by_category, rng)]
        else:
            request = parse_issue_request(args.body_file.read_text(encoding="utf-8"))
            if request.mode == "create-all":
                created_paths = create_all(prompts_by_category, rng)
            elif request.mode == "create-one":
                if request.username is None:
                    raise ValueError("Create-one mode requires a username")
                created_paths = [create_one(request.username, prompts_by_category, rng)]
            else:
                if request.username is None:
                    raise ValueError("Update-existing mode requires a username")
                created_paths = [update_existing(request.username, request.prompts, prompts_by_category, rng)]
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")

    for path in created_paths:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
