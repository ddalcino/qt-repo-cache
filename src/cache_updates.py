import json
import logging
import posixpath
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, Optional, Tuple

import aqt.metadata
import bs4
from aqt.helper import Settings, ssplit
from aqt.metadata import ArchiveId
from defusedxml import ElementTree

fetch_http = aqt.metadata.MetadataFactory.fetch_http
logging.basicConfig()
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
DEV_REGEX = re.compile(r"^qt\d_dev")
PUBLIC_ROOT = Path(__file__).parent.parent / "public"
LAST_UPDATED_JSON_FILE = PUBLIC_ROOT / "last_updated.json"


def banner_message(msg: str):
    msg = f"* {msg} *"
    stars = "*" * len(msg)
    return f"\n{stars}\n{msg}\n{stars}"


def iterate_hosts_targets() -> Generator[Tuple[str, str], None, None]:
    for host in ArchiveId.HOSTS:
        for target in ArchiveId.TARGETS_FOR_HOST[host]:
            yield host, target


def iterate_folders(html_doc: str) -> Generator[Tuple[str, datetime], None, None]:
    def table_row_to_folder(tr: bs4.element.Tag) -> Optional[Tuple[str, datetime]]:
        try:
            folder: str = tr.find_all("td")[1].a.contents[0].rstrip("/")
            date_str = tr.find_all("td")[2].contents[0].rstrip()
            dt = datetime.strptime(date_str, "%d-%b-%Y %H:%M")
            return folder, dt
        except (AttributeError, IndexError, ValueError):
            return None

    soup: bs4.BeautifulSoup = bs4.BeautifulSoup(html_doc, "html.parser")
    for row in soup.body.table.find_all("tr"):
        content: Optional[Tuple[str, datetime]] = table_row_to_folder(row)
        if not content:
            continue
        if should_use(content[0]):
            yield content


def should_use(folder: str) -> bool:
    if DEV_REGEX.match(folder) is not None:
        return False
    if "backup" in folder:
        return False
    return folder.startswith("tools_") or folder.startswith("qt")


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


def xml_to_modules(xml_text: str) -> Dict[str, Dict[str, str]]:
    """Converts an XML document to a dict of `PackageUpdate` dicts, indexed by `Name` attribute.
    Only report elements that satisfy `predicate(element)`.
    Reports all keys available in the PackageUpdate tag as strings.

    :param xml_text: The entire contents of an xml file
    """
    try:
        parsed_xml = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as perror:
        raise RuntimeError(f"Downloaded metadata is corrupted. {perror}") from perror
    packages = {}
    for packageupdate in parsed_xml.iter("PackageUpdate"):
        downloads = packageupdate.find("DownloadableArchives")
        update_file = packageupdate.find("UpdateFile")
        if downloads is None or update_file is None or not downloads.text:
            continue
        name = packageupdate.find("Name").text
        packages[name] = {
            "CompressedSize": human_readable_amt(update_file.attrib["CompressedSize"]),
            "UncompressedSize": human_readable_amt(
                update_file.attrib["UncompressedSize"]
            ),
            "DownloadableArchives": [s for s in ssplit(downloads.text)],
        }
        for key in ["Name", "DisplayName", "Description", "Version", "ReleaseDate"]:
            packages[name][key] = packageupdate.find(key).text
    return packages


def update_xml_files():
    last_update: datetime = get_date_of_last_update()
    most_recent = last_update
    for host, target in iterate_hosts_targets():
        LOGGER.info(banner_message(f"Entering {host}/{target}"))
        cache_dir = PUBLIC_ROOT / host / target
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True)
        tools = set()
        qts = set()
        # Download html file:
        html_path = ArchiveId("qt", host, target).to_url()
        for folder, date in iterate_folders(fetch_http(html_path, is_check_hash=False)):
            # Skip files that have not been updated
            if date <= last_update:
                (tools if folder.startswith("tools") else qts).add(folder)
                continue
            LOGGER.info(f"Update for {html_path}{folder}")
            # Download the xml file
            url = posixpath.join(html_path, folder, "Updates.xml")
            xml_data = fetch_http(url)
            content = xml_to_modules(xml_data)
            if not content:
                continue
            json_file = cache_dir / f"{folder}.json"
            json_file.write_text(json.dumps(content))
            if date > most_recent:
                most_recent = date
            (tools if folder.startswith("tools") else qts).add(folder)

        # Record the new directory listing
        dir_file = cache_dir / "directory.json"
        dir_file.write_text(json.dumps({"tools": sorted(tools), "qt": sorted(qts)}))

        # Prune cache of files that no longer exist in the qt repo
        all_files = tools.union(qts)
        for json_file in cache_dir.glob("*.json"):
            filename = json_file.with_suffix("").name
            if filename != "directory" and filename not in all_files:
                LOGGER.info(f"Removing {json_file}")
                json_file.unlink()

    save_date_of_last_update(most_recent)


def human_readable_amt(num_bytes_str: str) -> str:
    size = int(num_bytes_str)
    for label in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.4g} {label}"
        size = size / 1024
    return f"{size:.4g} PB"


if __name__ == "__main__":
    Settings.load_settings()
    update_xml_files()
