"""Compatibility alias for pysteambridge.

`SteamworksPy`, the third-party package this one is a drop-in for,
is imported as `steamworks`. Keeping that name available means an
application switches over by dropping both folders in, with no edit
to a single call site:

    from steamworks import STEAMWORKS          # this shim
    from pysteambridge import STEAMWORKS       # the same object

There is no second implementation here. Every name is the one
pysteambridge defines, so a `STEAMWORKS` built through either import
is the same class and `isinstance` holds across them.

`steamworks.steamworks` is registered as an alias of
`pysteambridge.steamworks` as well, since that submodule path was
importable before the package was renamed.
"""
import sys

from pysteambridge import steamworks as _impl
from pysteambridge import (
    STEAMWORKS,
    SteamNotRunningException,
    restart_app_if_necessary,
)

sys.modules[__name__ + ".steamworks"] = _impl
steamworks = _impl

__all__ = ["STEAMWORKS", "SteamNotRunningException",
           "restart_app_if_necessary"]
