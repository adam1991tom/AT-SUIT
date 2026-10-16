# AT SUITE website and shop

The public website, the shop (Paddle subscriptions), the customer portal and the admin pages for selling
AT SUITE. It is one small Python app (FastAPI, SQLite) that runs on its own server, separate from the
AT SUITE server venues use.

What it does:

- **Website**: home, features, how it works, pricing (monthly or yearly), self-hosted vs managed hosting,
  FAQ, downloads, docs, support and contact, and the legal pages (marked as drafts).
- **Accounts**: sign-in by emailed link (no passwords to leak), one free 30-day trial per account.
- **Checkout**: Paddle Billing, as merchant of record (Paddle handles VAT, invoices and card details).
  Monthly or yearly plans, managed hosting as an add-on, and a one-off event pass.
- **Licence keys**: issued automatically when Paddle confirms a payment, in exactly the signed format
  `tools/licence.py` makes, so the AT SUITE server checks them offline as it does today. Renewals,
  upgrades and downgrades issue a new key; the app's own warnings, 14-day grace and show-safe lock are
  unchanged.
- **Customer portal**: keys (copy or download), plan changes, billing and invoices (via Paddle's portal),
  hosted venue status, details and support messages.
- **Admin** (`/admin`, a 6-digit authenticator code on top of sign-in): customers, keys (issue by hand
  for enterprise deals, revoke), plans and Paddle prices, hosted venues and their jobs, support
  messages, webhook events (with retry), the email outbox and an audit log of every change.

## Prices

Prices live in [`catalogue.toml`](catalogue.toml) and nowhere else. To change one, edit it, then run
`python tools/paddle_setup.py --apply` so Paddle has a matching price. Customers already subscribed
stay on the price they bought at.

| Plan | Laptops | Monthly | Yearly |
|---|---|---|---|
| Small | 10 | £99 | £990 |
| Venue | 30 | £229 | £2,290 |
| Large | 75 | £429 | £4,290 |
| Managed hosting (add-on) | | £39 | £390 |
| Event pass (7 days, 10 laptops) | | £149 one-off | |

Enterprise (more than 75 laptops or several sites) is "contact sales"; its keys are made in
Admin → Licences. Prices exclude VAT; Paddle adds it at checkout.

## Try it on your computer

```sh
cd website
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q                       # the tests use a throwaway signing key and a fake Paddle

# a throwaway signing key, only for trying it out
python3 -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; \
from cryptography.hazmat.primitives import serialization as s; \
open('dev-key.pem','wb').write(K.generate().private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))"
chmod 600 dev-key.pem

SHOP_DEV=1 LICENCE_SIGNING_KEY_FILE=dev-key.pem ADMIN_EMAILS=you@example.com \
  python3 -m uvicorn atshop.app:app --port 8090
```

Open http://localhost:8090. With `SHOP_DEV=1` and no email server, emails (sign-in links included)
are printed in the terminal instead of sent. Keys made with the throwaway key won't open a real AT SUITE server,
which is what you want while testing.

## Going live, step by step

Everything below uses Paddle's **sandbox** (test cards, no real money) until live billing is approved.

1. **Paddle sandbox account**: sign up at https://sandbox-vendors.paddle.com.
   - Developer tools → Authentication: make an **API key** and a **client-side token**.
   - Checkout → Website approval is only needed for live; sandbox works at once.
   - Checkout settings: set the default payment link to `https://<your domain>/account`.
2. **Settings**: `cp .env.example .env` and fill it in (`SHOP_SECRET`, the Paddle keys, `ADMIN_EMAILS`, `SITE_URL`).
3. **Prices in Paddle**: `python3 tools/paddle_setup.py` shows what it would make; add `--apply` to make
   them. It saves the price list to `data/paddle-prices.sandbox.json`.
4. **Webhook**: Paddle → Developer tools → Notifications → New destination:
   - URL `https://<your domain>/webhooks/paddle`
   - Events: `subscription.created`, `subscription.updated`, `subscription.activated`,
     `subscription.canceled`, `subscription.past_due`, `subscription.paused`, `subscription.resumed`,
     `subscription.trialing`, `transaction.completed`, `transaction.payment_failed`, `transaction.past_due`
   - Put its secret key in `PADDLE_WEBHOOK_SECRET`.
5. **The signing key**: the vendor private key (the pair of `server/atsuit/vendor_pubkey.txt`) goes on the
   shop server only, as `secrets/vendor-private-key.pem`:
   `chmod 400 secrets/vendor-private-key.pem && sudo chown 10001 secrets/vendor-private-key.pem`
   (10001 is the shop's user in the container). It is never committed, never in a page, and no
   endpoint signs on request: keys are only made for a verified payment, a trial or an admin.
6. **Email**: any SMTP service (`SMTP_*`). Sign-in links go by email, so this is needed before launch.
7. **Run it**: `docker compose up -d --build`, with Caddy in front ([deploy/Caddyfile](deploy/Caddyfile))
   and Cloudflare in front of that. Nightly database copies: [deploy/backup.sh](deploy/backup.sh).
8. **Admin**: sign in with an `ADMIN_EMAILS` address, open `/admin`, and scan the code into an
   authenticator app (Google Authenticator, Microsoft Authenticator, 1Password...).
9. **Test a purchase** in sandbox with Paddle's test card `4242 4242 4242 4242`, any future date, any CVC.
10. **Live** (https://vendors.paddle.com). This changes settings only; the code is the same in both.
    Nothing below deletes or replaces anything already in the live account.
    - Developer tools → Authentication: a live **API key** (`pdl_live_apikey_...`) and a live
      **client-side token** (`live_...`). The shop refuses to start if a key belongs to the other account.
    - `PADDLE_ENV=production`, `SHOP_ALLOW_LIVE=yes` (the shop refuses production without it).
    - `python3 tools/paddle_setup.py` reads the live account first and shows what it would make; then
      `--apply`. Prices it finds already there (made by it earlier) are recorded, not made twice, and it
      prints the sandbox → live price id list. The live list is `data/paddle-prices.production.json`;
      the sandbox one stays beside it. There are no price ids in the code to change.
    - Webhook: if the live account already has a notification destination for
      `https://<your domain>/webhooks/paddle`, keep it and copy its secret key. Only make a new one if
      there is none (same events as step 4). Its secret goes in `PADDLE_WEBHOOK_SECRET`.
    - Webhooks are only accepted from the addresses Paddle lists at `https://api.paddle.com/ips`
      (`https://sandbox-api.paddle.com/ips` in sandbox), read by the shop and refreshed hourly, and
      only with a good signature. Behind Cloudflare the visitor address comes from `CF-Connecting-IP`,
      otherwise from Caddy's `X-Forwarded-For`, so keep the shop's port bound to 127.0.0.1.
    - Checkout → Checkout settings: the default payment link is `https://<your real domain>/account`
      (not localhost). Payment methods: switch on the ones you want.
    - Settings → Website approval: submit the domain. Live checkout only opens on approved domains.
    - Payouts → Payout settings: bank details.
    - Paddle Retain gets the signed-in customer's Paddle id (`pwCustomer`) when checkout opens.

The shop should run on its own small server, not on a venue server or the hosting server: it holds the
key that makes licences, so it is kept away from anything customers run.

## Managed hosting

The shop talks to the hosting server only through the `atcloud` tool (`cloud/atcloud`). When a customer
buys hosting, the shop records the venue and queues a job:

- `HOSTING_MODE=manual` (the default): jobs wait in Admin → Hosting with the exact command to paste on the
  hosting server and the key file to download. Mark each job done (paste atcloud's output so the setup
  link is saved).
- `HOSTING_MODE=ssh`: the shop runs the same commands itself over SSH (`HOSTING_SSH=user@host`,
  `HOSTING_SSH_KEY`). Keys go to atcloud on stdin, never on the command line.

Commands used: `atcloud new`, `atcloud licence`, `atcloud suspend`, `atcloud resume`. Nothing removes a
venue or its data automatically; when a hosted subscription ends, admins get an email and decide.

## Licence key renewal

A yearly key runs to the end of the paid year plus 7 spare days; a monthly key to the end of the month
plus 7. Each renewal emails the new key and shows it in the portal. `POST /api/v1/licence/refresh`
with `{"key": "<current key>"}` returns that subscription's newest key, ready for a future "renew
automatically" setting in the app (the app doesn't call it yet).

## What's done and what isn't

Done and tested (`pytest`, 48 tests, fake Paddle and a throwaway key):

- signed webhooks (bad, missing or old signatures refused), duplicates applied once, out-of-order
  subscription events skipped, a failed event recorded and retried
- the full journey: sign up, checkout, payment, a key the AT SUITE server's own licence code accepts
- monthly renewal, upgrade and downgrade (with the right Paddle proration), past due, scheduled cancel,
  cancelled; keys never cut short; unknown prices flagged and nothing issued
- event pass, trial (once per account), key refresh, invoices only for your own payments
- admin: 403 for customers, the authenticator code, hand-issued keys need a reason and are audited,
  revoking; CSRF on every form; sign-in links single use and time-limited
- managed hosting in manual and ssh modes; the Paddle setup tool, including a price change
- the catalogue's laptop limits match `tools/licence.py`

Not done yet, needs a person:

- a real Paddle account (sandbox first), the live switch-on, and an SMTP service
- the domain, the shop server, and Paddle's website approval
- a solicitor's review of the legal pages (they are drafts with placeholders)
- the AT SUITE app calling the key refresh endpoint (it can't renew itself yet; customers paste keys)
- nothing here has been run against Paddle's real sandbox yet: the tests use a stand-in that follows
  Paddle's documented API
