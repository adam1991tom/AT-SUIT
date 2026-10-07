// Lets custom views written for Ontime run inside AT-SUIT unchanged.
// Ontime runs one server per room (one port each); AT-SUIT runs one server for
// every room, so this points the view's websocket and data requests at the
// room given in the page address: /external/<view>/?room=<id>.
(() => {
  const params = new URLSearchParams(location.search);
  let room = params.get("room");
  try {
    if (room) sessionStorage.setItem("atsuit_view_room", room);
    else room = sessionStorage.getItem("atsuit_view_room");
  } catch (_) {}
  if (!room) return;
  const base = `/ontime/${encodeURIComponent(room)}`;
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
  const nativeFetch = window.fetch && window.fetch.bind(window);
  if (nativeFetch) {
    window.fetch = (input, init) => {
      try {
        const u = new URL(typeof input === "string" ? input : input.url, location.href);
        const m = u.pathname.match(/\/data\/(runtime|rundowns\/current)\/?$/);
        if (m) input = `${base}/data/${m[1]}`;
      } catch (_) {}
      return nativeFetch(input, init);
    };
  }
})();
