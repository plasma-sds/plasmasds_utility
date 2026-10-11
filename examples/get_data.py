"""Get a data file with plasmasds_utility, from a terminal.

Run it with ``python examples/get_data.py``. It uses renate's public test file, so
it works without an SSH key; the same notebook example is ``get_data.ipynb``.
"""

import plasmasds_utility
from plasmasds_utility import DataClient

# A client package creates one client, named after its folder on the server.
data = DataClient("renate-od")
print("data of renate-od is kept in", data.client_dir())

key = "test_dataset/access_tests/public_test.txt"

# get() returns the local path, downloading the file first if it is missing.
# private=False takes public data only. Client packages normally call
# data.get(key), which takes private data if you have access (an SSH key, see
# plasmasds_utility.set_ssh_key) and public data otherwise.
path = data.get(key, private=False)
print("got", path)
print("it says:", path.read_text(encoding="utf-8"))

# Calling it again only checks that the file is on disk: no download.
print("again:", data.get(key, private=False))

# The local copy is used as it is; to download it again if the server has a
# newer version:
data.get(key, private=False, update="if_newer")

# Lists the files that came from public data instead of private data; none here,
# as public data was asked for. When there are some, the list is also printed
# when Python exits.
plasmasds_utility.show_public_fallbacks()
