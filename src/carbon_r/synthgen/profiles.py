"""The fictional cast: one electricity retailer, three tariff plans, six customers.

Everything here is invented. No real company name, UEN, logo, address or published rate
appears, and the rendered pages say so. What is borrowed is only the *shape* of a Singapore
commercial electricity bill: a flat cents-per-kWh energy rate for a given tension category,
plus a flat monthly service charge, plus GST.

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
