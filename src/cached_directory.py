import logging
from pathlib import Path
import json
from typing import Set


class CachedDirectory:
    def __init__(self, cached_dir_path: Path, indent: int = 1):
        self.indent = indent
        self.cached_dir_path = cached_dir_path
        self.directory = self.cached_dir_path / "directory.json"
        if not cached_dir_path.exists():
            cached_dir_path.mkdir(parents=True)
        if self.directory.exists():
            previous_cache = json.loads(self.directory.read_text())
            self._previous_tools = set(previous_cache["tools"])
            self._previous_qt = set(previous_cache["qt"])
        else:
            self._previous_tools, self._previous_qt = set(), set()
        self._new_tools, self._new_qt = set(), set()

    def use_cached_folder(self, folder: str):
        if folder.startswith("qt"):
            previous_folder_set, new_folder_set = self._previous_qt, self._new_qt
        else:
            previous_folder_set, new_folder_set = self._previous_tools, self._new_tools
        new_folder_set.update(entry for entry in previous_folder_set if entry.startswith(folder))

    def out(self) -> str:
        return json.dumps({"qt": sorted(self._new_qt), "tools": sorted(self._new_tools)}, indent=self.indent)

    def save(self):
        self.directory.write_text(self.out())

    def add_folder(self, folder: str):
        self._new_set(folder).add(folder)

    def _previous_set(self, folder: str) -> Set[str]:
        """mutable reference to self._previous_qt or self._previous_tools"""
        return self._previous_qt if folder.startswith("qt") else self._previous_tools

    def _new_set(self, folder: str) -> Set[str]:
        """mutable reference to self._new_qt or self._new_tools"""
        return self._new_qt if folder.startswith("qt") else self._new_tools

    def __contains__(self, folder: str) -> bool:
        """
        :param folder: The folder path as a string to check for existence within the previous set of folders.
        :return: Boolean value indicating whether the specified folder or any of its subfolders exist in the previous set.
        """
        for f in self._previous_set(folder):
            if f == folder or f.startswith(folder + "/"):
                return True

    def prune_removed_files(self, logger: logging.Logger):
        for previous_folder_set, new_folder_set in (
            (self._previous_qt, self._new_qt),
            (self._previous_tools, self._new_tools),
        ):
            for previous_folder in previous_folder_set:
                if previous_folder not in new_folder_set:
                    json_file = self.cached_dir_path / f"{previous_folder}.json"
                    logger.info(f"Removing {json_file}")
                    json_file.unlink()
