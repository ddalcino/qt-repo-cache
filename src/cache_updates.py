import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Generator, Optional, Set, Tuple

import aqt.metadata
import bs4
from aqt.helper import Settings
from aqt.metadata import ArchiveId, MetadataFactory

fetch_http = aqt.metadata.MetadataFactory.fetch_http
logging.basicConfig()
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
DEV_REGEX = re.compile(r"^qt\d_dev")
PUBLIC_ROOT = Path(__file__).parent.parent / "public"
LAST_UPDATED_JSON_FILE = PUBLIC_ROOT / "last_updated.json"
INDENT_SIZE = 1


def banner_message(msg: str):
    msg = f"* {msg} *"
    stars = "*" * len(msg)
    return f"\n{stars}\n{msg}\n{stars}"


def iterate_hosts_targets() -> Generator[Tuple[str, str], None, None]:
    for host in ArchiveId.HOSTS:
        for target in ArchiveId.TARGETS_FOR_HOST[host]:
            yield host, target


def is_qt_or_tools(folder: str) -> bool:
    if DEV_REGEX.match(folder) is not None:
        return False
    if "backup" in folder or "preview" in folder:
        return False
    return folder.startswith("tools_") or folder.startswith("qt")


def iter_folders(
    html_doc: str, folder_predicate: Callable[[str], bool] = is_qt_or_tools
) -> Generator[Tuple[str, datetime, str], None, None]:
    def table_row_to_folder(tr: bs4.element.Tag) -> Optional[Tuple[str, datetime, str]]:
        try:
            folder: str = tr.find_all("td")[1].a.contents[0].rstrip("/")
            date_str = tr.find_all("td")[2].contents[0].rstrip()
            dt = datetime.strptime(date_str, "%d-%b-%Y %H:%M")
            size_str = tr.find_all("td")[3].contents[0].strip()
            return folder, dt, size_str
        except (AttributeError, IndexError, ValueError):
            return None

    soup: bs4.BeautifulSoup = bs4.BeautifulSoup(html_doc, "html.parser")
    for row in soup.body.table.find_all("tr"):
        content: Optional[Tuple[str, datetime]] = table_row_to_folder(row)
        if not content:
            continue
        if folder_predicate(content[0]):
            yield content


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


def save_date_of_last_update(time_last_update: datetime):
    if not LAST_UPDATED_JSON_FILE.parent.is_dir():
        LAST_UPDATED_JSON_FILE.parent.mkdir(parents=True)
    LAST_UPDATED_JSON_FILE.write_text(
        json.dumps({"date_of_last_update": time_last_update.timestamp()})
    )


def get_date_of_last_update():
    timestamp = json.loads(LAST_UPDATED_JSON_FILE.read_text())["date_of_last_update"]
    return datetime.fromtimestamp(timestamp)


def update_xml_files():
    last_update: datetime = get_date_of_last_update()
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
            LOGGER.info(f"Update for {html_path}{folder}")
            content = meta._fetch_module_metadata(folder)
            if not content:
                continue
            insert_archive_sizes(content, html_path + folder, meta)
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

    save_date_of_last_update(most_recent)


if __name__ == "__main__":
    Settings.load_settings()
    update_xml_files()
