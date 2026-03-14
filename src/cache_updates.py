import json
import logging
import posixpath
import argparse
import requests_cache
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, Tuple, Union

import aqt.metadata
from aqt.exceptions import ChecksumDownloadFailure
from aqt.helper import Settings
from aqt.metadata import ArchiveId, MetadataFactory

from cached_directory import CachedDirectory
from cache_aqt_list_output import cache_aqt_list_qt
from html_util import iter_html_content, iter_folders

requests_cache.install_cache("aqt_list_http_cache")
fetch_http = aqt.metadata.MetadataFactory.fetch_http
logging.basicConfig()
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
PUBLIC_ROOT = Path(__file__).parent.parent / "public"
LAST_UPDATED_JSON_FILE = PUBLIC_ROOT / "last_updated.json"
INDENT_SIZE = 1
REQUIRED_FETCH_DEPTH = 1



def banner_message(msg: str):
    msg = f"* {msg} *"
    stars = "*" * len(msg)
    return f"\n{stars}\n{msg}\n{stars}"


def iterate_hosts_targets() -> Generator[Tuple[str, str], None, None]:
    for host in ArchiveId.HOSTS:
        for target in ArchiveId.TARGETS_FOR_HOST[host]:
            yield host, target


"""
A recursive dictionary, where the keys are either folders or filenames, 
and the values are either the size of the file or another recursive dictionary.
Keys that end in "/" are folders; all other keys are filenames.
"""
RecursiveStrDict = Dict[str, Union[str, Dict]]


def fetch_file_directory(root_folder: str, existing_dict: RecursiveStrDict, last_update: datetime, required_depth: int) -> Tuple[RecursiveStrDict, datetime]:
    """

    :param root_folder:
    :param existing_dict:   The existing cache of official_releases metadata.
    :param last_update:     The time at which `existing_dict` was last updated.
    :param required_depth:  The depth at which it's acceptable to rely on the `existing_dict` metadata, rather than
                            downloading a fresh copy, when the modification date is older than the last update.
                            There is an issue where the `last_modified` date of a parent folder is older than some of
                            its contents, and the `required_depth` param is a workaround for this.
    :return:                A recursive dictionary, where the keys are either folders or filenames,
                            and the values are either the size of the file or another recursive dictionary.
    """
    dummy_archive_id = ArchiveId("qt", "linux", "desktop")  # ignored
    meta = MetadataFactory(dummy_archive_id)
    most_recent = last_update

    def get_info_from_page(rest_of_url: str, existing_data_at_level: RecursiveStrDict, depth: int) -> RecursiveStrDict:
        nonlocal most_recent
        new_content = dict()
        html_doc = meta.fetch_http(rest_of_url, is_check_hash=False)
        for folder, date, size in iter_html_content(html_doc, lambda s: s.strip() != "Parent Directory"):
            # Skip folders that have not changed since the last update
            if date <= last_update and depth >= required_depth and folder in existing_data_at_level.keys():
                new_content[folder] = existing_data_at_level[folder]
            elif folder.endswith("/"):
                LOGGER.info(f"Entering {rest_of_url}{folder}")
                new_content[folder] = \
                    get_info_from_page(rest_of_url + folder, existing_data_at_level.get(folder, dict()), depth + 1)
            else:
                new_content[folder] = size  # humanize.naturalsize(size, gnu=True)
            if date > most_recent and depth > required_depth:
                most_recent = date
        return new_content

    return get_info_from_page(f"{root_folder}/", existing_dict, 0), most_recent


def insert_archive_sizes(
    content: Dict[str, Dict[str, str]], folder_path: str, meta: MetadataFactory
):
    def should_use_7z(filename_7z: str) -> bool:
        return filename_7z.endswith(".7z") and not filename_7z.endswith("meta.7z")

    for subfolder in content.keys():
        # Don't download archive sizes if there's only one size
        if "," not in content[subfolder]["DownloadableArchives"]:
            continue
        rest_of_url = f"{folder_path}/{subfolder}/"
        subfolder_html = meta.fetch_http(rest_of_url, is_check_hash=False)
        archive_sizes = {}
        version = content[subfolder]["Version"]
        for filename_7z, _, archive_size in iter_folders(subfolder_html, should_use_7z):
            archive_sizes[filename_7z.removeprefix(version)] = archive_size
        content[subfolder]["ArchiveSizes"] = archive_sizes


def is_recently_updated(date: datetime, date_of_last_update: datetime) -> bool:
    return date > date_of_last_update


def save_last_update_dates(dates: Dict[str, datetime]):
    def timestamp_or_zero(d: datetime) -> float:
        try:
            return d.timestamp()
        except (OSError, OverflowError, ):
            return 0.0

    if not LAST_UPDATED_JSON_FILE.parent.is_dir():
        LAST_UPDATED_JSON_FILE.parent.mkdir(parents=True)
    dates_dict: Dict[str, Union[float, str]] = {key: timestamp_or_zero(value) for key, value in dates.items()}
    for key, value in dates.items():
        dates_dict[f"{key}_utc_pretty"] = f'{value:%Y-%m-%d %H:%M:%S}'
    LAST_UPDATED_JSON_FILE.write_text(json.dumps(dates_dict, indent=INDENT_SIZE))


def get_last_update_dates() -> Dict[str, datetime]:
    timestamps = json.loads(LAST_UPDATED_JSON_FILE.read_text())
    return {key: datetime.fromtimestamp(timestamps[key]) for key in timestamps.keys() if not key.endswith("_utc_pretty")}


def spider_folder(meta: MetadataFactory, path_to_folder: str) -> Generator[str, None, None]:
    """Returns a list of all folders that contain Updates.xml files."""
    html_doc = meta.fetch_http(posixpath.join(meta.archive_id.to_url(), path_to_folder), is_check_hash=False)
    if '<a href="Updates.xml">Updates.xml</a>' in html_doc:
        yield path_to_folder
    else:
        for folder, _, _ in iter_folders(html_doc):
            yield from spider_folder(meta, f"{path_to_folder}/{folder}")


def is_folder_a_new_minor_version(folder: str, cache_dir: CachedDirectory) -> bool:
    pass


def update_xml_files(last_update: datetime) -> datetime:
    most_recent = last_update
    for host, target in iterate_hosts_targets():
        LOGGER.info(banner_message(f"Entering {host}/{target}"))
        cache_dir = CachedDirectory(PUBLIC_ROOT / host / target, indent=INDENT_SIZE)
        # Download html file:
        archive_id = ArchiveId("qt", host, target)
        html_path = archive_id.to_url()
        meta = MetadataFactory(archive_id)
        html_doc = meta.fetch_http(html_path, is_check_hash=False)
        for folder, date, _ in iter_folders(html_doc):
            # Skip files that have not changed since the last update, unless it's the latest patch=0 release
            if date <= last_update and folder in cache_dir:
                cache_dir.use_cached_folder(folder)
                continue
            for xml_folder in spider_folder(meta, folder):
                try:
                    content = meta._fetch_module_metadata(xml_folder)
                except ChecksumDownloadFailure:
                    # Skip it and wait for the next update
                    continue
                if not content:
                    continue
                LOGGER.info(f"Update for {html_path}{xml_folder}")
                insert_archive_sizes(content, html_path + xml_folder, meta)
                cache_dir.add_folder(xml_folder)
                json_file = cache_dir.cached_dir_path / f"{xml_folder}.json"
                if not json_file.parent.exists():
                    json_file.parent.mkdir(parents=True)
                json_file.write_text(json.dumps(content, indent=INDENT_SIZE))
                if date > most_recent:
                    most_recent = date
                cache_dir.add_folder(xml_folder)

        # Record the new directory listing
        cache_dir.save()

        # Prune cache of files that no longer exist in the qt repo
        cache_dir.prune_removed_files(LOGGER)
    return most_recent


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force refresh of cached data",
    )
    args = parser.parse_args()

    force_refresh = args.force_refresh
    # TODO: remove patch for TARGETS_FOR_HOST when fixed upstream
    Settings.load_settings()

    try:
        cache_aqt_list_qt(is_force_refresh=force_refresh)
    except Exception:
        LOGGER.exception("cache_aqt_list_qt failed")
    last_updates: Dict[str, datetime] = get_last_update_dates()
    new_dates: Dict[str, datetime] = {key: val for key, val in last_updates.items()}
    for root_folder in [
        "official_releases",
        # "new_archive",
        # "ministro",
        # "linguist_releases",
        # "learning",
        # "community_releases",
        # "archive",
    ]:
        cached_meta_file = PUBLIC_ROOT / f"{root_folder}.json"
        previous_metadata = json.loads(cached_meta_file.read_text()) if cached_meta_file.is_file() else dict()
        previous_update_time = last_updates.get(root_folder, datetime.fromtimestamp(0))
        folder_metadata, folder_update_time = \
            fetch_file_directory(root_folder, previous_metadata, previous_update_time, REQUIRED_FETCH_DEPTH)
        new_dates[root_folder] = folder_update_time
        cached_meta_file.write_text(json.dumps(folder_metadata, indent=INDENT_SIZE))

    new_dates["online"] = update_xml_files(last_updates["online"])

    save_last_update_dates(new_dates)
