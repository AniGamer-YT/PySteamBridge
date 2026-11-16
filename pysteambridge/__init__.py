"""pysteambridge: Steamworks for Python over the flat C API.

Loads Valve's redistributable binary with ctypes and exposes the
Steamworks interfaces directly. One folder, no build step, no compiler,
and nothing outside the standard library.

    from pysteambridge import STEAMWORKS
    api = STEAMWORKS()
    api.initialize()

The attribute layout matches the third-party `SteamworksPy` package,
and the `steamworks` package beside this one re-exports everything
here under that name, so code written against SteamworksPy runs
unchanged under either import:

    from steamworks import STEAMWORKS     # same objects

Only the flat C API function names covered by the sub-interfaces in
steamworks.py are wired (SteamUserStats, SteamUser, SteamFriends,
SteamApps, SteamRemoteStorage). Add more if you need them.

Anything callers import has to be re-exported HERE, not just defined
in steamworks.py: the package shadows the module.
"""
from .steamworks import (
    STEAMWORKS,
    SteamNotRunningException,
    restart_app_if_necessary,
)

__all__ = ["STEAMWORKS", "SteamNotRunningException",
           "restart_app_if_necessary"]
