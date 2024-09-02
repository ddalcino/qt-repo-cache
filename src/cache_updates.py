import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Generator, Set, Tuple, Union

import aqt.metadata
import bs4
from aqt.helper import Settings
from aqt.metadata import ArchiveId, MetadataFactory, get_semantic_version

fetch_http = aqt.metadata.MetadataFactory.fetch_http
logging.basicConfig()
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
DEV_REGEX = re.compile(r"^qt\d_dev")
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


UNSUPPORTED_FOLDER_REGEX = re.compile(r"^qt6_(\d{1,2})_(\d{1,2})")
def is_qt_or_tools(folder: str) -> bool:
    if DEV_REGEX.match(folder) is not None:
        return False
    if "backup" in folder or "preview" in folder:
        return False
    # TODO: when aqtinstall works with folders that look like `qt6_7_3`, remove this!
    # Depends on aqtinstall/issues/817
    if UNSUPPORTED_FOLDER_REGEX.match(folder) is not None:
        return False
    return folder.startswith("tools_") or folder.startswith("qt")


def iter_folders(
    html_doc: str, folder_predicate: Callable[[str], bool] = is_qt_or_tools
):
    yield from iter_html_content(html_doc, folder_predicate, lambda s: s.rstrip("/"))


def iter_html_content(
    html_doc: str, item_predicate: Callable[[str], bool], transform_item: Callable[[str], str] = lambda s: s
) -> Generator[Tuple[str, datetime, str], None, None]:
    def table_row_to_folder(tr: bs4.element.Tag) -> Tuple[str, datetime, str]:
        _folder: str = transform_item(tr.find_all("td")[1].a.contents[0].strip())
        date_str = tr.find_all("td")[2].contents[0].strip()
        _dt = datetime.strptime(date_str, "%d-%b-%Y %H:%M")
        _size_str = tr.find_all("td")[3].contents[0].strip()
        return _folder, _dt, _size_str

    soup: bs4.BeautifulSoup = bs4.BeautifulSoup(html_doc, "html.parser")
    for row in soup.body.table.find_all("tr"):
        try:
            folder, dt, size_str = table_row_to_folder(row)
            if item_predicate(folder):
                yield folder, dt, size_str
        except (AttributeError, IndexError, ValueError):
            continue


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
            if date > most_recent:
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
    dates_dict = {key: timestamp_or_zero(value) for key, value in dates.items()}
    LAST_UPDATED_JSON_FILE.write_text(json.dumps(dates_dict, indent=INDENT_SIZE))


def get_last_update_dates() -> Dict[str, datetime]:
    timestamps = json.loads(LAST_UPDATED_JSON_FILE.read_text())
    return {key: datetime.fromtimestamp(timestamp) for key, timestamp in timestamps.items()}


def update_xml_files(last_update: datetime) -> datetime:
    most_recent = last_update
    for host, target in iterate_hosts_targets():
        LOGGER.info(banner_message(f"Entering {host}/{target}"))
        cache_dir = PUBLIC_ROOT / host / target
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True)
        tools: Set[str] = set()
        qts: Set[str] = set()
        # Download html file:
        archive_id = ArchiveId("qt", host, target)
        html_path = archive_id.to_url()
        meta = MetadataFactory(archive_id)
        html_doc = meta.fetch_http(html_path, is_check_hash=False)
        for folder, date, _ in iter_folders(html_doc):
            # Skip files that have not changed since the last update
            if date <= last_update:
                (tools if folder.startswith("tools") else qts).add(folder)
                continue
            try:
                if (host != 'all_os') and (match := re.match(r"^qt6_(?P<ver_no_dots>\d{3}\d*)(_(?P<ext>.+))?", folder)):
                    qt_version = get_semantic_version(match.group("ver_no_dots"), False)
                    xml_folder = archive_id.to_folder(qt_version, match.group("ver_no_dots"), match.group("ext"))
                else:
                    xml_folder = folder
            except ValueError as e:
                LOGGER.error(f"Failed to parse version from folder {folder}: {e}")
                continue
            LOGGER.info(f"Update for {html_path}{xml_folder}")
            content = meta._fetch_module_metadata(xml_folder)
            if not content:
                continue
            insert_archive_sizes(content, html_path + xml_folder, meta)
            json_file = cache_dir / f"{folder}.json"
            json_file.write_text(json.dumps(content, indent=INDENT_SIZE))
            if date > most_recent:
                most_recent = date
            (tools if folder.startswith("tools") else qts).add(folder)

        # Record the new directory listing
        dir_file = cache_dir / "directory.json"
        dir_file.write_text(
            json.dumps({"tools": sorted(tools), "qt": sorted(qts)}, indent=INDENT_SIZE)
        )

        # Prune cache of files that no longer exist in the qt repo
        all_files = tools.union(qts)
        for json_file in cache_dir.glob("*.json"):
            filename = json_file.with_suffix("").name
            if filename != "directory" and filename not in all_files:
                LOGGER.info(f"Removing {json_file}")
                json_file.unlink()
    return most_recent


if __name__ == "__main__":
    Settings.load_settings()
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
