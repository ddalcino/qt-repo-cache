import logging
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Dict, List, Optional, TypedDict, TypeVar, Callable, Set

from aqt.metadata import ArchiveId, MetadataFactory, Version

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
    class CacheForArch(TypedDict):
        modules: Dict[str, Dict[str, str]]
        archives: List[str]

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
                archives = log_and_reraise_exceptions(
                    lambda: self.meta.fetch_archives(version, arch, []),
                    f"Failed fetching qt archives for %s version=%s arch=%s",
                    self.meta.archive_id, version, arch,
                )
                cache[arch] = {'modules': modules.table_data, 'archives': archives}
            return cache
        except Exception:
            return None

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
            if self.has_cache_entry_for(version):
                cached_versions.add(version)
            if is_force_refresh or self.should_update_cache(version, last_version):
                made_update = self.update_cache_for(version)
                if made_update:
                    cached_versions.add(version)
        self.write_cache_directory(cached_versions)


def cache_aqt_list_qt(is_force_refresh: bool = False) -> None:
    for archive_id in iter_archive_ids():
        cached_data = CachedMetadata(archive_id)
        cached_data.refresh_all_cache(is_force_refresh)



