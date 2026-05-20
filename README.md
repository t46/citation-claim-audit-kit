# Citation / Claim Audit Kit

A minimal metascience experiment testing whether a lightweight LLM-as-judge can detect when a paper's citations don't actually support the claims they're attached to.

## Hypothesis

> "An LLM judge given a (claim, cited-work-metadata) pair can identify citations that are weak, tangential, or mischaracterized -- not just citations that fail to exist."

This is the layer beyond existence-checkers like HalluCiteChecker: the citation *can be real* and the cited paper *can be real* and yet the citation can still be a bad citation because the cited work doesn't actually establish what the body text claims it does.

## How It Works

1. **Extract pairs**: From each source paper, manually extract `(claim_text, cited_work, section)` tuples in `data/pairs.json`. Each pair records a claim from the body of the paper and the exact reference entry the citation marker points to.

2. **Judge each pair**: For every pair, prompt an LLM with the claim text and the cited-work bibliographic data. Ask it to return a strict-JSON verdict:
   ```json
   {
     "citation_exists": true,
     "support_verdict": "supports | partial | weak | contradicts | not_relevant | unavailable",
     "rationale": "1-3 sentences explaining the verdict",
     "evidence_excerpt": "what the cited work actually says/does"
   }
   ```
   The judge relies on its own world-knowledge of the cited works (since arXiv full-text fetching is out of scope for a 3-hour artifact). For unknown works the judge is instructed to return `unavailable` rather than hallucinate.

3. **Aggregate**: The script saves per-pair verdicts to `output/audit-YYYY-MM-DD.json` and a readable Markdown summary to `output/audit-YYYY-MM-DD.md`.

### Verdict semantics

| verdict | meaning |
| --- | --- |
| supports | cited work directly establishes the claim |
| partial | cited work is relevant but only establishes part of the claim |
| weak | cited work is tangentially related; a stronger citation likely exists |
| contradicts | cited work argues the opposite of the claim |
| not_relevant | cited work is on a different topic; looks like a misattribution |
| unavailable | judge cannot judge reliably (declines rather than hallucinates) |

### Manual fallback

If `ANTHROPIC_API_KEY` is not set, the script merges in pre-recorded verdicts from `data/verdicts_manual.json`. Those verdicts were produced by a Claude-family model (Opus 4.7) acting as judge, working from the same prompt schema as the live mode. They are honest hand-recorded outputs of the same logic the live script would execute, NOT independent ground truth.

## Data

Two papers, both already reviewed in `~/dev/trending-paper-reviews/reviews/`:

| arXiv ID | Title | Pairs |
| --- | --- | --- |
| 2604.20156 | Shen & Henderson 2026, "Temporally Extended Mixture-of-Experts Models" | 10 |
| 2605.06663 | Wang, Bhagia, Min 2026, "EMO: Pretraining Mixture of Experts for Emergent Modularity" | 10 |

Full text was available in TPR review folders (`paper.md`, `paper_clean.txt`). Claims were drawn from §1 Introduction, §2/§7 Related Works, §3 Methodology, and §4 Experimental Setup. Citation markers were resolved against the reference lists.

Both papers happen to be in the MoE-architecture cluster -- this was convenience, not selection bias. A future iteration would diversify across subfields.

## Running

```bash
# With API key (live judge):
export ANTHROPIC_API_KEY=sk-ant-...
uv run python audit.py

# Without API key (uses pre-recorded manual verdicts):
uv run python audit.py
# or
uv run python audit.py --force-manual

# Output:
#   output/audit-YYYY-MM-DD.json   (full schema'd verdicts)
#   output/audit-YYYY-MM-DD.md     (readable summary)
```

## Findings

20 claim-citation pairs audited (2026-05-20 run). Verdict distribution:

| verdict | count |
| --- | --- |
| supports | 14 |
| partial | 3 |
| weak | 3 |
| contradicts | 0 |
| not_relevant | 0 |
| unavailable | 0 |

**~30% of pairs (6/20) received less-than-direct-support verdicts.** Three weak citations that the experiment surfaced as real-data failure cases:

1. **`p2-10` -- EMO citing Vaswani 2017 for the MoE-replaces-FFN-in-decoder-only-Transformer claim**.
   Claim: *"MoE models are decoder-only Transformer language models in which the feedforward sublayer is replaced by a sparse mixture of expert networks."*
   Cited: Vaswani et al. 2017, *Attention is all you need*.
   Why weak: Vaswani 2017 introduced the encoder-decoder Transformer with dense FFN. It did NOT introduce decoder-only LMs (that lineage is GPT / Radford et al.) and did NOT introduce mixture-of-experts (that is Shazeer et al. 2017, which IS reference [11] in the same paper). The citation is illustrative-of-the-Transformer-base but does not establish the specific architectural choice the sentence describes. The "right" citation for this specific claim would be [11] Shazeer 2017.

2. **`p2-01` -- EMO citing Olmo 3 for the monolithic-LLM-paradigm claim**.
   Claim: *"LLMs are typically trained and deployed as monolithic systems."*
   Cited: Olmo 3 tech report.
   Why weak: Olmo 3 is itself an instance of a monolithic LLM release, not a study of or argument for the monolithic-deployment paradigm. The citation marker `[1, 2, 3]` is meant illustratively (here is a list of recent monolithic LLM releases), but [1] alone is too instance-as-evidence to establish the paradigm-level claim. A reader expecting a citation about the paradigm's drawbacks gets a model card.

3. **`p2-03` -- EMO citing Li et al. 2026 for "memory-constrained scaling laws"**.
   Claim: *"This has motivated a line of work such as memory-constrained scaling laws [14]."*
   Cited: Li et al., *Can mixture-of-experts surpass dense LLMs under strictly equal resources?* ICLR 2026.
   Why weak: Li et al.'s framing is "MoE vs dense at equal compute / equal parameter budget" -- a fair-resources comparison study. The EMO body text re-labels it as "memory-constrained scaling laws", which is a different axis. Partial overlap, but the label given to the cited work doesn't match the cited work's own framing.

Three partial-support cases (`p1-07`, `p1-10`, `p2-04`) were also surfaced:

- `p1-07` -- Citation [26] in the Temporally Extended MoE paper points to "Nemotron-Post-Training-Dataset-v2" with no author, no year, only a `Link`. The artefact exists on HuggingFace, but the bibliographic record is a stub and would fail standard citation-hygiene rules.
- `p1-10` -- SEER-MoE listed alongside pruning papers, but its mechanism is regularization-driven sparsity, not literal expert removal/merging.
- `p2-04` -- Mixtral cited for the strong claim "specialization is driven by surface-level patterns / token IDs", but that conclusion is at most a sidenote in the Mixtral paper, not a central result.

These are not "the paper is wrong" findings. They are "the citation is doing less work than the reader thinks" findings -- precisely the failure mode this kit is designed to surface.

## Limitations

- **N is small (20 pairs, 2 papers).** Findings indicate *the method can surface real weak citations*, not that 30% of all citations in the wild are weak.
- **Judge model knows the cited works from training data, not from fetching them.** A production version would fetch the cited paper's abstract or full text. The "unavailable" verdict exists to limit hallucination, but the judge is still working from priors, not from the actual cited PDF.
- **No inter-rater reliability check.** A single judge per pair; no n-of-3 majority. Adding a second judge family (e.g., GPT) would let us measure judge disagreement as a proxy for verdict confidence.
- **Single domain (MoE / LLM architecture).** Verdicts may not generalize to citation patterns in other subfields (biology, physics, social sciences).
- **Manual claim extraction.** Pairs were hand-picked, biasing toward citations the curator already suspected might be interesting. A real pipeline would auto-extract every (claim, citation) pair from a paper, not 10.
- **Verdict ladder is opinion-laden.** "weak" vs "partial" is a judgement call; reasonable judges would disagree on edge cases.
- **No quantitative ground truth.** We have no human-labeled reference set yet, so the kit can produce verdicts but cannot yet be scored for precision/recall.

## Integration notes

Light-touch sketches; not commitments.

- **[[../../knowledgebase/operations/projects/unktok-reviewer]]** -- Reviewer's `novelty-verifier` and `critical-reviewer` skills could call this kit as a sub-step. After the existing existence-check, run an additional support-check pass and surface any `weak | partial | contradicts | not_relevant` verdicts in the reviewer's critical-findings registry. The two are complementary: existence-check is mechanical, support-check needs an LLM judge.

- **[[../../knowledgebase/operations/projects/metareview]]** -- MetaReview runs review-time analyses; a citation-support audit fits naturally as one of its review-time checks. Run on the candidate paper's reference graph, surface the top-K weakest citations to the reviewer as discussion prompts. Output JSON is already in a shape MetaReview can consume.

- **[[../../knowledgebase/operations/projects/ai-researcher-hub]]** -- Snapshot artefacts could carry a new optional field `citation_audit_summary` that records the verdict distribution for citations used in the snapshot's claim text. No schema changes proposed yet; this is a "would be nice if" hook, not a deliverable. Per dispatch ticket: do NOT modify ARH code or schemas in this experiment.

## Files

- `audit.py` -- CLI (live + manual-fallback modes)
- `data/pairs.json` -- 20 claim-citation pairs extracted from the two papers
- `data/verdicts_manual.json` -- pre-recorded judge verdicts (used when no API key)
- `output/audit-2026-05-20.json` -- audit output (this run)
- `output/audit-2026-05-20.md` -- readable summary

## Cost (live mode)

Uses `claude-opus-4-7-1m` (configurable via `--model`). 20 pairs x 1 call = 20 API calls, ~400-600 output tokens each. Estimated cost: < $1.
