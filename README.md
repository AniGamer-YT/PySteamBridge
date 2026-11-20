<img src="pysteambridge.png" alt="pySteamBridge" width="256">

# pySteamBridge

A modern FOSS wrapper for the Steamworks SDK by Valve. This wrapper is
the same one used in **PixelRift**.
This does not redistribute any of Valve's binaries, so you'll need to
bring your own to get the project working.

One folder, no build step, no compiler, and nothing outside the
standard library. It loads Valve's redistributable with `ctypes` and
calls the flat C exports directly, so there is no extension module to
compile and no wheel to match against your interpreter.

```python
from pysteambridge import STEAMWORKS

api = STEAMWORKS()
api.initialize()
api.UserStats.SetAchievement(b"FIRST_WIN")
api.UserStats.StoreStats()
print(api.Friends.GetPlayerName(), api.Utils.GetIPCountry())
```

Eight interfaces are wired: `UserStats`, `Users`, `Friends`, `Apps`,
`Utils`, `Music`, `Screenshots` and `RemoteStorage`. Ninety-one
methods over ninety flat-API functions, all listed further down.

## Why this exists

The usual Python route to Steamworks is [SteamworksPy][swpy], which
wraps the SDK in a C++ shim you build yourself. That shim is a real
dependency: a compiler, a matching SDK checkout, and a rebuild for
every platform you ship on.

pySteamBridge skips it. Valve already exports a flat C ABI from the
redistributable (`SteamAPI_ISteamUserStats_SetAchievement` and
friends), which is exactly what `ctypes` can call. The result is a
pure-Python package that ships the same way everywhere.

It keeps SteamworksPy's attribute layout, so code written against that
package runs unchanged.

---

# Getting it running

## 1. Drop it into your project

There is nothing to install. Copy the `pysteambridge/` folder next to
your entry point and import it:

```
yourgame/
    main.py
    pysteambridge/
        __init__.py
        steamworks.py
```

It has no dependencies beyond the standard library, which is what
makes vendoring it the normal way to use it.

Want the `steamworks` import name too? Copy the `steamworks/` folder
across as well. It holds no implementation, only the alias.

## 2. Go and fetch Valve's binary

**No Steamworks binary is included here.** Valve's redistributable is covered by the Steamworks SDK Access Agreement, not by this project's MIT license. Download the SDK from the [Steamworks partner site][sdk] and take the file for your platform out of `sdk/redistributable_bin/`:

| Platform | File | In the SDK zip |
|---|---|---|
| Windows (64-bit) | `steam_api64.dll` | `redistributable_bin/win64/` |
| Windows (32-bit) | `steam_api.dll` | `redistributable_bin/` |
| Linux (64-bit) | `libsteam_api.so` | `redistributable_bin/linux64/` |
| macOS | `libsteam_api.dylib` | `redistributable_bin/osx/` |

### So where does the file actually go?

**Inside the `pysteambridge/` folder.** That is the first place
looked, it works no matter what directory the game is launched from,
and the binary travels with the package if you move it:

```
yourgame/
    main.py
    pysteambridge/
        __init__.py
        steamworks.py
        steam_api64.dll      <-- here
```

That is the only answer most projects need. If it suits your layout
better, any of these also work, and are checked in this order:

| # | Location | Example |
|---|---|---|
| 1 | The package directory | `yourgame/pysteambridge/steam_api64.dll` |
| 2 | The folder holding the package | `yourgame/steam_api64.dll` |
| 3 | The working directory the process was launched from | wherever you ran `python` |
| 4 | Beside the executable | next to `yourgame.exe` |
| 5 | PyInstaller's `_MEIPASS` unpack directory | handled by your `.spec` |

Under rows 2 through 5 a `pysteambridge/` or `steamworks/` subfolder
is checked too, so `dist/yourgame/pysteambridge/steam_api64.dll` is
found just as well as `dist/yourgame/steam_api64.dll`.

Row 3 is worth avoiding as your only copy: the working directory is
wherever the player happened to launch from, which is not somewhere
you control.

If nothing is found, the loader raises
`MissingSteamworksLibraryException` listing every path it tried, so a
missing binary is never a mystery:

```
MissingSteamworksLibraryException: Steamworks binary not found. Looked
for steam_api64.dll, steam_api.dll in:
C:\yourgame\pysteambridge\steam_api64.dll | ...
```

That exception is also an `OSError`, which is what this used to raise,
so either `except` clause catches it.

Keep the file name exactly as Valve ships it. Do not rename, and on
Windows use the 64-bit `steam_api64.dll` unless your interpreter is
32-bit.

Ship the binary with your game under your own agreement with Valve.

## 3. Tell Steam who you are

Steam identifies your process by App ID. Launched through the Steam
client, that happens automatically. Running from your IDE it does not,
so during development put a file named `steam_appid.txt` next to your
entry point containing nothing but the number:

```
480
```

480 is Spacewar, Valve's public test app, which any Steam account can
use. Swap in your own App ID once you have one.

**Delete `steam_appid.txt` from your shipping build.** While it is
present, `restart_app_if_necessary` is suppressed and anyone can run
your game as any App ID.

## 4. Start it up, and cope when Steam is missing

`initialize()` raises if Steam is not there. That is deliberate: it
lets you tell "no Steam" apart from "Steam says no". Every other method
is a no-op instead, so one code path covers both worlds.

```python
try:
    from pysteambridge import STEAMWORKS
    steam = STEAMWORKS()
    steam.initialize()
except Exception:
    steam = None          # no Steam client, not installed, not owned
```

From here on, guard with `if steam:` and nothing else needs to care.

## 5. Give Steam a heartbeat

Call this once per frame, or on any regular timer. It is cheap, and
Steam needs it to deliver overlay events and stats round-trips:

```python
while running:
    ...
    if steam:
        steam.run_callbacks()
```

## 6. Pop an achievement

Achievement API names come from the Steamworks partner site, under
your app's Stats & Achievements page. They are the API name, not the
display name.

```python
if steam:
    steam.UserStats.SetAchievement(b"FIRST_WIN")
    steam.UserStats.StoreStats()        # required: this is what uploads
```

`SetAchievement` alone changes nothing on Steam's side. `StoreStats()`
is the commit, and the overlay toast fires from it.

To read one back:

```python
got = steam.UserStats.GetAchievement(b"FIRST_WIN")
# True  -> unlocked
# False -> locked
# None  -> Steam does not know yet
```

`None` is distinct from `False` on purpose. It means the stats round
trip has not landed, or that name is not in your app's schema. Code
that revokes or syncs achievements must not treat it as "locked".

Give Steam a few `run_callbacks()` pumps after init before trusting a
read. Fetching the player's stats is something Steam now starts by
itself during init, and it arrives a moment later over those pumps.
`RequestCurrentStats()` is still here because SteamworksPy has it, but
current SDKs no longer export it and it returns `False`: that means
"nothing to ask", not a failure.

Stats work the same way, and are declared alongside achievements on
the partner site:

```python
steam.UserStats.SetStat(b"ENEMIES_KILLED", 41)      # int stat
steam.UserStats.SetStat(b"DISTANCE_RUN", 12.5)      # float stat
steam.UserStats.StoreStats()
kills = steam.UserStats.GetStatInt(b"ENEMIES_KILLED")
```

A Python `float` picks the SDK's float setter and an `int` picks the
int one, so the type you pass has to match how the stat was declared
or Steam rejects the write.

## 7. Put a save in the Cloud

Cloud filenames are Steam's own flat namespace, not disk paths. Cloud
must be enabled both for the app (partner site) and for the account.

```python
rs = steam.RemoteStorage
if rs.IsCloudEnabledForApp() and rs.IsCloudEnabledForAccount():
    rs.FileWrite("save.json", json.dumps(save).encode("utf-8"))
    raw = rs.FileRead("save.json")      # b"" if absent
```

Writes are synchronous into Steam's quota-managed store and upload in
the background, so there is no callback to wait on.

## 8. Let Steam relaunch you, then check they own it

Before anything else in your entry point:

```python
from pysteambridge import restart_app_if_necessary

if restart_app_if_necessary(480):
    raise SystemExit      # Steam is starting a fresh copy; let it
```

True means Steam is relaunching your process through the client, so
you must exit immediately. This is what makes a double-clicked exe come
back up Steam-owned, with the overlay attached and the right App ID.

Then, if you care whether they own it:

```python
owns = steam.Apps.BIsSubscribed()   # True / False / None (couldn't ask)
```

## 9. Bow out cleanly

Optional, and safe to skip. It just lets Steam record a clean
disconnect:

```python
if steam:
    steam.shutdown()
```

## The whole thing, start to finish

```python
import json
from pysteambridge import STEAMWORKS, restart_app_if_necessary

APP_ID = 480

if restart_app_if_necessary(APP_ID):
    raise SystemExit

try:
    steam = STEAMWORKS()
    steam.initialize()
except Exception:
    steam = None
    print("running without Steam")

def on_first_win():
    if not steam:
        return
    if steam.UserStats.GetAchievement(b"FIRST_WIN") is False:
        steam.UserStats.SetAchievement(b"FIRST_WIN")
        steam.UserStats.StoreStats()

while True:
    ...                      # your game loop
    if steam:
        steam.run_callbacks()
```

---

## Two names, one object

```python
from pysteambridge import STEAMWORKS   # canonical
from steamworks import STEAMWORKS      # SteamworksPy-compatible alias
```

Both give the same object. The `steamworks` package contains no
implementation: it re-exports pysteambridge and registers
`steamworks.steamworks`, `steamworks.enums` and
`steamworks.exceptions` as aliases of the one implementation module,
so `isinstance`, `except SteamNotRunningException` and
`from steamworks.enums import FriendFlags` all hold whichever import a
caller used. Migrating off SteamworksPy means swapping the folder,
with no edit to a single call site.

> **Conflict note.** If SteamworksPy is already installed in the same
> environment, do not copy the `steamworks/` folder in. Two things
> would be claiming one import name, and which one wins depends on
> path order. Vendor `pysteambridge/` alone and import it under its
> own name.

## More of the SDK than achievements

### The overlay, and what friends see

Every way of opening the Steam overlay is on `Friends`, including the
one that saves you shipping an in-game browser:

```python
steam.Friends.ActivateGameOverlay("Friends")        # or Community, Settings...
steam.Friends.ActivateGameOverlayToWebPage("https://example.com/patch-notes")
steam.Friends.ActivateGameOverlayToStore(480)
steam.Friends.ActivateGameOverlayToUser("steamid", friend_id)
```

Rich presence is the line Steam shows under your game in a friend's
list. The keys it renders are configured per app on the partner site;
an unconfigured key is stored but never displayed.

```python
steam.Friends.SetGameInfo("status", "Frozen Frontier, 3 lives left")
steam.Friends.ClearGameInfo()
```

And the friends list itself:

```python
from pysteambridge import FriendFlags

for i in range(steam.Friends.GetFriendCount(FriendFlags.IMMEDIATE)):
    sid = steam.Friends.GetFriendByIndex(i, FriendFlags.IMMEDIATE)
    print(steam.Friends.GetFriendPersonaName(sid))
```

The order is not stable between calls, so read the count and the
indices in the same frame.

### Ownership, DLC and install paths

```python
steam.Apps.IsSubscribed()                   # owns the app we run as
steam.Apps.IsDLCInstalled(123456)
steam.Apps.InstallDLC(123456)               # then poll IsDLCInstalled
steam.Apps.GetAppBuildId()                  # a real build number for bug reports
steam.Apps.GetCurrentGameLanguage()         # "english", "brazilian", ...
steam.Apps.GetEarliestPurchaseUnixTime(my_app_id)   # early supporter?
steam.Apps.GetLaunchQueryParam("connect")   # parameters from a steam:// link
```

`GetAppInstallDir(app_id)` answers with the path an app would occupy
whether or not it is there, so pair it with `IsAppInstalled` rather
than reading a path as proof.

### Clock, locale and overlay state

```python
steam.Utils.GetServerRealTime()      # Steam's clock, not the player's
steam.Utils.GetIPCountry()           # "SG", "DE", ...
steam.Utils.GetSteamUILanguage()
steam.Utils.IsOverlayEnabled()
steam.Utils.OverlayNeedsPresent()    # the overlay drew: flip this frame
steam.Utils.GetCurrentBatteryPower() # percent left, or 255 on mains
steam.Utils.IsSteamInBigPictureMode()
```

`GetServerRealTime()` is the one to trust for anything a player could
cheat by moving their system clock: daily rewards, timed events, run
timings.

Overlay notifications can be moved out from under your HUD:

```python
from pysteambridge import ENotificationPosition

steam.Utils.SetOverlayNotificationPosition(ENotificationPosition.TOP_LEFT)
steam.Utils.SetOverlayNotificationInset(16, 16)
```

### Steam's music player

Not your game's audio: the player's own Steam music, which you can
duck during a cutscene and hand back afterwards.

```python
if steam.Music.MusicIsEnabled() and steam.Music.MusicIsPlaying():
    was_playing = True
    steam.Music.MusicPause()
...
if was_playing:
    steam.Music.MusicPlay()
```

### Screenshots

```python
steam.Screenshots.TriggerScreenshot()       # exactly as the player's key would
handle = steam.Screenshots.AddScreenshotToLibrary("shot.png", None, 1920, 1080)
steam.Screenshots.SetLocation(handle, "Frozen Frontier")
```

`HookScreenshots(True)` takes the player's screenshot key over, so
Steam stops writing its own capture and asks your game to write one
instead. That request arrives as a callback, which this wrapper does
not pump yet, so leave the hook off until it does or the key does
nothing.

## What is actually wired up

Ninety-one methods over ninety flat-API functions, across eight
interfaces. Names and argument order follow SteamworksPy, so a call
site written against that package works here unchanged.

| Interface | Methods |
|---|---|
| `UserStats` | `RequestCurrentStats`, `SetAchievement`, `ClearAchievement`, `StoreStats`, `GetNumAchievements`, `GetAchievement`, `GetAchievementName`, `GetAchievementDisplayAttribute`, `GetStatInt`, `GetStatFloat`, `SetStat`, `ResetAllStats` |
| `Users` | `GetSteamID`, `LoggedOn`, `GetPlayerSteamLevel`, `GetGameBadgeLevel`, `GetAuthSessionTicket` |
| `Friends` | `GetPlayerName`, `GetPlayerState`, `GetFriendCount`, `GetFriendByIndex`, `GetFriendPersonaName`, `GetFriendPersonaState`, `SetGameInfo`, `ClearGameInfo`, `InviteFriend`, `SetPlayedWith`, `ActivateGameOverlay`, `ActivateGameOverlayToUser`, `ActivateGameOverlayToWebPage`, `ActivateGameOverlayToStore`, `ActivateGameOverlayInviteDialog` |
| `Apps` | `IsSubscribed`, `BIsSubscribed`, `IsSubscribedApp`, `BIsSubscribedApp`, `IsSubscribedFromFreeWeekend`, `IsLowViolence`, `IsCybercafe`, `IsVACBanned`, `GetCurrentGameLanguage`, `GetAvailableGameLanguages`, `GetDLCCount`, `IsDLCInstalled`, `InstallDLC`, `UninstallDLC`, `GetAppInstallDir`, `IsAppInstalled`, `GetAppOwner`, `GetAppBuildId`, `GetEarliestPurchaseUnixTime`, `GetLaunchQueryParam`, `MarkContentCorrupt`, `GetFileDetails` |
| `Utils` | `GetAppID`, `GetIPCountry`, `GetSteamUILanguage`, `GetServerRealTime`, `GetSecondsSinceAppActive`, `GetSecondsSinceComputerActive`, `GetCurrentBatteryPower`, `GetIPCCallCount`, `IsOverlayEnabled`, `OverlayNeedsPresent`, `IsSteamInBigPictureMode`, `SetOverlayNotificationPosition`, `SetOverlayNotificationInset`, `ShowGamepadTextInput`, `IsVRHeadsetStreamingEnabled`, `SetVRHeadsetStreamingEnabled`, `StartVRDashboard` |
| `Music` | `MusicIsEnabled`, `MusicIsPlaying`, `MusicGetVolume`, `MusicSetVolume`, `MusicPlay`, `MusicPause`, `MusicPlayNext`, `MusicPlayPrev` |
| `Screenshots` | `TriggerScreenshot`, `AddScreenshotToLibrary`, `SetLocation`, `HookScreenshots`, `IsScreenshotsHooked` |
| `RemoteStorage` | `FileWrite`, `FileRead`, `FileExists`, `FileDelete`, `GetFileSize`, `IsCloudEnabledForApp`, `IsCloudEnabledForAccount` |

On the handle itself: `initialize()`, `run_callbacks()`, `shutdown()`,
`loaded()`, `relaunch(app_id)`, `unload()` and `app_id`. Plus
`restart_app_if_necessary(app_id)` at module level, which `relaunch`
is SteamworksPy's name for.

`RemoteStorage` is this wrapper's own addition. SteamworksPy has no
such interface, so nothing written against that package can collide
with it.

Two string getters return `bytes` rather than `str`, because they did
so before the rest of the surface existed and call sites decode them
themselves: `Friends.GetPlayerName` and `UserStats.GetAchievementName`.
Every other string getter returns `str`, the way SteamworksPy does.

### Enums and exceptions

`FriendFlags`, `ENotificationPosition`, `EGamepadTextInputMode`,
`EGamepadTextInputLineMode` and `EPersonaState` are importable from
either package name, and from `steamworks.enums` as well. They are
`IntEnum`, not `Enum`, so a member works both as an enum and as the
bare int the SDK wants: passing an int keeps working.

The exceptions are SteamworksPy's names, importable from
`steamworks.exceptions` too: `GenericSteamException` (the base),
`SteamNotRunningException`, `MissingSteamworksLibraryException`,
`SteamNotLoadedException`, `SteamConnectionException` and
`UnsupportedPlatformException`. Only the first two are ever raised
here; the rest exist so an `except` clause written against
SteamworksPy compiles.

## What is not wired up, and why

Matchmaking, Workshop, Input and MicroTxn are absent, along with
leaderboards on `UserStats`. Every useful call on those returns its
answer through Steam's callback and call-result dispatch rather than
as a return value, which needs the manual-dispatch pump
(`SteamAPI_ManualDispatch_*`) rather than a plain function call.

The same goes for a handful of calls that are here: `InstallDLC`,
`GetFileDetails`, `ShowGamepadTextInput` and `HookScreenshots` fire
their request, but their completion is not reported back.

Wiring that dispatch loop is the one piece of work standing between
this wrapper and those interfaces. It is the most useful pull request
anyone could send.

## Adding an interface

Each interface is an `_Interface` subclass with two class attributes:
`_PREFIX`, the flat-API prefix, and `_BINDINGS`, a table of (short
name, argument types after the interface pointer, return type).
Everything is bound once at construction, and a method body is then
one line through `_bool`, `_int`, `_float`, `_str`, `_bytes` or
`_void`:

```python
class _Music(_Interface):
    _PREFIX = "SteamAPI_ISteamMusic_"
    _BINDINGS = (
        ("BIsPlaying", [], _BOOL),
        ("SetVolume",  [_FLT], None),
    )

    def MusicIsPlaying(self):
        return self._bool("BIsPlaying")

    def MusicSetVolume(self, volume):
        return self._void("SetVolume", _FLT(float(volume)))
```

Add the new class to `STEAMWORKS._INTERFACES` with its accessor tuple
and it is attached, logged at init, and no-opped when absent. The flat
export names are in `sdk/public/steam/steam_api_flat.h`, and the one
authority on what your redistributable actually exports is its own
export table. Pull requests welcome.

## Shipping it with PyInstaller

Add the binary as a data file landing in the package folder:

```python
datas = [(os.path.join('pysteambridge', 'steam_api64.dll'), 'pysteambridge')]
```

Guard it with `os.path.isfile` so a clone without the SDK still builds.

## When Valve bumps the SDK

Valve version-suffixes the flat accessors
(`SteamAPI_SteamUserStats_v013`) and bumps them as the SDK evolves.
The wrapper tries the newest version it knows and falls back through
older ones, so a slightly out-of-date redistributable still works. On
a successful `initialize()` it prints which version it bound for each
interface.

If a new SDK ships an accessor newer than any listed, add it to the
front of the matching tuple at the top of `pysteambridge/steamworks.py`.
The live version string is the `#define <IFACE>_INTERFACE_VERSION`
line in `sdk/public/steam/isteam*.h`.

## When it goes wrong

| Symptom | Cause |
|---|---|
| `MissingSteamworksLibraryException: Steamworks binary not found` | Step 2. The message lists every path searched. |
| `SteamNotRunningException` | Steam client not running, account does not own the App ID, or `steam_appid.txt` is missing or wrong. |
| Achievement never appears | `StoreStats()` not called, or the API name does not match the partner site. |
| `GetAchievement` returns `None` forever | `run_callbacks()` never runs, so the stats round-trip never lands. |
| `RequestCurrentStats()` returns `False` | Expected on a current SDK, which no longer exports it. Steam fetches stats itself during init. |
| One interface is `MISSING` in the init log | Your redistributable exports no accessor version this wrapper knows. See "When Valve bumps the SDK". |
| Everything returns `False` / `0` / `""` / `b""` | `initialize()` failed and you carried on, or that interface was `MISSING`. The no-op is by design; check the exception and the init log. |
| A `Set...` call returns `True` but nothing changes on Steam | Some calls only queue; `StoreStats()` is the commit for stats and achievements. |

## Licence, and Valve's half of it

MIT, for this wrapper's own code. See [LICENSE](LICENSE).

The Steamworks SDK, its redistributable binaries, and the Steam
platform are Valve's, and are governed by your own agreement with
Valve. This project is not affiliated with or endorsed by Valve
Corporation.

[swpy]: https://github.com/philippj/SteamworksPy
[sdk]: https://partner.steamgames.com/downloads/list
