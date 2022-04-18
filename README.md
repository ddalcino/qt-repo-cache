# qt-repo-cache

This project scans the repository at download.qt.io for changes to Updates.xml
files on a daily basis, and caches a copy of each file, stored as json.
This is meant to make it easier to programmatically check what updates are
available in the repo, without needing to fetch any data from download.qt.io.
Since it is hosted on Github Pages, the data is available to frontend-only
web applications, without violating any CORS policies.