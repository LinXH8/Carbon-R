"""The three tools as the agent sees them: handles in, `{status, data, message}` out.

A thin layer over `carbon_r.tools.*`, and every bit of thinness is deliberate - the
arithmetic, the SQL and the vision call stay where they are. What this layer adds is:

1. **Address by id, never by path.** `extract_from_document` takes a `document_id` that
   must already be in the session. There is no tool argument that can name a file on
   disk, so a model (or a prompt injected into a document) cannot ask for /etc/passwd.
2. **Handles instead of values.** A looked-up factor is stored and named `factor_1`; the
   calculation reads it back from the session. See `toolspec.py`.
3. **One failure shape.** An unknown tool, unparseable arguments, arguments that fail
   validation and a tool that genuinely found nothing all come back as a `ToolResult`.
   The loop never sees an exception, so a bad model turn costs a turn, not the request.

The three implementations are injected rather than imported at call time, so the loop can
be tested end to end with no API key, no database and no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from carbon_r.agent.prompt import render_disclosures
from carbon_r.agent.session import Session
from carbon_r.agent.toolspec import ARG_MODELS, CalculateArgs, ExtractArgs, LookupFactorArgs
from carbon_r.tools.base import ToolResult, error, no_results, ok
from carbon_r.tools.calculate import calculate_emissions as _calculate_emissions
from carbon_r.tools.extract import extract_from_document as _extract_from_document
from carbon_r.tools.factors import lookup_emission_factor as _lookup_emission_factor

ACTIVITY_UNIT = "kWh"  # Tier 1 reads electricity bills only


class Toolkit:
    """The tools bound to one session. One instance per request; cheap to build."""

    def __init__(
        self,
        session: Session,
        *,
        extract: Callable[..., ToolResult] = _extract_from_document,
        lookup: Callable[..., ToolResult] = _lookup_emission_factor,
        calculate: Callable[..., ToolResult] = _calculate_emissions,
    ) -> None:
        self.session = session
        self._extract = extract
        self._lookup = lookup
        self._calculate = calculate

    # --- dispatch ----------------------------------------------------------------

    def dispatch(self, name: str, arguments: str) -> ToolResult:
        """Run one tool call from the model. Never raises; always records the result."""
        result = self._dispatch(name, arguments)
        self.session.record(name, result)
        return result

    def _dispatch(self, name: str, arguments: str) -> ToolResult:
        model = ARG_MODELS.get(name)
        if model is None:
            return error(f"No tool named {name!r}. Available tools: {', '.join(ARG_MODELS)}.")

        try:
            payload = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return error(f"Arguments for {name} were not valid JSON: {exc}.")
        if not isinstance(payload, dict):
            return error(
                f"Arguments for {name} must be a JSON object, got {type(payload).__name__}."
            )

        try:
            args = model.model_validate(payload)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors()
            )
            return error(f"Arguments for {name} are not valid ({problems}).")

        handler = {
            "extract_from_document": self.extract_from_document,
            "lookup_emission_factor": self.lookup_emission_factor,
            "calculate_emissions": self.calculate_emissions,
        }[name]
        return handler(args)  # type: ignore[arg-type]

    # --- the tools ---------------------------------------------------------------

    def extract_from_document(self, args: ExtractArgs) -> ToolResult:
        """Read an uploaded document. Cached per document: one page, one vision call."""
        document = self.session.known_document(args.document_id)
        if document is None:
            known = ", ".join(self.session.documents) or "none"
            return error(
                f"No document {args.document_id!r} in this session. Uploaded documents: {known}."
            )

        cached = self.session.readings.get(args.document_id)
        if cached is not None:
            return ok(cached, f"{document.filename} has already been read in this session.")

        result = self._extract(document.path)
        if result.status != "ok" or result.data is None:
            return result

        trimmed = {
            "document_id": args.document_id,
            "filename": document.filename,
            "normalised": result.data["normalised"],
            "field_problems": result.data["field_problems"],
            "consistency_flags": result.data["consistency_flags"],
        }
        self.session.readings[args.document_id] = trimmed

        hint = ""
        period_start = trimmed["normalised"].get("period_start")
        if period_start:
            hint = f" The activity year for the factor lookup is {period_start[:4]}."
        return ok(trimmed, result.message + hint)

    def lookup_emission_factor(self, args: LookupFactorArgs) -> ToolResult:
        """Exact SQL, full citation, and a handle the calculation can refer to."""
        result = self._lookup(args.category, args.unit, args.year)
        if result.status != "ok" or result.data is None:
            return result

        handle = self.session.next_handle("factor")
        self.session.factors[handle] = result.data["factor"]
        return ok(
            result.data | {"factor_ref": handle},
            f"{result.message} Stored as {handle}; pass it to calculate_emissions.",
        )

    def calculate_emissions(self, args: CalculateArgs) -> ToolResult:
        """Figure from a stored reading and a stored factor. No value crosses the model."""
        reading = self.session.readings.get(args.document_id)
        if reading is None:
            if self.session.known_document(args.document_id) is None:
                known = ", ".join(self.session.documents) or "none"
                return error(
                    f"No document {args.document_id!r} in this session. Uploaded: {known}."
                )
            return error(
                f"{args.document_id} has not been read yet. Call extract_from_document on "
                "it first; the consumption figure comes from the reading, not from you."
            )

        factor = self.session.factors.get(args.factor_ref)
        if factor is None:
            known = ", ".join(self.session.factors) or "none"
            return error(
                f"No factor {args.factor_ref!r} in this session. Looked-up factors: {known}. "
                "Call lookup_emission_factor first."
            )

        consumption = reading["normalised"].get("consumption_kwh")
        if consumption is None:
            return no_results(
                "The consumption was not read from this document, so there is nothing to "
                "multiply. Report that the figure could not be read and ask the user to "
                "confirm it.",
                {"document_id": args.document_id, "field_problems": reading["field_problems"]},
            )

        try:
            amount = Decimal(str(consumption))
        except InvalidOperation:  # pragma: no cover - normalise guarantees a number
            return error(f"Stored consumption for {args.document_id} is not a number.")

        result = self._calculate(
            activity_amount=amount,
            activity_unit=ACTIVITY_UNIT,
            factor=factor,
            is_estimated_reading=bool(reading["normalised"].get("is_estimated_reading")),
        )
        if result.status != "ok" or result.data is None:
            return result

        handle = self.session.next_handle("calc")
        data = result.data | {
            "calculation_ref": handle,
            "document_id": args.document_id,
            "factor_ref": args.factor_ref,
            # The sentences this result must carry, rendered in one place so the wording
            # cannot drift between tools (CLAUDE.md rule 7).
            "required_disclosures": render_disclosures(result.data["disclosures"]),
        }
        self.session.calculations[handle] = data
        return ok(data, result.message)
