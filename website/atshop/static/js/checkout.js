// Opens Paddle's checkout on the transaction the server made. The browser chooses nothing about
// the price: the transaction already holds the items, the customer and the order reference.
(() => {
  const el = document.getElementById("checkout");
  if (!el) return;
  const { token, txn, ref, customer, sandbox } = el.dataset;
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
    // Live is Paddle.js's default; the sandbox switch is only made when PADDLE_ENV=sandbox.
    if (sandbox === "1") window.Paddle.Environment.set("sandbox");
    window.Paddle.Initialize({
      token,
      // Paddle Retain needs the signed-in customer's Paddle id (ctm_...), never our own id or email.
      ...(customer ? { pwCustomer: { id: customer } } : {}),
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
