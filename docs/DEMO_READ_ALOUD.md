# NetCoop AI demo: read-aloud script

**Target 4:30. Read the quoted lines. Do the "DO" lines.**
Pause about 1 second after each result appears so viewers can read it.

**Before you start:** `uvicorn api.server:app` is running, the browser is at http://127.0.0.1:8000 and freshly reloaded, the README is open in another tab, and a terminal is open in the repo folder.

---

## 1 · 0:00 – 0:20 · README tab

**DO:** Slowly scroll over the architecture diagram.

> Hi, this is NetCoop AI.
> On a campus network, one fault shows up as many overlapping symptoms, in messy telemetry from different tools.
> NetCoop AI is a swarm of six agents. They talk over a documented JSON protocol and agree on one evidence-based report.
> It runs fully offline. The LLM is optional.

---

## 2 · 0:20 – 1:00 · Run the swarm

**DO:** Switch to the website. Click **Trigger Swarm Diagnostic Run**. Point at the agent cards and the message bus as they fill in.

> Let's diagnose a congestion scenario.
> First, the normalizer cleans and checks the input.
> Then three analysis agents (performance, infrastructure and connectivity) work in parallel.
> Every message they send appears here on the agent bus. Each one is a validated envelope: a task, a finding, a query or a response.
> Finally, the correlator weighs all the findings, and the reporter writes the report.

---

## 3 · 1:00 – 1:50 · The report

**DO:** Scroll down slowly: root cause → **Limitations** (click to open) → evidence tiles → telemetry table → hypothesis bars → actions.

> The result: congestion on the core router, CORE_RTR_01.
> Diagnosis confidence is 94 percent, high. Two of the three analysis agents agree.
> It's marked P1, critical, because the readings cross critical thresholds.
> Each evidence tile shows the value, its normal baseline, and the device it came from.
> This table shows how the raw fields were mapped to normalized metrics.
> And notice: the connectivity agent preferred a different answer, latency degradation. The correlator set it aside and explains why here in the limitations. It doesn't hide the disagreement.
> At the bottom are the recommended actions.

---

## 4 · 1:50 – 2:10 · Topology

**DO:** Click **Topology** in the top bar. Click the **CORE_RTR_01** node.

> The topology view marks the affected router and the devices upstream of it.
> Clicking a node shows its metrics.

---

## 5 · 2:10 – 2:55 · Different formats, broken input

**DO:** Click **Spawn Agent Run** (green button, top of the left sidebar). Under **Scenario**, pick **interop renamed fields units (Congestion)**. Click **Start diagnostic run**. Point at the **Format Normalization** chip, which reads "3 formats reconciled · 10 fields converted".

> Real networks don't all speak the same format.
> This file renames the fields and uses different units.
> The normalizer maps the names, converts the units (ten fields here), and still finds the congestion, at 97 percent.
> It also accepts older schema versions and the organizers' own format.

**DO:** Click **Spawn Agent Run** → pick **malformed json (Input rejected)** → **Start diagnostic run**. Point at "input rejected" and the red **Failed** normalizer card.

> And if the file is broken JSON, it's rejected with a clear reason, "invalid JSON telemetry", instead of crashing. The other agents stand down safely.

---

## 6 · 2:55 – 3:25 · Kill an agent

**DO:** Click **Spawn Agent Run** → pick **DNS failure (DNS failure)** → under **Fault injection** tick **infrastructure** → **Start diagnostic run**. Open **Limitations**.

> Now a resilience test. I'll make the infrastructure agent fail during this run.
> The orchestrator catches the failure and records it here as a limitation.
> The remaining agents still find the root cause: the DNS server, DNS_SRV_01, is down, at 97 percent confidence.

*(The tick clears itself after the run, so you don't need to untick it.)*

---

## 7 · 3:25 – 3:45 · Not enough data

**DO:** Click **Spawn Agent Run** → pick **insufficient data (Insufficient evidence)** → **Start diagnostic run**.

> What if there just isn't enough data?
> It doesn't guess. It says insufficient evidence, at 6 percent confidence, and tells you what to collect next.

---

## 8 · 3:45 – 4:10 · Simulated fix

**DO:** Click **Incidents** in the top bar. Click the **INC-T3-CONGESTION** row (not the INTEROP one). Scroll to the actions, then click **Apply Simulated Remediation**. In the dialog, click **Apply Simulated Remediation** again. Point at "Congestion → Normal — verified ✓".

> From the incident list, I reopen the congestion incident.
> The playbook action runs on a copy of the telemetry, and then the swarm diagnoses it again.
> Congestion becomes normal: verified.
> Nothing touches real equipment.

---

## 9 · 4:10 – 4:20 · Export

**DO:** Click **Export Root-Cause Report (JSON/PDF)** → **Print / Save PDF**. Show the preview, then click **Cancel**.

> The report exports as JSON, Markdown, or a clean PDF.

---

## 10 · 4:20 – 4:40 · Terminal

**DO:** Switch to the terminal. Type `python -m netcoop evaluate --no-llm` and press Enter. Wait for the 16/16 result.

> Finally, the evaluation: sixteen labelled scenarios, sixteen correct.
> The project has 524 automated tests and has been through four independent adversarial audits.
> Setup, the protocol, and the confidence formula are all in the README.
> Thank you.

---

**Running long?** Skip the node click in scene 4. In scene 5, skip the malformed json run and just say its line.
**Something went wrong?** Stop, then re-record just that scene and join the takes in the editor. A run never needs a page reload: open **Spawn Agent Run** and run it again.
