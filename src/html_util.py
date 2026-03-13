import re
from datetime import datetime
from typing import Callable, Generator, Tuple

import bs4

DEV_REGEX = re.compile(r"^qt\d_dev")
UNSUPPORTED_FOLDER_REGEX = re.compile(r"^qt6_(\d{1,2})_(\d{1,2})")
# Allowed tools that don't start with 'tools_' (see aqtinstall#677)
HARDCODED_ALLOWED_TOOLS = ('sdktool',)

def is_qt_or_tools(folder: str) -> bool:
    if DEV_REGEX.match(folder) is not None:
        return False
    if "backup" in folder or "preview" in folder:
        return False
    # TODO: when aqtinstall works with folders that look like `qt6_7_3`, remove this!
    # Depends on aqtinstall/issues/817
    if UNSUPPORTED_FOLDER_REGEX.match(folder) is not None:
        return False
    return folder.startswith("tools_") or folder.startswith("qt") or folder in HARDCODED_ALLOWED_TOOLS


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


