from __future__ import annotations

import argparse
from pathlib import Path


def compile_profile_prompt(*, source_dir: Path, output_file: Path) -> Path:
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Profile source directory not found: {source_dir}")

    parts = sorted(
        [
            child
            for child in source_dir.rglob("*")
            if child.is_file() and child.suffix.lower() in {".txt", ".md"}
        ],
        key=lambda item: item.as_posix(),
    )

    blocks: list[str] = []
    for part in parts:
        text = part.read_text(encoding="utf-8").strip()
        if text:
            blocks.append(text)

    compiled = "\n\n".join(blocks).strip() + "\n"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(compiled, encoding="utf-8")
    return output_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile modular private profile prompts into one file.")
    parser.add_argument(
        "--source-dir",
        default="backend/prompts/private/profile",
        help="Directory containing profile prompt sections.",
    )
    parser.add_argument(
        "--output-file",
        default="backend/prompts/private/my_profile.txt",
        help="Compiled prompt output path.",
    )
    args = parser.parse_args()

    output = compile_profile_prompt(
        source_dir=Path(args.source_dir),
        output_file=Path(args.output_file),
    )
    print({"compiled": output.as_posix()})


if __name__ == "__main__":
    main()
