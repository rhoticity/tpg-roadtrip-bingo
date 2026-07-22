# tpg-roadtrip-bingo

## Bingo board generator

The repository includes a Python-based bingo board generator that reads prompts from `prompts.md`, writes PNG boards to `boards/`, and stores matching JSON metadata beside each board so existing boards can be rerolled in place.

### Local usage

Install dependencies:

```bash
python -m pip install --requirement requirements.txt
```

Create one board:

```bash
python scripts/generate_boards.py create-one <username>
```

Create one board for every username listed in `users.md`:

```bash
python scripts/generate_boards.py create-all
```

Update an existing board by rerolling one or more prompts already present on it:

```bash
python scripts/generate_boards.py update-existing <username> "<prompt on board>" "<another prompt on board>"
```

### GitHub Actions issue trigger

Opening a new issue runs `.github/workflows/generate-bingo-boards.yml`.

- Issue body `create all` runs create-all mode.
- A single-line issue body is treated as a username and runs create-one mode.
- A multi-line issue body uses the first line as the target username and each remaining line as a prompt to reroll on that existing board.
