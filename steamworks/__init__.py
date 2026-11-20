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

SteamworksPy keeps its enums in `steamworks.enums` and its exceptions
in `steamworks.exceptions`. Both names are registered here, pointing
at the one implementation module, so `from steamworks.enums import
FriendFlags` resolves the same way it would there.
"""
import sys

from pysteambridge import steamworks as _impl
from pysteambridge import (
    STEAMWORKS,
    EGamepadTextInputLineMode,
    EGamepadTextInputMode,
    ENotificationPosition,
    EPersonaState,
    FriendFlags,
    GenericSteamException,
    MissingSteamworksLibraryException,
    SteamConnectionException,
    SteamNotLoadedException,
    SteamNotRunningException,
    UnsupportedPlatformException,
    restart_app_if_necessary,
)

sys.modules[__name__ + ".steamworks"] = _impl
sys.modules[__name__ + ".enums"] = _impl
sys.modules[__name__ + ".exceptions"] = _impl
steamworks = _impl
enums = _impl
exceptions = _impl

__all__ = [
    "STEAMWORKS",
    "EGamepadTextInputLineMode",
    "EGamepadTextInputMode",
    "ENotificationPosition",
    "EPersonaState",
    "FriendFlags",
    "GenericSteamException",
    "MissingSteamworksLibraryException",
    "SteamConnectionException",
    "SteamNotLoadedException",
    "SteamNotRunningException",
    "restart_app_if_necessary",
    "UnsupportedPlatformException",
]
