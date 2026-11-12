from pathlib import Path


def get_resource_filename(name):
    # V21.1 (audit F): no pkg_resources. It belongs to setuptools, which is
    # not a dependency of this package (and is not installed with Python
    # 3.12+): without it the window did not start. The package is always
    # installed as plain files (zip_safe = False).
    return Path(__file__).resolve().parent / name
