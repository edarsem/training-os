from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptBundle:
    generic_key: str
    generic_path: str
    generic_text: str
    private_key: str | None
    private_path: str | None
    private_text: str | None


class PromptRepository:
    def __init__(self, prompts_root: Path):
        self.prompts_root = prompts_root
        self.generic_dir = prompts_root / "generic"
        self.private_dir = prompts_root / "private"

    def resolve(self, *, generic_key: str, private_key: str | None = None) -> PromptBundle:
        generic_path = self._resolve_generic_path(generic_key)
        generic_text = generic_path.read_text(encoding="utf-8").strip()

        private_path: Path | None = None
        private_text: str | None = None
        if private_key:
            private_path = self._resolve_private_path(private_key)
            private_text = private_path.read_text(encoding="utf-8").strip()

        return PromptBundle(
            generic_key=generic_key,
            generic_path=generic_path.as_posix(),
            generic_text=generic_text,
            private_key=private_key,
            private_path=private_path.as_posix() if private_path else None,
            private_text=private_text,
        )

    def _resolve_generic_path(self, key: str) -> Path:
        path = self._resolve_with_extensions(self.generic_dir, key)
        if not path:
            raise FileNotFoundError(f"Generic prompt not found for key '{key}'")
        return path

    def _resolve_private_path(self, key: str) -> Path:
        path = self._resolve_with_extensions(self.private_dir, key)
        if not path:
            raise FileNotFoundError(f"Private prompt not found for key '{key}'")
        return path

    @staticmethod
    def _resolve_with_extensions(base_dir: Path, key: str) -> Path | None:
        direct = base_dir / key
        if direct.exists() and direct.is_file():
            return direct

        for suffix in (".txt", ".md"):
            candidate = base_dir / f"{key}{suffix}"
            if candidate.exists() and candidate.is_file():
                return candidate

        return None
