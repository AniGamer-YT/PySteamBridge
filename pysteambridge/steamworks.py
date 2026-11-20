"""Self-contained Steamworks wrapper built on ctypes.

Loads Valve's redistributable `steam_api64.dll` (Windows) /
`libsteam_api.so` (Linux) / `libsteam_api.dylib` (Mac) directly and
exposes the same attribute layout as the third-party `SteamworksPy`
package, so code written against that package runs unchanged under
either of this project's two import names:

    from pysteambridge import STEAMWORKS   # or: from steamworks ...
    api = STEAMWORKS()
    api.initialize()
    api.run_callbacks()

Seven interfaces are wired, each matching SteamworksPy's method names
and argument order one for one:

    api.UserStats     ISteamUserStats    achievements and stats
    api.Users         ISteamUser         who is signed in
    api.Friends       ISteamFriends      names, friends, the overlay
    api.Apps          ISteamApps         ownership, DLC, install paths
    api.Utils         ISteamUtils        overlay state, locale, clock
    api.Music         ISteamMusic        the Steam music player
    api.Screenshots   ISteamScreenshots  the screenshot key
    api.RemoteStorage ISteamRemoteStorage  Steam Cloud file I/O

RemoteStorage is this module's own addition: SteamworksPy has no such
interface, so nothing written against that package can collide with it.

Two string getters return bytes rather than str, because they did so
before the rest of the surface existed and call sites decode them
themselves: `Friends.GetPlayerName` and `UserStats.GetAchievementName`.
Every other string getter returns str, the way SteamworksPy does.
`UserStats.GetAchievement` likewise returns True / False / None where
SteamworksPy returns a bare bool, so a caller can tell "Steam says
locked" from "Steam has not told us yet".

What is deliberately absent: Matchmaking, Workshop, Input and MicroTxn.
Every useful call on those delivers its answer through Steam's
callback and call-result dispatch, which needs the manual-dispatch
pump (`SteamAPI_ManualDispatch_*`) rather than a plain function call.
The same goes for leaderboards on UserStats, and for the results of
`Apps.GetFileDetails` and `Apps.InstallDLC`. Those calls fire here but
their completion is not reported. Wiring the dispatch loop is the one
piece of work this module would need to carry those interfaces.

There is nothing to build and nothing to pip-install: drop this
package beside your entry point, put the redistributable binary for
your platform next to it, and import it. The only hard dependency is
the standard library.

Every method is defensive. If the binary did not load, or the SDK's
interface accessor returned NULL (Steam client not running, AppID not
owned), each method returns a no-op value (False / 0 / "" / b"")
rather than raising, so a caller that treats Steam as optional can run
the same code path whether or not Steam is there. `initialize()` is the
one exception: it raises, so a caller can tell "Steam is absent" from
"Steam is present and said no".

Import it only on the builds that ship the binary. A build without
Steam should not reach this module, and does not need the binary
present.
"""
import ctypes
import enum
import os
import sys

# --- Interface versions ----------------------------------------------------
# The SDK bumps these as APIs evolve. The flat C accessors are named
# `SteamAPI_<Iface>_v<NNN>`, and when Valve ships a new SDK zip the
# version number climbs. We try the newest known first and fall back
# through older ones so a slightly-out-of-date DLL still works.
#
# Cross-reference: sdk/public/steam/isteam*.h in the Steamworks SDK
# Each header has a `#define <IFACE>_INTERFACE_VERSION "..."` line
# giving the currently-live version string.
_USER_STATS_ACCESSORS = (
    "SteamAPI_SteamUserStats_v013",
    "SteamAPI_SteamUserStats_v012",
    "SteamAPI_SteamUserStats_v011",
)
_USER_ACCESSORS = (
    "SteamAPI_SteamUser_v023",
    "SteamAPI_SteamUser_v022",
    "SteamAPI_SteamUser_v021",
)
_FRIENDS_ACCESSORS = (
    "SteamAPI_SteamFriends_v018",
    "SteamAPI_SteamFriends_v017",
)
_APPS_ACCESSORS = (
    "SteamAPI_SteamApps_v009",
    "SteamAPI_SteamApps_v008",
    "SteamAPI_SteamApps_v007",
)
_UTILS_ACCESSORS = (
    "SteamAPI_SteamUtils_v011",
    "SteamAPI_SteamUtils_v010",
    "SteamAPI_SteamUtils_v009",
)
_MUSIC_ACCESSORS = (
    "SteamAPI_SteamMusic_v001",
)
_SCREENSHOTS_ACCESSORS = (
    "SteamAPI_SteamScreenshots_v003",
    "SteamAPI_SteamScreenshots_v002",
)
_REMOTE_STORAGE_ACCESSORS = (
    "SteamAPI_SteamRemoteStorage_v016",
    "SteamAPI_SteamRemoteStorage_v015",
    "SteamAPI_SteamRemoteStorage_v014",
)


# --- Exceptions ------------------------------------------------------------
# SteamworksPy raises these names, so a caller's except clauses carry
# across unchanged. This module only ever raises two of them; the rest
# exist so `except MissingSteamworksLibraryException` compiles against
# either package.
class GenericSteamException(Exception):
    """Base for every error this module raises."""


class SteamNotRunningException(GenericSteamException):
    """Raised by initialize() when Steam's init call fails, typically
    because the Steam client isn't running, the account doesn't own
    this AppID, or steam_appid.txt is missing / wrong."""


class MissingSteamworksLibraryException(GenericSteamException, OSError):
    """The redistributable binary was not found next to the package.
    Also an OSError, which is what this module raised before the name
    existed, so either except clause catches it."""


class SteamNotLoadedException(GenericSteamException):
    """A call needed a loaded library and there was none."""


class SteamConnectionException(GenericSteamException):
    """Steam is running but the connection to it did not come up."""


class UnsupportedPlatformException(GenericSteamException):
    """This platform has no redistributable binary in the package."""


# --- Enums -----------------------------------------------------------------
# SteamworksPy declares these as plain Enum, which means a caller there
# passes the member and the wrapper reads `.value`. These are IntEnum
# instead, so a member works both ways: as an enum and as the raw int
# the SDK wants. Passing a bare int keeps working too.
class FriendFlags(enum.IntEnum):
    """EFriendFlags: which relationships GetFriendCount counts."""
    NONE                  = 0x00
    BLOCKED               = 0x01
    FRIENDSHIP_REQUESTED  = 0x02
    IMMEDIATE             = 0x04
    CLAN_MEMBER           = 0x08
    ON_GAME_SERVER        = 0x10
    REQUESTING_FRIENDSHIP = 0x80
    REQUESTING_INFO       = 0x100
    IGNORED               = 0x200
    IGNORED_FRIEND        = 0x400
    SUGGESTED             = 0x800
    ALL                   = 0xFFFF


class ENotificationPosition(enum.IntEnum):
    """Which corner the overlay's own notifications appear in."""
    TOP_LEFT     = 0
    TOP_RIGHT    = 1
    BOTTOM_LEFT  = 2
    BOTTOM_RIGHT = 3


class EGamepadTextInputMode(enum.IntEnum):
    """Whether the Big Picture keyboard masks what is typed."""
    NORMAL   = 0
    PASSWORD = 1


class EGamepadTextInputLineMode(enum.IntEnum):
    """Whether the Big Picture keyboard accepts newlines."""
    SINGLE_LINE    = 0
    MULTIPLE_LINES = 1


class EPersonaState(enum.IntEnum):
    """What Friends.GetPlayerState and GetFriendPersonaState return.
    Not in SteamworksPy's enums, which returns the raw int; the names
    are the SDK's, so the int values match either way."""
    OFFLINE        = 0
    ONLINE         = 1
    BUSY           = 2
    AWAY           = 3
    SNOOZE         = 4
    LOOKING_TO_TRADE = 5
    LOOKING_TO_PLAY  = 6
    INVISIBLE      = 7


def _load_library():
    """Locate + load the Steamworks redistributable binary. Looks first
    in this package's directory (where the binary ships), then falls
    back to the folder holding the package, the process cwd, the
    directory holding the executable, and the PyInstaller bundle dir.
    Returns the loaded CDLL, or raises MissingSteamworksLibraryException
    (an OSError) with a diagnostic message naming every path it tried.

    The executable's directory is what a Nuitka standalone build needs:
    it sets neither sys.frozen nor _MEIPASS, and its data files sit
    beside the exe rather than in an unpacked temp tree.

    Both package names are searched under every root. A bundler is
    told one folder to drop the binary into, and which of the two it
    was told is not knowable from here."""
    _here = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        _names = ("steam_api64.dll", "steam_api.dll")
    elif sys.platform == "darwin":
        _names = ("libsteam_api.dylib",)
    else:
        _names = ("libsteam_api.so",)
    _pkgs = ("pysteambridge", "steamworks")
    _roots = [os.path.dirname(_here), os.getcwd(),
              os.path.dirname(os.path.abspath(sys.executable))]
    _meipass = getattr(sys, "_MEIPASS", None)   # PyInstaller onefile temp dir
    if _meipass:
        _roots.append(_meipass)
    _search = [_here]
    for _r in _roots:
        _search.append(_r)
        for _pkg in _pkgs:
            _search.append(os.path.join(_r, _pkg))
    _tried = []
    for _d in _search:
        for _n in _names:
            _p = os.path.join(_d, _n)
            _tried.append(_p)
            if os.path.isfile(_p):
                return ctypes.CDLL(_p)
    raise MissingSteamworksLibraryException(
        "Steamworks binary not found. Looked for "
        + ", ".join(_names)
        + " in: "
        + " | ".join(_tried)
    )


def _resolve(dll, names):
    """Return the first attribute in `names` that exists on `dll`, or
    None if none do. Used to pick the current flat-interface accessor
    from the version-suffixed candidates above."""
    for _n in names:
        _fn = getattr(dll, _n, None)
        if _fn is not None:
            return _fn, _n
    return None, None


def _cstr(value):
    """Encode a Python string for a `const char *` argument. bytes pass
    through, and None stays None so ctypes sends a NULL pointer."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8")
    return value


def _num(value):
    """Read an int out of either an enum member or a bare int, so a
    caller can pass ENotificationPosition.TOP_LEFT or 0."""
    if isinstance(value, enum.Enum):
        return int(value.value)
    return int(value)


# --- Shared interface plumbing ---------------------------------------------
class _Interface:
    """One Steamworks flat interface, bound lazily through ctypes.

    `_PREFIX` names the interface in the flat C API, so each method
    below asks for a short name and the prefix is added here. `_iface`
    is a raw void* to the interface instance; None means the accessor
    returned NULL (Steam client not running, or this SDK build has no
    such interface) and every call returns its `default` instead.

    `_BINDINGS` is a table of (short name, argtypes after the interface
    pointer, restype). Everything is bound once at construction, which
    is also where a function missing from an older DLL is noticed: it
    is skipped, and the call that wants it returns its default.
    """

    _PREFIX = ""
    _BINDINGS = ()

    def __init__(self, dll, iface):
        self._dll = dll
        self._iface = iface
        if iface is None:
            return
        for _n, _args, _ret in self._BINDINGS:
            _fn = getattr(dll, self._PREFIX + _n, None)
            if _fn is None:
                continue
            _fn.argtypes = [ctypes.c_void_p] + list(_args)
            _fn.restype = _ret

    def _call(self, name, *args, default=None):
        if self._iface is None:
            return default
        _fn = getattr(self._dll, self._PREFIX + name, None)
        if _fn is None:
            return default
        try:
            return _fn(self._iface, *args)
        except Exception:
            return default

    # Typed wrappers, so a method body is one line and the no-op value
    # for a missing interface is never the wrong type.
    def _bool(self, name, *args):
        return bool(self._call(name, *args, default=False))

    def _int(self, name, *args):
        return int(self._call(name, *args, default=0) or 0)

    def _float(self, name, *args):
        return float(self._call(name, *args, default=0.0) or 0.0)

    def _str(self, name, *args):
        _res = self._call(name, *args, default=None)
        if not _res:
            return ""
        return _res.decode("utf-8", errors="replace")

    def _bytes(self, name, *args):
        _res = self._call(name, *args, default=None)
        return _res if _res else b""

    def _void(self, name, *args):
        self._call(name, *args, default=None)
        return None


_VOID = ctypes.c_void_p
_BOOL = ctypes.c_bool
_CHARP = ctypes.c_char_p
_I32 = ctypes.c_int32
_U32 = ctypes.c_uint32
_U64 = ctypes.c_uint64
_U8 = ctypes.c_uint8
_FLT = ctypes.c_float
_INT = ctypes.c_int


# --- SteamUserStats interface ----------------------------------------------
class _UserStats(_Interface):
    """ISteamUserStats: achievements and the per-player stats they are
    often built on.

    Nothing here answers truthfully until a RequestCurrentStats
    round-trip has landed, which takes a few run_callbacks() turns
    after init. Before that, getters report zero and setters fail, so a
    caller that unlocks early has to retry rather than assume.
    """

    _PREFIX = "SteamAPI_ISteamUserStats_"
    _BINDINGS = (
        ("RequestCurrentStats",          [], _BOOL),
        ("SetAchievement",               [_CHARP], _BOOL),
        ("ClearAchievement",             [_CHARP], _BOOL),
        ("StoreStats",                   [], _BOOL),
        ("GetNumAchievements",           [], _U32),
        ("GetAchievementName",           [_U32], _CHARP),
        ("GetAchievement",               [_CHARP, ctypes.POINTER(_BOOL)],
                                         _BOOL),
        ("GetAchievementDisplayAttribute", [_CHARP, _CHARP], _CHARP),
        ("GetStatFloat",                 [_CHARP, ctypes.POINTER(_FLT)],
                                         _BOOL),
        ("GetStatInt32",                 [_CHARP, ctypes.POINTER(_I32)],
                                         _BOOL),
        ("SetStatFloat",                 [_CHARP, _FLT], _BOOL),
        ("SetStatInt32",                 [_CHARP, _I32], _BOOL),
        ("ResetAllStats",                [_BOOL], _BOOL),
    )

    def RequestCurrentStats(self):
        #False on a current SDK, which no longer exports this call:
        #Steam fetches the signed-in player's stats itself during init
        #and delivers them over the next few run_callbacks() turns. The
        #method stays because SteamworksPy has it, and a False here
        #means "nothing to ask", not "the fetch failed".
        return self._bool("RequestCurrentStats")

    def SetAchievement(self, name):
        return self._bool("SetAchievement", _cstr(name))

    def ClearAchievement(self, name):
        return self._bool("ClearAchievement", _cstr(name))

    def StoreStats(self):
        return self._bool("StoreStats")

    def GetNumAchievements(self):
        return self._int("GetNumAchievements")

    def GetAchievement(self, name):
        #Whether Steam currently has this achievement granted: True or
        #False when Steam knows, None when it does not - the stats
        #round-trip has not landed, or the name is not in the AppID's
        #schema. None is deliberately distinct from False, since a
        #caller revoking achievements must not act on "don't know".
        _flag = _BOOL(False)
        _ok = self._call("GetAchievement", _cstr(name), ctypes.byref(_flag),
                         default=None)
        if not _ok:
            return None
        return bool(_flag.value)

    def GetAchievementName(self, index):
        # The SDK returns const char*; ctypes hands us bytes, which is
        # what this method has always returned.
        return self._bytes("GetAchievementName", _U32(int(index)))

    def GetAchievementDisplayAttribute(self, name, key):
        #Read one of the achievement's localised schema fields. `key`
        #is "name", "desc" or "hidden", as the SDK defines them; the
        #last comes back as "0" or "1" rather than a bool.
        return self._str("GetAchievementDisplayAttribute",
                         _cstr(name), _cstr(key))

    def GetStatFloat(self, name):
        _val = _FLT(0.0)
        if not self._call("GetStatFloat", _cstr(name), ctypes.byref(_val),
                          default=False):
            return 0.0
        return float(_val.value)

    def GetStatInt(self, name):
        _val = _I32(0)
        if not self._call("GetStatInt32", _cstr(name), ctypes.byref(_val),
                          default=False):
            return 0
        return int(_val.value)

    def SetStat(self, name, value):
        #Stats are typed on the Steamworks dashboard, and the SDK has a
        #separate setter per type. A float here picks the float setter
        #and an int picks the int one, so the Python type has to match
        #how the stat was declared or Steam rejects the write.
        if isinstance(value, float):
            return self._bool("SetStatFloat", _cstr(name), _FLT(value))
        return self._bool("SetStatInt32", _cstr(name), _I32(int(value)))

    def ResetAllStats(self, achievements):
        #Wipe this player's stats, and their achievements too when
        #`achievements` is true. Irreversible, and Steam applies it to
        #the live account, so this belongs behind a developer switch.
        return self._bool("ResetAllStats", _BOOL(bool(achievements)))


# --- SteamUser interface ---------------------------------------------------
class _Users(_Interface):
    """ISteamUser: the account this process is running as."""

    _PREFIX = "SteamAPI_ISteamUser_"
    _BINDINGS = (
        ("GetSteamID",           [], _U64),
        ("BLoggedOn",            [], _BOOL),
        ("GetPlayerSteamLevel",  [], _INT),
        ("GetGameBadgeLevel",    [_INT, _BOOL], _INT),
        ("GetAuthSessionTicket", [_VOID, _I32, ctypes.POINTER(_U32), _VOID],
                                 _U32),
    )

    def GetSteamID(self):
        return self._int("GetSteamID")

    def LoggedOn(self):
        #True while the client holds a live connection to Steam. False
        #in offline mode, where achievements still queue locally.
        return self._bool("BLoggedOn")

    def GetPlayerSteamLevel(self):
        return self._int("GetPlayerSteamLevel")

    def GetGameBadgeLevel(self, series, foil):
        return self._int("GetGameBadgeLevel", _INT(int(series)),
                         _BOOL(bool(foil)))

    def GetAuthSessionTicket(self):
        #Hex of an authentication ticket a game server or web backend
        #can hand to Steam to prove who this player is. Returns "" when
        #Steam declined to issue one.
        #
        #The ticket is only valid once the client reports it accepted,
        #which arrives as a callback this module does not pump, so
        #treat the value as "issued" rather than "known good".
        _buf = ctypes.create_string_buffer(1024)
        _got = _U32(0)
        _handle = self._call("GetAuthSessionTicket",
                             ctypes.cast(_buf, _VOID), _I32(1024),
                             ctypes.byref(_got), None, default=0)
        if not _handle or _got.value <= 0:
            return ""
        return _buf.raw[:_got.value].hex()


# --- SteamFriends interface ------------------------------------------------
class _Friends(_Interface):
    """ISteamFriends: the player's name and friends, rich presence, and
    every way of opening the Steam overlay."""

    _PREFIX = "SteamAPI_ISteamFriends_"
    _BINDINGS = (
        ("GetPersonaName",          [], _CHARP),
        ("GetPersonaState",         [], _INT),
        ("GetFriendCount",          [_INT], _INT),
        ("GetFriendByIndex",        [_INT, _INT], _U64),
        ("GetFriendPersonaName",    [_U64], _CHARP),
        ("GetFriendPersonaState",   [_U64], _INT),
        ("SetRichPresence",         [_CHARP, _CHARP], _BOOL),
        ("ClearRichPresence",       [], None),
        ("InviteUserToGame",        [_U64, _CHARP], _BOOL),
        ("SetPlayedWith",           [_U64], None),
        ("ActivateGameOverlay",     [_CHARP], None),
        ("ActivateGameOverlayToUser", [_CHARP, _U64], None),
        ("ActivateGameOverlayToWebPage", [_CHARP, _INT], None),
        ("ActivateGameOverlayToStore", [_U32, _INT], None),
        ("ActivateGameOverlayInviteDialog", [_U64], None),
    )

    def GetPlayerName(self):
        # SteamworksPy renames GetPersonaName -> GetPlayerName on the
        # Python side; the underlying C call is GetPersonaName. The
        # alias is kept so callers written against that package work.
        # This one returns bytes rather than str, which is what it has
        # always returned here and what its call sites decode.
        return self._bytes("GetPersonaName")

    def GetPlayerState(self):
        #An EPersonaState value: ONLINE, AWAY, OFFLINE and so on.
        return self._int("GetPersonaState")

    def GetFriendCount(self, flag=FriendFlags.ALL):
        #How many people match `flag`. Negative means the friends list
        #has not loaded yet, which the SDK reports as -1; that is
        #flattened to 0 here so a caller can range() over it.
        _n = self._int("GetFriendCount", _INT(_num(flag)))
        return _n if _n > 0 else 0

    def GetFriendByIndex(self, friend_index, flag=FriendFlags.ALL):
        #The SteamID at that position in the list `flag` selects. The
        #order is not stable between calls, so pair the index with a
        #count read in the same frame.
        return self._int("GetFriendByIndex", _INT(int(friend_index)),
                         _INT(_num(flag)))

    def GetFriendPersonaName(self, steam_id):
        return self._str("GetFriendPersonaName", _U64(int(steam_id)))

    def GetFriendPersonaState(self, steam_id):
        return self._int("GetFriendPersonaState", _U64(int(steam_id)))

    def SetGameInfo(self, server_key, server_value):
        #Rich presence: a key and value Steam shows next to the player
        #in a friend's list, and that a friend's client can read back.
        #The keys Steam renders are configured per app on the partner
        #site; an unconfigured key is stored but not displayed.
        return self._bool("SetRichPresence",
                          _cstr(server_key), _cstr(server_value))

    def ClearGameInfo(self):
        return self._void("ClearRichPresence")

    def InviteFriend(self, steam_id, connection):
        #Send a friend an invite carrying `connection`, the string
        #Steam hands our process on the other end as a launch
        #parameter.
        return self._bool("InviteUserToGame", _U64(int(steam_id)),
                          _cstr(connection))

    def SetPlayedWith(self, steam_id):
        #Add someone to the player's "recently played with" list.
        return self._void("SetPlayedWith", _U64(int(steam_id)))

    def ActivateGameOverlay(self, dialog=""):
        #Open the overlay on one of its own pages: "Friends",
        #"Community", "Players", "Settings", "OfficialGameGroup",
        #"Stats", "Achievements". An empty string opens the overlay
        #where the player last left it.
        return self._void("ActivateGameOverlay", _cstr(dialog))

    def ActivateGameOverlayToUser(self, dialog, steam_id):
        #`dialog` is "steamid", "chat", "jointrade", "stats",
        #"achievements", "friendadd", "friendremove",
        #"friendrequestaccept" or "friendrequestignore".
        return self._void("ActivateGameOverlayToUser", _cstr(dialog),
                          _U64(int(steam_id)))

    def ActivateGameOverlayToWebPage(self, url):
        #Open a URL in the overlay's browser. The second SDK argument
        #is the window mode, and 0 is k_EActivateGameOverlayToWebPage
        #Mode_Default: the overlay's normal browser window.
        return self._void("ActivateGameOverlayToWebPage", _cstr(url),
                          _INT(0))

    def ActivateGameOverlayToStore(self, app_id):
        #Open a store page in the overlay. The second SDK argument is
        #an EOverlayToStoreFlag, and 0 is None: show the page without
        #adding to the cart.
        return self._void("ActivateGameOverlayToStore", _U32(int(app_id)),
                          _INT(0))

    def ActivateGameOverlayInviteDialog(self, steam_lobby_id):
        return self._void("ActivateGameOverlayInviteDialog",
                          _U64(int(steam_lobby_id)))


# --- SteamApps interface ---------------------------------------------------
class _Apps(_Interface):
    """ISteamApps: what this account owns and where it is installed.

    IsSubscribed() answers "does the signed-in account own the app this
    process is running as", which is what a launch gate needs;
    IsSubscribedApp(id) asks about some other app, such as the full
    game from inside the demo.
    """

    _PREFIX = "SteamAPI_ISteamApps_"
    _BINDINGS = (
        ("BIsSubscribed",                 [], _BOOL),
        ("BIsLowViolence",                [], _BOOL),
        ("BIsCybercafe",                  [], _BOOL),
        ("BIsVACBanned",                  [], _BOOL),
        ("GetCurrentGameLanguage",        [], _CHARP),
        ("GetAvailableGameLanguages",     [], _CHARP),
        ("BIsSubscribedApp",              [_U32], _BOOL),
        ("BIsDlcInstalled",               [_U32], _BOOL),
        ("GetEarliestPurchaseUnixTime",   [_U32], _U32),
        ("BIsSubscribedFromFreeWeekend",  [], _BOOL),
        ("GetDLCCount",                   [], _I32),
        ("InstallDLC",                    [_U32], None),
        ("UninstallDLC",                  [_U32], None),
        ("MarkContentCorrupt",            [_BOOL], _BOOL),
        ("GetAppInstallDir",              [_U32, _CHARP, _U32], _U32),
        ("BIsAppInstalled",               [_U32], _BOOL),
        ("GetAppOwner",                   [], _U64),
        ("GetLaunchQueryParam",           [_CHARP], _CHARP),
        ("GetAppBuildId",                 [], _INT),
        ("GetFileDetails",                [_CHARP], _U64),
    )

    # Path buffer for GetAppInstallDir. Steam paths are far shorter
    # than this, and the SDK truncates rather than failing.
    MAX_PATH = 1024

    def IsSubscribed(self):
        return self._bool("BIsSubscribed")

    def BIsSubscribed(self):
        #Three-state ownership for a launch gate: True = owns it,
        #False = does not, None = could not ask, because this SDK build
        #has no Apps interface. A caller must not lock a paying player
        #out over None.
        return self._call("BIsSubscribed", default=None)

    def IsLowViolence(self):
        #True on the censored build some territories require.
        return self._bool("BIsLowViolence")

    def IsCybercafe(self):
        return self._bool("BIsCybercafe")

    def IsVACBanned(self):
        return self._bool("BIsVACBanned")

    def GetCurrentGameLanguage(self):
        #The language the player picked for this app, as an API
        #language code such as "english" or "brazilian".
        return self._str("GetCurrentGameLanguage")

    def GetAvailableGameLanguages(self):
        #Every language the app's depots offer, comma separated.
        return self._str("GetAvailableGameLanguages")

    def IsSubscribedApp(self, app_id):
        return self._bool("BIsSubscribedApp", _U32(int(app_id)))

    def BIsSubscribedApp(self, app_id):
        #Three-state, matching BIsSubscribed above.
        return self._call("BIsSubscribedApp", _U32(int(app_id)), default=None)

    def IsDLCInstalled(self, dlc_id):
        return self._bool("BIsDlcInstalled", _U32(int(dlc_id)))

    def GetEarliestPurchaseUnixTime(self, app_id):
        #When this account first bought the app, which is how a game
        #tells an early supporter from a sale buyer.
        return self._int("GetEarliestPurchaseUnixTime", _U32(int(app_id)))

    def IsSubscribedFromFreeWeekend(self):
        return self._bool("BIsSubscribedFromFreeWeekend")

    def GetDLCCount(self):
        return self._int("GetDLCCount")

    def InstallDLC(self, dlc_id):
        #Ask Steam to download optional DLC. Progress arrives as a
        #callback this module does not pump, so poll IsDLCInstalled.
        return self._void("InstallDLC", _U32(int(dlc_id)))

    def UninstallDLC(self, dlc_id):
        return self._void("UninstallDLC", _U32(int(dlc_id)))

    def MarkContentCorrupt(self, missing_files_only=True):
        #Tell Steam this install is damaged, which queues a validation
        #pass the next time the player launches from the client.
        return self._bool("MarkContentCorrupt",
                          _BOOL(bool(missing_files_only)))

    def GetAppInstallDir(self, app_id):
        #Where Steam put an app, even one that is not this one. Steam
        #answers with the path the app would occupy in its default
        #library whether or not it is there, so pair this with
        #IsAppInstalled rather than reading a path as proof.
        _buf = ctypes.create_string_buffer(self.MAX_PATH)
        _n = self._int("GetAppInstallDir", _U32(int(app_id)), _buf,
                       _U32(self.MAX_PATH))
        if _n <= 0:
            return ""
        return _buf.value.decode("utf-8", errors="replace")

    def IsAppInstalled(self, app_id):
        return self._bool("BIsAppInstalled", _U32(int(app_id)))

    def GetAppOwner(self):
        #The SteamID that owns the licence, which differs from the
        #signed-in user under Family Sharing.
        return self._int("GetAppOwner")

    def GetLaunchQueryParam(self, key):
        #Read one parameter out of the steam://run URL that started
        #this process, which is how a Steam invite hands over a target.
        return self._str("GetLaunchQueryParam", _cstr(key))

    def GetAppBuildId(self):
        #The BuildID Steam installed, for putting a real build number
        #in a bug report.
        return self._int("GetAppBuildId")

    def GetFileDetails(self, filename):
        #Ask Steam for a file's size and hash as its depots record it.
        #The answer arrives as a call result this module does not pump,
        #so this fires the request and returns nothing.
        self._call("GetFileDetails", _cstr(filename), default=0)
        return None


# --- SteamUtils interface --------------------------------------------------
class _Utils(_Interface):
    """ISteamUtils: the overlay's state, the client's locale and clock,
    and the Big Picture keyboard."""

    _PREFIX = "SteamAPI_ISteamUtils_"
    _BINDINGS = (
        ("BOverlayNeedsPresent",          [], _BOOL),
        ("GetAppID",                      [], _U32),
        ("GetCurrentBatteryPower",        [], _U8),
        ("GetIPCCallCount",               [], _U32),
        ("GetIPCountry",                  [], _CHARP),
        ("GetSecondsSinceAppActive",      [], _U32),
        ("GetSecondsSinceComputerActive", [], _U32),
        ("GetServerRealTime",             [], _U32),
        ("GetSteamUILanguage",            [], _CHARP),
        ("IsOverlayEnabled",              [], _BOOL),
        ("IsSteamInBigPictureMode",       [], _BOOL),
        ("IsVRHeadsetStreamingEnabled",   [], _BOOL),
        ("SetOverlayNotificationInset",   [_INT, _INT], None),
        ("SetOverlayNotificationPosition", [_INT], None),
        ("SetVRHeadsetStreamingEnabled",  [_BOOL], None),
        ("ShowGamepadTextInput",          [_INT, _INT, _CHARP, _U32, _CHARP],
                                          _BOOL),
        ("StartVRDashboard",              [], None),
    )

    def OverlayNeedsPresent(self):
        #True when the overlay has drawn something the game has not
        #presented yet, which is how a game that skips frames when
        #idle knows it still has to flip this one.
        return self._bool("BOverlayNeedsPresent")

    def GetAppID(self):
        return self._int("GetAppID")

    def GetCurrentBatteryPower(self):
        #Percent of battery left, or 255 on mains power.
        return self._int("GetCurrentBatteryPower")

    def GetIPCCallCount(self):
        #How many calls have crossed into the Steam client since this
        #was last read. Reading it resets the counter.
        return self._int("GetIPCCallCount")

    def GetIPCountry(self):
        #Two-letter country code Steam geolocated the client to.
        return self._str("GetIPCountry")

    def GetSecondsSinceAppActive(self):
        return self._int("GetSecondsSinceAppActive")

    def GetSecondsSinceComputerActive(self):
        return self._int("GetSecondsSinceComputerActive")

    def GetServerRealTime(self):
        #Steam's own clock as a Unix time, which is the one to trust
        #for anything a player could cheat by moving their system
        #clock.
        return self._int("GetServerRealTime")

    def GetSteamUILanguage(self):
        #The language the Steam client itself is in, which can differ
        #from the one picked for this app.
        return self._str("GetSteamUILanguage")

    def IsOverlayEnabled(self):
        return self._bool("IsOverlayEnabled")

    def IsSteamInBigPictureMode(self):
        return self._bool("IsSteamInBigPictureMode")

    def IsVRHeadsetStreamingEnabled(self):
        return self._bool("IsVRHeadsetStreamingEnabled")

    def SetOverlayNotificationInset(self, horizontal, vertical):
        #Push the overlay's notifications in from the corner, so they
        #miss a HUD element sitting there.
        return self._void("SetOverlayNotificationInset",
                          _INT(int(horizontal)), _INT(int(vertical)))

    def SetOverlayNotificationPosition(self, position):
        #Takes an ENotificationPosition, or the bare int.
        return self._void("SetOverlayNotificationPosition",
                          _INT(_num(position)))

    def SetVRHeadsetStreamingEnabled(self, enabled):
        return self._void("SetVRHeadsetStreamingEnabled",
                          _BOOL(bool(enabled)))

    def ShowGamepadTextInput(self, input_mode, line_input_mode, description,
                             max_characters, preset):
        #Open Big Picture's on-screen keyboard. True means Steam put it
        #up; what the player typed arrives as a callback this module
        #does not pump, so this is only useful once that dispatch
        #exists.
        return self._bool("ShowGamepadTextInput", _INT(_num(input_mode)),
                          _INT(_num(line_input_mode)), _cstr(description),
                          _U32(int(max_characters)), _cstr(preset))

    def StartVRDashboard(self):
        return self._void("StartVRDashboard")


# --- SteamMusic interface --------------------------------------------------
class _Music(_Interface):
    """ISteamMusic: the Steam client's own music player.

    This is what lets a game pause the player's Steam music while a
    cutscene runs, and hand it back afterwards. It controls Steam's
    player, not the game's audio.
    """

    _PREFIX = "SteamAPI_ISteamMusic_"
    _BINDINGS = (
        ("BIsEnabled",   [], _BOOL),
        ("BIsPlaying",   [], _BOOL),
        ("GetVolume",    [], _FLT),
        ("Pause",        [], None),
        ("Play",         [], None),
        ("PlayNext",     [], None),
        ("PlayPrevious", [], None),
        ("SetVolume",    [_FLT], None),
    )

    def MusicIsEnabled(self):
        #False when the player has no Steam music player available, in
        #which case every call below does nothing.
        return self._bool("BIsEnabled")

    def MusicIsPlaying(self):
        return self._bool("BIsPlaying")

    def MusicGetVolume(self):
        #0.0 to 1.0.
        return self._float("GetVolume")

    def MusicPause(self):
        return self._void("Pause")

    def MusicPlay(self):
        return self._void("Play")

    def MusicPlayNext(self):
        return self._void("PlayNext")

    def MusicPlayPrev(self):
        return self._void("PlayPrevious")

    def MusicSetVolume(self, volume):
        return self._void("SetVolume", _FLT(float(volume)))


# --- SteamScreenshots interface --------------------------------------------
class _Screenshots(_Interface):
    """ISteamScreenshots: the player's F12 key.

    HookScreenshots(True) takes the key over, so Steam stops writing
    its own capture and the game is told to write one instead. The
    request arrives as a callback this module does not pump, so hooking
    without that dispatch would swallow the key: leave it off until the
    game has somewhere to receive the request. TriggerScreenshot and
    AddScreenshotToLibrary work regardless.
    """

    _PREFIX = "SteamAPI_ISteamScreenshots_"
    _BINDINGS = (
        ("AddScreenshotToLibrary", [_CHARP, _CHARP, _INT, _INT], _U32),
        ("HookScreenshots",        [_BOOL], None),
        ("IsScreenshotsHooked",    [], _BOOL),
        ("SetLocation",            [_U32, _CHARP], _BOOL),
        ("TriggerScreenshot",      [], None),
    )

    def AddScreenshotToLibrary(self, filename, thumbnail_filename,
                               width, height):
        #File a picture the game wrote itself into the player's Steam
        #screenshot library. Returns a handle, or 0 if Steam refused.
        #A thumbnail of None lets Steam make its own.
        return self._int("AddScreenshotToLibrary", _cstr(filename),
                         _cstr(thumbnail_filename), _INT(int(width)),
                         _INT(int(height)))

    def HookScreenshots(self, hook):
        return self._void("HookScreenshots", _BOOL(bool(hook)))

    def IsScreenshotsHooked(self):
        return self._bool("IsScreenshotsHooked")

    def SetLocation(self, screenshot_handle, location):
        #Tag a filed screenshot with where in the game it was taken,
        #which Steam shows under the picture.
        return self._bool("SetLocation", _U32(int(screenshot_handle)),
                          _cstr(location))

    def TriggerScreenshot(self):
        #Take a screenshot exactly as the player's own key would.
        return self._void("TriggerScreenshot")


# --- SteamRemoteStorage interface ------------------------------------------
class _RemoteStorage(_Interface):
    """ISteamRemoteStorage - Steam Cloud file I/O.

    FileWrite / FileRead are the synchronous pair, which is what suits
    a file of a few hundred bytes: Steam takes the write into its own
    quota-managed store and uploads it in the background, so there is
    no callback to pump here. Cloud has to be on for both the app (set
    on the partner site) and the account (the player's setting), and
    IsCloudEnabledForApp / ForAccount report which.

    Steam Cloud filenames are its own flat namespace, not disk paths.

    SteamworksPy has no RemoteStorage interface, so nothing written
    against that package depends on these names.
    """

    _PREFIX = "SteamAPI_ISteamRemoteStorage_"
    _BINDINGS = (
        ("FileWrite",                 [_CHARP, _VOID, _I32], _BOOL),
        ("FileRead",                  [_CHARP, _VOID, _I32], _I32),
        ("FileExists",                [_CHARP], _BOOL),
        ("FileDelete",                [_CHARP], _BOOL),
        ("GetFileSize",               [_CHARP], _I32),
        ("IsCloudEnabledForAccount",  [], _BOOL),
        ("IsCloudEnabledForApp",      [], _BOOL),
    )

    MAX_READ = 1 << 20      # refuse to allocate for an implausible file

    def IsCloudEnabledForAccount(self):
        return self._bool("IsCloudEnabledForAccount")

    def IsCloudEnabledForApp(self):
        return self._bool("IsCloudEnabledForApp")

    def FileExists(self, path):
        return self._bool("FileExists", _cstr(path))

    def GetFileSize(self, path):
        return self._int("GetFileSize", _cstr(path))

    def FileDelete(self, path):
        return self._bool("FileDelete", _cstr(path))

    def FileWrite(self, path, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data:
            # A zero-byte write is an error in the SDK, and FileRead
            # cannot tell an empty file from a missing one anyway.
            return False
        _buf = ctypes.create_string_buffer(data, len(data))
        return self._bool("FileWrite", _cstr(path),
                          ctypes.cast(_buf, _VOID), _I32(len(data)))

    def FileRead(self, path):
        #Return the file's bytes, or b"" when it is absent, empty,
        #implausibly large, or the read came back short.
        _size = self.GetFileSize(path)
        if _size <= 0 or _size > self.MAX_READ:
            return b""
        _buf = ctypes.create_string_buffer(_size)
        _got = self._int("FileRead", _cstr(path),
                         ctypes.cast(_buf, _VOID), _I32(_size))
        if _got <= 0:
            return b""
        return _buf.raw[:_got]


def restart_app_if_necessary(app_id):
    """True when Steam is relaunching us through the client, in which
    case the caller must exit immediately and let that copy take over.

    This is what makes a double-clicked exe come back up as a Steam-owned
    process: it gets the overlay, the right AppID, and the ownership
    check becomes meaningful. False means either we were already
    launched by Steam, or steam_appid.txt is present (the dev path, which
    deliberately suppresses the relaunch).

    Returns False if the binary can't be loaded at all, so a build
    shipped without Steam simply carries on."""
    try:
        _dll = _load_library()
    except Exception:
        return False
    _fn = getattr(_dll, "SteamAPI_RestartAppIfNecessary", None)
    if _fn is None:
        return False
    try:
        _fn.argtypes = [_U32]
        _fn.restype = _BOOL
        return bool(_fn(_U32(int(app_id))))
    except Exception:
        return False


# --- Top-level STEAMWORKS handle -------------------------------------------
class STEAMWORKS:
    """Match SteamworksPy's public class name so `from steamworks
    import STEAMWORKS` keeps working. Instantiating this loads the
    DLL and binds SteamAPI_Init / _Shutdown / _RunCallbacks; call
    .initialize() to actually boot Steam. Sub-interfaces are attached
    as no-op instances up front and replaced with live ones after a
    successful initialize(), so a caller may touch any of them before
    deciding whether Steam is there."""

    # (attribute name, class, accessor candidates, label for the log)
    _INTERFACES = (
        ("UserStats",     _UserStats,     _USER_STATS_ACCESSORS,     "UserStats"),
        ("Users",         _Users,         _USER_ACCESSORS,           "User"),
        ("Friends",       _Friends,       _FRIENDS_ACCESSORS,        "Friends"),
        ("Apps",          _Apps,          _APPS_ACCESSORS,           "Apps"),
        ("Utils",         _Utils,         _UTILS_ACCESSORS,          "Utils"),
        ("Music",         _Music,         _MUSIC_ACCESSORS,          "Music"),
        ("Screenshots",   _Screenshots,   _SCREENSHOTS_ACCESSORS,    "Screenshots"),
        ("RemoteStorage", _RemoteStorage, _REMOTE_STORAGE_ACCESSORS, "RemoteStorage"),
    )

    def __init__(self):
        self._dll = _load_library()
        # Bind the shutdown + callback pump. Init is picked lazily in
        # initialize() below because the DLL export name for it varies
        # between SDK versions (Init vs InitFlat vs InitEx).
        for _name, _args, _ret in (
            ("SteamAPI_Shutdown",     [], None),
            ("SteamAPI_RunCallbacks", [], None),
        ):
            _fn = getattr(self._dll, _name, None)
            if _fn is not None:
                _fn.argtypes = _args
                _fn.restype = _ret
        self._initialized = False
        self.app_id = 0
        for _attr, _cls, _accessors, _label in self._INTERFACES:
            setattr(self, _attr, _cls(self._dll, None))

    def initialize(self):
        """Boot Steamworks. Raises SteamNotRunningException on any
        failure (Steam not running, AppID not owned, appid.txt missing,
        and so on). A caller that treats Steam as optional wraps this
        in try/except, drops its handle, and carries on without it."""
        # Newer SDKs (v1.59+) no longer export a bare `SteamAPI_Init`:
        # it is an inline C++ helper in the header now. The actual
        # DLL export is `SteamAPI_InitFlat`, which takes a 1024-byte
        # error buffer and returns an ESteamAPIInitResult enum where
        # 0 == OK. Old SDKs still expose `SteamAPI_Init` returning
        # bool. Try flat first, fall back to the classic name.
        _init_flat = getattr(self._dll, "SteamAPI_InitFlat", None)
        _init_ex   = getattr(self._dll, "SteamAPI_InitEx", None)
        _init_old  = getattr(self._dll, "SteamAPI_Init", None)
        if _init_flat is not None or _init_ex is not None:
            _fn = _init_flat if _init_flat is not None else _init_ex
            _err_buf = ctypes.create_string_buffer(1024)
            _fn.argtypes = [_CHARP]
            _fn.restype  = _INT
            _res = int(_fn(_err_buf))
            if _res != 0:   # k_ESteamAPIInitResult_OK == 0
                raise SteamNotRunningException(
                    f"SteamAPI_InitFlat failed (result={_res}): "
                    f"{_err_buf.value.decode('utf-8', errors='ignore')}")
        elif _init_old is not None:
            _init_old.argtypes = []
            _init_old.restype  = _BOOL
            if not bool(_init_old()):
                raise SteamNotRunningException(
                    "SteamAPI_Init returned false: Steam client not "
                    "running, AppID not owned by this account, or "
                    "steam_appid.txt missing/wrong")
        else:
            raise OSError(
                "steam_api DLL exports no known init function "
                "(tried SteamAPI_InitFlat, SteamAPI_InitEx, SteamAPI_Init)")
        self._initialized = True
        # Now that init succeeded, resolve the versioned accessors and
        # instantiate the sub-interfaces with the live pointers. An
        # accessor the DLL does not export leaves that interface as the
        # no-op instance it already is.
        _report = []
        for _attr, _cls, _accessors, _label in self._INTERFACES:
            _fn, _ver = _resolve(self._dll, _accessors)
            _iface = None
            if _fn is not None:
                _fn.argtypes = []
                _fn.restype = _VOID
                _iface = _fn()
            setattr(self, _attr, _cls(self._dll, _iface))
            _report.append(f"{_label}={_ver or 'MISSING'}")
        self.app_id = self.Utils.GetAppID()
        print("[steamworks] init OK: " + " ".join(_report))
        return True

    def loaded(self):
        """True once initialize() has succeeded and not been undone."""
        return self._initialized

    def relaunch(self, app_id):
        """SteamworksPy's name for restart_app_if_necessary. Same
        meaning: True means Steam is bringing us back up and this
        process must exit."""
        return restart_app_if_necessary(app_id)

    def run_callbacks(self):
        if not self._initialized:
            return
        _fn = getattr(self._dll, "SteamAPI_RunCallbacks", None)
        if _fn is None:
            return
        try:
            _fn()
        except Exception:
            pass

    def shutdown(self):
        """Optional. Call before process exit so Steam records a
        clean disconnect. Safe to skip; SteamAPI_Shutdown is idempotent
        and the OS reclaims the DLL on exit anyway."""
        if not self._initialized:
            return
        _fn = getattr(self._dll, "SteamAPI_Shutdown", None)
        if _fn is None:
            return
        try:
            _fn()
        finally:
            self._initialized = False
            self.app_id = 0
            for _attr, _cls, _accessors, _label in self._INTERFACES:
                setattr(self, _attr, _cls(self._dll, None))

    def unload(self):
        """SteamworksPy's name for shutdown()."""
        self.shutdown()
