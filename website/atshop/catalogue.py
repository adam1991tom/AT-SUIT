"""The plans and prices (catalogue.toml), and which Paddle price is which (paddle-prices.<env>.json).

Paddle prices can't be changed once customers are on them, so a price change makes a new Paddle
price. The prices file keeps every price ever made, mapped to its plan, so renewals of an old price
still get the right licence key.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CYCLES = ("month", "year")


@dataclass
class Plan:
    key: str
    name: str
    for_: str
    laptops: int
    licence_plan: str
    monthly: int
    yearly: int
    order: int = 0
    featured: bool = False

    def amount(self, cycle: str) -> int:
        return self.monthly if cycle == "month" else self.yearly

    @property
    def yearly_saving(self) -> int:
        return self.monthly * 12 - self.yearly


@dataclass
class Catalogue:
    currency: str
    vat_note: str
    spare_days: int
    plans: dict[str, Plan]
    hosting: dict
    event: dict
    trial: dict
    enterprise: dict
    prices: dict = field(default_factory=dict)  # Paddle price id → what it is
    products: dict = field(default_factory=dict)  # "plan:small" → Paddle product id
    prices_file: Path | None = None

    @property
    def plan_list(self) -> list[Plan]:
        return sorted(self.plans.values(), key=lambda p: p.order)

    # ----------------------------------------------------- Paddle price ids --
    def price_id(self, kind: str, key: str, cycle: str = "") -> str | None:
        """The Paddle price to sell now for this item: the one at today's catalogue amount."""
        want = self.amount_of(kind, key, cycle)
        for pid, p in self.prices.items():
            if p["kind"] == kind and p["key"] == key and p.get("cycle", "") == cycle and p["amount"] == want * 100 \
                    and p.get("currency", self.currency) == self.currency and p.get("active", True):
                return pid
        return None

    def lookup(self, price_id: str) -> dict | None:
        return self.prices.get(price_id)

    def amount_of(self, kind: str, key: str, cycle: str = "") -> int:
        if kind == "plan":
            return self.plans[key].amount(cycle)
        if kind == "addon" and key == "hosting":
            return int(self.hosting["monthly"] if cycle == "month" else self.hosting["yearly"])
        if kind == "pass" and key == "event":
            return int(self.event["price"])
        raise KeyError(f"{kind}:{key}")

    def items(self):
        """Every sellable item: (kind, key, cycle, name, amount in pounds)."""
        for p in self.plan_list:
            for c in CYCLES:
                yield "plan", p.key, c, f"AT SUITE {p.name}", p.amount(c)
        for c in CYCLES:
            yield "addon", "hosting", c, "AT SUITE Managed hosting", self.amount_of("addon", "hosting", c)
        yield "pass", "event", "", "AT SUITE Event pass", self.amount_of("pass", "event")

    def missing_prices(self) -> list[str]:
        return [f"{k}:{key}{'/' + c if c else ''}" for k, key, c, _, _ in self.items() if not self.price_id(k, key, c)]

    def save_prices(self) -> None:
        assert self.prices_file
        self.prices_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.prices_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"products": self.products, "prices": self.prices}, indent=2) + "\n")
        tmp.replace(self.prices_file)


def load(path: Path, prices_file: Path | None = None) -> Catalogue:
    raw = tomllib.loads(path.read_text())
    plans = {}
    for key, p in raw["plans"].items():
        plans[key] = Plan(key=key, name=p["name"], for_=p.get("for", ""), laptops=int(p["laptops"]),
                          licence_plan=p["licence_plan"], monthly=int(p["monthly"]), yearly=int(p["yearly"]),
                          order=int(p.get("order", 0)), featured=bool(p.get("featured", False)))
        if plans[key].yearly <= 0 or plans[key].monthly <= 0:
            raise ValueError(f"plan {key}: prices must be above zero")
    cat = Catalogue(currency=raw.get("currency", "GBP"), vat_note=raw.get("vat_note", ""),
                    spare_days=int(raw.get("spare_days", 7)), plans=plans,
                    hosting=raw["addons"]["hosting"], event=raw["passes"]["event"], trial=raw["trial"],
                    enterprise=raw.get("enterprise", {}), prices_file=prices_file)
    if prices_file and prices_file.is_file():
        data = json.loads(prices_file.read_text())
        cat.prices = data.get("prices", {})
        cat.products = data.get("products", {})
    return cat


def money(pounds: int | float, currency: str = "GBP") -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, currency + " ")
    return f"{sym}{pounds:,.0f}" if float(pounds).is_integer() else f"{sym}{pounds:,.2f}"
