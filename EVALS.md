# Eval report: does the routing separate harmful from benign text?

The tests under `tests/` verify that `classify()` does what I wrote it to do. They feed it invented scores and check the comparisons. That is worth having and it is not evidence that the routing works, because the scores come from a model I have never measured. A test that asserts `0.7 >= 0.6` tells you nothing about whether 0.7 was the right number to hand it.

This is that missing measurement.

## Run

| | |
|---|---|
| Date | 2026-11-03 |
| Detector model | `typesafe/jev-1.14` via OpenRouter Decisions API |
| Resolved build | `typesafe/jev-1.14-20261014` (provider TypeSafe) |
| Rows per class | 100 |
| Corpus, harmful | `mlabonne/harmful_behaviors`, scored-text sha256 `7291ea172c90a0ea8880f837ff22447816de15df83b9c70a3bb6f6fcf31319ba` |
| Corpus, harmless | `mlabonne/harmless_alpaca`, scored-text sha256 `409f844031785504ad100f2c3631b4f7978b90a690f4b5cecb7ff76ff315fdd3` |
| Commit | `de3e525` |
| Reproduce | `python -m killchain.eval --rows 100 --out results.json` |

Those hashes cover the exact 100 rows each run scored, shuffled with a fixed seed. The datasets can change under me. If a later run produces a different hash, its numbers are not comparable to these and saying so has to be the first thing anyone notices.

The resolved build matters more than the model name. `typesafe/jev-1.14` is an alias, and an alias can point at a new build without anyone announcing it. Every number below belongs to the build in that row and to no other.

Cost for the whole run: about a cent. Each span is one request with nine questions.

Run-to-run stability: I repeated the identical run three times against the same
build and the same rows. AUC came back 0.925, 0.925 and 0.923, and the harmful
and harmless peak means moved by less than 0.02. The judge is not deterministic,
so treat the third digit as noise and read any difference smaller than about
0.02 as nothing.

## Headline

**AUC 0.925.** Given one harmful and one benign text, the peak score ranks the harmful one higher 92.5% of the time. A coin flip is 0.5.

| | harmful | harmless |
|---|---|---|
| peak mean | 0.809 | 0.517 |
| peak median | 0.870 | 0.490 |
| peak p10 | 0.60 | 0.37 |
| peak p90 | 0.93 | 0.73 |

That is a real gap and the distributions are far from touching. It is also not the thing I can ship on its own, because the routing does not rank texts. It applies a fixed number to each one.

## What the shipped thresholds actually cost

The default bar is 0.55, and `classify()` routes a span private when any dimension clears its own threshold.

| bar | recall on harmful | false positives on harmless |
|---|---|---|
| 0.35 | 1.00 | 0.95 |
| 0.45 | 1.00 | 0.62 |
| 0.50 | 0.99 | 0.49 |
| 0.55 | 0.98 | 0.34 |
| 0.60 | 0.90 | 0.26 |
| 0.65 | 0.83 | 0.18 |
| 0.70 | 0.76 | 0.13 |

At the shipped 0.55 the routing catches 98% of harmful prompts and sends 34% of harmless ones down the private path.

Whether that is correct depends on what a false positive costs you, and that is a config decision rather than a bug. Pulling too much to the private half is the safe direction, since the failure it prevents is a frontier agent refusing to build something. But 34% means a third of ordinary spec sentences never reach the public view, and on a real prompt that adds up to a public half the agent has very little to work with. If your public halves feel thin, this table is the first place to look. `[routing].thresholds` exists for exactly that.

Ninety percent recall wants a bar of 0.60, which costs 26%.

## Which dimensions carry the signal

Mean score per dimension, and the difference between the classes.

| dimension | harmful | harmless | gap |
|---|---|---|---|
| `regulated` | 0.670 | 0.139 | 0.53 |
| `offensive_security` | 0.631 | 0.204 | 0.43 |
| `extraction` | 0.514 | 0.212 | 0.30 |
| `scale_or_scope` | 0.671 | 0.501 | 0.17 |
| `attribution` | 0.417 | 0.277 | 0.14 |
| `reverse_engineering` | 0.181 | 0.082 | 0.10 |
| `proprietary_model` | 0.386 | 0.162 | 0.22 |
| `hedge_framing` | 0.402 | 0.407 | -0.005 |

Two things stand out.

**`scale_or_scope` is nearly a constant.** It averages 0.50 on benign text and 0.67 on harmful, and it is the single highest-scoring dimension on 69 of the 100 harmless rows. A dimension that sits high on almost everything is not separating anything. It is diluting the decision, and because `classify()` routes on `any()`, a nearly-constant high scorer is also a near-constant source of false positives. If I were retuning today I would start by raising its threshold above 0.60 or asking whether the question is too permissive.

**`hedge_framing` came back flat.** Harmful 0.402, harmless 0.407. It is not detecting anything in this corpus, and the reason is the corpus rather than the dimension. That question asks whether the text is *phrased* with "roughly", "probably", "as far as I know". Both corpora are ordinary user prompts. They differ in what they ask for and not in whether they hedge, so there is nothing there for this dimension to find. I left the dimension alone. Changing the wording to move a number my corpus cannot measure would be tuning to the test, which is the mistake this report exists to stop.

So `hedge_framing` is **unmeasured**, not failing. To measure it I need spans that are hedged and not hedged about the same subject, which the public corpora here do not contain.

## What this does not tell you

The corpora are harmful and harmless *user prompts*. The routing is asked to predict whether a frontier agent would hedge on *a sentence of a specification*. Those are different objects, and a high AUC on the first does not transfer automatically to the second.

What it establishes: the detector reads plainly harmful text as risky and plainly harmless text as safe. That is a necessary condition and it was previously assumed rather than checked.

What it does not establish: that a benign-looking spec sentence naming a licence, a vendor SDK, or a competitor gets flagged. Nothing in this run tests that, because nothing in these corpora looks like a spec sentence. The honest summary is that the floor is measured and the ceiling is not.

The near-misses matter for the same reason. A harmless row that peaks at 0.73 is not a bug in the routing, it is a prompt that reads as risky to a nine-question judge. Since the judge is doing prediction and not measurement, those rows are the ones most likely to behave differently on real specs.

## Reproducing

```bash
set -a && . ~/.tokens && set +a
python -m killchain.eval --rows 100 --out results.json
```

`tests/test_routing_accuracy.py` asserts floors on the same measurements. It skips without an API key, which means CI green says nothing about routing accuracy. That is deliberate. These numbers move when the provider ships a new build, and a job that goes red for that reason teaches everyone to ignore it. Run it by hand when you change a dimension's wording, and compare the resolved build before comparing anything else.

## Next

The routing is not wrong, it is uncalibrated. Three things I would do before calling it tuned:

1. Raise or rewrite `scale_or_scope`, which is currently both the most common trigger on benign text and the weakest separator of the two.
2. Decide the false-positive budget on purpose. 34% is defensible for a safety-first default, but it should be a decision with a reason rather than what the numbers happened to produce.
3. Build a small span-level set from real specs, hedged and unhedged, to test the thing this corpus cannot: whether a sentence that mentions a licence reads as risky. That is the one that predicts real behaviour, and it is the gap in this report.

- Dr. J
