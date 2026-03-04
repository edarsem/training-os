from __future__ import annotations

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


def ensure_compiled_profile_prompt(
    *,
    prompts_root: Path,
    source_relative: str = "private/profile",
    output_relative: str = "private/my_profile.txt",
    force: bool = False,
) -> Path:
    source_dir = prompts_root / source_relative
    output_file = prompts_root / output_relative

    if force or (not output_file.exists()):
        return compile_profile_prompt(source_dir=source_dir, output_file=output_file)

    return output_file
