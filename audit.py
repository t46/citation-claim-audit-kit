"""Citation / Claim Audit Kit -- minimal CLI.

For each (claim_text, cited_work) pair in data/pairs.json:
    1. Ask an LLM judge (Claude) to read the claim and cited work,
       fetch what the cited work is actually about (using its own world-knowledge
       + the metadata we provide), and emit a verdict.
    2. Save verdicts to output/audit-YYYY-MM-DD.json (and a Markdown summary).

If ANTHROPIC_API_KEY is not set we fall back to data/verdicts_manual.json,
which contains hand-coded verdicts produced by a Claude-family model acting
as judge (see README §How It Works -> "Manual fallback").

Schema (per row):
{
    "pair_id": "...",
    "claim_text": "...",
    "cited_work": "...",
    "citation_exists": true|false,
    "support_verdict": "supports | partial | weak | contradicts | not_relevant | unavailable",
    "rationale": "...",
    "evidence_excerpt": "..."
}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"

VERDICT_VALUES = {
    "supports",
    "partial",
    "weak",
    "contradicts",
    "not_relevant",
    "unavailable",
}

JUDGE_PROMPT = """You are an academic citation auditor.

You are given:
1. A claim made in a paper's body text.
2. The exact citation attached to that claim (title, authors, arXiv ID, venue).

Your job:
- Decide whether the cited work actually supports the claim.
- If you do not have reliable knowledge of the cited work, say so honestly (verdict = "unavailable").
- Cite a brief excerpt or summary of the cited work's content as your evidence.

Return STRICT JSON with these keys ONLY:
{{
  "citation_exists": true | false,
  "support_verdict": "supports" | "partial" | "weak" | "contradicts" | "not_relevant" | "unavailable",
  "rationale": "1-3 sentences. Be specific about WHY the cited work does (or does not) support the claim.",
  "evidence_excerpt": "A short quote-or-paraphrase of what the cited work actually says/does, drawn from your knowledge of the work."
}}

Verdict semantics:
- supports        -> cited work directly establishes the claim
- partial         -> cited work is relevant but only establishes part of the claim, or only in a narrow setting
- weak            -> cited work is tangentially related; a stronger citation likely exists
- contradicts     -> cited work argues the opposite of the claim
- not_relevant    -> cited work is on a different topic; citation looks like a misattribution
- unavailable     -> you do not have reliable knowledge of the cited work to judge

Claim (from paper body):
"""

USER_TMPL = """{judge_prompt}{claim}

Cited work attached to that claim:
{cited}

Additional context (section / paper id):
- source_paper: {source}
- section: {section}

Return only the JSON object, no surrounding text.
"""


def call_anthropic(client, model: str, claim: str, cited: str, source: str, section: str) -> dict:
    prompt = USER_TMPL.format(
        judge_prompt=JUDGE_PROMPT,
        claim=claim,
        cited=cited,
        source=source,
        section=section,
    )
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
    # Strip fences if present
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def merge_with_manual(pairs: list[dict], manual: list[dict]) -> list[dict]:
    by_id = {row["pair_id"]: row for row in manual}
    merged = []
    for p in pairs:
        v = by_id.get(p["pair_id"])
        if v is None:
            merged.append(
                {
                    **p,
                    "citation_exists": None,
                    "support_verdict": "unavailable",
                    "rationale": "No manual verdict recorded and no API key in env.",
                    "evidence_excerpt": "",
                }
            )
        else:
            merged.append({**p, **v})
    return merged


def validate(row: dict) -> list[str]:
    errs = []
    if row.get("support_verdict") not in VERDICT_VALUES:
        errs.append(f"{row.get('pair_id')}: invalid verdict {row.get('support_verdict')!r}")
    if not isinstance(row.get("rationale"), str) or not row.get("rationale"):
        errs.append(f"{row.get('pair_id')}: empty rationale")
    return errs


def write_markdown_summary(rows: list[dict], path: Path) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["support_verdict"]] = counts.get(r["support_verdict"], 0) + 1
    lines = ["# Citation Audit Summary", ""]
    lines.append(f"Total pairs audited: **{len(rows)}**")
    lines.append("")
    lines.append("## Verdict counts")
    for k in ["supports", "partial", "weak", "contradicts", "not_relevant", "unavailable"]:
        lines.append(f"- {k}: {counts.get(k, 0)}")
    lines.append("")
    lines.append("## Per-pair detail")
    for r in rows:
        lines.append(f"### {r['pair_id']} -- `{r['support_verdict']}`")
        lines.append(f"- **Paper**: {r['source_paper']}")
        lines.append(f"- **Section**: {r['section']}")
        lines.append(f"- **Claim**: {r['claim_text']}")
        lines.append(f"- **Cited work**: {r['cited_work']}")
        lines.append(f"- **Rationale**: {r['rationale']}")
        if r.get("evidence_excerpt"):
            lines.append(f"- **Evidence**: {r['evidence_excerpt']}")
        lines.append("")
    path.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pairs",
        default=str(DATA / "pairs.json"),
        help="Path to claim-citation pairs JSON.",
    )
    ap.add_argument(
        "--manual",
        default=str(DATA / "verdicts_manual.json"),
        help="Path to manual fallback verdicts JSON.",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: output/audit-YYYY-MM-DD.json).",
    )
    ap.add_argument("--model", default="claude-opus-4-7-1m", help="Anthropic model id.")
    ap.add_argument(
        "--force-manual",
        action="store_true",
        help="Skip API and use manual verdicts.",
    )
    args = ap.parse_args()

    pairs = json.loads(Path(args.pairs).read_text())
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    rows: list[dict]
    mode: str

    if args.force_manual or not api_key:
        if not Path(args.manual).exists():
            print(
                "ERROR: ANTHROPIC_API_KEY not set and no manual verdicts file found.",
                file=sys.stderr,
            )
            return 1
        manual = json.loads(Path(args.manual).read_text())
        rows = merge_with_manual(pairs, manual)
        mode = "manual"
        print(
            f"[mode=manual] ANTHROPIC_API_KEY {'unset' if not api_key else 'ignored'};"
            f" using {len(manual)} pre-recorded verdicts.",
            file=sys.stderr,
        )
    else:
        try:
            from anthropic import Anthropic
        except ImportError:
            print("ERROR: anthropic package not installed. Run `uv add anthropic`.", file=sys.stderr)
            return 1
        client = Anthropic(api_key=api_key)
        rows = []
        for p in pairs:
            print(f"[live] {p['pair_id']} ...", file=sys.stderr)
            try:
                v = call_anthropic(
                    client,
                    args.model,
                    claim=p["claim_text"],
                    cited=p["cited_work"],
                    source=p["source_paper"],
                    section=p["section"],
                )
            except Exception as e:  # noqa: BLE001
                v = {
                    "citation_exists": None,
                    "support_verdict": "unavailable",
                    "rationale": f"API call failed: {e}",
                    "evidence_excerpt": "",
                }
            rows.append({**p, **v})
        mode = "live"

    errors = []
    for r in rows:
        errors.extend(validate(r))
    if errors:
        print("VALIDATION WARNINGS:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    out_path = (
        Path(args.out)
        if args.out
        else OUTPUT / f"audit-{date.today().isoformat()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"mode": mode, "rows": rows}, indent=2))
    md_path = out_path.with_suffix(".md")
    write_markdown_summary(rows, md_path)

    # Console summary
    print(f"\nWrote {out_path} and {md_path}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["support_verdict"]] = counts.get(r["support_verdict"], 0) + 1
    print("Verdict counts:")
    for k in ["supports", "partial", "weak", "contradicts", "not_relevant", "unavailable"]:
        print(f"  {k}: {counts.get(k, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
