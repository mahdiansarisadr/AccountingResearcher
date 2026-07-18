# PRD — Accounting Research Assistant (AI Agent)

- **Author:** _TBD_
- **Date:** _TBD_
- **Status:** Draft (in progress)

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
_TBD_

## 3. Target Users & Personas

**Primary user:** Accountants and auditors at accounting firms.

- **Product model:** Multi-tenant SaaS sold to multiple firms (long-term). The first release is
  an **MVP scoped to a single firm** to prove the core question-answering experience before
  adding multi-tenancy.
- **Interaction model:** Chat-based. Users simply ask questions in natural language; they are
  not expected to write SQL or build queries. Assume "Excel is the ceiling" for technical skill.
- **Access model (MVP):** All users can see all data. _Future extension:_ per-firm (tenant)
  isolation and per-user / per-client data permissions. The MVP architecture should keep this
  in mind so it can be added later without a rewrite.

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
- Ingesting structured tables (Excel), PDFs, and images (with OCR) into a searchable store.
- Uploading new PDF invoices / images — both extracted into the dataset and answerable immediately.
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
- **Format (MVP):** Text answers are sufficient. Tables and charts (e.g., trend visualizations)
  are deferred to a later version.
- **Accuracy over completeness — zero tolerance for confident wrong answers:** A confidently
  incorrect number is worse than no answer. When the data is missing, ambiguous, or the agent is
  not confident, it must **say so** ("I'm not sure" / "I couldn't find that" / ask a clarifying
  question) rather than guess or fabricate a figure.

**Behavioral principles:**
- Prefer abstaining or asking a clarifying question over guessing.
- Ground every quantitative claim in retrieved source data; do not infer numbers not present in
  the data.

## 7. Data Sources & Inputs

The agent must answer questions over a **mixed, growing corpus** of financial data:

- **Structured / table-like data:** Excel spreadsheets with clean rows and columns.
- **PDF documents:** e.g., invoices and other financial documents.
- **Images:** e.g., photos/scans of invoices.

**Volume & characteristics:**
- On the order of **thousands of tables**, each potentially **hundreds of rows and/or columns**.
- Data is **dropped in over time** — the corpus grows continuously; ingestion must be incremental.
- (Implication: the dataset is far too large to fit in a model's context window, so the system
  will rely on ingestion into a queryable store + retrieval, not stuffing raw files into prompts.
  Detailed approach in Section 8.)

**Handling uploads (PDF invoices / images):** Both behaviors are required —
1. **Extract & persist:** parse the document (OCR for images/PDFs), structure the extracted data,
   and add it to the searchable dataset for future questions.
2. **Answer in the moment:** the user can immediately ask questions about the document they just
   uploaded.

**MVP scope:** Single firm's data corpus.

## 8. Model & Technical Requirements
_TBD_

## 9. Guardrails, Safety & Failure Handling
_TBD_

## 10. Evaluation & Success Metrics (offline + online)
_TBD_

## 11. Cost, Operations & Lifecycle
_TBD_

## 12. Implementation Phases
_TBD_

## 13. Open Questions
_TBD_
