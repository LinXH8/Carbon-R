"""`FactorCitation` - an emission factor that cannot exist without its provenance.

CLAUDE.md rule 2 says a factor is incomplete unless it travels with factor, unit,
publisher, year, source document and the page or table it came from. That rule is enforced
*here*, as a type, rather than by remembering to pass the right dictionary keys: there is
no way to construct a `FactorCitation` anonymously, so there is no code path by which a
bare number reaches `calculate_emissions`.

`lookup_emission_factor` builds one of these from a database row; `calculate_emissions`
accepts nothing else. A factor hand-assembled in a test is held to the same requirements,
which is deliberate - a test that could skip provenance would stop testing the rule.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactorCitation(BaseModel):
    """One emission factor with everything needed to re-derive a figure by hand."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str = Field(min_length=1)
    factor: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, description="The publisher's own unit string, verbatim")

    # Two years, never one. `year_requested` is what the activity period asked for;
    # `year_used` is what the table could answer with. They differ on a substitution, and
    # a result that reported only one of them would hide the substitution it made.
    year_requested: int
    year_used: int
    substituted: bool = False
    substitution_note: str | None = None

    publisher: str = Field(min_length=1)
    source_document: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    # Nullable only because spreadsheet sources have no page; see the migration's comment.
    source_page: str | None = None
    retrieved_at: date

    jurisdiction: str = Field(min_length=1, default="SG")
    verified: bool = False
    notes: str | None = None

    # What the factor classifies as, carried from the row rather than assumed by the
    # caller. Before Tier 2 these were module constants in `tools/calculate.py`, which was
    # tenable only while every factor in the table was Singapore grid electricity: METHOD =
    # "location-based" is a Scope 2 term, and printing it beside a fuel-combustion figure
    # is a wrong label on an otherwise correct number.
    #
    # Nullable to match the schema, where a column added to a populated table could not be
    # NOT NULL without inventing a default for rows the migration had not read. Both
    # loaders set them explicitly; `calculate_emissions` refuses a factor missing either.
    scope: int | None = Field(default=None, ge=1, le=3)
    method: str | None = None

    # 'CO2' or 'CO2e'. EMA's grid factor is carbon dioxide only; DESNZ's fuel factors
    # include methane and nitrous oxide. Totalling one with the other understates the
    # CO2-only line, so the basis has to be known per factor to be disclosed per result.
    gas_basis: Literal["CO2", "CO2e"] | None = None

    @property
    def is_non_singapore(self) -> bool:
        """Drives the 'non-Singapore factor' disclosure (CLAUDE.md rule 7)."""
        return self.jurisdiction != "SG"

    @property
    def classification(self) -> str:
        """`'Scope 1, combustion, CO2e'` - for a citation line, never for a calculation."""
        parts = [f"Scope {self.scope}" if self.scope else "scope not recorded"]
        if self.method:
            parts.append(self.method)
        if self.gas_basis:
            parts.append(self.gas_basis)
        return ", ".join(parts)
