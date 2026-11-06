# Agentic development: how a selai sprint was specified, split and verified

This document reconstructs, **from evidence that exists in this repository only**, how selai was built with AI coding agents. Every claim cites a commit, a file or a test. Anything that cannot be backed by the repo is listed in [§6, Evidence gaps](#6-evidence-gaps-to-be-filled-in-by-teferi). That section is a to-do list for the author, not content.

> **About commit references.** This public repository is a clean snapshot with a single commit. The short hashes below (`3f58de9`, `4888cb8`, …) point to the original private history and are quoted as evidence; they will not resolve here. Business and sales documents from that history are deliberately not included in this copy.

## 0. What the git history can and cannot show

- The history starts on **2026-07-24** with `3f58de9 Initial commit`. Its message says: *"Sprints 1-5 completados segun README"*.
  - The original work began earlier: `CLAUDE.md` says *"Arrancado el 2026-04-23"*, and Alembic migrations `0001`–`0005` are dated 2026-04-23 → 2026-04-27.
  - Sprints 1–5 (the 100-item [`docs/ROADMAP.md`](ROADMAP.md) and the checklists in [`README.es.md`](README.es.md)) therefore **cannot be reconstructed commit by commit**. They arrive as a single import.
- What *is* fully traceable is the work after the import, from 2026-07-24 to 2026-09-02, in 89 commits on `main`. Three phases are visible:

| Phase | Dates | Commits | Agent attribution in the commit trailers |
|---|---|---|---|
| A. Backlog v2, **Oleada A and Oleada B** (wave A and wave B) | 07-25 → 07-26 | `8c95bae` → `1683814` | `Co-Authored-By: Claude Fable 5` (41 commits, 07-24 → 07-26) |
| B. **Post-audit plan**, Phases 0–5, then the re-audit "F0" and the `nXX` findings | 07-30 → 08-05 | `62384f9` → `9dbc276` | `Co-Authored-By: Claude Opus 5` (28 commits, 07-30 → 08-05) |
| C. Launch hardening | 08-30 → 09-02 | `1064483` → `fdbbc05` | No trailer. The work was merged from a `codex/…` branch. *Which agent did it is inferred from the branch name and has not been confirmed.* |

The case study below is **Phase A, Oleada A**. It is the only sprint for which the repo contains all three pieces: a written spec, a task split visible in commits and tests, and a recorded verification. Phase B is summarised in §5 because its verification is the strongest in the repo, but its spec (the audit report) is missing.

---

## 1. The specification

### 1.1 Standing instructions to the agent: `CLAUDE.md`

The repo's `CLAUDE.md` is the persistent brief that every Claude Code session loads. It fixes the stack and the architecture (*channel adapter → agent core → tools → DB*), and it sets hard rules that the code later enforces:

- *"Toda query a DB filtra por `agency_id`. Si no, es bug."* ("Every DB query filters by `agency_id`; otherwise it is a bug.") This became a structural test: a route without the membership guard fails the suite (`f424d52`).
- *"No introducir LangChain/LlamaIndex. SDK de Anthropic directo."* ("No LangChain or LlamaIndex; the Anthropic SDK directly.") Respected: `app/agent/runner.py` calls `client.messages.create` directly.
- *"Toda llamada al LLM se loguea en `agent_runs`…"* ("Every LLM call is logged in `agent_runs`.") Implemented in `runner.py`, with PII redaction.
- `docs/` is internal: *"no las generes sin pedirlo"* ("don't generate them unless asked").

### 1.2 The sprint spec: `docs/mejoras-ux-2.md` (commit `8c95bae`, 2026-07-25 20:37)

The header of the file states how it was produced:

> *"Generado el 2026-07-25 por 8 analistas (conversión web, panel diario, activación/canales, móvil, calidad IA, lifecycle/dinero, robustez, confianza/accesibilidad) leyendo el código real, + síntesis con deduplicación. Cada item cita el archivo que lo motiva."*
> (Generated on 2026-07-25 by 8 analysts reading the real code, plus a deduplicating synthesis. Each item cites the file that motivates it.)

Properties of this spec, all checkable in the file:

- **100 numbered items (151–250)**, triaged **P0 / P1 / P2**. There are 25 P0 items, defined as *"mueve dinero o desbloquea autoservicio ya"* ("moves money or unblocks self-service now").
- Each item has the format `N. [Priority] [Area] action — why it matters to the end customer _(código: file:line)_`. Example, item 151: *"Sumar durationSeconds del end-of-call de Vapi a Subscription.voice_minutes_used…"* ("add Vapi's end-of-call durationSeconds to the subscription's voice minutes"), followed by the reason, followed by `backend/app/api/voice.py (_on_call_end no toca Subscription); backend/app/services/plan_limits.py:85,123`.
- Because every item is anchored to a file and line, it can be verified, both by the implementing agent and by a reviewer.
- It is the second backlog. The first, [`docs/mejoras-ux.md`](mejoras-ux.md) (150 items, commit `ac5ea28`), was used the same way for the "wave-1 UX batch" (`f9513c1`: *"49 backlog items"*).

**Not in the repo:** the prompt Teferi wrote to commission the 8-analyst research, and the analysts' individual outputs. Only the synthesised result was committed (§6, item 1).

---

## 2. How the work was split into tasks

### 2.1 Scope: the P0 slice

Oleada A took the P0 items. Commit `1f9a03a` (*"21 P0 de la oleada A marcados en mejoras-ux-2"*, "21 P0 items of wave A marked in mejoras-ux-2") flips exactly these to ✅: **151–163, 165–169, 171, 172, 174**.

Four P0 items were deliberately left out of wave A:

- **164, 173 and 175** are cited by number in the Oleada B commit `457aee5`.
- **170** is still unchecked in the file. It was fixed by hand in `b719369` (*"switch production builds to Clerk pk_live"*), but the backlog was never updated.

### 2.2 Tracks: one per area, each tied to item numbers

The backend commit `4888cb8` (*"oleada A — real billing, complete WhatsApp channel, smarter agent"*) is organised into tracks, and each track names its items:

| Track | Items | New test modules (they cite the item numbers in their docstrings or comments) |
|---|---|---|
| Billing | 151–155, 172 | `test_voice_minutes` (151), `test_trial_expiry` (152), `test_billing_dunning` (153), `test_billing_webhook` (154), `test_billing_checkout` (154, 155), `test_billing_plans_sync` (155), `test_billing_worker_queries` (172), **`test_billing_adversarial`** |
| WhatsApp channel | 159, 160, 162, 171 | `test_whatsapp_webhook_config` (159, 160), `test_whatsapp_signature` (160), `test_whatsapp_audio` (162), `test_whatsapp_media_download`, `test_webchat_ratelimit` (171) |
| Agent | 161, 163, 165, 174 | `test_prompt_now` (161, 174), `test_seller_lead` (163), `test_hot_sticky` (165), **`test_agent_track_review`** (161, 163, 165, 174) |
| Panel APIs | 166–169 | `test_visits_listing` (167), `test_manual_reply` (168), `test_import_upsert` (169), **`test_panel_adversarial`** |
| Landing (web) | 156–158 | Frontend commit `195217b`. There are no automated tests; it was verified by `tsc` and `next build` (see §3). |

Both wave commits end with an **"Orchestrator integration"** section. It lists the cross-track glue that no single track owned. For `4888cb8`, that was: paused or cancelled subscriptions are dropped (fail-open) by the WhatsApp, e-mail and web-chat inbound paths; the demo agency is seeded on boot; and a shared `conftest.py` resets the in-memory rate limiter between tests. That points to a set-up where parallel agents each built one track and a coordinating step integrated them.

Later commits make the parallelism explicit:

- `f5b7535`: *"routers registered here rather than by four agents editing the same file in parallel. Migration chain verified linear — a branch would have broken `alembic upgrade head` on deploy."*
- `a7b2550`: *"Each track first checked whether the finding was still alive… the mobile track's adversarial review could not run (weekly agent limit); it was done by hand instead."*

### 2.3 Build, then attack: an adversarial pass per track

Every track has a companion `*_adversarial.py` or `*_review.py` module, whose docstring describes it as an attack on the feature just built. For example:

- `test_billing_adversarial.py`: *"Every test here started life as a way to break the shipped behaviour and is kept as the regression guard for the fix"*. The attacks: out-of-order Stripe deliveries in the same second, `payment_failed` retries flooding dunning, a paused agency whose agent keeps answering calls, minutes billed twice.
- `test_panel_adversarial.py`: answering another agency's conversation, a WhatsApp reply outside Meta's 24 h window, a "sent" id that is really a stub, an agenda grouped by the server's timezone instead of the agency's.
- `test_agent_track_review.py`: a machine with no tz database, a re-capture that erases known data, two alerts racing for one message.

Oleada B (`457aee5`) repeats the pattern (`test_agent_track_adversarial.py`, whose docstring says *"Every test here failed before the fix it documents"*, and `test_lifecycle_billing_adversarial.py`).

---

## 3. How it was verified

| Gate | Evidence |
|---|---|
| **Test suite grows with the feature** | It went from 259 tests (end of the previous phase, `a7ce9e2`) to **593 tests** (`4888cb8`: *"593 tests green, ruff clean"*). In that commit, test code outweighs product code by about 2.6 to 1: **7,691** inserted lines under `backend/tests/` against **2,936** elsewhere (`git show --numstat 4888cb8`). |
| **Lint** | `ruff clean` is stated in `4888cb8` and in every backend commit of the period. |
| **Frontend** | `195217b`: *"tsc + next build green."* |
| **CI** | `.github/workflows/ci.yml` existed since `02593e9` (2026-07-24) and ran ruff + pytest for the backend and typecheck + build for the dashboard on every push to `main`. A coverage gate (`--cov-fail-under=80`), `pip-audit`, `npm audit` and mobile checks were added later, in `90fb8b3`. |
| **Spec closed against the code** | `1f9a03a` marks the 21 delivered P0 items ✅ in the spec itself. Undelivered ones stay open (170 still is). |
| **Next wave** | Oleada B, `457aee5`: 31 items (`1683814`), **1,158 tests**, *"ruff clean, next build green"*. Test lines: 9,602 against 5,795 of code. |

The pattern went on after the sprint, which shows that verification was a habit and not a one-off:

- **Guards are tested by breaking the code on purpose.** `3430a9f`: *"Verificada devolviendo el texto a /ayuda: señala el fichero y la línea exactos, y pasa al quitarlo"* ("verified by putting the text back into /ayuda: it points to the exact file and line, and passes once removed"). `781f78b`: *"17 tests nuevos, verificados desactivando el arreglo: 4 fallan sin él"* ("17 new tests, verified by disabling the fix: 4 fail without it").
- **Cross-layer contract tests.** `e8d4e8d` introduced `tests/test_cross_layer_contracts.py`, which reads the dashboard's and the mobile app's TypeScript and fails when a shared constant drifts from the backend. It was written after a weekday off-by-one, 1–7 in the panel against 0–6 in the backend, shifted out-of-hours mode by a day.
- **Verification in production, not only in CI.**
  - `d6cafce` found that a pasted secret with trailing whitespace made every inbound webhook return 401: *"Se descubrió al verificar en producción con una petición firmada de verdad"* ("found while verifying in production with a really signed request").
  - `6d71054` found that AI e-mail replies died silently because of a placeholder sender. It was caught *"al verificar el canal de punta a punta en producción"* ("while verifying the channel end to end in production").

---

## 4. How the agent was guided (division of labour)

What the repo shows about the human's role:

- **Explicit split of responsibilities.** `docs/LANZAMIENTO.md` marks every step **[TÚ]** (*"solo puedes hacerlo tú (cuentas, claves, pagos)"*, "only you can do it: accounts, keys, payments") or **[YO]** (*"lo hace Claude cuando le avises"*, "Claude does it when you tell it"). Commits end with `[TU]` hand-backs when a step needs the human: `cd33242` (create a read-only Resend key), `f9a15a5` (set `EMAIL_LEADS_DOMAIN` in Railway, with the warning that the change applies to every agency at once), `14e19c7` (copy the Meta credentials to the worker service).
- **Secrets never pass through the agent.** The same guide says *"Las claves se pegan siempre en archivos `.env` locales o en el panel de variables… nunca en el chat ni en git"* ("keys always go into local `.env` files or the variables panel, never into the chat or git"). The audit of this repo found no committed secret.
- **Least privilege over convenience.** `cd33242` rejected widening a deployed Resend key to full access. It added a separate read-only key instead, with tests for the fallback.
- **Real-world feedback fed back in.** `c39b74b` fixes e-mail formatting *"reportado por el dueño desde un buzón real"* ("reported by the owner from a real mailbox"). `62384f9` fixes Anthropic 400s that *"Sentry kept reporting"* from real inboxes.
- **Honesty as an acceptance criterion.** Several commits remove claims the product could not back: `cb935ba` (*"removed fabricated social proof, fake features"*), `e8d4e8d` (*"stop telling the customer a channel works when it doesn't"*), `9a6fa58` (prices aligned with what Stripe actually charges).

---

## 5. Second example (partially evidenced): the post-audit plan

From 2026-07-31 to 2026-08-05, work followed an audit, in numbered phases.

| Phase | Commit | Theme | Tests at the end |
|---|---|---|---|
| 0 | `ec4c736` | Safety net: error screens, Sentry, in-product bug report | 1,259 |
| 1 | `6ca9aa8` | Make the advertised plans buyable, plan gating | 1,384 |
| 2 | `e8d4e8d` | Channels: stop showing a "connected" status for channels that don't work; cross-layer contract test | 1,524 |
| 3 | `fcffb64` | Real control over the AI, mobile takeover, keyset pagination | 1,746 |
| 4 | `f5b7535` | Team seats, SMS, Google Calendar, multi-office | 2,016 |
| 5 | `a7b2550` | *"the last 21 findings"* | 2,081 |
| Re-audit F0 | `dc955c5` | *"58 confirmados de 72 brutos, verificación adversarial individual"* ("58 confirmed out of 72 raw, each verified adversarially"). Findings n3, n15, n16, n7, n4, n35, n14 | 2,146 |
| Findings | `c39b74b` … `9dbc276` | n22, n19/n20/n36, n5/n6, n17, n21, n18, n33/n32, n26, n24, one commit per finding group | 2,172 |

Each `nXX` commit states the bug, why it mattered, the fix, and the test that proves it, often with "verified by reverting the fix". Examples are `525d0cb` (n18) and `dc955c5` (n7). **The audit report itself, with the 72 raw findings, the triage and the definitions of n1–n36, is not in the repo** (§6, item 3).

---

## 6. Evidence gaps (to be filled in by Teferi)

These are needed before this document can answer "the specification you wrote, how you guided the agent, how you verified the result" in the first person:

1. **The spec prompt you wrote** that produced `docs/mejoras-ux-2.md`: how you asked for the 8 analyst lenses, and the instruction to cite `file:line`. Only the synthesis is committed.
2. **The orchestration for Oleada A.**
   - How many agents ran, and how tracks were assigned (Claude Code subagents? separate sessions or worktrees?).
   - What "Orchestrator integration" concretely was.
   - Whether the adversarial pass was a separate agent with its own prompt.
   - Paste the prompt or template you used per track.
3. **The audit report behind Phases 0–5 and F0/nXX:** who or what produced the 72 findings, and the triage criteria.
4. **CI evidence:** links to the GitHub Actions runs for `4888cb8`, `195217b` and `457aee5`. They could not be fetched from this environment. The test counts above come from commit messages.
5. **Your review decisions:** examples of agent output you rejected or redirected, and what you checked by hand before each merge. The repo only shows accepted work.
6. **Pre-July history:** Sprints 1–5 (April → July) were squashed into `3f58de9`. If the earlier repo or the session logs exist, they would document those sprints.
7. **Tool attribution for Phase C** (08-30 → 09-02): confirm whether it was Codex, as the `codex/…` branch name suggests, and why those commits have no detailed messages.
8. **Framing:** the evidence shows *phases and waves* (Oleada A/B, post-audit Phases 0–5, launch). It does not show three sprints between July and September; the "Sprint 1–5" labels belong to the pre-import period. Use the repo's own names, or explain the mapping.
9. **Time and effort:** the wall-clock time per wave. Commit timestamps alone are 20:37 (spec) → 02:14 (Oleada A) → 03:17 (Oleada B), which suggests overlapping or parallel work that the repo cannot confirm.
