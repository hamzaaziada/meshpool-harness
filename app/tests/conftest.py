"""Keep test runs from writing into the package folder (the failure catalog defaults to it)."""
import os

os.environ.setdefault("MH_FAILURES_LOG", "off")
