from __future__ import annotations

import argparse
import curses
import locale
import subprocess
import textwrap
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from orbital_wifi import __version__


REFRESH_INTERVAL_SECONDS = 15


class NmcliError(RuntimeError):
    """Raised when an nmcli command fails."""


@dataclass(slots=True)
class WifiNetwork:
    active: bool
    ssid: str
    bssid: str
    signal: int
    security: str
    bars: str

    @property
    def display_name(self) -> str:
        return self.ssid or "<hidden network>"

    @property
    def security_label(self) -> str:
        return self.security or "Open"


@dataclass(slots=True)
class SavedProfile:
    name: str
    uuid: str
    device: str

    @property
    def active(self) -> bool:
        return bool(self.device)


@dataclass(slots=True)
class AppState:
    preferred_interface: str | None = None
    interfaces: list[str] = field(default_factory=list)
    interface_index: int = 0
    radio_enabled: bool = True
    networks: list[WifiNetwork] = field(default_factory=list)
    profiles: list[SavedProfile] = field(default_factory=list)
    view: str = "networks"
    network_index: int = 0
    profile_index: int = 0
    status_message: str = "Mission Control online."
    status_level: str = "info"
    last_refresh: float = 0.0

    @property
    def current_interface(self) -> str | None:
        if not self.interfaces:
            return None
        self.interface_index = max(0, min(self.interface_index, len(self.interfaces) - 1))
        return self.interfaces[self.interface_index]

    @property
    def selected_network(self) -> WifiNetwork | None:
        if not self.networks:
            return None
        self.network_index = max(0, min(self.network_index, len(self.networks) - 1))
        return self.networks[self.network_index]

    @property
    def selected_profile(self) -> SavedProfile | None:
        if not self.profiles:
            return None
        self.profile_index = max(0, min(self.profile_index, len(self.profiles) - 1))
        return self.profiles[self.profile_index]


def parse_nmcli_fields(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False

    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":":
            fields.append("".join(current))
            current = []
            continue
        current.append(char)

    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def run_nmcli(args: Sequence[str]) -> list[str]:
    completed = subprocess.run(
        ["nmcli", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "nmcli command failed"
        raise NmcliError(message)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def run_nmcli_text(args: Sequence[str]) -> str:
    return "\n".join(run_nmcli(args)).strip()


def list_wifi_interfaces() -> list[str]:
    devices: list[str] = []
    for line in run_nmcli(["-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]):
        fields = parse_nmcli_fields(line)
        if len(fields) != 3:
            continue
        device, device_type, _state = fields
        if device_type == "wifi":
            devices.append(device)
    return devices


def wifi_radio_enabled() -> bool:
    return run_nmcli_text(["radio", "wifi"]).strip() == "enabled"


def scan_networks(interface: str) -> list[WifiNetwork]:
    networks: list[WifiNetwork] = []
    lines = run_nmcli(
        [
            "-t",
            "-f",
            "IN-USE,SSID,BSSID,SIGNAL,SECURITY,BARS",
            "device",
            "wifi",
            "list",
            "ifname",
            interface,
            "--rescan",
            "auto",
        ]
    )
    for line in lines:
        fields = parse_nmcli_fields(line)
        if len(fields) != 6:
            continue
        in_use, ssid, bssid, signal_text, security, bars = fields
        try:
            signal = int(signal_text or 0)
        except ValueError:
            signal = 0
        networks.append(
            WifiNetwork(
                active=in_use.strip() == "*",
                ssid=ssid,
                bssid=bssid,
                signal=signal,
                security=security.strip(),
                bars=bars.strip() or "____",
            )
        )
    networks.sort(key=lambda network: (not network.active, -network.signal, network.display_name.lower()))
    return networks


def list_saved_profiles() -> list[SavedProfile]:
    profiles: list[SavedProfile] = []
    for line in run_nmcli(["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"]):
        fields = parse_nmcli_fields(line)
        if len(fields) != 4:
            continue
        name, uuid, connection_type, device = fields
        if connection_type != "802-11-wireless":
            continue
        profiles.append(SavedProfile(name=name, uuid=uuid, device=device))
    profiles.sort(key=lambda profile: (not profile.active, profile.name.lower()))
    return profiles


def set_status(state: AppState, message: str, level: str = "info") -> None:
    state.status_message = message
    state.status_level = level


def refresh_state(state: AppState, *, rescan: bool = False) -> None:
    interfaces = list_wifi_interfaces()
    if state.preferred_interface and state.preferred_interface in interfaces:
        state.interface_index = interfaces.index(state.preferred_interface)
        state.preferred_interface = None
    elif state.current_interface in interfaces:
        state.interface_index = interfaces.index(state.current_interface)
    elif state.interface_index >= len(interfaces):
        state.interface_index = max(0, len(interfaces) - 1)
    state.interfaces = interfaces
    state.radio_enabled = wifi_radio_enabled()
    state.profiles = list_saved_profiles()

    interface = state.current_interface
    if interface and state.radio_enabled:
        if rescan:
            run_nmcli(["device", "wifi", "rescan", "ifname", interface])
        state.networks = scan_networks(interface)
    else:
        state.networks = []

    state.network_index = max(0, min(state.network_index, max(0, len(state.networks) - 1)))
    state.profile_index = max(0, min(state.profile_index, max(0, len(state.profiles) - 1)))
    state.last_refresh = time.time()


def connect_network(interface: str, ssid: str, *, password: str | None = None, bssid: str | None = None, hidden: bool = False) -> None:
    args = ["device", "wifi", "connect", ssid, "ifname", interface]
    if bssid:
        args.extend(["bssid", bssid])
    if hidden:
        args.extend(["hidden", "yes"])
    if password:
        args.extend(["password", password])
    run_nmcli(args)


def activate_profile(interface: str, profile: SavedProfile) -> None:
    run_nmcli(["connection", "up", "uuid", profile.uuid, "ifname", interface])


def disconnect_interface(interface: str) -> None:
    run_nmcli(["device", "disconnect", interface])


def toggle_radio(enabled: bool) -> None:
    run_nmcli(["radio", "wifi", "off" if enabled else "on"])


def delete_profile(profile: SavedProfile) -> None:
    run_nmcli(["connection", "delete", "uuid", profile.uuid])


def fit_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def draw_text(screen: curses.window, y: int, x: int, text: str, width: int | None = None, attr: int = 0) -> None:
    if y < 0 or x < 0:
        return
    if width is None:
        width = max(0, screen.getmaxyx()[1] - x)
    if width <= 0:
        return
    try:
        screen.addnstr(y, x, text, width, attr)
    except curses.error:
        return


def color_pair(state: AppState) -> int:
    if state.status_level == "success":
        return curses.color_pair(3)
    if state.status_level == "error":
        return curses.color_pair(5)
    return curses.color_pair(2)


def active_ssid(networks: Iterable[WifiNetwork]) -> str:
    for network in networks:
        if network.active:
            return network.display_name
    return "none"


def draw_header(screen: curses.window, state: AppState, width: int) -> int:
    title = " ORBITAL WIFI // MISSION CONTROL "
    status = f"Interface: {state.current_interface or 'none'} | Radio: {'ONLINE' if state.radio_enabled else 'OFFLINE'} | Docked: {active_ssid(state.networks)}"
    draw_text(screen, 0, 0, " " * width, width, curses.color_pair(1))
    draw_text(screen, 0, 2, fit_text(title, width - 4), width - 4, curses.color_pair(1) | curses.A_BOLD)
    draw_text(screen, 1, 2, fit_text(status, width - 4), width - 4, curses.color_pair(2))
    return 3


def draw_tabs(screen: curses.window, state: AppState, top: int, width: int) -> int:
    network_attr = curses.color_pair(6) if state.view == "networks" else curses.color_pair(2)
    profile_attr = curses.color_pair(6) if state.view == "profiles" else curses.color_pair(2)
    draw_text(screen, top, 2, "[ Networks ]", 12, network_attr | curses.A_BOLD)
    draw_text(screen, top, 16, "[ Profiles ]", 12, profile_attr | curses.A_BOLD)
    draw_text(screen, top, 31, fit_text(state.status_message, max(0, width - 33)), max(0, width - 33), color_pair(state))
    return top + 2


def draw_list_panel(
    screen: curses.window,
    *,
    top: int,
    left: int,
    height: int,
    width: int,
    title: str,
    rows: Sequence[str],
    selected_index: int,
    empty_message: str,
) -> None:
    if height <= 2 or width <= 4:
        return
    draw_text(screen, top, left, "+" + "-" * (width - 2) + "+", width, curses.color_pair(2))
    draw_text(screen, top + 1, left, "|", 1, curses.color_pair(2))
    draw_text(screen, top + 1, left + 2, fit_text(title, width - 4), width - 4, curses.color_pair(1) | curses.A_BOLD)
    draw_text(screen, top + 1, left + width - 1, "|", 1, curses.color_pair(2))

    visible_rows = max(0, height - 3)
    if rows:
        start = 0
        if selected_index >= visible_rows:
            start = selected_index - visible_rows + 1
        rows_to_show = rows[start : start + visible_rows]
    else:
        rows_to_show = []

    for offset in range(visible_rows):
        line_y = top + 2 + offset
        draw_text(screen, line_y, left, "|", 1, curses.color_pair(2))
        draw_text(screen, line_y, left + width - 1, "|", 1, curses.color_pair(2))
        fill_width = width - 2
        row_index = (start + offset) if rows else -1
        row = rows_to_show[offset] if offset < len(rows_to_show) else ""
        attr = curses.A_NORMAL
        if rows and row_index == selected_index:
            attr = curses.color_pair(6)
        draw_text(screen, line_y, left + 1, " " * fill_width, fill_width, attr)
        if row:
            draw_text(screen, line_y, left + 2, fit_text(row, fill_width - 2), fill_width - 2, attr)

    if not rows:
        message_y = top + min(height - 2, max(2, height // 2))
        draw_text(screen, message_y, left + 2, fit_text(empty_message, width - 4), width - 4, curses.color_pair(4))

    draw_text(screen, top + height - 1, left, "+" + "-" * (width - 2) + "+", width, curses.color_pair(2))


def network_row(network: WifiNetwork) -> str:
    marker = "*" if network.active else " "
    return f"{marker} {network.display_name:<28} {network.signal:>3}%  {network.bars:<4}  {network.security_label}"


def profile_row(profile: SavedProfile) -> str:
    marker = "*" if profile.active else " "
    target = profile.device or "stored"
    return f"{marker} {profile.name:<28} {target}"


def draw_details(screen: curses.window, state: AppState, top: int, left: int, height: int, width: int) -> None:
    if height <= 2 or width <= 4:
        return
    draw_text(screen, top, left, "+" + "-" * (width - 2) + "+", width, curses.color_pair(2))
    draw_text(screen, top + 1, left, "|", 1, curses.color_pair(2))
    draw_text(screen, top + 1, left + 2, "Telemetry", width - 4, curses.color_pair(1) | curses.A_BOLD)
    draw_text(screen, top + 1, left + width - 1, "|", 1, curses.color_pair(2))

    details: list[str] = []
    if state.view == "networks" and state.selected_network is not None:
        network = state.selected_network
        details = [
            f"SSID      : {network.display_name}",
            f"BSSID     : {network.bssid or 'n/a'}",
            f"Signal    : {network.signal}%",
            f"Security  : {network.security_label}",
            f"Bars      : {network.bars}",
            f"Active    : {'yes' if network.active else 'no'}",
            "",
            "Enter to connect.",
            "h to dock to a hidden network.",
            "d disconnects the interface.",
        ]
    elif state.view == "profiles" and state.selected_profile is not None:
        profile = state.selected_profile
        details = [
            f"Profile   : {profile.name}",
            f"UUID      : {profile.uuid}",
            f"Device    : {profile.device or 'not active'}",
            f"Active    : {'yes' if profile.active else 'no'}",
            "",
            "Enter activates this profile.",
            "x forgets the saved profile.",
        ]
    else:
        details = [
            "No item selected.",
            "",
            "Use Tab to swap views.",
            "Use r to refresh telemetry.",
        ]

    line_y = top + 2
    available_rows = height - 3
    for offset in range(available_rows):
        draw_text(screen, line_y + offset, left, "|", 1, curses.color_pair(2))
        draw_text(screen, line_y + offset, left + width - 1, "|", 1, curses.color_pair(2))
        draw_text(screen, line_y + offset, left + 1, " " * (width - 2), width - 2)
        if offset < len(details):
            draw_text(screen, line_y + offset, left + 2, fit_text(details[offset], width - 4), width - 4)

    draw_text(screen, top + height - 1, left, "+" + "-" * (width - 2) + "+", width, curses.color_pair(2))


def draw_footer(screen: curses.window, state: AppState, height: int, width: int) -> None:
    age = max(0, int(time.time() - state.last_refresh)) if state.last_refresh else 0
    if state.view == "networks":
        footer = "Enter connect | h hidden | d disconnect | r refresh | w radio | i interface | Tab profiles | q quit"
    else:
        footer = "Enter activate | x forget | r refresh | w radio | i interface | Tab networks | q quit"
    timestamp = f"Last sync: {age}s ago"
    draw_text(screen, height - 2, 2, fit_text(footer, width - 4), width - 4, curses.color_pair(2))
    draw_text(screen, height - 1, 2, fit_text(timestamp, width - 4), width - 4, curses.color_pair(4))


def prompt_input(screen: curses.window, title: str, prompt: str, *, secret: bool = False, allow_blank: bool = False) -> str | None:
    height, width = screen.getmaxyx()
    box_width = min(max(48, len(prompt) + 12), max(30, width - 4))
    box_height = 7
    start_y = max(0, (height - box_height) // 2)
    start_x = max(0, (width - box_width) // 2)
    buffer: list[str] = []

    screen.timeout(-1)
    try:
        while True:
            screen.attrset(curses.A_NORMAL)
            for row in range(box_height):
                draw_text(screen, start_y + row, start_x, " " * box_width, box_width, curses.color_pair(7))
            draw_text(screen, start_y, start_x, "+" + "-" * (box_width - 2) + "+", box_width, curses.color_pair(2))
            for row in range(1, box_height - 1):
                draw_text(screen, start_y + row, start_x, "|", 1, curses.color_pair(2))
                draw_text(screen, start_y + row, start_x + box_width - 1, "|", 1, curses.color_pair(2))
            draw_text(screen, start_y + box_height - 1, start_x, "+" + "-" * (box_width - 2) + "+", box_width, curses.color_pair(2))
            draw_text(screen, start_y + 1, start_x + 2, fit_text(title, box_width - 4), box_width - 4, curses.color_pair(1) | curses.A_BOLD)
            draw_text(screen, start_y + 2, start_x + 2, fit_text(prompt, box_width - 4), box_width - 4)
            visible = "*" * len(buffer) if secret else "".join(buffer)
            draw_text(screen, start_y + 4, start_x + 2, fit_text(visible, box_width - 4), box_width - 4, curses.color_pair(6))
            draw_text(screen, start_y + 5, start_x + 2, "Enter confirm | Esc cancel", box_width - 4, curses.color_pair(4))
            screen.refresh()

            key = screen.get_wch()
            if isinstance(key, str):
                if key in ("\n", "\r"):
                    value = "".join(buffer).strip()
                    if value or allow_blank:
                        return value
                    continue
                if key == "\x1b":
                    return None
                if key in ("\b", "\x7f"):
                    if buffer:
                        buffer.pop()
                    continue
                if key.isprintable():
                    buffer.append(key)
                    continue
            if key in (curses.KEY_BACKSPACE, curses.KEY_DC):
                if buffer:
                    buffer.pop()
    finally:
        screen.timeout(250)


def prompt_yes_no(screen: curses.window, title: str, prompt: str) -> bool:
    height, width = screen.getmaxyx()
    box_width = min(max(48, len(prompt) + 12), max(30, width - 4))
    box_height = 6
    start_y = max(0, (height - box_height) // 2)
    start_x = max(0, (width - box_width) // 2)

    screen.timeout(-1)
    try:
        while True:
            for row in range(box_height):
                draw_text(screen, start_y + row, start_x, " " * box_width, box_width, curses.color_pair(7))
            draw_text(screen, start_y, start_x, "+" + "-" * (box_width - 2) + "+", box_width, curses.color_pair(2))
            for row in range(1, box_height - 1):
                draw_text(screen, start_y + row, start_x, "|", 1, curses.color_pair(2))
                draw_text(screen, start_y + row, start_x + box_width - 1, "|", 1, curses.color_pair(2))
            draw_text(screen, start_y + box_height - 1, start_x, "+" + "-" * (box_width - 2) + "+", box_width, curses.color_pair(2))
            draw_text(screen, start_y + 1, start_x + 2, fit_text(title, box_width - 4), box_width - 4, curses.color_pair(1) | curses.A_BOLD)
            for index, line in enumerate(textwrap.wrap(prompt, width=box_width - 4)[:2]):
                draw_text(screen, start_y + 2 + index, start_x + 2, line, box_width - 4)
            draw_text(screen, start_y + box_height - 2, start_x + 2, "y confirm | n cancel", box_width - 4, curses.color_pair(4))
            screen.refresh()
            key = screen.get_wch()
            if key in ("y", "Y"):
                return True
            if key in ("n", "N", "\x1b"):
                return False
    finally:
        screen.timeout(250)


def attempt_network_connection(screen: curses.window, state: AppState) -> None:
    interface = state.current_interface
    network = state.selected_network
    if not interface or not network:
        set_status(state, "No network selected.", "error")
        return
    if network.active:
        set_status(state, f"Already docked to {network.display_name}.", "success")
        return
    if not network.ssid:
        set_status(state, "Use h for hidden network entry.", "error")
        return

    known_profile = any(profile.name == network.ssid for profile in state.profiles)
    try:
        if network.security_label != "Open" and not known_profile:
            password = prompt_input(screen, "Authentication", f"Passphrase for {network.display_name}:", secret=True)
            if password is None:
                set_status(state, "Connection cancelled.", "info")
                return
            connect_network(interface, network.ssid, password=password, bssid=network.bssid)
        else:
            connect_network(interface, network.ssid, bssid=network.bssid)
        refresh_state(state)
        set_status(state, f"Docked to {network.display_name}.", "success")
    except NmcliError as error:
        if network.security_label != "Open" and known_profile:
            password = prompt_input(screen, "Authentication", f"Saved credentials failed. Enter passphrase for {network.display_name}:", secret=True)
            if password is None:
                set_status(state, str(error), "error")
                return
            try:
                connect_network(interface, network.ssid, password=password, bssid=network.bssid)
                refresh_state(state)
                set_status(state, f"Docked to {network.display_name}.", "success")
                return
            except NmcliError as retry_error:
                set_status(state, str(retry_error), "error")
                return
        set_status(state, str(error), "error")


def connect_hidden_network(screen: curses.window, state: AppState) -> None:
    interface = state.current_interface
    if not interface:
        set_status(state, "No wireless interface available.", "error")
        return
    ssid = prompt_input(screen, "Hidden Network", "SSID:")
    if ssid is None:
        set_status(state, "Hidden network entry cancelled.", "info")
        return
    password = prompt_input(screen, "Hidden Network", f"Passphrase for {ssid} (leave blank for open):", secret=True, allow_blank=True)
    if password is None:
        set_status(state, "Hidden network entry cancelled.", "info")
        return
    try:
        connect_network(interface, ssid, password=password or None, hidden=True)
        refresh_state(state)
        set_status(state, f"Docked to {ssid}.", "success")
    except NmcliError as error:
        set_status(state, str(error), "error")


def activate_selected_profile(state: AppState) -> None:
    interface = state.current_interface
    profile = state.selected_profile
    if not interface or not profile:
        set_status(state, "No profile selected.", "error")
        return
    try:
        activate_profile(interface, profile)
        refresh_state(state)
        set_status(state, f"Activated profile {profile.name}.", "success")
    except NmcliError as error:
        set_status(state, str(error), "error")


def delete_selected_profile(screen: curses.window, state: AppState) -> None:
    profile = state.selected_profile
    if not profile:
        set_status(state, "No profile selected.", "error")
        return
    if not prompt_yes_no(screen, "Delete Saved Profile", f"Forget {profile.name}? This removes the stored NetworkManager connection."):
        set_status(state, "Deletion cancelled.", "info")
        return
    try:
        delete_profile(profile)
        refresh_state(state)
        set_status(state, f"Forgot profile {profile.name}.", "success")
    except NmcliError as error:
        set_status(state, str(error), "error")


def disconnect_current_interface(state: AppState) -> None:
    interface = state.current_interface
    if not interface:
        set_status(state, "No wireless interface available.", "error")
        return
    try:
        disconnect_interface(interface)
        refresh_state(state)
        set_status(state, f"Disconnected {interface}.", "success")
    except NmcliError as error:
        set_status(state, str(error), "error")


def cycle_interface(state: AppState) -> None:
    if not state.interfaces:
        set_status(state, "No wireless interface available.", "error")
        return
    state.interface_index = (state.interface_index + 1) % len(state.interfaces)
    try:
        refresh_state(state)
        set_status(state, f"Tracking interface {state.current_interface}.", "success")
    except NmcliError as error:
        set_status(state, str(error), "error")


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_BLUE, -1)
    curses.init_pair(3, curses.COLOR_GREEN, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_RED, -1)
    curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(7, -1, -1)


def run_app(screen: curses.window, preferred_interface: str | None) -> None:
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.nodelay(False)
    screen.timeout(250)
    screen.keypad(True)
    init_colors()

    state = AppState(preferred_interface=preferred_interface)
    try:
        refresh_state(state)
    except NmcliError as error:
        set_status(state, str(error), "error")

    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if height < 14 or width < 72:
            draw_text(screen, 1, 2, "Terminal too small for Orbital WiFi.", width - 4, curses.color_pair(5) | curses.A_BOLD)
            draw_text(screen, 3, 2, "Resize to at least 72x14 or press q to exit.", width - 4, curses.color_pair(4))
            screen.refresh()
        else:
            top = draw_header(screen, state, width)
            top = draw_tabs(screen, state, top, width)
            body_height = height - top - 3
            left_width = max(40, width // 2)
            right_width = width - left_width - 3
            if right_width < 28:
                left_width = width - 4
                right_width = 0

            if state.view == "networks":
                rows = [network_row(network) for network in state.networks]
                selected_index = state.network_index
                empty_message = "No networks detected. Toggle radio or refresh telemetry."
                title = "Orbit Map"
            else:
                rows = [profile_row(profile) for profile in state.profiles]
                selected_index = state.profile_index
                empty_message = "No saved WiFi profiles on this system."
                title = "Stored Missions"

            draw_list_panel(
                screen,
                top=top,
                left=2,
                height=body_height,
                width=left_width,
                title=title,
                rows=rows,
                selected_index=selected_index,
                empty_message=empty_message,
            )
            if right_width:
                draw_details(screen, state, top, left_width + 3, body_height, right_width)
            draw_footer(screen, state, height, width)
            screen.refresh()

        if time.time() - state.last_refresh >= REFRESH_INTERVAL_SECONDS:
            try:
                refresh_state(state)
            except NmcliError as error:
                set_status(state, str(error), "error")

        try:
            key = screen.get_wch()
        except curses.error:
            continue

        try:
            if key in ("q", "Q"):
                return
            if key == "\t":
                state.view = "profiles" if state.view == "networks" else "networks"
                continue
            if key in ("r", "R"):
                try:
                    refresh_state(state, rescan=True)
                    set_status(state, "Telemetry refreshed.", "success")
                except NmcliError as error:
                    set_status(state, str(error), "error")
                continue
            if key in ("w", "W"):
                try:
                    toggle_radio(state.radio_enabled)
                    refresh_state(state)
                    set_status(state, f"WiFi radio {'enabled' if state.radio_enabled else 'disabled'}.", "success")
                except NmcliError as error:
                    set_status(state, str(error), "error")
                continue
            if key in ("i", "I"):
                cycle_interface(state)
                continue
            if key in (curses.KEY_UP, "k"):
                if state.view == "networks" and state.networks:
                    state.network_index = max(0, state.network_index - 1)
                elif state.view == "profiles" and state.profiles:
                    state.profile_index = max(0, state.profile_index - 1)
                continue
            if key in (curses.KEY_DOWN, "j"):
                if state.view == "networks" and state.networks:
                    state.network_index = min(len(state.networks) - 1, state.network_index + 1)
                elif state.view == "profiles" and state.profiles:
                    state.profile_index = min(len(state.profiles) - 1, state.profile_index + 1)
                continue
            if key in ("\n", "\r", curses.KEY_ENTER):
                if state.view == "networks":
                    attempt_network_connection(screen, state)
                else:
                    activate_selected_profile(state)
                continue
            if key in ("h", "H") and state.view == "networks":
                connect_hidden_network(screen, state)
                continue
            if key in ("d", "D") and state.view == "networks":
                disconnect_current_interface(state)
                continue
            if key in ("x", "X") and state.view == "profiles":
                delete_selected_profile(screen, state)
                continue
        except NmcliError as error:
            set_status(state, str(error), "error")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NASA-themed WiFi TUI for NetworkManager")
    parser.add_argument("--interface", help="preferred wireless interface to control")
    parser.add_argument("--version", action="version", version=f"orbital-wifi {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    curses.wrapper(run_app, args.interface)


if __name__ == "__main__":
    main()
