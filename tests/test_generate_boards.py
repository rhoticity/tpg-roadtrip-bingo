from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generate_boards


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
            png_path.write_bytes(b"placeholder")

            with patch.object(generate_boards, "BOARDS_DIR", boards_dir):
                updated_path = generate_boards.update_existing(
                    "tester",
                    ["Hard 1"],
                    self.prompts,
                    random.Random(4),
                )

            updated_cells = json.loads(metadata_path.read_text(encoding="utf-8"))["cells"]
            updated_cell = next(cell for cell in updated_cells if cell["index"] == 3)
            self.assertEqual(updated_path, png_path)
            self.assertEqual(updated_cell["category"], "Hard")
            self.assertEqual(updated_cell["row"], 0)
            self.assertEqual(updated_cell["col"], 3)
            self.assertNotEqual(updated_cell["text"], "Hard 1")


if __name__ == "__main__":
    unittest.main()
