"""The fictional cast: three vendors, their price lists, and six customers.

Everything here is invented. No real company name, UEN, logo, address or published rate
appears, and the rendered pages say so. What is borrowed is only the *shape* of the
documents: a flat cents-per-kWh energy rate for a given tension category plus a monthly
service charge for electricity; a price per cylinder for LPG; a price per litre by product
and month for fuel. All three end in GST.

Tier 2 Block 1 added the LPG supplier and the fuel-card issuer. The pattern from the
electricity retailer is kept deliberately: **a price belongs to the vendor, not to the
customer**, so two customers of one vendor pay the same rate and a test can assert that.

See SOURCES.md: that this is the real shape of non-residential pricing is recorded as
`secondary only - VERIFY`, as is where the low-tension/high-tension boundary falls. No
threshold is stated anywhere in this code; Kranji Cold Store is sized to be unambiguously a
large consumer rather than to sit near any particular line.

Declared as module-level literals rather than drawn from Faker, so the cast is stable and
reviewable. Faker fills only incidental text (street address, postal code, meter serial).
"""

from dataclasses import dataclass
from decimal import Decimal

VENDOR_NAME = "Straits Power Retail Pte Ltd"

# Spec 1.7 requires identifiers that cannot collide with real ones, and requires VERIFYING
# any claim that a chosen format is invalid. The first draft used "202411234K", which is
# shaped exactly like a real ACRA local-company UEN - the mistake recorded as lesson 13.
#
# ACRA's UEN format page could not be fetched (HTTP 403), so no claim is made about which
# UEN patterns are issuable. Instead the token is made SELF-EVIDENTLY synthetic: no
# verification is needed to know that "SYN-0000-0001" is not anybody's UEN. A
# realistic-but-supposedly-impossible pattern would be the riskier choice, because it rests
# on an assumption that has not been checked.
VENDOR_UEN = "SYN-0000-0001"
VENDOR_ADDRESS = "18 Harbour Mews, #01-02, Singapore 099253"  # fictional
VENDOR_SUPPORT = "support@straitspower.example"  # .example is reserved; never routable


@dataclass(frozen=True)
class TariffPlan:
    """A retail plan. The rate belongs to the plan, so customers on it share the rate."""

    code: str
    name: str
    tension: str  # "Low Tension" | "High Tension"
    cents_per_kwh: Decimal
    fixed_charge_sgd: Decimal


# The high-tension plan has a LOWER rate but a HIGHER standing charge. That is the real
# shape of a voltage category: a large consumer takes supply at higher voltage, pays less
# per unit and more as a fixed charge. It also means "cheaper per kWh" and "cheaper bill"
# are not the same thing anywhere in this test set.
TARIFF_PLANS: dict[str, TariffPlan] = {
    "SPR-LT-FIX-24": TariffPlan(
        code="SPR-LT-FIX-24",
        name="Business Fixed 24",
        tension="Low Tension",
        cents_per_kwh=Decimal("29.86"),
        fixed_charge_sgd=Decimal("12.00"),
    ),
    "SPR-HT-FIX-24": TariffPlan(
        code="SPR-HT-FIX-24",
        name="Industrial Fixed 24",
        tension="High Tension",
        cents_per_kwh=Decimal("27.65"),
        fixed_charge_sgd=Decimal("32.00"),
    ),
    "SPR-LT-FIX-25": TariffPlan(
        code="SPR-LT-FIX-25",
        name="Business Fixed 25",
        tension="Low Tension",
        cents_per_kwh=Decimal("30.12"),
        fixed_charge_sgd=Decimal("12.00"),
    ),
}


@dataclass(frozen=True)
class CustomerProfile:
    """One fictional business and the three consecutive months it is billed for.

    base_kwh
        Usage in a month of **average length** (AVG_DAYS_PER_MONTH = 30.4 days), *not*
        usage in a calendar month. A real period is scaled from this by its own day count,
        so a 28-day February bills about 10% less than a 31-day March at the same
        intensity. Stating it as "a calendar month" would make the day-scaling term
        meaningless.

    seasonal_amplitude
        How far this business's use swings across the year, as a fraction. A property of
        the business, not of the weather: refrigeration tracks ambient temperature closely
        (0.18), while a small fully-conditioned clinic barely moves (0.05). The *timing* of
        the peak is global (PEAK_MONTH); only the size of the swing lives here.
    """

    key: str
    name: str
    account_number: str
    plan_code: str
    base_kwh: int
    seasonal_amplitude: float
    start_year: int
    start_month: int
    # First document number for this account's run. Explicit rather than derived from
    # position, so adding a profile cannot renumber anybody else's documents (spec 1.4).
    first_doc_number: int
    street: str
    # Postal sector (first two digits) for the street's area. See POSTAL_SECTORS.
    postal_sector: str
    # True for businesses that occupy ground-floor premises: F&B, workshops, cold stores.
    # Lesson 13: Faker gave a car workshop a 7th-floor unit.
    ground_floor_only: bool

    @property
    def plan(self) -> TariffPlan:
        return TARIFF_PLANS[self.plan_code]


# Nine bills in 2024, nine in 2025: half the set finds an exact-year EMA factor, half must
# fall back to 2024 and say so. February 2024 is included deliberately - a leap year, so
# calendar.monthrange handling is exercised rather than assumed.
CUSTOMER_PROFILES: tuple[CustomerProfile, ...] = (
    CustomerProfile(
        key="kopi_corner",
        name="Kopi Corner Pte Ltd",
        account_number="8820-1134-02",
        plan_code="SPR-LT-FIX-24",
        base_kwh=3_800,
        seasonal_amplitude=0.10,  # kitchen load partly offsets cooling load
        start_year=2024,
        start_month=1,
        first_doc_number=1,
        street="Lorong Tempua",
        postal_sector="53",  # coffee shop: ground floor
        ground_floor_only=True,
    ),
    CustomerProfile(
        key="lion_city_laundry",
        name="Lion City Laundry Pte Ltd",
        account_number="8820-7741-06",
        plan_code="SPR-LT-FIX-24",
        base_kwh=9_200,
        seasonal_amplitude=0.08,  # process heat and motors, not cooling
        start_year=2024,
        start_month=4,
        first_doc_number=4,
        street="Jalan Sinar Bulan",
        postal_sector="53",  # industrial laundry: ground floor
        ground_floor_only=True,
    ),
    CustomerProfile(
        key="kranji_cold_store",
        name="Kranji Cold Store Pte Ltd",
        account_number="8820-3309-01",
        plan_code="SPR-HT-FIX-24",
        base_kwh=180_000,
        seasonal_amplitude=0.18,  # refrigeration tracks ambient temperature
        start_year=2024,
        start_month=10,
        first_doc_number=7,
        street="Neo Tiew Crescent North",
        postal_sector="72",  # cold store: ground floor, Kranji
        ground_floor_only=True,
    ),
    CustomerProfile(
        key="tampines_print_works",
        name="Tampines Print Works Pte Ltd",
        account_number="8820-5520-04",
        plan_code="SPR-LT-FIX-25",
        base_kwh=5_100,
        seasonal_amplitude=0.11,  # machine heat plus cooling
        start_year=2025,
        start_month=2,
        first_doc_number=10,
        street="Tampines Industrial Walk 7",
        postal_sector="52",  # print works: ground floor
        ground_floor_only=True,
    ),
    CustomerProfile(
        key="serangoon_dental",
        name="Serangoon Dental Group Pte Ltd",
        account_number="8820-9163-08",
        plan_code="SPR-LT-FIX-25",
        base_kwh=2_400,
        seasonal_amplitude=0.05,  # small, fully conditioned, near-constant
        start_year=2025,
        start_month=5,
        first_doc_number=13,
        street="Serangoon Garden Rise",
        postal_sector="55",  # clinic: upper floors are normal
        ground_floor_only=False,
    ),
    CustomerProfile(
        key="jurong_auto",
        name="Jurong Auto Services Pte Ltd",
        account_number="8820-4487-03",
        plan_code="SPR-LT-FIX-25",
        base_kwh=6_700,
        seasonal_amplitude=0.12,  # roller doors open; poor thermal envelope
        start_year=2025,
        start_month=9,
        first_doc_number=16,
        street="Jalan Pesawat Lama",
        postal_sector="61",  # workshop: ground floor, Jurong
        ground_floor_only=True,
    ),
)


# Postal sectors (the first two digits of a six-digit code) used to keep each fictional
# address internally coherent - a Kranji street should not carry a Toa Payoh code.
#
# SECONDARY ONLY - VERIFY (SOURCES.md, spec Part 10). The sector-to-area mapping was taken
# from secondary sources, not from SingPost's own documentation, which could not be read
# here. It costs nothing if wrong: these are fictional addresses on documents labelled
# synthetic, and nothing in the repo claims the mapping is correct. It must not be stated
# as fact in the README, and must be verified before any claim that the addresses are
# realistic.
POSTAL_SECTORS: dict[str, str] = {
    "52": "Tampines / Pasir Ris",
    "53": "Serangoon Garden / Hougang / Ang Mo Kio",
    "55": "Bishan / Serangoon",
    "61": "Jurong",
    "72": "Kranji / Woodgrove",
}


def incidental_details(profile: CustomerProfile, seed: int) -> tuple[str, str]:
    """(customer_address, meter_number) - the page text that is not load-bearing.

    Seeded per account so it is stable across runs and independent of every other account.
    Faker supplies digits only; streets, sectors and floor constraints come from the
    profile, because unconstrained Faker produced a 7th-floor car workshop and a Kranji
    address with a Toa Payoh postal code (lesson 13).
    """
    from faker import Faker

    fake = Faker("en_US")  # numerics only; no en_SG locale exists
    fake.seed_instance(f"{seed}:{profile.account_number}:identity")

    block = fake.random_int(min=1, max=98)
    # A cold store, kopitiam or workshop occupies the ground floor.
    unit_floor = 1 if profile.ground_floor_only else fake.random_int(min=2, max=6)
    unit_number = fake.random_int(min=1, max=24)
    postal_tail = fake.random_int(min=0, max=9999)
    postal = f"{profile.postal_sector}{postal_tail:04d}"

    address = f"{block} {profile.street}, #{unit_floor:02d}-{unit_number:02d}, Singapore {postal}"
    meter_number = f"SP{fake.random_int(min=10_000_000, max=99_999_999)}"
    return address, meter_number


# =======================================================================================
# Scope 1 vendors, added in Tier 2 Block 1
# =======================================================================================
#
# Both are invented, and both carry self-evidently synthetic UENs for the reason given
# above: no claim is made about which UEN patterns ACRA can issue, so the token is one
# nobody could hold.

LPG_VENDOR_NAME = "Island Gas Supply Pte Ltd"
LPG_VENDOR_UEN = "SYN-0000-0002"
LPG_VENDOR_ADDRESS = "7 Jalan Tiga Belas, #01-09, Singapore 619050"  # fictional
LPG_VENDOR_SUPPORT = "orders@islandgas.example"  # .example is reserved; never routable

FUEL_VENDOR_NAME = "Straits Fleet Card Pte Ltd"
FUEL_VENDOR_UEN = "SYN-0000-0003"
FUEL_VENDOR_ADDRESS = "212 Jalan Kilang Baru, #02-04, Singapore 159353"  # fictional
FUEL_VENDOR_SUPPORT = "fleet@straitsfleet.example"


# --- LPG --------------------------------------------------------------------------------
#
# Cylinder sizes. 12.7 kg and 50 kg are the two sizes used here: a small one for a kitchen
# and a large one for a commercial burner.
#
# SECONDARY ONLY - VERIFY (SOURCES.md, spec Part 10). That these are the common commercial
# sizes in Singapore has NOT been read from a supplier's own published range, and nothing
# in the repo claims they are typical. They are plausible and internally consistent, which
# is all the generated documents need; the assumption is recorded rather than asserted.
#
# The decimal size is deliberate: 12.7 makes `count x size` a figure with a fractional
# part, so an extraction that rounded the cylinder arithmetic would be caught.


@dataclass(frozen=True)
class LPGPriceList:
    """A gas supplier's prices. Per cylinder, plus two money decoys that are not fuel."""

    code: str
    name: str
    # Price per cylinder, by cylinder size in kg. Priced per cylinder rather than per kg
    # because that is the harder case for extraction: the kg never appears as a money
    # quantity, so a reading cannot recover it from the amounts.
    price_per_cylinder_sgd: dict[Decimal, Decimal]
    delivery_charge_sgd: Decimal
    # Charged per cylinder on site, and NOT a purchase of gas. The single most important
    # decoy on this document: a reading that summed invoice quantities would count rental
    # cylinders as delivered gas.
    rental_per_cylinder_sgd: Decimal


LPG_PRICE_LISTS: dict[str, LPGPriceList] = {
    "IGS-COM-26": LPGPriceList(
        code="IGS-COM-26",
        name="Commercial Cylinder 2026",
        price_per_cylinder_sgd={
            Decimal("12.7"): Decimal("48.50"),
            Decimal("50"): Decimal("165.00"),
        },
        delivery_charge_sgd=Decimal("10.00"),
        rental_per_cylinder_sgd=Decimal("5.00"),
    ),
}


# --- fuel cards -------------------------------------------------------------------------


@dataclass(frozen=True)
class Vehicle:
    """One fictional van. `tank_litres` bounds a fill; `product` fixes what it takes."""

    plate: str
    product: str  # the printed product name, e.g. "Diesel", "Petrol 95"
    tank_litres: int


@dataclass(frozen=True)
class Fleet:
    """The vehicles on one customer's card, and how often they fill up."""

    card_number: str
    vehicles: tuple[Vehicle, ...]
    fills_per_vehicle_per_month: int


# Vehicle plates. Singapore plates are three letters then up to four digits then a
# checksum letter (e.g. SLA1234A). These use a leading "SYN" group, which no real plate
# carries, for the same reason the UENs do: spec 1.7 asks for identifiers that cannot
# collide with a real one, and ACRA's and LTA's own format documentation was not readable
# here (SOURCES.md #8). A self-evidently synthetic plate needs no verification to be safe.
#
# VERIFY outstanding: that "SYN" is not an issuable prefix. Treated as an assumption.


@dataclass(frozen=True)
class FuelPriceList:
    """Pump prices per litre, by product and by month.

    Drawn from a declared table rather than randomly, so every customer of this vendor
    sees the same price in the same month - the vendor-owns-the-price rule again, and the
    thing that makes a cross-customer price test possible.

    GST CONVENTION, and it is a VERIFY item: these are treated as GST-EXCLUSIVE, with GST
    added as its own line, matching the electricity bill and the LPG invoice. Whether
    Singapore pump prices are quoted inclusive of GST, and how fuel-card statements
    present it, has NOT been verified (SOURCES.md). The convention is enforced by
    `FuelCardStatement`'s validator so the documents are at least internally consistent.

    Worth stating plainly: litres are unaffected by this choice either way, so it cannot
    move an emissions figure. It changes only the money decoys.
    """

    code: str
    name: str
    # (year, month) -> {printed product name: price per litre}
    prices: dict[tuple[int, int], dict[str, Decimal]]


def _fuel_prices() -> dict[tuple[int, int], dict[str, Decimal]]:
    """A plausible 2024-2026 pump-price series, declared month by month.

    Literal rather than generated: a price list is reference data, and a reviewer should be
    able to read the whole thing. The drift is gentle and the two products move together,
    which is what pump prices do.
    """
    diesel = {
        (2024, 10): "2.74",
        (2024, 11): "2.71",
        (2024, 12): "2.76",
        (2025, 1): "2.79",
        (2025, 2): "2.81",
        (2025, 3): "2.84",
        (2025, 4): "2.86",
        (2025, 5): "2.83",
        (2025, 6): "2.80",
        (2025, 7): "2.82",
        (2025, 8): "2.85",
        (2025, 9): "2.88",
        (2025, 10): "2.91",
        (2025, 11): "2.89",
        (2025, 12): "2.92",
        (2026, 1): "2.95",
        (2026, 2): "2.97",
        (2026, 3): "2.99",
        (2026, 4): "3.02",
        (2026, 5): "3.04",
        (2026, 6): "3.01",
        (2026, 7): "3.03",
        (2026, 8): "3.06",
        (2026, 9): "3.08",
    }
    # Petrol 95 runs above diesel here, as it generally does at Singapore pumps.
    petrol_offset = Decimal("0.18")
    return {
        key: {
            "Diesel": Decimal(value),
            "Petrol 95": Decimal(value) + petrol_offset,
        }
        for key, value in diesel.items()
    }


FUEL_PRICE_LISTS: dict[str, FuelPriceList] = {
    "SFC-PUMP-26": FuelPriceList(
        code="SFC-PUMP-26",
        name="Fleet Pump Price 2026",
        prices=_fuel_prices(),
    ),
}

# Per-litre discount the card gives back. Money only: it reduces the amount payable and
# leaves the litres - and therefore the emissions - untouched. That is the trap.
FUEL_DISCOUNT_PER_LITRE_SGD = Decimal("0.06")


@dataclass(frozen=True)
class LPGCustomer:
    """A customer's gas use. Mirrors CustomerProfile's shape for electricity."""

    key: str  # matches a CustomerProfile key, so one business has both documents
    account_number: str
    price_list_code: str
    # kg of LPG in a month of average length, before seasonality and noise. Cooking and
    # process heat, so the seasonal swing is small: a kitchen's gas use tracks covers
    # served, not the weather.
    base_kg: int
    seasonal_amplitude: float
    cylinder_size_kg: Decimal
    start_year: int
    start_month: int
    first_doc_number: int

    @property
    def price_list(self) -> LPGPriceList:
        return LPG_PRICE_LISTS[self.price_list_code]


@dataclass(frozen=True)
class FuelCustomer:
    """A customer's fleet and card."""

    key: str
    account_number: str
    price_list_code: str
    fleet: Fleet
    start_year: int
    start_month: int
    first_doc_number: int

    @property
    def price_list(self) -> FuelPriceList:
        return FUEL_PRICE_LISTS[self.price_list_code]


# Gas goes to the two businesses that would burn it: a coffee shop kitchen and a laundry
# with gas dryers.
#
# `kopi_corner` starts in Dec 2024 ON PURPOSE. DESNZ publishes a standalone set per year
# and only 2025 and 2026 are loaded, so a 2024 supply period has no factor at or before it
# and `lookup_emission_factor` returns no_results. That is the one honesty path Tier 1
# never exercised live (every synthetic bill fell inside the substitution cap), and it is
# worth having a document that reaches it.
#
# Starting in December rather than October gives exactly ONE such document out of the
# account's three: enough to exercise the refusal, while the other two still produce
# figures. Three un-calculable documents would have made a quarter of the set untestable
# for everything downstream of the factor lookup.
LPG_CUSTOMERS: tuple[LPGCustomer, ...] = (
    LPGCustomer(
        key="kopi_corner",
        account_number="GAS-4401-07",
        price_list_code="IGS-COM-26",
        # Sized so that noise and seasonality carry the monthly requirement across
        # cylinder boundaries. A base that always rounded to the same number of cylinders
        # would make every invoice for this account identical but for its dates, and a
        # constant guess would score on it.
        base_kg=230,
        seasonal_amplitude=0.06,
        cylinder_size_kg=Decimal("50"),
        start_year=2024,
        start_month=12,
        first_doc_number=1,
    ),
    LPGCustomer(
        key="lion_city_laundry",
        account_number="GAS-7718-02",
        price_list_code="IGS-COM-26",
        base_kg=565,
        seasonal_amplitude=0.05,
        cylinder_size_kg=Decimal("50"),
        start_year=2025,
        start_month=3,
        first_doc_number=4,
    ),
    LPGCustomer(
        key="serangoon_dental",
        account_number="GAS-9163-05",
        price_list_code="IGS-COM-26",
        base_kg=41,
        seasonal_amplitude=0.04,
        # The small cylinder, so one document in the set has a fractional kg total and an
        # extraction that rounded the cylinder arithmetic is caught.
        cylinder_size_kg=Decimal("12.7"),
        start_year=2025,
        start_month=11,
        first_doc_number=7,
    ),
    LPGCustomer(
        key="tampines_print_works",
        account_number="GAS-5520-09",
        price_list_code="IGS-COM-26",
        base_kg=138,
        seasonal_amplitude=0.05,
        cylinder_size_kg=Decimal("50"),
        start_year=2026,
        start_month=1,
        first_doc_number=10,
    ),
)


# Cards go to the businesses with vans. `jurong_auto` runs a mixed fleet - one diesel van
# and one petrol car - so its statements carry TWO activities needing two different
# factors, which is the case the whole pairs-based calculation exists for.
#
# It starts in November so that its three months span a year boundary: the December
# statement is dated in January, which is the case spec Part 8 item 11 asks every kind to
# contain - a document whose period year differs from its document-date year, so an eval
# can catch a model that took the factor year from the wrong date.
FUEL_CUSTOMERS: tuple[FuelCustomer, ...] = (
    FuelCustomer(
        key="jurong_auto",
        account_number="FLT-4487-01",
        price_list_code="SFC-PUMP-26",
        fleet=Fleet(
            card_number="6677-****-****-2201",
            vehicles=(
                Vehicle(plate="SYN 1241 J", product="Diesel", tank_litres=70),
                Vehicle(plate="SYN 5582 K", product="Petrol 95", tank_litres=50),
            ),
            fills_per_vehicle_per_month=3,
        ),
        start_year=2025,
        start_month=11,
        first_doc_number=1,
    ),
    FuelCustomer(
        key="lion_city_laundry",
        account_number="FLT-7741-03",
        price_list_code="SFC-PUMP-26",
        fleet=Fleet(
            card_number="6677-****-****-7742",
            vehicles=(
                Vehicle(plate="SYN 3390 L", product="Diesel", tank_litres=80),
                Vehicle(plate="SYN 3391 L", product="Diesel", tank_litres=80),
            ),
            fills_per_vehicle_per_month=4,
        ),
        start_year=2026,
        start_month=2,
        first_doc_number=4,
    ),
)


def customer_by_key(key: str) -> CustomerProfile:
    """The electricity profile for a key, so one business keeps one name and address.

    An LPG invoice and an electricity bill for Kopi Corner must agree on who Kopi Corner
    is - same legal name, same premises. Keeping the identity on the electricity profile
    and looking it up here means there is one definition of each business rather than
    three that can drift apart.
    """
    for profile in CUSTOMER_PROFILES:
        if profile.key == key:
            return profile
    raise KeyError(f"No customer profile {key!r}; have {[p.key for p in CUSTOMER_PROFILES]}")
