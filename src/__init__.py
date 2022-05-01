#!/usr/bin/env python

from aqt.helper import Settings

from cache_updates import update_xml_files


def main():
    Settings.load_settings()
    update_xml_files()
