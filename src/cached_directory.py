import logging
from pathlib import Path
import json
from typing import Dict, Literal, Set


class CachedDirectory:
    ToolsQtSet = Dict[Literal['tools', 'qt'], Set[str]]
    def __init__(self, cached_dir_path: Path, indent: int = 1):
        self.indent = indent
        self.cached_dir_path = cached_dir_path
        self.directory = self.cached_dir_path / 'directory.json'
        if not cached_dir_path.exists():
            cached_dir_path.mkdir(parents=True)
        if self.directory.exists():
            self.previous_dir: CachedDirectory.ToolsQtSet = json.loads(cached_dir_path.read_text())
        else:
            self.previous_dir: CachedDirectory.ToolsQtSet = {'tools': set(), 'qt': set()}
        self.new_dir: CachedDirectory.ToolsQtSet = {'tools': set(), 'qt': set()}

    def use_cached_folder(self, folder: str):
        which = CachedDirectory.which(folder)
        self.new_dir[which].update(entry for entry in self.previous_dir[which] if entry.startswith(folder))

    def out(self) -> str:
        return json.dumps(
            {'qt': sorted(self.new_dir['qt']), 'tools': sorted(self.new_dir['tools'])},
            indent=self.indent
        )

    def save(self):
        self.directory.write_text(self.out())

    def add_folder(self, folder: str):
        self.new_dir[CachedDirectory.which(folder)].add(folder)

    @staticmethod
    def which(folder: str) -> Literal['qt', 'tools']:
        return 'qt' if folder.startswith('qt') else 'tools'

    def __contains__(self, folder: str) -> bool:
        which = CachedDirectory.which(folder)
        for f in self.previous_dir[which]:
            if f == folder or f.startswith(folder + '/'):
                return True

    def prune_removed_files(self, logger: logging.Logger):
        for which in ('qt', 'tools'):
            for folder in self.previous_dir[which]:
                if folder not in self.new_dir[which]:
                    json_file = self.cached_dir_path / f'{folder}.json'
                    logger.info(f"Removing {json_file}")
                    json_file.unlink()
