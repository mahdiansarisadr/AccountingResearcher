# PRD — Accounting Research Assistant (AI Agent)

- **Author:** _TBD_
- **Date:** _TBD_
- **Status:** Draft

> One-line summary: An AI agent that lets an accounting researcher ask natural-language questions about their data — from files already in the system or newly uploaded documents (including PDF invoices and images).

---

## 1. Problem Statement

Accountants and auditors at the firm need to answer questions about financial data that
currently lives in Excel files. Today, getting an answer means manually opening and
searching through multiple spreadsheets — the majority of a researcher's time is spent
hunting through sheets rather than analyzing results.

Typical questions they need answered include:
- **Aggregations across time:** e.g., "What is the total cost of travel by the finance team over the last 3 years?"
- **Trend analysis:** e.g., "What is the trend in spending since the beginning of 2026?"
- **Status / exception finding:** e.g., "Find cases that have not been audited yet."

The manual, spreadsheet-by-spreadsheet workflow is slow, error-prone, and does not scale
as the number of files grows. The goal is an AI agent that lets a researcher ask these
questions in natural language and get accurate, sourced answers directly from their data —
whether that data already exists in the system or is newly uploaded (including PDF invoices
and images).

## 2. Justification for AI (why not deterministic rules)

The value is in letting non-technical accountants ask **open-ended, varied questions in natural
language** over a **heterogeneous, messy, growing** data corpus (structured tables + PDFs + images).
A deterministic/rules-only approach cannot cover this because:

- **Unbounded question space:** Users phrase ad-hoc questions ("trend since 2026", "cases not
  audited yet", "total travel over 3 years") that can't be pre-enumerated as fixed reports.
- **Understanding intent:** Interpreting free-form questions and matching them to the right data is
  exactly where AI adds value over hard-coded reports.
- **Unstructured inputs:** Reading fields from PDF invoices and images requires understanding
  varied layouts, not fixed parsers.

## 3. Target Users & Personas

**Primary user:** Accountants and auditors at accounting firms.

- **Product model:** Multi-tenant SaaS sold to multiple firms (long-term). The first release is
  an **MVP scoped to a single firm** to prove the core question-answering experience before
  adding multi-tenancy.
- **Interaction model:** Chat-based. Users simply ask questions in natural language; they are
  not expected to write queries. Assume "Excel is the ceiling" for technical skill.
- **Access model (MVP):** All users can see all data. _Future extension:_ per-firm isolation and
  per-user / per-client data permissions. The MVP must be extensible to support this later.

**Not for (initial release):** Non-accounting roles, and use cases requiring fine-grained
data-access controls (deferred to a later version).

## 4. Goals & Success Metrics

**Primary goal:** Let a researcher get a **correct, cited answer** to a data question in seconds
instead of manually searching Excel files — with enough trust that they rely on it over manual work.

**What matters most:** **Accuracy is the #1 success criterion.** The product succeeds only if
answers are trustworthy and verifiable. Speed and breadth are secondary to correctness.

**Success metrics (to be finalized with targets):**
- **Answer accuracy** — % of answers that are factually correct and correctly sourced (top priority).
- **Abstention correctness** — when the agent says "I don't know," it should genuinely be a case
  where a correct answer wasn't available (i.e., low rate of "gave up when it shouldn't have").
- **Latency** — answers returned within the target below.
- **Adoption / trust** — researchers prefer the agent over manual Excel search for covered questions.

**Latency target:** **≤ ~10 seconds** per answer (hard-ish ceiling for the MVP).

## 5. Scope (IN / OUT)

**IN scope (MVP):**
- Chat-based, natural-language Q&A over a single firm's data corpus.
- Answering over the firm's existing data (structured tables, PDFs, images).
- Uploading new PDF invoices / images — both added to the dataset and answerable immediately.
- Cited, traceable **text** answers.
- **Read-only** behavior: the agent answers questions; it never edits or writes back to the data.

**OUT of scope (MVP / deferred):**
- Charts and visualizations.
- Multi-firm (multi-tenant) support and per-user / per-client data permissions.
- Any write/edit/modify operations on the data (read-only only).
- Integrations with external accounting systems (e.g., QuickBooks, SAP, Xero).

## 6. Agent Capabilities & Behavior

**Core capability:** Answer natural-language questions over the firm's data corpus
(structured tables, PDFs, images) via a chat interface.

**Answer requirements:**
- **Citations & traceability (required):** Every answer must show where the numbers came from —
  the source file(s), and where possible the specific sheet/rows/invoice used — so an auditor can
  independently verify the result. Traceability is a first-class requirement, not a nice-to-have.
- **Exact numbers:** Quantitative results must be computed exactly from the underlying data, not
  estimated or approximated by the model.
- **Format (MVP):** Text answers are sufficient. Tables and charts (e.g., trend visualizations)
  are deferred to a later version.
- **Accuracy over completeness — zero tolerance for confident wrong answers:** A confidently
  incorrect number is worse than no answer. When the data is missing, ambiguous, or the agent is
  not confident, it must **say so** ("I'm not sure" / "I couldn't find that" / ask a clarifying
  question) rather than guess or fabricate a figure.

### Behavior when unsure (situational)
In order of preference:
1. **Ask a clarifying question** when the request is ambiguous or under-specified (preferred —
   be helpful, not just refuse).
2. **Abstain with a reason** when it genuinely can't answer confidently: say *"I can't answer that
   confidently,"* **explain why** (data not found, conflicting records, ambiguous scope), and where
   possible **suggest how the user could help** (e.g., "upload the Q3 invoices," or "did you mean the
   finance team or all departments?").
3. **Never fabricate** numbers or sources to fill a gap.

### Confidence for extracted documents
- **Cite the source document** for any answer derived from an extracted PDF/image so the user can
  eyeball the original.
- Surface an **overall confidence** indication for extraction-derived answers (a simple signal for
  the MVP, not per-field review).

## 7. Data Sources & Inputs

The agent must answer questions over a **mixed, growing corpus** of financial data:

- **Structured / table-like data:** Excel spreadsheets with clean rows and columns.
- **PDF documents:** e.g., invoices and other financial documents.
- **Images:** e.g., photos/scans of invoices.

**Volume & characteristics:**
- On the order of **thousands of tables**, each potentially **hundreds of rows and/or columns**.
- Data is **dropped in over time** — the corpus grows continuously.

**Handling uploads (PDF invoices / images):** Both behaviors are required —
1. **Add to the dataset:** the uploaded document's data becomes searchable for future questions.
2. **Answer in the moment:** the user can immediately ask questions about the document they just
   uploaded.

**MVP scope:** Single firm's data corpus.

## 8. Constraints

- **Deployment:** The application and the firm's data run **on-prem**.
- **Model access:** Using **hosted model APIs** (e.g., OpenAI / Anthropic) is permitted for the AI
  itself; the outbound-data policy (what may leave the network) is an open question (Section 11).

## 9. Evaluation

Because accuracy is the #1 goal, evaluation is a core deliverable, not an afterthought.

- **Golden test set (to be built):** No test set exists yet — building one is part of the project.
  Target ~**30–50 real questions** paired with **verified correct answers** (and expected source(s)),
  covering the main question types: aggregations over time, trends, and status/exception finding.
  Curated with input from actual accountants/auditors so answers are truly ground-truth.
- **Automated evaluation:** Regularly score answers on the golden set for **numeric correctness**,
  **citation correctness**, and **appropriate abstention** (answers when it can, abstains when it
  should).
- **Human + operational feedback:** **Thumbs up / down** per answer, human spot-checks against
  sources, and live tracking of **cost** (per question and per user/month) and **latency**
  (against the ≤10s target). Feedback and failures feed back into the golden set.

## 10. Cost, Operations & Lifecycle

_TBD._ Cost ceiling/budget per question and per user/month, and post-launch ownership &
maintenance, are not yet decided (see Open Questions).

## 11. Roadmap (MVP)

Outcome-oriented milestones for the single-firm MVP:

1. **Core Q&A** — a researcher can ask the three core question types and get correct, cited answers.
2. **Trustworthy behavior** — the agent clarifies or abstains (with a reason) instead of guessing.
3. **Measurable accuracy** — a golden set and evaluation are in place, plus in-chat feedback.
4. **Full data + uploads** — the firm's full corpus is available, and users can upload PDF invoices
   / images that are both searchable and answerable immediately.

## 12. Open Questions

- **Outbound data policy:** With on-prem hosting but hosted model APIs, what data may leave the
  network in model calls (raw rows vs. only derived/aggregated values)?
- **Cost:** Budget/ceiling per question and per user/month?
- **Post-launch ownership & maintenance:** Who operates the system after MVP?
- **Golden set ownership:** Who curates/verifies the golden Q&A set?
