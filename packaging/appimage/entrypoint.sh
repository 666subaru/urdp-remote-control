#! /bin/bash
# -I keeps a user's PYTHONPATH or site-packages from leaking into the image.
{{ python-executable }} -I -m urdp "$@"
