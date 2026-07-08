# Sentiment Analysis and NLP for Finance

> **Code links:** [`features/sentiment.py`](../../features/sentiment.py) · [`data/feeds/newsapi_feed.py`](../../data/feeds/newsapi_feed.py) · [`strategies/sentiment.py`](../../strategies/sentiment.py)

---

## Table of Contents

1. [The Transformer Architecture](#1-the-transformer-architecture)
2. [BERT Pre-training](#2-bert-pre-training)
3. [FinBERT: Finance-Domain Fine-Tuning](#3-finbert-finance-domain-fine-tuning)
4. [Mapping Softmax Outputs to Scalar Signals](#4-mapping-softmax-outputs-to-scalar-signals)
5. [Aggregation Strategies](#5-aggregation-strategies)
6. [Implementation in This Platform](#6-implementation-in-this-platform)
7. [Limitations and Pitfalls](#7-limitations-and-pitfalls)

---

## 1. The Transformer Architecture

The **Transformer** (Vaswani et al., 2017 — "Attention Is All You Need") replaced recurrent networks as the dominant NLP architecture. Its core innovation is the **self-attention mechanism**, which allows every token in a sequence to directly attend to every other token, regardless of distance.

**Self-attention:**

```
Attention(Q, K, V) = softmax( QK^T / sqrt(d_k) ) · V
```

Where Q (queries), K (keys), V (values) are linear projections of the input embeddings, and `d_k` is the key dimension (scaling prevents vanishing gradients in the softmax).

**Multi-head attention** runs `h` attention heads in parallel, each learning different relationship patterns (e.g., syntax, coreference, subject-verb agreement):

```
MultiHead(Q, K, V) = Concat(head_1, ..., head_h) · W^O
head_i = Attention(Q·W^Q_i, K·W^K_i, V·W^V_i)
```

A Transformer **encoder** stacks N layers of:
1. Multi-head self-attention
2. Position-wise feed-forward network
3. Layer normalisation + residual connections

The encoder turns a sequence of tokens into a sequence of contextualised embeddings — each token's representation depends on the full sentence context.

---

## 2. BERT Pre-training

**BERT** (Bidirectional Encoder Representations from Transformers, Devlin et al., 2018) is a Transformer encoder pre-trained on:

1. **Masked Language Modeling (MLM):** 15% of tokens are replaced with `[MASK]`; the model learns to predict them from context. This forces bidirectional understanding — unlike GPT which only attends left-to-right.

2. **Next Sentence Prediction (NSP):** Given two sentences A and B, predict whether B follows A in the original text. This teaches discourse-level relationships.

Pre-training is done on Wikipedia + BookCorpus (3.3 billion words). The result is a model with rich linguistic knowledge baked into its weights. **Fine-tuning** then adapts these weights to a specific task with a small, labelled dataset.

**Why pre-training matters for finance:** Financial language is highly domain-specific. Terms like "beat estimates", "guidance cut", "impairment charge" have precise sentiment implications that a general-domain model misses. Fine-tuning on financial text solves this.

---

## 3. FinBERT: Finance-Domain Fine-Tuning

**FinBERT** (`ProsusAI/finbert`) is BERT-base fine-tuned on ~10,000 financial news sentences labelled as `positive`, `negative`, or `neutral` by financial domain experts.

The fine-tuning architecture adds a **classification head** on top of the `[CLS]` token embedding:

```
[CLS] token embedding (768-dim)
        ↓
Linear(768 → 3)
        ↓
Softmax → [p_positive, p_negative, p_neutral]
```

The `[CLS]` token is prepended to every input; its embedding after the final layer captures the sentence-level meaning used for classification.

**Fine-tuning data examples:**

| Headline | Label |
|----------|-------|
| "Apple beats Q3 earnings estimates by $0.15" | Positive |
| "Company announces CEO resignation amid fraud probe" | Negative |
| "Fed holds rates steady at September meeting" | Neutral |

FinBERT significantly outperforms general BERT on financial sentiment tasks: +8–12% accuracy on the Financial PhraseBank benchmark.

---

## 4. Mapping Softmax Outputs to Scalar Signals

FinBERT returns three probabilities: `[p+, p-, p0]`. The platform maps these to a scalar sentiment score `s ∈ [-1, +1]`:

```python
# features/sentiment.py
def score_article(text: str) -> float:
    inputs  = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    outputs = model(**inputs)
    probs   = softmax(outputs.logits, dim=-1).squeeze()

    p_pos, p_neg, p_neu = probs[0].item(), probs[1].item(), probs[2].item()

    # Weighted combination: positive = +1, negative = -1, neutral = 0
    return p_pos - p_neg   # range [-1, +1]
```

Alternative mappings (not used, but worth knowing):
- `argmax` → discrete {-1, 0, +1}: loses confidence information
- `p_pos - p_neg` (used) → scalar in (-1, +1): preserves magnitude
- `(p_pos - p_neg) / (1 - p_neu)`: downweights neutral articles more aggressively

---

## 5. Aggregation Strategies

A single headline has noisy sentiment. Multiple headlines about the same ticker over a time window provide a more reliable signal through aggregation.

**Simple average:** `s_ticker = mean(s_i)` — treats all articles equally regardless of age.

**Exponential decay (used in this platform):**

```python
# features/sentiment.py
def aggregate_sentiment(articles: list[ScoredArticle], decay_halflife_hours: float = 6.0) -> float:
    now = datetime.utcnow()
    weights, scores = [], []
    for a in articles:
        age_hours = (now - a.published_at).total_seconds() / 3600
        weight = 0.5 ** (age_hours / decay_halflife_hours)   # exponential decay
        weights.append(weight)
        scores.append(a.score)
    if not weights:
        return 0.0
    return sum(w * s for w, s in zip(weights, scores)) / sum(weights)
```

With a 6-hour half-life, an article from 12 hours ago has 25% the weight of a current article. This is appropriate because market-moving news is priced in quickly — stale sentiment contributes diminishing information.

**Article-count conviction scaling:** A single extreme article is less reliable than 10 mildly positive articles. The strategy applies:

```python
conviction_multiplier = min(1.0, len(recent_articles) / 5)
signal = raw_sentiment * conviction_multiplier
```

---

## 6. Implementation in This Platform

```
NewsAPI / GDELT / Bloomberg News
        ↓  (data/feeds/)
  NewsArticle objects (headline + body + published_at + source)
        ↓
  features/sentiment.py
    → FinBERT scores each article
    → Exponential decay × source quality aggregation per ticker
    → Returns per-ticker sentiment time-series
        ↓
  strategies/sentiment.py
    → z-score normalises against rolling baseline
    → Size by conviction (article count)
    → Long if z > entry_threshold, Short if z < -entry_threshold
    → Force-close after max_hold_bars
```

**Source quality weighting:** When Bloomberg B-PIPE is active alongside free-tier feeds, the aggregation applies a per-source quality multiplier before normalisation. The effective weight for each article is:

```
w_i = quality(source_i) × exp(−λ × hours_ago_i)
```

Source quality multipliers (defined in `features/sentiment.py`):

| Source | Multiplier | Rationale |
|--------|-----------|-----------|
| bloomberg | 2.0 | Curated institutional feed; primary information event proximity |
| newsapi | 1.0 | Standard — well-sourced but re-syndicated |
| gdelt | 0.8 | Aggregated web scrape — highest duplication risk |

When Bloomberg is absent (not installed or not configured), the quality multipliers for the present free-tier sources still apply, so the aggregation logic is consistent regardless of which feeds are active.

**Latency note:** FinBERT inference takes ~50ms per article on CPU. In production (AWS ECS with GPU), batch-score all new articles every minute. For backtesting, pre-compute and cache scores to avoid re-running inference on the same articles.

---

## 7. Limitations and Pitfalls

1. **Sarcasm and negation:** "Not a great quarter" scores positive on the word "great". FinBERT handles this better than VADER/TextBlob but still struggles with complex negation.

2. **Context dependence:** "Inflation rises" is negative for tech stocks but positive for financials. FinBERT gives a global sentiment; ticker-specific context is lost.

3. **Event leakage:** Earnings surprises are announced *before* market open. If your news feed delivers them pre-market, including them in a daily backtest creates look-ahead bias. The `fetch_timestamp` vs `event_timestamp` separation in `data/schemas.py` is the fix.

4. **Price reaction asymmetry:** Research shows negative news causes faster, larger reactions than equally-sized positive news (loss aversion). A symmetric ±threshold signal may underperform an asymmetric one.

5. **Model drift:** FinBERT was fine-tuned on pre-2019 data. Post-2020 financial jargon (meme stocks, crypto/DeFi terminology, Fed pivot language) may score incorrectly. Periodic fine-tuning on recent labelled data is recommended.
