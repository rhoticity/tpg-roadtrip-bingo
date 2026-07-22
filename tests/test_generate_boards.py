from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generate_boards

PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC_BYTES = b"%PDF-"


class GenerateBoardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts = {
            "Easy": [f"Easy {index}" for index in range(20)],
            "Easy license plate letter/number game, only one per card from this category": [f"Plate {index}" for index in range(10)],
            "Medium": [f"Medium {index}" for index in range(20)],
            "Hard": [f"Hard {index}" for index in range(20)],
        }

    def test_build_randomized_cells_uses_expected_distribution(self) -> None:
        cells = generate_boards.build_randomized_cells(self.prompts, random.Random(1))

        self.assertEqual(len(cells), 25)
        self.assertEqual(cells[generate_boards.CENTER_INDEX].text, generate_boards.FREE_SPACE_TEXT)

        counts: dict[str, int] = {}
        for cell in cells:
            counts[cell.category] = counts.get(cell.category, 0) + 1

        self.assertEqual(counts["Easy"], 11)
        self.assertEqual(counts["Easy license plate letter/number game, only one per card from this category"], 1)
        self.assertEqual(counts["Medium"], 7)
        self.assertEqual(counts["Hard"], 5)
        self.assertEqual(counts[generate_boards.FREE_SPACE_CATEGORY], 1)

    def test_parse_issue_request_detects_all_modes(self) -> None:
        self.assertEqual(generate_boards.parse_issue_request("create all\n").mode, "create-all")
        self.assertEqual(generate_boards.parse_issue_request("discord-user\n").mode, "create-one")

        update_request = generate_boards.parse_issue_request("discord-user\nPrompt A\nPrompt B\n")
        self.assertEqual(update_request.mode, "update-existing")
        self.assertEqual(update_request.username, "discord-user")
        self.assertEqual(update_request.prompts, ("Prompt A", "Prompt B"))

    def test_update_existing_preserves_category_and_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            boards_dir = Path(tmpdir)
            pdf_path = boards_dir / "tester.pdf"
            png_path = boards_dir / "tester.png"
            metadata_path = boards_dir / "tester.json"
            initial_cells = [
                generate_boards.PromptCell(index=index, row=index // 5, col=index % 5, category="Easy", text=f"Easy {index}")
                for index in range(25)
            ]
            initial_cells[generate_boards.CENTER_INDEX] = generate_boards.PromptCell(
                index=generate_boards.CENTER_INDEX,
                row=2,
                col=2,
                category=generate_boards.FREE_SPACE_CATEGORY,
                text=generate_boards.FREE_SPACE_TEXT,
            )
            initial_cells[3] = generate_boards.PromptCell(index=3, row=0, col=3, category="Hard", text="Hard 1")
            generate_boards.write_metadata("tester", initial_cells, metadata_path)
            pdf_path.write_bytes(b"placeholder")

            with patch.object(generate_boards, "BOARDS_DIR", boards_dir):
                updated_path = generate_boards.update_existing(
                    "tester",
                    ["Hard 1"],
                    self.prompts,
                    random.Random(4),
                )

            updated_cells = json.loads(metadata_path.read_text(encoding="utf-8"))["cells"]
            updated_cell = next(cell for cell in updated_cells if cell["index"] == 3)
            self.assertEqual(updated_path, pdf_path)
            self.assertEqual(png_path.read_bytes()[:8], PNG_MAGIC_BYTES)
            self.assertEqual(updated_cell["category"], "Hard")
            self.assertEqual(updated_cell["row"], 0)
            self.assertEqual(updated_cell["col"], 3)
            self.assertNotEqual(updated_cell["text"], "Hard 1")

    def test_update_existing_never_replaces_prompt_with_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            boards_dir = Path(tmpdir)
            metadata_path = boards_dir / "tester.json"
            initial_cells = [
                generate_boards.PromptCell(index=index, row=index // 5, col=index % 5, category="Easy", text=f"Easy {index}")
                for index in range(25)
            ]
            initial_cells[generate_boards.CENTER_INDEX] = generate_boards.PromptCell(
                index=generate_boards.CENTER_INDEX,
                row=2,
                col=2,
                category=generate_boards.FREE_SPACE_CATEGORY,
                text=generate_boards.FREE_SPACE_TEXT,
            )
            initial_cells[3] = generate_boards.PromptCell(index=3, row=0, col=3, category="Hard", text="Hard 0")
            generate_boards.write_metadata("tester", initial_cells, metadata_path)
            (boards_dir / "tester.pdf").write_bytes(b"placeholder")

            # Use a mock RNG that always picks index 0 from the candidates list.
            # Before the fix, candidates included "Hard 0" itself, so index 0 would
            # pick "Hard 0" again. After the fix, "Hard 0" is excluded from candidates
            # and index 0 picks the first genuinely different prompt.
            class AlwaysFirstRNG:
                def choice(self, seq: list) -> object:
                    if not seq:
                        raise IndexError("Cannot choose from an empty sequence")
                    return seq[0]

            with patch.object(generate_boards, "BOARDS_DIR", boards_dir):
                generate_boards.update_existing(
                    "tester",
                    ["Hard 0"],
                    self.prompts,
                    AlwaysFirstRNG(),  # type: ignore[arg-type]
                )

            updated_cells = json.loads(metadata_path.read_text(encoding="utf-8"))["cells"]
            updated_cell = next(cell for cell in updated_cells if cell["index"] == 3)
            self.assertNotEqual(updated_cell["text"], "Hard 0")

    def test_create_one_generates_png_pdf_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            boards_dir = Path(tmpdir)

            with patch.object(generate_boards, "BOARDS_DIR", boards_dir):
                pdf_path = generate_boards.create_one("tester", self.prompts, random.Random(1))

            png_path = boards_dir / "tester.png"
            metadata_path = boards_dir / "tester.json"
            self.assertEqual(pdf_path, boards_dir / "tester.pdf")
            self.assertEqual(pdf_path.read_bytes()[:5], PDF_MAGIC_BYTES)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["username"], "tester")
            self.assertEqual(len(metadata["cells"]), 25)
            self.assertEqual(png_path.read_bytes()[:8], PNG_MAGIC_BYTES)

    def test_parse_prompts_extracts_markdown_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_file = Path(tmpdir) / "prompts.md"
            prompts_file.write_text(
                "\n".join(
                    (
                        "## Easy",
                        "- Easy prompt",
                        "## Easy license plate letter/number game, only one per card from this category",
                        "- Plate prompt",
                        "## Medium",
                        "- Medium prompt with a [link](https://example.com/path)",
                        "## Hard",
                        "- Hard prompt",
                    )
                ),
                encoding="utf-8",
            )

            prompts = generate_boards.parse_prompts(prompts_file)
            medium_prompt = prompts["Medium"][0]
            self.assertEqual(medium_prompt.text, "Medium prompt with a link")
            self.assertEqual(medium_prompt.urls, ("https://example.com/path",))
            self.assertEqual(len(medium_prompt.inline_links), 1)
            self.assertEqual(medium_prompt.inline_links[0].text, "link")
            self.assertEqual(medium_prompt.inline_links[0].url, "https://example.com/path")

    def test_extract_inline_links_handles_multiple_links_same_anchor(self) -> None:
        prompt = "See [list](https://example.com/a) and also [list](https://example.com/b)"
        links = generate_boards.extract_inline_links(prompt)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0].text, "list")
        self.assertEqual(links[0].url, "https://example.com/a")
        self.assertEqual(links[1].text, "list")
        self.assertEqual(links[1].url, "https://example.com/b")

    def test_metadata_round_trips_inline_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = Path(tmpdir) / "board.json"
            original_cells = [
                generate_boards.PromptCell(
                    index=i,
                    row=i // 5,
                    col=i % 5,
                    category="Medium",
                    text="Any restaurant on this list",
                    urls=("https://example.com/list",),
                    inline_links=(generate_boards.InlineLink(text="list", url="https://example.com/list"),),
                )
                for i in range(25)
                if i != generate_boards.CENTER_INDEX
            ]
            original_cells.insert(
                generate_boards.CENTER_INDEX,
                generate_boards.PromptCell(
                    index=generate_boards.CENTER_INDEX,
                    row=2,
                    col=2,
                    category=generate_boards.FREE_SPACE_CATEGORY,
                    text=generate_boards.FREE_SPACE_TEXT,
                ),
            )
            generate_boards.write_metadata("test", original_cells, metadata_path)
            restored_cells = generate_boards.read_metadata(metadata_path)

            cell_with_link = next(c for c in restored_cells if c.index == 0)
            self.assertEqual(len(cell_with_link.inline_links), 1)
            self.assertEqual(cell_with_link.inline_links[0].text, "list")
            self.assertEqual(cell_with_link.inline_links[0].url, "https://example.com/list")

    def test_metadata_backward_compat_no_inline_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = Path(tmpdir) / "board.json"
            # Write metadata without inline_links (simulating old format)
            import json
            payload = {
                "username": "old_user",
                "generated_at": "2025-01-01T00:00:00+00:00",
                "cells": [
                    {
                        "index": i,
                        "row": i // 5,
                        "col": i % 5,
                        "category": "Easy",
                        "text": f"Easy {i}",
                        "urls": [],
                    }
                    for i in range(25)
                ],
            }
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")
            restored_cells = generate_boards.read_metadata(metadata_path)
            self.assertEqual(len(restored_cells), 25)
            self.assertEqual(restored_cells[0].inline_links, ())

    def test_build_randomized_cells_preserves_inline_links(self) -> None:
        prompts_with_links = {
            "Easy": [f"Easy {i}" for i in range(20)],
            "Easy license plate letter/number game, only one per card from this category": [f"Plate {i}" for i in range(10)],
            "Medium": [
                generate_boards.PromptOption(
                    text="A restaurant on this list",
                    urls=("https://example.com",),
                    inline_links=(generate_boards.InlineLink(text="list", url="https://example.com"),),
                )
            ]
            + [f"Medium {i}" for i in range(1, 20)],
            "Hard": [f"Hard {i}" for i in range(20)],
        }
        cells = generate_boards.build_randomized_cells(prompts_with_links, random.Random(1))
        cells_with_links = [c for c in cells if c.inline_links]
        self.assertGreater(len(cells_with_links), 0)
        linked_cell = cells_with_links[0]
        self.assertEqual(linked_cell.inline_links[0].text, "list")
        self.assertEqual(linked_cell.inline_links[0].url, "https://example.com")


if __name__ == "__main__":
    unittest.main()
