import requests

from interpreter._version import get_version


def check_for_update():
    # Fetch the latest version from the PyPI API
    response = requests.get(f"https://pypi.org/pypi/open-interpreter/json")
    latest_version = response.json()["info"]["version"]

    current_version = get_version()

    return latest_version > current_version
