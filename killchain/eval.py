"""Does the routing actually work? Measure it instead of asserting it.

The unit tests pin the arithmetic in routing.py against scores we invented.
That proves `classify()` compares correctly and proves nothing about whether
Jev returns a sensible probability for a real span. This module answers the
second question: take a corpus with known labels, score it through the same
code path a run uses, and report whether the two score distributions separate.

    uv run python -m killchain.eval --rows 200

Two things this deliberately does NOT claim:

  * The mlabonne corpora are harmful *user prompts*; routing predicts whether
    a frontier agent would hedge on a *sentence of a specification*. The
    distributions overlap where a spec describes sensitive work, and nowhere
    else. A high score here means Jev reads harmful text as harmful. It does
    not mean Jev flags a spec sentence that mentions the GPL.
  * A single threshold number is a fiction. What matters is where the two
    distributions sit relative to it, which is why this prints the full
    separability picture and not just accuracy.

Everything scored is sent to OpenRouter and TypeSafe, so --rows is capped and
reported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import urllib.request
from dataclasses import dataclass, field

from killchain import detector, routing

HF_ROWS = "https://datasets-server.huggingface.co/rows"
CORPORA = {
    "harmful": "mlabonne/harmful_behaviors",
    "harmless": "mlabonne/harmless_alpaca",
}


@dataclass
class Result:
    label: str                  # "harmful" | "harmless"
    text: str
    scores: dict[str, float] = field(default_factory=dict)
    flagged: bool = False

    @property
    def overall(self) -> float:
        return self.scores.get("overall", 0.0)

    @property
    def peak(self) -> float:
        """Highest dimension score. This is what classify() acts on."""
        return max(self.scores.values(), default=0.0)

    @property
    def peak_dim(self) -> str:
        if not self.scores:
            return "-"
        return max(self.scores.items(), key=lambda kv: kv[1])[0]


def fetch(dataset: str, want: int, offset: int = 0, seed: int = 0) -> list[str]:
    """Pull `want` texts from a HF dataset via the rows server.

    The rows endpoint pages at 100, so this walks pages and samples rather than
    pulling a whole corpus: harmless_alpaca is 31k rows and we do not need them.
    """
    out: list[str] = []
    page = 0
    while len(out) < want:
        url = f"{HF_ROWS}?dataset={dataset}&config=default&split=train&offset={page * 100}&length=100"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                rows = json.loads(r.read()).get("rows", [])
        except (urllib.error.URLError, TimeoutError) as e:
            raise SystemExit(f"cannot reach the HF rows server for {dataset}: {e}") from e
        if not rows:
            break
        out.extend(str(row["row"]["text"]).strip() for row in rows)
        page += 1
        if offset and page > 200:      # hard stop; nobody wants 31k API calls
            break
    if len(out) > want:
        random.Random(seed).shuffle(out)
        out = out[:want]
    return out


def run_scores(texts: list[str], label: str, cfg: dict) -> list[Result]:
    """Score each text as a single span, through the real detector path."""
    spans = [(f"{label}[{i}]", t) for i, t in enumerate(texts)]
    verdicts = detector.score_spans(cfg, spans, risky_terms=[])
    return [Result(label, v.text, v.scores, v.flagged) for v in verdicts]


def resolved_model(cfg: dict) -> str:
    """The exact build the alias pointed at today, e.g. typesafe/jev-1.14-20261014.

    An alias like `typesafe/jev-1.14` can be repointed at a new build without
    warning, so a report that names only the alias cannot be reproduced.
    """
    try:
        ans = detector._call(cfg, "hello", {"q": {"type": "noul",
                                                 "instructions": "Is this risky?",
                                                 "criteria": {"true": "yes", "false": "no"}}})
    except Exception:
        return "unknown"
    return ans.get("model", "unknown") if isinstance(ans, dict) else "unknown"


def corpora_provenance(by_label: dict[str, list[Result]]) -> dict:
    """Fingerprint the exact rows scored, so a later run over a changed
    dataset is visibly different rather than quietly comparable."""
    out = {}
    for label, results in by_label.items():
        blob = "\n".join(r.text for r in results).encode("utf-8")
        out[label] = {
            "dataset": CORPORA.get(label, "?"),
            "n": len(results),
            "sha256_of_scored_texts": hashlib.sha256(blob).hexdigest(),
        }
    return out


def summarise(by_label: dict[str, list[Result]]) -> dict:
    stats: dict = {}
    for label, results in by_label.items():
        peaks = sorted(r.peak for r in results)
        # `overall` is only asked of an assembled public view, never of a bare
        # span, so it does not appear in these scores. Reporting its mean would
        # print a confident 0.0 that means "never asked", not "no risk".
        stats[label] = {
            "n": len(results),
            "peak_mean": round(statistics.fmean(peaks), 3) if peaks else 0.0,
            "peak_median": round(statistics.median(peaks), 3) if peaks else 0.0,
            "peak_p10": round(peaks[len(peaks) // 10], 3) if peaks else 0.0,
            "peak_p90": round(peaks[len(peaks) * 9 // 10], 3) if peaks else 0.0,
        }
    for label, results in by_label.items():
        counts: dict[str, int] = {}
        for r in results:
            counts[r.peak_dim] = counts.get(r.peak_dim, 0) + 1
        stats[label]["peak_dim"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return stats


def separability(by_label: dict[str, list[Result]]) -> dict:
    """The number that matters: how much the two distributions overlap.

    Reports accuracy at each routing threshold, plus the bar that would be
    needed to reach 90% recall on the harmful set, and the false-positive rate
    that bar costs on the harmless set.
    """
    harmful = [r.peak for r in by_label.get("harmful", [])]
    harmless = [r.peak for r in by_label.get("harmless", [])]
    if not harmful or not harmless:
        return {}
    out: dict = {"sweep": []}
    for bar in (0.35, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        recall = sum(1 for p in harmful if p >= bar) / len(harmful)
        fpr = sum(1 for p in harmless if p >= bar) / len(harmless)
        out["sweep"].append({
            "bar": bar,
            "recall": round(recall, 3),
            "false_positive": round(fpr, 3),
        })
    # The bar needed for 90% recall, and what it costs on the benign side.
    need = min((p for p in harmful if sum(1 for q in harmful if q >= p) / len(harmful) <= 0.90),
               default=min(harmful))
    out["bar_for_90_recall"] = round(need, 3)
    out["fpr_at_90_recall"] = round(sum(1 for p in harmless if p >= need) / len(harmless), 3)

    # A crude AUC by rank, to compare runs. 1.0 = perfect separation, 0.5 = coin flip.
    wins = sum((1.0 if h > b else 0.5 if h == b else 0.0) for h in harmful for b in harmless)
    out["auc"] = round(wins / (len(harmful) * len(harmless)), 3)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m killchain.eval",
        description="Measure refusal-risk routing against labelled corpora.")
    ap.add_argument("--rows", type=int, default=100,
                    help="texts per class (default 100; each costs one API call)")
    ap.add_argument("--out", default="", help="write the full result JSON here")
    ap.add_argument("--base-url", default="https://openrouter.ai/api/alpha/decisions")
    ap.add_argument("--model", default="typesafe/jev-1.14")
    a = ap.parse_args(argv)

    if a.rows > 500:
        raise SystemExit("--rows above 500 is a lot of API calls; raise the cap in code if you mean it")

    cfg = {"base_url": a.base_url, "model": a.model, "api_key_env": "OPENROUTER_API_KEY",
           "timeout": 90}
    resolved_name = resolved_model(cfg)

    by_label: dict[str, list[Result]] = {}
    for label, dataset in CORPORA.items():
        print(f"[eval] fetching {a.rows} from {dataset} ...", file=sys.stderr, flush=True)
        texts = fetch(dataset, a.rows)
        print(f"[eval] scoring {len(texts)} {label} spans ...", file=sys.stderr, flush=True)
        by_label[label] = run_scores(texts, label, cfg)

    stats = summarise(by_label)
    sep = separability(by_label)

    print(f"\n{'=' * 72}\nREFUSAL-RISK ROUTING: {a.model}\n{'=' * 72}")
    print(f"resolved build: {resolved_name}")
    for label in ("harmful", "harmless"):
        s = stats.get(label)
        if s:
            print(f"\n{label}  n={s['n']}")
            print(f"  peak score   mean {s['peak_mean']}  median {s['peak_median']}  "
                  f"p10 {s['peak_p10']}  p90 {s['peak_p90']}")
            top = list(s["peak_dim"].items())[:3]
            print("  most often the peak: " + ", ".join(f"{d} x{c}" for d, c in top))

    if sep:
        print("\nseparability (peak dimension vs the threshold classify() uses)")
        print(f"  AUC {sep['auc']}   (1.0 perfect, 0.5 coin flip)")
        print(f"\n  {'bar':>5} {'recall':>7} {'false pos':>10}")
        for row in sep["sweep"]:
            print(f"  {row['bar']:>5.2f} {row['recall']:>7.2f} {row['false_positive']:>10.2f}")
        print(f"\n  90% recall needs a bar of {sep['bar_for_90_recall']}, "
              f"which flags {sep['fpr_at_90_recall']:.0%} of benign text")

    if a.out:
        payload = {
            "model": a.model,
            "resolved_model": resolved_name,
            "rows_per_class": a.rows,
            "corpora": corpora_provenance(by_label),
            "stats": stats,
            "separability": sep,
            "thresholds": dict(routing.THRESHOLDS),
            "results": [{"label": r.label, "text": r.text, "scores": r.scores,
                         "peak": r.peak, "flagged": r.flagged}
                        for rs in by_label.values() for r in rs],
        }
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n[eval] full results -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
