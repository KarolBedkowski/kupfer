"""
Network Manager plugin.
"""
from __future__ import annotations

__kupfer_name__ = _("NetworkManager")
__kupfer_sources__ = ("DevicesSource",)
__kupfer_actions__ = ()
__description__ = _("Manage NetworkManager connections")
__version__ = "2023.01"
__author__ = "Karol Będkowski <karol.bedkowski@gmail.com>"

import typing as ty

import dbus

from kupfer import plugin_support
from kupfer.obj import Action, Leaf, NotAvailableError, Source
from kupfer.support import pretty, weaklib
from kupfer.ui import uiutils

plugin_support.check_dbus_connection()

if ty.TYPE_CHECKING:
    _ = str

_SERVICE_NAME = "org.freedesktop.NetworkManager"
_OBJECT_NAME = "/org/freedesktop/NetworkManager"
_IFACE_NAME = "org.freedesktop.NetworkManager"

_DEV_IFACE_NAME = "org.freedesktop.NetworkManager.Device"
_PROPS_IFACE_NAME = "org.freedesktop.DBus.Properties"
_CONNSETT_IFACE_NAME = "org.freedesktop.NetworkManager.Settings.Connection"

_SETT_OBJ_NAME = "/org/freedesktop/NetworkManager/Settings"
_SETT_IFACE_NAME = "org.freedesktop.NetworkManager.Settings"


def _create_dbus_connection(iface, obj, service, /, sbus=None):
    """Create dbus connection to NetworkManager"""
    sbus = sbus or dbus.SystemBus()
    try:
        if dobj := sbus.get_object(service, obj):
            return dbus.Interface(dobj, iface)

    except dbus.exceptions.DBusException as err:
        pretty.print_debug(__name__, err)

    raise NotAvailableError(_("NetworkManager"))


def _create_dbus_connection_nm(sbus=None):
    return _create_dbus_connection(
        _IFACE_NAME, _OBJECT_NAME, _SERVICE_NAME, sbus=sbus
    )


def _create_dbus_connection_device(obj, /, sbus=None):
    return _create_dbus_connection(
        _DEV_IFACE_NAME, obj, _SERVICE_NAME, sbus=sbus
    )


_NM_DEVICE_STATE = {
    0: _("unknown"),
    10: _("unmanaged"),
    20: _("unavailable"),
    30: _("disconnected"),
    40: _("prepare"),
    50: _("config"),
    60: _("need auth"),
    70: _("ip config"),
    80: _("ip check"),
    90: _("secondaries"),
    100: _("activated"),
    110: _("deactivating"),
    120: _("failed"),
}

_NM_DEVICE_TYPES = {
    0: "unknown",
    1: "ethernet",
    2: "wifi",
    3: "unused1",
    4: "unused2",
    5: "bt",
    6: "olpc mesh",
    7: "wimax",
    8: "modem",
    9: "infiniband",
    10: "bond",
    11: "vlan",
    12: "adsl",
    13: "bridge",
    14: "generic",
    15: "team",
    16: "tun",
    17: "ip tunnel",
    18: "macvlan",
    19: "vxlan",
    20: "veth",
    21: "macsec",
    22: "dummy",
    23: "ppp",
    24: "ovs interface",
    25: "ovs port",
    26: "ovs bridge",
    27: "wpan",
    28: "6lowpan",
    29: "wireguard",
    30: "wifi_p2p",
    31: "vrf",
}


class Device(Leaf):
    def __init__(
        self, path: str, name: str, status: int, managed: bool, devtype: int
    ):
        Leaf.__init__(self, path, name)
        self._status = status
        self.managed = managed
        self.devtype = devtype

    def get_description(self):
        return _("Network device %(dtype)s; state: %(state)s") % {
            "dtype": _NM_DEVICE_TYPES.get(self.devtype) or "unknown",
            "state": _NM_DEVICE_STATE.get(self._status) or str(self._status),
        }

    def get_icon_name(self):
        if self.devtype == 2:
            return "network-wireless"

        if self.devtype == 29:
            return "network-vpn"

        return "network-wired"

    def get_actions(self):
        yield Disconnect()
        yield Connect()
        yield ShowInfo()

    def status(self) -> int:
        conn = _create_dbus_connection(
            _PROPS_IFACE_NAME, self.object, _SERVICE_NAME
        )
        self._status = int(conn.Get(_DEV_IFACE_NAME, "State"))
        return self._status


class Disconnect(Action):
    def __init__(self):
        Action.__init__(self, _("Disconnect"))

    def activate(self, leaf, iobj=None, ctx=None):
        try:
            interface = _create_dbus_connection_device(leaf.object)
            interface.Disconnect()
        except:
            pretty.print_exc(__name__)

    def get_icon_name(self):
        return "disconnect"

    def get_description(self):
        return _("Disconnect connection")

    def valid_for_item(self, leaf):
        return leaf.status() == 100


class Connect(Action):
    def __init__(self):
        Action.__init__(self, _("Connect..."))

    def activate(self, leaf, iobj=None, ctx=None):
        assert iobj
        try:
            interface = _create_dbus_connection_nm()
            interface.ActivateConnection(iobj.object, leaf.object, "/")
        except:
            pretty.print_exc(__name__)

    def get_description(self):
        return _("Activate connection")

    def get_icon_name(self):
        return "connect"

    def requires_object(self):
        return True

    def object_types(self):
        yield Connection

    def object_source(self, for_item=None):
        return ConnectionsSource(for_item.object, for_item.name)

    def valid_for_item(self, leaf):
        return leaf.status() != 100


def _get_info_recursive(item, level=0):
    prefix = "    " * level
    if isinstance(item, (dict, dbus.Dictionary)):
        for key, val in item.items():
            yield (f"{prefix}{key}:")
            yield from _get_info_recursive(val, level + 1)

    elif isinstance(item, (tuple, list, dbus.Array)):
        for val in item:
            yield from _get_info_recursive(val)

    elif level > 0:
        # skip garbage on first level
        yield (f"{prefix}{item}")


class ShowInfo(Action):
    def __init__(self):
        Action.__init__(self, _("Show informations"))

    def wants_context(self):
        return True

    def activate(self, leaf, iobj=None, ctx=None):
        assert ctx
        conn_info = ""
        props_info = ""
        try:
            interface = _create_dbus_connection_device(leaf.object)
            info = interface.GetAppliedConnection(0)
        except Exception as err:
            conn_info = f"Error: {err}"
        else:
            conn_info = "\n".join(_get_info_recursive(info))

        try:
            interface = _create_dbus_connection(
                _PROPS_IFACE_NAME, leaf.object, _SERVICE_NAME
            )
            props = interface.GetAll(_DEV_IFACE_NAME)
        except Exception as err:
            props_info = f"Error: {err}"
        else:
            props_info = "\n".join(_get_info_recursive(props))

        msg = f"DEVICE\n{props_info}\n------------\n\nCONNECTION\n{conn_info}"
        uiutils.show_text_result(msg, title=_("Connection details"), ctx=ctx)

    def get_description(self):
        return _("Show informations about device")


class Connection(Leaf):
    def __init__(self, path: str, name: str, descr: str):
        Leaf.__init__(self, path, name)
        self.descr = descr

    def get_description(self):
        return self.descr

    @staticmethod
    def from_setting(conn: str, settings: dict[str, ty.Any]) -> Connection:
        conn_id = str(settings["id"])
        conn_type = str(settings["type"])
        return Connection(conn, conn_id, conn_type)


class ConnectionsSource(Source):
    source_use_cache = False

    def __init__(self, device_path, interface):
        super().__init__(_("Connections"))
        self.device = device_path
        self.interface = interface

    def get_items(self):
        sbus = dbus.SystemBus()
        dconn = _create_dbus_connection(
            _PROPS_IFACE_NAME, self.device, _SERVICE_NAME, sbus=sbus
        )
        if not dconn:
            return

        # get available connection for device
        need_check_conn = False
        connections = dconn.Get(_DEV_IFACE_NAME, "AvailableConnections")
        if not connections:
            # no connections for given device, check all
            dconn = _create_dbus_connection(
                _SETT_IFACE_NAME, _SETT_OBJ_NAME, _SERVICE_NAME, sbus=sbus
            )
            if not dconn:
                return

            need_check_conn = True
            connections = dconn.ListConnections()

        for conn in connections:
            cset = _create_dbus_connection(
                _CONNSETT_IFACE_NAME, conn, _SERVICE_NAME, sbus=sbus
            )
            settings = cset.GetSettings()
            settings_connection = settings.get("connection")
            if need_check_conn:
                iface_name = str(settings_connection.get("interface-name"))
                if iface_name != self.interface:
                    continue

            yield Connection.from_setting(conn, settings_connection)


class DevicesSource(Source):
    source_use_cache = False

    def __init__(self, name=None):
        Source.__init__(self, name or __kupfer_name__)

    def initialize(self):
        # TODO: source is for now not-cached
        bus = dbus.SystemBus()
        weaklib.dbus_signal_connect_weakly(
            bus,
            "StateChanged",
            self._on_nm_updated,
            dbus_interface=_IFACE_NAME,
        )
        weaklib.dbus_signal_connect_weakly(
            bus,
            "DeviceAdded",
            self._on_nm_updated,
            dbus_interface=_IFACE_NAME,
        )
        weaklib.dbus_signal_connect_weakly(
            bus,
            "DeviceRemoved",
            self._on_nm_updated,
            dbus_interface=_IFACE_NAME,
        )

    def _on_nm_updated(self, *args):
        self.mark_for_update()

    def get_items(self):
        sbus = dbus.SystemBus()
        if interface := _create_dbus_connection_nm(sbus=sbus):
            for dev in interface.GetAllDevices():
                if conn := _create_dbus_connection(
                    _PROPS_IFACE_NAME, dev, _SERVICE_NAME, sbus=sbus
                ):
                    yield Device(
                        str(dev),
                        str(conn.Get(_DEV_IFACE_NAME, "Interface")),
                        int(conn.Get(_DEV_IFACE_NAME, "State")),
                        bool(conn.Get(_DEV_IFACE_NAME, "Managed")),
                        int(conn.Get(_DEV_IFACE_NAME, "DeviceType")),
                    )

    def provides(self):
        yield Device

    def get_icon_name(self):
        return "network-wired"
