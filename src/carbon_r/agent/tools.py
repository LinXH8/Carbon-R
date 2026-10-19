"""The three tools as the agent sees them: handles in, `{status, data, message}` out.

A thin layer over `carbon_r.tools.*`, and every bit of thinness is deliberate - the
arithmetic, the SQL and the vision call stay where they are. What this layer adds is:

1. **Address by id, never by path.** `extract_from_document` takes a `document_id` that
   must already be in the session. There is no tool argument that can name a file on
   disk, so a model (or a prompt injected into a document) cannot ask for /etc/passwd.
2. **Handles instead of values.** A looked-up factor is stored and named `factor_1`; each
   measurable quantity on a document is stored and named `act_1`, `act_2`; the calculation
   reads both back from the session. See `toolspec.py`.
3. **One failure shape.** An unknown tool, unparseable arguments, arguments that fail
   validation and a tool that genuinely found nothing all come back as a `ToolResult`.
   The loop never sees an exception, so a bad model turn costs a turn, not the request.

The three implementations are injected rather than imported at call time, so the loop can
be tested end to end with no API key, no database and no network.

What changed in Tier 2 Block 1: there is no longer a single activity per document, and no
`ACTIVITY_UNIT` constant. A document yields a list of activities, each with its own unit,
and `calculate_emissions` takes a list of (activity, factor) pairs so the total is summed
in Python rather than by the model.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import ValidationError

from carbon_r.agent.prompt import render_disclosures
from carbon_r.agent.session import Session
from carbon_r.agent.toolspec import ARG_MODELS, CalculateArgs, ExtractArgs, LookupFactorArgs
from carbon_r.tools.base import ToolResult, error, ok
from carbon_r.tools.calculate import calculate_emissions as _calculate_emissions
from carbon_r.tools.extract import extract_from_document as _extract_from_document
from carbon_r.tools.factors import lookup_emission_factor as _lookup_emission_factor


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

        # Store each activity under its own handle, so a calculation can name one line.
        stored: list[dict] = []
        for activity in result.data.get("activities", []):
            handle = self.session.next_handle("act")
            entry = activity | {"activity_ref": handle, "document_id": args.document_id}
            self.session.activities[handle] = entry
            stored.append(entry)

        trimmed = {
            "document_id": args.document_id,
            "filename": document.filename,
            "document_kind": result.data.get("document_kind"),
            "normalised": result.data["normalised"],
            "activities": stored,
            "field_problems": result.data["field_problems"],
            "consistency_flags": result.data["consistency_flags"],
        }
        self.session.readings[args.document_id] = trimmed

        hints: list[str] = []
        period_start = trimmed["normalised"].get("period_start")
        if period_start:
            hints.append(f"The activity year for the factor lookup is {period_start[:4]}.")
        if stored:
            named = ", ".join(
                f"{a['activity_ref']} = {a['amount']} {a['unit']} of {a['kind']}" for a in stored
            )
            hints.append(f"Activities: {named}.")
        else:
            hints.append(
                "No measurable activity was read from this page, so there is nothing to "
                "calculate. Report that and ask the user."
            )
        return ok(trimmed, " ".join([result.message, *hints]))

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
        """Figures from stored activities and stored factors. No value crosses the model.

        Unresolvable handles are reported together, before any calculation is attempted:
        the model gets one message naming every bad reference rather than discovering them
        one turn at a time. If any handle is bad, nothing is calculated - the same
        all-or-nothing rule `tools/calculate.py` applies to the arithmetic itself, for the
        same reason (a total over the lines that happened to resolve would be quietly
        short).
        """
        if not args.pairs:
            return error(
                "No pairs were given. Pass one activity_ref and one factor_ref per "
                "activity found on the document."
            )

        problems: list[str] = []
        pairs: list[dict] = []
        for index, pair in enumerate(args.pairs, start=1):
            activity = self.session.activities.get(pair.activity_ref)
            if activity is None:
                known = ", ".join(self.session.activities) or "none"
                problems.append(
                    f"line {index}: no activity {pair.activity_ref!r} in this session "
                    f"(known: {known}) - call extract_from_document first; the quantity "
                    f"comes from the reading, not from you"
                )
            factor = self.session.factors.get(pair.factor_ref)
            if factor is None:
                known = ", ".join(self.session.factors) or "none"
                problems.append(
                    f"line {index}: no factor {pair.factor_ref!r} in this session "
                    f"(known: {known}) - call lookup_emission_factor first"
                )
            if activity is None or factor is None:
                continue
            # Only the Activity's own fields; the handle and document_id are bookkeeping
            # this layer added and `Activity` forbids extras.
            pairs.append(
                {
                    "activity": {
                        key: value
                        for key, value in activity.items()
                        if key not in ("activity_ref", "document_id")
                    },
                    "factor": factor,
                }
            )

        if problems:
            return error(
                f"No figure was produced: {len(problems)} handle(s) could not be resolved. "
                + " | ".join(problems)
                + "."
            )

        result = self._calculate(pairs=pairs)
        if result.status != "ok" or result.data is None:
            return result

        handle = self.session.next_handle("calc")
        document_ids = sorted(
            {self.session.activities[pair.activity_ref]["document_id"] for pair in args.pairs}
        )
        data = result.data | {
            "calculation_ref": handle,
            "document_ids": document_ids,
            "activity_refs": [pair.activity_ref for pair in args.pairs],
            "factor_refs": [pair.factor_ref for pair in args.pairs],
            # The sentences this result must carry, rendered in one place so the wording
            # cannot drift between tools (CLAUDE.md rule 7).
            "required_disclosures": render_disclosures(result.data["disclosures"]),
        }
        self.session.calculations[handle] = data
        return ok(data, result.message)
