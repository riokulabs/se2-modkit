"""The ISteamUGC conversation, as a backend-independent sequence of calls.

Backends:
* DryRunBackend   - records and prints every call with its values; never loads Steam. The default.
* FakeBackend     - an in-memory Workshop for the tests.
* FlatApiBackend  - the real thing: the Steamworks flat C API (libsteam_api.so) via ctypes against the logged-in
                    native Linux Steam client. Only constructed after --i-have-user-approval + confirmation.

The call order is the one the HUB uses for an item update, with two deliberate differences: the change note is
sent (the HUB sends ""), and the first upload is not forced Public.
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .metadata import KV_TAG_KEY, STEAM_VISIBILITY
from .. import progress

NEW_ITEM = 0   # placeholder id in a dry run before CreateItem


class SteamFailure(RuntimeError):
    pass


@dataclass
class RemoteItem:
    item_id: int
    owner: int
    creator_app: int
    visibility: str | None


class Backend(Protocol):
    def steam_id(self) -> int: ...
    def find_by_project_kv(self, kv_value: str) -> list[RemoteItem]: ...
    def create_item(self, consumer_app: int) -> tuple[int, bool]: ...
    def update_item(self, consumer_app: int, item_id: int, calls: list[tuple[str, object]],
                    change_note: str) -> bool: ...


@dataclass
class UpdatePlan:
    """Every setter the upload makes, in order (name, value)."""
    item_id: int
    calls: list[tuple[str, object]] = field(default_factory=list)
    change_note: str = ""


def plan_update(item_id: int, kv_value: str, title: str, tags: list[str], visibility: str | None,
                description: str | None, content_dir: Path, preview: Path | None, metadata: str,
                change_note: str) -> UpdatePlan:
    calls: list[tuple[str, object]] = [
        ("RemoveItemKeyValueTags", KV_TAG_KEY),
        ("AddItemKeyValueTag", (KV_TAG_KEY, kv_value)),
        ("SetItemTitle", title),
        ("SetItemTags", list(tags)),
    ]
    if visibility is not None:
        calls.append(("SetItemVisibility", visibility))
    if description is not None and description.strip():   # HUB: SetItemDescription only if not blank
        calls.append(("SetItemDescription", description))
    calls.append(("SetItemContent", str(content_dir)))
    if preview is not None:
        calls.append(("SetItemPreview", str(preview)))
    calls.append(("SetItemMetadata", metadata))
    return UpdatePlan(item_id, calls, change_note)


# --- dry run -----------------------------------------------------------------------------------------------------

class DryRunBackend:
    def __init__(self, out=print, author: int = 0):
        self.out = out
        self.author = author
        self.log: list[tuple[str, object]] = []

    def _say(self, name: str, value: object = None):
        self.log.append((name, value))

    def steam_id(self) -> int:
        self._say("SteamUser.GetSteamID")
        return self.author

    def find_by_project_kv(self, kv_value: str) -> list[RemoteItem]:
        self._say("QueryUGC(VRageProjectID)", kv_value)
        return []

    def create_item(self, consumer_app: int) -> tuple[int, bool]:
        self._say("CreateItem", (consumer_app, "k_EWorkshopFileTypeCommunity"))
        return NEW_ITEM, False

    def update_item(self, consumer_app, item_id, calls, change_note) -> bool:
        self._say("StartItemUpdate", (consumer_app, item_id))
        for c in calls:
            self._say(*c)
        self._say("SubmitItemUpdate", change_note)
        return False


# --- fake (tests) ------------------------------------------------------------------------------------------------

class FakeBackend:
    def __init__(self, me: int = 76561190000000001, items: dict[int, dict] | None = None,
                 legal_needed: bool = False):
        self.me = me
        self.items = items or {}
        self.next_id = 3900000000
        self.legal_needed = legal_needed
        self.calls: list[tuple] = []

    def steam_id(self) -> int:
        return self.me

    def find_by_project_kv(self, kv_value):
        return [RemoteItem(i, d.get("owner", self.me), d.get("creator_app", 1133870), d.get("visibility"))
                for i, d in self.items.items() if d.get("kv", {}).get(KV_TAG_KEY) == kv_value]

    def create_item(self, consumer_app):
        self.next_id += 1
        self.items[self.next_id] = {"owner": self.me, "visibility": "Private", "kv": {}}
        self.calls.append(("CreateItem", consumer_app, self.next_id))
        return self.next_id, self.legal_needed

    def update_item(self, consumer_app, item_id, calls, change_note):
        it = self.items[item_id]
        for name, value in calls:
            if name == "RemoveItemKeyValueTags":
                it["kv"].pop(value, None)
            elif name == "AddItemKeyValueTag":
                it["kv"][value[0]] = value[1]
            elif name == "SetItemVisibility":
                it["visibility"] = value
            else:
                it[name] = value
        it.setdefault("change_notes", []).append(change_note)
        self.calls.append(("Submit", item_id))
        return self.legal_needed


# --- the real Steam client ---------------------------------------------------------------------------------------
# Struct layouts: Steamworks headers pack callback structs to 4 bytes on Linux (VALVE_CALLBACK_PACK_SMALL).

K_ERESULT_OK = 1
CB_STEAM_API_CALL_COMPLETED = 703      # k_iSteamUtilsCallbacks + 3
CB_UGC_QUERY_COMPLETED = 3401          # k_iSteamUGCCallbacks + 1
CB_CREATE_ITEM_RESULT = 3403
CB_SUBMIT_ITEM_UPDATE_RESULT = 3404


class _CallbackMsg(ctypes.Structure):
    _pack_ = 4
    _layout_ = "ms"   # #pragma pack(4) semantics (GCC honours it the same way for these structs)
    _fields_ = [("hSteamUser", ctypes.c_int32), ("iCallback", ctypes.c_int32),
                ("pubParam", ctypes.c_void_p), ("cubParam", ctypes.c_int32)]


class _APICallCompleted(ctypes.Structure):
    _pack_ = 4
    _layout_ = "ms"   # #pragma pack(4) semantics (GCC honours it the same way for these structs)
    _fields_ = [("hAsyncCall", ctypes.c_uint64), ("iCallback", ctypes.c_int32), ("cubParam", ctypes.c_uint32)]


class _CreateItemResult(ctypes.Structure):
    _pack_ = 4
    _layout_ = "ms"   # #pragma pack(4) semantics (GCC honours it the same way for these structs)
    _fields_ = [("eResult", ctypes.c_int32), ("nPublishedFileId", ctypes.c_uint64),
                ("bUserNeedsToAcceptWorkshopLegalAgreement", ctypes.c_bool)]


class _SubmitItemUpdateResult(ctypes.Structure):
    _pack_ = 4
    _layout_ = "ms"   # #pragma pack(4) semantics (GCC honours it the same way for these structs)
    _fields_ = [("eResult", ctypes.c_int32), ("bUserNeedsToAcceptWorkshopLegalAgreement", ctypes.c_bool),
                ("nPublishedFileId", ctypes.c_uint64)]


class _QueryCompleted(ctypes.Structure):
    _pack_ = 4
    _layout_ = "ms"   # #pragma pack(4) semantics (GCC honours it the same way for these structs)
    _fields_ = [("handle", ctypes.c_uint64), ("eResult", ctypes.c_int32), ("unNumResultsReturned", ctypes.c_uint32),
                ("unTotalMatchingResults", ctypes.c_uint32), ("bCachedData", ctypes.c_bool),
                ("rgchNextCursor", ctypes.c_char * 256)]


class _StringArray(ctypes.Structure):
    _fields_ = [("ppStrings", ctypes.POINTER(ctypes.c_char_p)), ("nNumStrings", ctypes.c_int32)]


# SteamUGCDetails_t offsets (pack 4): m_nPublishedFileId 0, m_eResult 8, m_eFileType 12, m_nCreatorAppID 16,
# m_nConsumerAppID 20, m_rgchTitle[129] 24, m_rgchDescription[8000] 153, m_ulSteamIDOwner 8156, m_rtimeCreated 8164,
# m_rtimeUpdated 8168, m_rtimeAddedToUserList 8172, m_eVisibility 8176.
_DET_OWNER, _DET_CREATOR, _DET_VIS = 8156, 16, 8176
_VIS_NAMES = {v: k for k, v in STEAM_VISIBILITY.items()}


class FlatApiBackend:
    """Talks to the running, logged-in Steam client. Never stores or asks for credentials."""

    def __init__(self, lib_path: Path, app_id: int, timeout: float = 600.0, out=print):
        self.out = out
        self.timeout = timeout
        os.environ["SteamAppId"] = str(app_id)     # instead of writing steam_appid.txt anywhere
        os.environ["SteamGameId"] = str(app_id)
        self.lib = ctypes.CDLL(str(lib_path))
        L = self.lib
        err = ctypes.create_string_buffer(1024)
        L.SteamAPI_InitFlat.argtypes = [ctypes.c_char_p]
        L.SteamAPI_InitFlat.restype = ctypes.c_int
        rc = L.SteamAPI_InitFlat(err)
        if rc != 0:
            raise SteamFailure(f"SteamAPI_InitFlat failed ({rc}): {err.value.decode(errors='replace')} - is the "
                               "Steam client running and logged in?")
        # Manual dispatch has to be switched on after the API is initialized, or every
        # RunFrame/GetNextCallback fails with "must call SteamAPI_ManualDispatch_Init first".
        L.SteamAPI_ManualDispatch_Init.restype = None
        L.SteamAPI_ManualDispatch_Init()
        L.SteamAPI_GetHSteamPipe.restype = ctypes.c_int32
        self.pipe = L.SteamAPI_GetHSteamPipe()
        self.ugc = self._iface("SteamAPI_SteamUGC_v021", "SteamAPI_SteamUGC_v020", "SteamAPI_SteamUGC_v018")
        self.user = self._iface("SteamAPI_SteamUser_v023", "SteamAPI_SteamUser_v021")
        u64, vp, cp, b = ctypes.c_uint64, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool
        u32, i32 = ctypes.c_uint32, ctypes.c_int32
        sig = {
            "SteamAPI_ISteamUser_GetSteamID": ([vp], u64),
            "SteamAPI_ISteamUGC_CreateItem": ([vp, u32, i32], u64),
            "SteamAPI_ISteamUGC_StartItemUpdate": ([vp, u32, u64], u64),
            "SteamAPI_ISteamUGC_SetItemTitle": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_SetItemDescription": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_SetItemMetadata": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_SetItemVisibility": ([vp, u64, i32], b),
            "SteamAPI_ISteamUGC_SetItemTags": ([vp, u64, ctypes.POINTER(_StringArray), b], b),
            "SteamAPI_ISteamUGC_SetItemContent": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_SetItemPreview": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_RemoveItemKeyValueTags": ([vp, u64, cp], b),
            "SteamAPI_ISteamUGC_AddItemKeyValueTag": ([vp, u64, cp, cp], b),
            "SteamAPI_ISteamUGC_SubmitItemUpdate": ([vp, u64, cp], u64),
            "SteamAPI_ISteamUGC_GetItemUpdateProgress": ([vp, u64, ctypes.POINTER(u64), ctypes.POINTER(u64)], i32),
            "SteamAPI_ISteamUGC_CreateQueryUserUGCRequest": ([vp, u32, i32, i32, i32, u32, u32, u32], u64),
            "SteamAPI_ISteamUGC_CreateQueryAllUGCRequestPage": ([vp, i32, i32, u32, u32, u32], u64),
            "SteamAPI_ISteamUGC_AddRequiredKeyValueTag": ([vp, u64, cp, cp], b),
            "SteamAPI_ISteamUGC_SetAllowCachedResponse": ([vp, u64, u32], b),
            "SteamAPI_ISteamUGC_SendQueryUGCRequest": ([vp, u64], u64),
            "SteamAPI_ISteamUGC_GetQueryUGCResult": ([vp, u64, u32, vp], b),
            "SteamAPI_ISteamUGC_ReleaseQueryUGCRequest": ([vp, u64], b),
            "SteamAPI_ManualDispatch_RunFrame": ([i32], None),
            "SteamAPI_ManualDispatch_GetNextCallback": ([i32, ctypes.POINTER(_CallbackMsg)], b),
            "SteamAPI_ManualDispatch_FreeLastCallback": ([i32], None),
            "SteamAPI_ManualDispatch_GetAPICallResult": ([i32, u64, vp, i32, i32, ctypes.POINTER(b)], b),
            "SteamAPI_Shutdown": ([], None),
        }
        for name, (args, res) in sig.items():
            f = getattr(L, name)
            f.argtypes, f.restype = args, res

    def _iface(self, *names):
        for n in names:
            f = getattr(self.lib, n, None)
            if f is not None:
                f.restype = ctypes.c_void_p
                ptr = f()
                if ptr:
                    return ptr
        raise SteamFailure(f"none of {names} available in libsteam_api")

    def close(self):
        self.lib.SteamAPI_Shutdown()

    def _await(self, call: int, struct_type, cb_id: int):
        if call == 0:
            raise SteamFailure("Steam returned an invalid API call handle")
        L = self.lib
        deadline = time.monotonic() + self.timeout
        msg = _CallbackMsg()
        beat = progress.Heartbeat(f"waiting for Steam (callback {cb_id})")
        while time.monotonic() < deadline:
            beat.tick()
            L.SteamAPI_ManualDispatch_RunFrame(self.pipe)
            while L.SteamAPI_ManualDispatch_GetNextCallback(self.pipe, ctypes.byref(msg)):
                try:
                    if msg.iCallback == CB_STEAM_API_CALL_COMPLETED:
                        done = _APICallCompleted.from_address(msg.pubParam)
                        if done.hAsyncCall == call:
                            res = struct_type()
                            failed = ctypes.c_bool(False)
                            ok = L.SteamAPI_ManualDispatch_GetAPICallResult(
                                self.pipe, call, ctypes.byref(res), ctypes.sizeof(res), cb_id, ctypes.byref(failed))
                            if not ok or failed.value:
                                raise SteamFailure(f"API call {cb_id} failed (IO failure or bad struct size)")
                            return res
                finally:
                    L.SteamAPI_ManualDispatch_FreeLastCallback(self.pipe)
            time.sleep(0.05)
        raise SteamFailure(f"timed out waiting for callback {cb_id}")

    def steam_id(self) -> int:
        return self.lib.SteamAPI_ISteamUser_GetSteamID(self.user)

    def _run_query(self, handle: int) -> list[RemoteItem]:
        L = self.lib
        if handle in (0, 0xFFFFFFFFFFFFFFFF):          # k_UGCQueryHandleInvalid: this creator/consumer pair is not queryable
            return []
        L.SteamAPI_ISteamUGC_SetAllowCachedResponse(self.ugc, handle, 0)
        res = self._await(L.SteamAPI_ISteamUGC_SendQueryUGCRequest(self.ugc, handle), _QueryCompleted,
                          CB_UGC_QUERY_COMPLETED)
        items = []
        try:
            if res.eResult != K_ERESULT_OK:
                raise SteamFailure(f"UGC query failed: EResult {res.eResult}")
            buf = ctypes.create_string_buffer(32768)
            for i in range(res.unNumResultsReturned):
                if not L.SteamAPI_ISteamUGC_GetQueryUGCResult(self.ugc, handle, i, buf):
                    continue
                raw = buf.raw
                item = int.from_bytes(raw[0:8], "little")
                owner = int.from_bytes(raw[_DET_OWNER:_DET_OWNER + 8], "little")
                creator = int.from_bytes(raw[_DET_CREATOR:_DET_CREATOR + 4], "little")
                vis = _VIS_NAMES.get(int.from_bytes(raw[_DET_VIS:_DET_VIS + 4], "little"))
                items.append(RemoteItem(item, owner, creator, vis))
        finally:
            L.SteamAPI_ISteamUGC_ReleaseQueryUGCRequest(self.ugc, handle)
        return items

    def find_by_project_kv(self, kv_value: str) -> list[RemoteItem]:
        """Items carrying VRageProjectID=<kv_value>: this account's own (any visibility) plus public ones,
        created by the game (1133870) or the SDK/HUB (3578990)."""
        L = self.lib
        found: dict[int, RemoteItem] = {}
        me = self.steam_id() & 0xFFFFFFFF
        for creator in (1133870, 3578990):
            # k_EUserUGCList_Published = 0, k_EUGCMatchingUGCType_Items = 0, k_EUserUGCListSortOrder_CreationOrderDesc = 0
            h = L.SteamAPI_ISteamUGC_CreateQueryUserUGCRequest(self.ugc, me, 0, 0, 0, creator, 1133870, 1)
            L.SteamAPI_ISteamUGC_AddRequiredKeyValueTag(self.ugc, h, KV_TAG_KEY.encode(), kv_value.encode())
            for it in self._run_query(h):
                found[it.item_id] = it
            # k_EUGCQuery_RankedByLastUpdatedDate = 19 (what Keen's FindItemByProjectIdAsync uses)
            h = L.SteamAPI_ISteamUGC_CreateQueryAllUGCRequestPage(self.ugc, 19, 0, creator, 1133870, 1)
            L.SteamAPI_ISteamUGC_AddRequiredKeyValueTag(self.ugc, h, KV_TAG_KEY.encode(), kv_value.encode())
            for it in self._run_query(h):
                found.setdefault(it.item_id, it)
        return list(found.values())

    def create_item(self, consumer_app: int) -> tuple[int, bool]:
        L = self.lib
        res = self._await(L.SteamAPI_ISteamUGC_CreateItem(self.ugc, consumer_app, 0), _CreateItemResult,
                          CB_CREATE_ITEM_RESULT)
        if res.eResult != K_ERESULT_OK:
            raise SteamFailure(f"CreateItem failed: EResult {res.eResult}")
        return int(res.nPublishedFileId), bool(res.bUserNeedsToAcceptWorkshopLegalAgreement)

    def update_item(self, consumer_app: int, item_id: int, calls, change_note: str) -> bool:
        L = self.lib
        h = L.SteamAPI_ISteamUGC_StartItemUpdate(self.ugc, consumer_app, item_id)
        keep = []
        for name, value in calls:
            if name == "SetItemTags":
                arr = (ctypes.c_char_p * len(value))(*[t.encode() for t in value])
                sa = _StringArray(arr, len(value))
                keep += [arr, sa]
                ok = L.SteamAPI_ISteamUGC_SetItemTags(self.ugc, h, ctypes.byref(sa), False)
            elif name == "SetItemVisibility":
                ok = L.SteamAPI_ISteamUGC_SetItemVisibility(self.ugc, h, STEAM_VISIBILITY[value])
            elif name == "AddItemKeyValueTag":
                ok = L.SteamAPI_ISteamUGC_AddItemKeyValueTag(self.ugc, h, value[0].encode(), value[1].encode())
            else:
                ok = getattr(L, f"SteamAPI_ISteamUGC_{name}")(self.ugc, h, str(value).encode("utf-8"))
            if not ok and name != "RemoveItemKeyValueTags":
                raise SteamFailure(f"{name} was rejected by Steam (bad value?)")
        call = L.SteamAPI_ISteamUGC_SubmitItemUpdate(self.ugc, h, change_note.encode("utf-8"))
        done, total = ctypes.c_uint64(), ctypes.c_uint64()
        last = -1
        deadline = time.monotonic() + self.timeout * 6
        msg = _CallbackMsg()
        while time.monotonic() < deadline:
            status = L.SteamAPI_ISteamUGC_GetItemUpdateProgress(self.ugc, h, ctypes.byref(done), ctypes.byref(total))
            if total.value and done.value * 20 // total.value != last:
                last = done.value * 20 // total.value
                self.out(f"  upload: status {status}, {done.value}/{total.value} bytes")
            try:
                res = self._await_once(call, msg)
            except SteamFailure:
                raise
            if res is not None:
                if res.eResult != K_ERESULT_OK:
                    raise SteamFailure(f"SubmitItemUpdate failed: EResult {res.eResult}")
                return bool(res.bUserNeedsToAcceptWorkshopLegalAgreement)
            time.sleep(0.25)
        raise SteamFailure("timed out waiting for SubmitItemUpdate")

    def _await_once(self, call: int, msg: _CallbackMsg):
        L = self.lib
        L.SteamAPI_ManualDispatch_RunFrame(self.pipe)
        while L.SteamAPI_ManualDispatch_GetNextCallback(self.pipe, ctypes.byref(msg)):
            try:
                if msg.iCallback == CB_STEAM_API_CALL_COMPLETED:
                    d = _APICallCompleted.from_address(msg.pubParam)
                    if d.hAsyncCall == call:
                        res = _SubmitItemUpdateResult()
                        failed = ctypes.c_bool(False)
                        ok = L.SteamAPI_ManualDispatch_GetAPICallResult(
                            self.pipe, call, ctypes.byref(res), ctypes.sizeof(res), CB_SUBMIT_ITEM_UPDATE_RESULT,
                            ctypes.byref(failed))
                        if not ok or failed.value:
                            raise SteamFailure("SubmitItemUpdate result unavailable")
                        return res
            finally:
                L.SteamAPI_ManualDispatch_FreeLastCallback(self.pipe)
        return None
