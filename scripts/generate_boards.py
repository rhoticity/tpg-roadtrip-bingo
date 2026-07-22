from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

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
class PromptCell:
    index: int
    row: int
    col: int
    category: str
    text: str


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


def parse_prompts(path: Path = PROMPTS_PATH) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
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
            categories[current_category].append(clean_prompt_text(line[2:].strip()))

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


def sanitize_username(username: str) -> str:
    cleaned = username.strip()
    if not cleaned:
        raise ValueError("Username must not be empty")
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    if cleaned in {".", ".."}:
        raise ValueError("Username must not resolve to a filesystem path")
    return cleaned.removesuffix(".png")


def board_paths(username: str) -> tuple[Path, Path]:
    safe_username = sanitize_username(username)
    return BOARDS_DIR / f"{safe_username}.png", BOARDS_DIR / f"{safe_username}.json"


def build_randomized_cells(prompts_by_category: dict[str, list[str]], rng: random.Random) -> list[PromptCell]:
    chosen_entries: list[tuple[str, str]] = []
    for category, count in CATEGORY_REQUIREMENTS.items():
        options = prompts_by_category[category]
        if len(options) < count:
            raise ValueError(f"Category '{category}' does not have enough prompts")
        chosen_entries.extend((category, text) for text in rng.sample(options, count))

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
        category, text = next(entry_iter)
        cells.append(PromptCell(index=index, row=row, col=col, category=category, text=text))

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


def render_board(username: str, cells: Iterable[PromptCell], output_path: Path) -> None:
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
        draw.multiline_text((text_x, text_y), wrapped_text, fill="black", font=font, spacing=4, align="center")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")


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
            }
            for cell in cells
        ],
    }
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_metadata(metadata_path: Path) -> list[PromptCell]:
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    return [PromptCell(**cell) for cell in payload["cells"]]


def create_one(username: str, prompts_by_category: dict[str, list[str]], rng: random.Random) -> Path:
    png_path, metadata_path = board_paths(username)
    cells = build_randomized_cells(prompts_by_category, rng)
    render_board(username, cells, png_path)
    write_metadata(username, cells, metadata_path)
    return png_path


def create_all(prompts_by_category: dict[str, list[str]], rng: random.Random) -> list[Path]:
    users = parse_users()
    if not users:
        raise ValueError("users.md does not contain any usernames")
    return [create_one(username, prompts_by_category, rng) for username in users]


def update_existing(username: str, prompts_to_replace: Iterable[str], prompts_by_category: dict[str, list[str]], rng: random.Random) -> Path:
    png_path, metadata_path = board_paths(username)
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
            candidate
            for candidate in prompts_by_category[category]
            if candidate not in occupied_texts
        ]
        if not candidates:
            raise ValueError(f"No remaining prompts available to replace '{prompt}' in category '{category}'")

        replacement = rng.choice(candidates)
        occupied_texts.add(replacement)
        updated_cells[match_index] = PromptCell(
            index=existing_cell.index,
            row=existing_cell.row,
            col=existing_cell.col,
            category=category,
            text=replacement,
        )

    render_board(username, updated_cells, png_path)
    write_metadata(username, updated_cells, metadata_path)
    return png_path


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
