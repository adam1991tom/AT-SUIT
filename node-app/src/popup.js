// A pop-up never plays sound: there is no audio element or API here, and the
// window's audio is muted by main.js as well.
const q = new URLSearchParams(location.search);
document.getElementById("title").textContent = q.get("title") || "AT-SUIT";
document.getElementById("body").textContent = q.get("body") || "";
if (q.get("kind") === "urgent") document.body.classList.add("urgent");
document.getElementById("box").onclick = () => window.popup.click();
document.getElementById("x").onclick = (e) => { e.stopPropagation(); window.popup.dismiss(); };
