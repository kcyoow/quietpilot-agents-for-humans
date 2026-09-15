# Project Scope

## Project Name Candidates

- QuietPilot — confirmed working name

## One-Line Summary

QuietPilot is a mobile-first Strands lifestyle agent that discovers useful routines from connected capabilities and signals, turns both passive events and direct requests into Cases, executes authorized work through real integrations, and surfaces only necessary approvals and auditable results.

## Target User

The initial audience is a broad everyday user who already relies on several digital services or connected devices but does not want to manually build and babysit automations. Students, developers, independent professionals, and busy households are useful example personas, but the product is not limited to any one occupation.

## Problem

People lose time not only by doing repetitive work, but also by noticing it, deciding which app owns it, translating it into steps, wiring automations, and repeatedly checking whether those automations worked. Existing automation builders usually make the user define triggers and actions up front, while general chat agents often require the user to remember to ask and then manage a long conversation.

QuietPilot should absorb that coordination burden. It should inspect only authorized capabilities and signals, propose bounded routines and Cases, explain the evidence and permissions involved, execute within visible policy, and interrupt the user only when a meaningful decision is required.

## Core Workflow

1. The user signs in and connects selected services or devices with explicit scopes.
2. QuietPilot inventories API-visible signals, actions, current states, and user policies. It does not pretend that mobile-only or unavailable resources are connected.
3. Connector specialists inspect new signals in the background. A direct user instruction enters the same pipeline as an email, calendar, location, or device event.
4. The Strands orchestrator groups related evidence into a Case, delegates focused analysis to specialist agents, and privately builds the detailed task graph.
5. QuietPilot proposes several outcome-oriented routines or actions grounded in the capabilities actually found. It does not start from a hard-coded routine catalog.
6. QuietPilot uses structured approval, correction, hide, suppression, stop, revocation, and verified-result history to improve which Candidates it discovers and ranks first. This preference learning cannot create or widen execution authority.
7. The user selects a Case or routine and chooses one-time, standing, or conditional authorization where the risk model permits it.
8. Safe and reversible actions execute through real integrations. Contextual external actions require the applicable policy; high-risk or materially irreversible actions always require approval.
9. Every action exposes reason, evidence, requested scope, side effects, reversibility, current status, and the way to revoke permission.
10. The user monitors a concise activity timeline and receives a notification only for approval, failure, material change, or completion.

## Minimum Case Portfolio

A **Case** is one bounded unit of outcome-oriented work with evidence, a plan, policy state, execution state, and an audit trail. A **Routine** is a reusable trigger, condition, or permission rule that may create or advance Cases. A Routine suggestion is never silently activated; accepting it is a separate user decision.

The first product must support four Case ingress patterns through the same lifecycle and UI:

1. **Connected-Signal Case** — created when one or more authorized connectors reveal related events that require an outcome. Examples include an email and calendar item that together imply a reservation, deadline, schedule conflict, return window, or preparation task. The agent groups evidence before proposing action.
2. **Direct-Delegation Case** — created when the user asks QuietPilot to investigate, prepare, organize, or carry out an outcome. The orchestrator gathers relevant connected context, privately decomposes the work, and returns one bounded plan rather than a chat transcript full of microtasks.
3. **Routine-Discovery Case** — created when available capabilities, current states, repeated behavior, or platform observations suggest a reusable automation. The result is an explainable Routine proposal with trigger, conditions, actions, risk, and revocation controls; it is not enabled until the user accepts it.
4. **Exception-And-Approval Case** — created or promoted when execution encounters ambiguity, insufficient permission, conflicting evidence, failure, material change, or a high-risk action. It compresses the unresolved decisions, shows what has already been completed safely, and resumes or stops only after the applicable user decision.

These are product contracts, not four hard-coded scenarios. Specific examples must be generated from the user's real connected capabilities and signals.

## What We Are Building

### Submission Core

- A mobile-first, card-based Case inbox with secondary chat for direct instructions and follow-up.
- A Strands orchestrator using focused agents as tools for connector inspection, Case planning, policy evaluation, and execution monitoring.
- One shared Case model for passive connector events and direct user requests.
- Four minimum Case ingress patterns: Connected Signal, Direct Delegation, Routine Discovery, and Exception/Approval.
- Capability-driven routine discovery that produces multiple grounded suggestions rather than one preselected routine.
- Approval-based discovery learning that improves Candidate retrieval, grouping, and ranking from structured feedback while keeping authorization in the separate policy engine.
- An inspectable policy model with one-time, standing, and conditional authorization plus permanent approval gates for high-risk work.
- An explanation and audit surface for proposed, running, completed, failed, and revoked work.
- Real Gmail ingestion and real Google Calendar or Google Tasks actions for a reversible digital-work path.
- Real SmartThings capability and state discovery for the API-visible Samsung air conditioners.
- A separately approved SmartThings command test during the build/demo, followed by state readback; no device command is considered complete merely because the API accepted it.
- Android or web push notifications for decisions and completion events.

### Expansion After The Core Loop Works

- Privacy-first Android message discovery: inspect only user-authorized SMS input, classify spam/smishing and uncertainty on-device where possible, extract unambiguous future events, and create cancellable local reminders automatically only under a previously approved narrow policy. Full historical SMS access remains a separate Android/Google Play permission spike, not an assumed capability.
- Google Drive/Docs artifacts for durable plans and briefs.
- Android geofencing as an additional context signal.
- More connector specialists that reuse the same Case, policy, and audit contracts.
- AgentCore deployment and observability if it does not delay the working end-to-end path.

## What We Are Not Building

- An unrestricted agent with hidden or silently expanding authority.
- A generic chatbot that requires the user to manually drive every step.
- A visual mock presented as proof of a real integration.
- A hard-coded collection of travel, homecoming, or bedtime routines presented as agent discovery.
- A claim that all SmartThings mobile registrations or mobile-app routines are available through the public API.
- Support for every IoT vendor, email provider, productivity suite, or device category in the first submission.
- Automatic payments, purchases, destructive deletion, sensitive-data transfer, or final submissions without explicit approval.
- Treating one model's `not spam` result as authority to act, uploading complete message history for training by default, or silently requesting restricted SMS permissions.
- Production-scale multi-tenant security, billing, organization administration, or a full consumer launch.
- More connector count at the cost of one verified end-to-end execution path.

## Inspiration And References

- SmartThings demonstrates capability-based device control, status, and existing household automations.
- IFTTT and shortcut builders demonstrate transparent trigger-action composition, but QuietPilot should reduce manual configuration by proposing grounded routines.
- General personal agents demonstrate the value of free-form delegation, but QuietPilot routes direct requests through the same visible Case and policy system as background signals.
- The product experience should feel like a calm control room: card-first, dark-mode capable, friendly in tone, and detailed only on demand.

## Demo Path

The demo is chosen from routines produced by the live discovery system, not permanently encoded into the product.

1. Sign in and connect the demo services with narrow scopes.
2. Show QuietPilot inventorying real available capabilities. For SmartThings, show that the public API exposes two online Samsung air conditioners while mobile-only personal devices remain honestly excluded.
3. Show at least one Connected-Signal Case and one Direct-Delegation Case becoming the same Case structure.
4. Show a Routine-Discovery Case producing several grounded candidates from actual capabilities, current context, and policy.
5. Select one candidate. Execute real reversible Google actions such as creating a Calendar event or Tasks checklist.
6. For the IoT branch, promote the work into an Exception-And-Approval Case, show the exact proposed command, and request explicit one-time approval. Execute only after separate demo-time consent, then verify the resulting state rather than treating `ACCEPTED` as completion.
7. Show the activity timeline, explanation fields, notification, failure/retry boundary, resume/stop choice, and permission revocation path.

## Submission Story

QuietPilot is not another app people must manage and not another chatbot they must repeatedly prompt. It is a supervised lifestyle agent that first learns what it can safely observe and do, then discovers useful routines, absorbs detailed coordination, and compresses human involvement into a few explainable decisions.

The technical story is a genuine Strands multi-agent implementation: an orchestrator routes capability inspection, planning, policy, and execution monitoring to focused specialist agents. The proof uses real connected services and devices, while clearly labeling the boundary between SmartThings mobile UI registrations and the smaller public API surface verified during development.

## Delivery Gates

The project is milestone-based rather than tied to a fixed seven-day promise.

1. **Discovery gate:** real connector authentication, capability inventory, direct-command ingestion, and at least one grounded example of each of the four Case ingress patterns.
2. **Safe execution gate:** at least one real reversible Google action with audit and notification.
3. **Supervised device gate:** explicit approval, one real SmartThings command, and post-command state confirmation.
4. **Product gate:** coherent mobile-first cards, secondary chat, policy controls, error states, and a repeatable demo script.
5. **Submission gate:** public repository, setup instructions, architecture diagram, working video, and honest testing notes.

Only one Case must prove the complete end-to-end execution loop before expansion, but the demo must visibly exercise all four ingress patterns and must not present them as unrelated mock screens.

The Scope stage is complete when these gates and exclusions are accepted as the boundary for the downstream PRD. They do not claim that every candidate connector or expansion feature has already been implemented.
