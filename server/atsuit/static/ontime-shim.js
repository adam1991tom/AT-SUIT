// Lets custom views written for Ontime run inside AT-SUIT unchanged.
// Ontime runs one server per room (one port each); AT-SUIT runs one server for
// every room, so this points the view's websocket and data requests at the
// room in the page address: /room/<id>/external/<view>/ (or the older
// /external/<view>/?room=<id>).
(() => {
  const inPath = location.pathname.match(/^\/room\/(\d+)\/external\//);
  let room = inPath ? inPath[1] : new URLSearchParams(location.search).get("room");
  if (!inPath) {
    try {
      if (room) sessionStorage.setItem("atsuit_view_room", room);
      else room = sessionStorage.getItem("atsuit_view_room");
    } catch (_) {}
  }
  if (!room || !/^\d+$/.test(room)) return;
  const base = `/ontime/${room}`;
  const NativeWS = window.WebSocket;
  function PatchedWS(url, protocols) {
    try {
      const u = new URL(url, location.href);
      // Any Ontime socket (".../ws", whatever host or port it names) becomes this room's feed.
      if (/\/ws\/?$/.test(u.pathname) && !u.pathname.startsWith("/ws/audio")) {
        url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${base}/ws`;
      }
    } catch (_) {}
    return protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
  }
  PatchedWS.prototype = NativeWS.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach((k) => (PatchedWS[k] = NativeWS[k]));
  window.WebSocket = PatchedWS;
  // Ontime's REST data (/data/runtime, /data/settings, /data/rundowns/current ...).
  const toRoom = (raw) => {
    try {
      const u = new URL(raw, location.href);
      const m = u.pathname.match(/\/data\/([\w/-]+?)\/?$/);
      if (m && !u.pathname.startsWith(base)) return `${base}/data/${m[1]}${u.search}`;
    } catch (_) {}
    return null;
  };
  const nativeFetch = window.fetch && window.fetch.bind(window);
  if (nativeFetch) {
    window.fetch = (input, init) => {
      const moved = toRoom(typeof input === "string" ? input : input && input.url);
      return nativeFetch(moved || input, init);
    };
  }
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    return open.call(this, method, toRoom(url) || url, ...rest);
  };
})();
