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
    api.UserStats.RequestCurrentStats()      # -> bool
    api.UserStats.SetAchievement(b"NAME")    # -> bool
    api.UserStats.ClearAchievement(b"NAME")  # -> bool
    api.UserStats.StoreStats()               # -> bool
    api.UserStats.GetNumAchievements()       # -> int
    api.UserStats.GetAchievement(b"NAME")    # -> True / False / None
    api.UserStats.GetAchievementName(idx)    # -> bytes
    api.Users.GetSteamID()                   # -> int (u64)
    api.Friends.GetPlayerName()              # -> bytes
    api.RemoteStorage.FileWrite(name, data)  # -> bool
    api.RemoteStorage.FileRead(name)         # -> bytes

There is nothing to build and nothing to pip-install: drop this
package beside your entry point, put the redistributable binary for
your platform next to it, and import it. The only hard dependency is
the standard library.

Every method is defensive. If the binary did not load, or the SDK's
interface accessor returned NULL (Steam client not running, AppID not
owned), each method returns a no-op value (False / 0 / b"") rather
than raising, so a caller that treats Steam as optional can run the
same code path whether or not Steam is there. `initialize()` is the
one exception: it raises, so a caller can tell "Steam is absent" from
"Steam is present and said no".

Import it only on the builds that ship the binary. A build without
Steam should not reach this module, and does not need the binary
present.
"""
import ctypes
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
    "SteamAPI_SteamApps_v008",
    "SteamAPI_SteamApps_v007",
)
_REMOTE_STORAGE_ACCESSORS = (
    "SteamAPI_SteamRemoteStorage_v016",
    "SteamAPI_SteamRemoteStorage_v015",
    "SteamAPI_SteamRemoteStorage_v014",
)


class SteamNotRunningException(Exception):
    """Raised by initialize() when SteamAPI_Init returns False,
    typically because the Steam client isn't running, the account
    doesn't own this AppID, or steam_appid.txt is missing / wrong."""


def _load_library():
    """Locate + load the Steamworks redistributable binary. Looks first
    in this package's directory (where the binary ships), then falls
    back to the folder holding the package, the process cwd, the
    directory holding the executable, and the PyInstaller bundle dir.
    Returns the loaded CDLL, or raises OSError with a diagnostic
    message naming every path it tried.

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
    raise OSError(
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


# --- SteamUserStats interface ----------------------------------------------
class _UserStats:
    def __init__(self, dll, iface):
        # iface is a raw void* to the interface instance. None means
        # the accessor returned NULL (SDK couldn't find the interface,
        # e.g. Steam client not running), and every method below no-ops.
        self._dll = dll
        self._iface = iface
        if iface is None:
            return
        # Bind the flat C wrappers we need. All take the interface
        # pointer as first arg (matches C++ `this`).
        _b = self._bind
        _b("SteamAPI_ISteamUserStats_RequestCurrentStats",
           [ctypes.c_void_p], ctypes.c_bool)
        _b("SteamAPI_ISteamUserStats_SetAchievement",
           [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        _b("SteamAPI_ISteamUserStats_ClearAchievement",
           [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        _b("SteamAPI_ISteamUserStats_StoreStats",
           [ctypes.c_void_p], ctypes.c_bool)
        _b("SteamAPI_ISteamUserStats_GetNumAchievements",
           [ctypes.c_void_p], ctypes.c_uint32)
        _b("SteamAPI_ISteamUserStats_GetAchievementName",
           [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_char_p)
        _b("SteamAPI_ISteamUserStats_GetAchievement",
           [ctypes.c_void_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_bool)], ctypes.c_bool)

    def _bind(self, name, argtypes, restype):
        _fn = getattr(self._dll, name, None)
        if _fn is None:
            return
        _fn.argtypes = argtypes
        _fn.restype = restype

    def _call(self, name, *args, default=None):
        if self._iface is None:
            return default
        _fn = getattr(self._dll, name, None)
        if _fn is None:
            return default
        try:
            return _fn(self._iface, *args)
        except Exception:
            return default

    def RequestCurrentStats(self):
        return bool(self._call(
            "SteamAPI_ISteamUserStats_RequestCurrentStats",
            default=False))

    def SetAchievement(self, api_name):
        # SteamworksPy passes bytes; accept str too for callers that
        # forget the encode.
        if isinstance(api_name, str):
            api_name = api_name.encode("ascii")
        return bool(self._call(
            "SteamAPI_ISteamUserStats_SetAchievement",
            api_name, default=False))

    def ClearAchievement(self, api_name):
        if isinstance(api_name, str):
            api_name = api_name.encode("ascii")
        return bool(self._call(
            "SteamAPI_ISteamUserStats_ClearAchievement",
            api_name, default=False))

    def StoreStats(self):
        return bool(self._call(
            "SteamAPI_ISteamUserStats_StoreStats",
            default=False))

    def GetNumAchievements(self):
        return int(self._call(
            "SteamAPI_ISteamUserStats_GetNumAchievements",
            default=0) or 0)

    def GetAchievement(self, api_name):
        #Whether Steam currently has this achievement granted: True or
        #False when Steam knows, None when it does not - the stats
        #round-trip has not landed, or the name is not in the AppID's
        #schema. None is deliberately distinct from False, since a
        #caller revoking achievements must not act on "don't know".
        if isinstance(api_name, str):
            api_name = api_name.encode("ascii")
        _flag = ctypes.c_bool(False)
        _ok = self._call("SteamAPI_ISteamUserStats_GetAchievement",
                         api_name, ctypes.byref(_flag), default=None)
        if not _ok:
            return None
        return bool(_flag.value)

    def GetAchievementName(self, index):
        _res = self._call(
            "SteamAPI_ISteamUserStats_GetAchievementName",
            ctypes.c_uint32(int(index)), default=None)
        # SDK returns const char*; ctypes hands us bytes (or None on
        # NULL). Match SteamworksPy which returns bytes.
        return _res if _res is not None else b""


# --- SteamUser interface ---------------------------------------------------
class _Users:
    def __init__(self, dll, iface):
        self._dll = dll
        self._iface = iface
        if iface is None:
            return
        _fn = getattr(dll, "SteamAPI_ISteamUser_GetSteamID", None)
        if _fn is not None:
            _fn.argtypes = [ctypes.c_void_p]
            _fn.restype = ctypes.c_uint64

    def GetSteamID(self):
        if self._iface is None:
            return 0
        _fn = getattr(self._dll, "SteamAPI_ISteamUser_GetSteamID", None)
        if _fn is None:
            return 0
        try:
            return int(_fn(self._iface))
        except Exception:
            return 0


# --- SteamFriends interface ------------------------------------------------
class _Friends:
    def __init__(self, dll, iface):
        self._dll = dll
        self._iface = iface
        if iface is None:
            return
        _fn = getattr(dll, "SteamAPI_ISteamFriends_GetPersonaName", None)
        if _fn is not None:
            _fn.argtypes = [ctypes.c_void_p]
            _fn.restype = ctypes.c_char_p

    def GetPlayerName(self):
        # SteamworksPy renames GetPersonaName -> GetPlayerName on the
        # Python side; the underlying C call is GetPersonaName. The
        # alias is kept so callers written against that package work.
        if self._iface is None:
            return b""
        _fn = getattr(self._dll, "SteamAPI_ISteamFriends_GetPersonaName", None)
        if _fn is None:
            return b""
        try:
            _res = _fn(self._iface)
            return _res if _res is not None else b""
        except Exception:
            return b""


# --- SteamRemoteStorage interface ------------------------------------------
class _RemoteStorage:
    """ISteamRemoteStorage - Steam Cloud file I/O.

    FileWrite / FileRead are the synchronous pair, which is what suits
    a file of a few hundred bytes: Steam takes the write into its own
    quota-managed store and uploads it in the background, so there is
    no callback to pump here. Cloud has to be on for both the app (set
    on the partner site) and the account (the player's setting), and
    IsCloudEnabledForApp / ForAccount report which.

    Steam Cloud filenames are its own flat namespace, not disk paths.
    """

    MAX_READ = 1 << 20      # refuse to allocate for an implausible file

    def __init__(self, dll, iface):
        self._dll = dll
        self._iface = iface
        if iface is None:
            return
        _b = self._bind
        _b("SteamAPI_ISteamRemoteStorage_FileWrite",
           [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p,
            ctypes.c_int32], ctypes.c_bool)
        _b("SteamAPI_ISteamRemoteStorage_FileRead",
           [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p,
            ctypes.c_int32], ctypes.c_int32)
        _b("SteamAPI_ISteamRemoteStorage_FileExists",
           [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        _b("SteamAPI_ISteamRemoteStorage_FileDelete",
           [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        _b("SteamAPI_ISteamRemoteStorage_GetFileSize",
           [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int32)
        _b("SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount",
           [ctypes.c_void_p], ctypes.c_bool)
        _b("SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp",
           [ctypes.c_void_p], ctypes.c_bool)

    def _bind(self, name, argtypes, restype):
        _fn = getattr(self._dll, name, None)
        if _fn is None:
            return
        _fn.argtypes = argtypes
        _fn.restype = restype

    def _call(self, name, *args, default=None):
        if self._iface is None:
            return default
        _fn = getattr(self._dll, name, None)
        if _fn is None:
            return default
        try:
            return _fn(self._iface, *args)
        except Exception:
            return default

    @staticmethod
    def _name(path):
        if isinstance(path, str):
            return path.encode("utf-8")
        return path

    def IsCloudEnabledForAccount(self):
        return bool(self._call(
            "SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount",
            default=False))

    def IsCloudEnabledForApp(self):
        return bool(self._call(
            "SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp",
            default=False))

    def FileExists(self, path):
        return bool(self._call(
            "SteamAPI_ISteamRemoteStorage_FileExists",
            self._name(path), default=False))

    def GetFileSize(self, path):
        return int(self._call(
            "SteamAPI_ISteamRemoteStorage_GetFileSize",
            self._name(path), default=0) or 0)

    def FileDelete(self, path):
        return bool(self._call(
            "SteamAPI_ISteamRemoteStorage_FileDelete",
            self._name(path), default=False))

    def FileWrite(self, path, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data:
            # A zero-byte write is an error in the SDK, and FileRead
            # cannot tell an empty file from a missing one anyway.
            return False
        _buf = ctypes.create_string_buffer(data, len(data))
        return bool(self._call(
            "SteamAPI_ISteamRemoteStorage_FileWrite",
            self._name(path), ctypes.cast(_buf, ctypes.c_void_p),
            ctypes.c_int32(len(data)), default=False))

    def FileRead(self, path):
        #Return the file's bytes, or b"" when it is absent, empty,
        #implausibly large, or the read came back short.
        _size = self.GetFileSize(path)
        if _size <= 0 or _size > self.MAX_READ:
            return b""
        _buf = ctypes.create_string_buffer(_size)
        _got = int(self._call(
            "SteamAPI_ISteamRemoteStorage_FileRead",
            self._name(path), ctypes.cast(_buf, ctypes.c_void_p),
            ctypes.c_int32(_size), default=0) or 0)
        if _got <= 0:
            return b""
        return _buf.raw[:_got]


# --- SteamApps interface ---------------------------------------------------
class _Apps:
    """ISteamApps ownership queries. BIsSubscribed() answers "does the
    signed-in account own the app this process is running as", which is
    what a launch gate needs; BIsSubscribedApp(id) asks about some other
    app (e.g. the full game, from the demo)."""

    def __init__(self, dll, iface):
        self._dll = dll
        self._iface = iface

    def _call_bool(self, name, *args, default=None):
        if self._iface is None:
            return default
        _fn = getattr(self._dll, name, None)
        if _fn is None:
            return default
        try:
            _fn.restype = ctypes.c_bool
            return bool(_fn(self._iface, *args))
        except Exception:
            return default

    def BIsSubscribed(self):
        """True = owns it, False = does not, None = could not ask."""
        return self._call_bool("SteamAPI_ISteamApps_BIsSubscribed")

    def BIsSubscribedApp(self, app_id):
        _fn = getattr(self._dll, "SteamAPI_ISteamApps_BIsSubscribedApp", None)
        if self._iface is None or _fn is None:
            return None
        try:
            _fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            _fn.restype = ctypes.c_bool
            return bool(_fn(self._iface, ctypes.c_uint32(int(app_id))))
        except Exception:
            return None


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
        _fn.argtypes = [ctypes.c_uint32]
        _fn.restype = ctypes.c_bool
        return bool(_fn(ctypes.c_uint32(int(app_id))))
    except Exception:
        return False


# --- Top-level STEAMWORKS handle -------------------------------------------
class STEAMWORKS:
    """Match SteamworksPy's public class name so `from steamworks
    import STEAMWORKS` keeps working. Instantiating this loads the
    DLL and binds SteamAPI_Init / _Shutdown / _RunCallbacks; call
    .initialize() to actually boot Steam. Sub-interfaces
    (`UserStats`, `Users`, `Friends`, `Apps`, `RemoteStorage`) are
    attached after successful initialize()."""

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
        self.UserStats = _UserStats(self._dll, None)
        self.Users     = _Users(self._dll, None)
        self.Friends   = _Friends(self._dll, None)
        self.Apps      = _Apps(self._dll, None)
        self.RemoteStorage = _RemoteStorage(self._dll, None)

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
            _fn.argtypes = [ctypes.c_char_p]
            _fn.restype  = ctypes.c_int
            _res = int(_fn(_err_buf))
            if _res != 0:   # k_ESteamAPIInitResult_OK == 0
                raise SteamNotRunningException(
                    f"SteamAPI_InitFlat failed (result={_res}): "
                    f"{_err_buf.value.decode('utf-8', errors='ignore')}")
        elif _init_old is not None:
            _init_old.argtypes = []
            _init_old.restype  = ctypes.c_bool
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
        # Now that init succeeded, resolve the versioned accessors
        # and instantiate the sub-interfaces with the live pointers.
        _us_fn, _us_ver   = _resolve(self._dll, _USER_STATS_ACCESSORS)
        _u_fn,  _u_ver    = _resolve(self._dll, _USER_ACCESSORS)
        _fr_fn, _fr_ver   = _resolve(self._dll, _FRIENDS_ACCESSORS)
        _ap_fn, _ap_ver   = _resolve(self._dll, _APPS_ACCESSORS)
        _rs_fn, _rs_ver   = _resolve(self._dll, _REMOTE_STORAGE_ACCESSORS)
        for _fn in (_us_fn, _u_fn, _fr_fn, _ap_fn, _rs_fn):
            if _fn is not None:
                _fn.argtypes = []
                _fn.restype = ctypes.c_void_p
        _us_iface = _us_fn() if _us_fn else None
        _u_iface  = _u_fn()  if _u_fn  else None
        _fr_iface = _fr_fn() if _fr_fn else None
        _ap_iface = _ap_fn() if _ap_fn else None
        _rs_iface = _rs_fn() if _rs_fn else None
        self.UserStats = _UserStats(self._dll, _us_iface)
        self.Users     = _Users(self._dll, _u_iface)
        self.Friends   = _Friends(self._dll, _fr_iface)
        self.Apps      = _Apps(self._dll, _ap_iface)
        self.RemoteStorage = _RemoteStorage(self._dll, _rs_iface)
        print(f"[steamworks] init OK: "
              f"UserStats={_us_ver or 'MISSING'} "
              f"User={_u_ver or 'MISSING'} "
              f"Friends={_fr_ver or 'MISSING'} "
              f"Apps={_ap_ver or 'MISSING'} "
              f"RemoteStorage={_rs_ver or 'MISSING'}")
        return True

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
