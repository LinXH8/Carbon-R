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

    @property
    def is_non_singapore(self) -> bool:
        """Drives the 'non-Singapore factor' disclosure (CLAUDE.md rule 7)."""
        return self.jurisdiction != "SG"
