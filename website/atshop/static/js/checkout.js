// Opens Paddle's checkout on the transaction the server made. The browser chooses nothing about
// the price: the transaction already holds the items, the customer and the order reference.
(() => {
  const el = document.getElementById("checkout");
  if (!el) return;
  const { token, txn, ref, sandbox } = el.dataset;
  const status = document.getElementById("checkout-status");
  const open = () => {
    if (!window.Paddle) {
      status.textContent = "The payment window didn't load. Check your connection or ad blocker, then press Pay again.";
      return;
    }
    window.Paddle.Checkout.open({
      transactionId: txn,
      settings: { displayMode: "overlay", theme: "dark", locale: "en",
                  successUrl: `${location.origin}/account/thanks?order=${encodeURIComponent(ref)}` },
    });
  };
  const start = () => {
    if (sandbox === "1") window.Paddle.Environment.set("sandbox");
    window.Paddle.Initialize({
      token,
      eventCallback: (e) => {
        if (e.name === "checkout.completed") location.href = `/account/thanks?order=${encodeURIComponent(ref)}`;
      },
    });
    open();
  };
  document.getElementById("pay").addEventListener("click", open);
  if (window.Paddle) start();
  else document.getElementById("paddle-js").addEventListener("load", start);
})();
