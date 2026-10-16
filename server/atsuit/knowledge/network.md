# Network and IT

## Laptop can't reach the AT-SUITE server
Keywords: can't connect, server unreachable, offline, network down, no connection, lost connection
1. Check the laptop is on the venue's production network (cable plugged in, or the right Wi-Fi), not guest Wi-Fi.
2. Open the server's address in a browser. If it doesn't load, check the network switch, the cable, and that the server is on.
3. The timer keeps running on the laptop while the connection is down, and your changes go to every screen when it's back. Captions made on the laptop keep going too.
4. If only one laptop is affected, restart its network adapter or the laptop itself.

### Ask others: Can other laptops reach the server?
- Yes: It's this laptop. Check its cable or Wi-Fi network, restart its network adapter, and check it has an address in the same range as the others.
- No: It's the server or the network: check the server is on, the switch it's plugged into has power and link lights, and nothing new was patched in (a loop can stop the whole network).

## Wi-Fi is slow or keeps dropping
Keywords: wifi, wireless, slow, dropping, congestion, access point, signal
1. Use a cable for anything important: presenter laptops, streams, AT-SUITE Main PCs.
2. A full room overloads Wi-Fi: keep production off the guest network.
3. Move closer to the access point, or use the 5 GHz network.

## IP address conflicts
Keywords: ip conflict, dhcp, static ip, duplicate address, address in use
1. Give production equipment fixed addresses from a range the DHCP server doesn't hand out, and keep a list of them.
2. If two devices clash, change one of them and restart both.

## Windows updates or restarts during a show
Keywords: windows update, restart, reboot, updating, forced restart
1. Pause updates for the show day (Settings → Windows Update → Pause updates).
2. Set active hours to cover the whole event.
3. Install updates the day before, never on show day.

## Laptop running slow
Keywords: slow, lagging, frozen, freezing, high cpu, memory, hot, fan noise
1. Close apps you don't need, especially browsers with many tabs.
2. Plug in the charger and set the power mode to best performance.
3. The AT-SUITE console shows each laptop's CPU and memory under Laptops & screens → Details.
4. Restart the laptop in a break if it stays slow.

## Network switch down
Keywords: switch down, network switch, no lights, all devices offline, everything offline, poe, link lights, network dead, whole network
1. Check the switch has power, and its PoE budget if cameras or access points run from it.
2. Look at the port lights: no light on a port means a cable or device fault on that port.
3. Power off and on only if you know what's on it: everything on it drops for a minute or two.
4. A loop (a cable from the switch back into itself, or two cables between the same switches) can take the whole network down: unplug the last patch made.

### Ask lights: Are the switch's power and port lights on?
- Yes: It has power. If every port light flashes wildly at once, there's a loop: unplug the last cable added. If they look normal, check its uplink to the rest of the network.
- No: It has no power: check the plug, its power supply and the socket or distro. A PoE switch that keeps restarting has too many devices drawing power: unplug some.

## Device not on the network
Keywords: device offline, not on the network, can't find device, no ip, ip address, 169.254, ping, not showing up, network cable, dhcp
1. Check the cable at both ends and the port's link light.
2. Check the device's IP address: 169.254.x.x means it got no address from DHCP.
3. A fixed-address device must be in the same range as the rest (for example 192.168.1.x with mask 255.255.255.0).
4. Ping it from a laptop on the same network.

### Ask link: Is the link light on at the device's network port, or its switch port?
- Yes: The cable is fine. → address
- No: Swap the cable, try another switch port, and check the device is switched on.

### Ask address: Does the device have an address in the same range as the others (not 169.254.x.x)?
- Yes: Check nothing else has the same address (unplug the device and ping its address: if something answers, there's a clash), and that it's on the right VLAN.
- No: It didn't get an address from DHCP: check the DHCP server or router is running, or give the device a fixed address in the right range.

## Wired or Wi-Fi?
Keywords: wired, wifi, wi-fi, ethernet, cable or wireless, which network, hardwire, should i use wifi
1. Wire anything the show depends on: playback and presenter laptops, streaming encoders, desks, lighting nodes, Dante and AT-SUITE Main PCs.
2. Wi-Fi is fine for crew phones, laptops that only chat, and tablets that remote-control a desk.
3. A full audience fills the Wi-Fi: keep production on its own network, wired or a separate Wi-Fi.
4. Desk control tablets work best on their own access point, not the venue's Wi-Fi.
