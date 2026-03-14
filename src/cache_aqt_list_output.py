import logging
import json
import posixpath
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Dict, Optional, TypedDict, TypeVar, Callable, Set

from aqt.metadata import ArchiveId, MetadataFactory, Version, QtRepoProperty

from html_util import iter_folders

logger = logging.getLogger(__name__)
PUBLIC_ROOT = Path(__file__).parent.parent / "public"


def iter_archive_ids(category: str = "qt") -> Iterator[ArchiveId]:
    """Yield all archive ids"""
    for host in ArchiveId.HOSTS:
        for target in ArchiveId.TARGETS_FOR_HOST[host]:
            yield ArchiveId(category, host, target)


T = TypeVar("T")

def log_and_reraise_exceptions(fn: Callable[[], T], msg: str, *args) -> T:
    try:
        return fn()
    except Exception:
        logger.exception(msg, *args)
        raise

class CachedMetadata:
    # matches '5.9.4-0-201801211432qtbase-Windows-Windows_7-Mingw53-Android-Android_ANY-ARMv7.7z'
    archive_name_pattern = re.compile(r"^\d+(?:\.\d+){2}-\d+-\d{12}(?P<archive>\w+)-")

    class CacheForArch(TypedDict):
        modules: Dict[str, Dict[str, str]]
        archives: Dict[str, str]

    def __init__(self, archive_id: ArchiveId):
        self.meta = MetadataFactory(archive_id)

    def path(self, version: Optional[Version] = None) -> Path:
        archive_id = self.meta.archive_id
        return PUBLIC_ROOT / archive_id.host / archive_id.target / (f"aqt_{version}.json" if version else '')

    def update_cache_for(self, version: Version) -> bool:
        """Returns true if it recorded a cache entry for this version, false otherwise"""
        logger.info("Updating aqt-list cache for archive=%s version=%s", self.meta.archive_id, version)
        cached_data = self.fetch_qt_data(version)
        if cached_data is None:
            return False  # Nothing to cache today
        self.write_cache(version, cached_data)
        return True

    def ensure_path_exists(self) -> None:
        """Creates the self.path() directory, with parents, when needed"""
        path = self.path()
        if not path.exists():
            path.mkdir(parents=True)
        if path.exists() and not path.is_dir():
            raise RuntimeError(f"{path} exists but is not a directory")
        path.parent.mkdir(parents=True, exist_ok=True)

    def write_cache(self, version: Version, cache: Dict[str, CacheForArch]) -> None:
        self.ensure_path_exists()
        path = self.path(version)
        tmp_path = path.with_suffix(".json.tmp")

        with tmp_path.open("w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
            f.write("\n")

        tmp_path.replace(path)

    def delete_cache_for(self, version: Version) -> None:
        """If there's a cache here, and there shouldn't be, delete it."""
        path = self.path(version)
        if path.exists() and path.is_file():
            path.unlink()

    def fetch_qt_data(self, version: Version) -> Optional[Dict[str, CacheForArch]]:
        try:
            arches = log_and_reraise_exceptions(lambda: self.meta.fetch_arches(version),
                                                 "Failed fetching arches for archive=%s version=%s",
                                                self.meta.archive_id, version)
            cache: Dict[str, CachedMetadata.CacheForArch] = {}
            for arch in arches:
                modules = log_and_reraise_exceptions(
                    lambda: self.meta.fetch_long_modules(version, arch),
                    f"Failed fetching modules for %s version=%s arch=%s",
                    self.meta.archive_id, version, arch,
                )
                archive_names = log_and_reraise_exceptions(
                    lambda: self.meta.fetch_archives(version, arch, []),
                    f"Failed fetching qt archives for %s version=%s arch=%s",
                    self.meta.archive_id, version, arch,
                )
                # version_date = modules.table_data[next(iter(modules.table_data))]['Version']
                all_archives = self.fetch_archive_sizes(version, arch)
                # archives = {archive: all_archives.get(archive, None) for archive in archive_names}
                archives = {archive: all_archives[archive] for archive in archive_names}
                cache[arch] = {'modules': modules.table_data, 'archives': archives}
            return cache
        except Exception:
            return None

    def fetch_archive_sizes(self, version: Version, arch: str) -> Dict[str, str]:
        def should_use_7z(filename: str) -> bool:
            return filename.endswith(".7z") and not filename.endswith("meta.7z")

        archive_sizes: Dict[str, str] = {}
        qt_version_str = self.meta._get_qt_version_str(version)
        module_name = f"qt.qt{version.major}.{qt_version_str}.{arch}" # Not true for all: should come from xml metadata
        if version < Version("5.9.7"):
            module_name = f"qt.{qt_version_str}.{arch}"

        # Get the path to the updates.xml file
        extension = QtRepoProperty.extension_for_arch(arch, version >= Version("6.0.0"))
        folder = self.meta.archive_id.to_folder(version, qt_version_str, extension)
        # updates_xml_rest_of_url = posixpath.join(self.meta.archive_id.to_url(), folder, "Updates.xml")
        rest_of_url = posixpath.join(self.meta.archive_id.to_url(), folder, module_name) + '/'
        html_doc = self.meta.fetch_http(rest_of_url, is_check_hash=False)
        for filename_7z, _, archive_size in iter_folders(html_doc, should_use_7z):
            if match := CachedMetadata.archive_name_pattern.match(filename_7z):
                archive_sizes[match.group("archive")] = archive_size
        return archive_sizes

    def has_cache_entry_for(self, version: Version) -> bool:
        return self.path(version).exists()

    def should_update_cache(self, ver: Version, last_version: Version) -> bool:
        # If it's the latest version, and patch = 0, then update cache
        if ver == last_version and ver.patch == 0:
            return True

        # If I don't have a file for this archive_id / version, then cache it for the first time
        return not self.has_cache_entry_for(ver)

    def write_cache_directory(self, cached_versions: Set[Version]) -> None:
        cache = { "qt": [str(ver) for ver in sorted(cached_versions)], }
        self.ensure_path_exists()
        path = self.path() / "aqt_list_directory.json"
        tmp_path = path.with_suffix(".json.tmp")

        with tmp_path.open("w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
            f.write("\n")

        tmp_path.replace(path)

    def refresh_all_cache(self, is_force_refresh: bool) -> None:
        cached_versions: Set[Version] = set()
        versions = self.meta.fetch_versions()
        last_version = versions.latest()
        for version in versions.flattened():
            if not is_force_refresh and self.has_cache_entry_for(version):
                cached_versions.add(version)
            if is_force_refresh or self.should_update_cache(version, last_version):
                made_update = self.update_cache_for(version)
                if made_update:
                    cached_versions.add(version)
                else:
                    self.delete_cache_for(version)
        self.write_cache_directory(cached_versions)


def cache_aqt_list_qt(is_force_refresh: bool = False) -> None:
    for archive_id in iter_archive_ids():
        cached_data = CachedMetadata(archive_id)
        cached_data.refresh_all_cache(is_force_refresh)



