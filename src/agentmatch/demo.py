"""A reproducible fictional journey across evidence, matching and consent."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from html import escape
import json
from pathlib import Path
import random
from typing import Any

from .audit import json_text, run_manifest, write_json, write_jsonl
from .channels import ChannelProposal, acceptance_for, open_channel
from .claims import resolve_usable
from .coach import CoachRequest, enable_private_coach, private_draft
from .config import load_defaults, selectivity
from .consent import DisclosureGrant, claim_digest, project_for_peer
from .explain import render_pair_local
from .fixtures import load_corpus, verify
from .interview import (DimensionDefinition, InterviewQuestion, InterviewSession,
                        ReviewChoice, run_scenario as interview_scenario,
                        scenario_names as interview_names)
from .models import load_owner, time_text
from .ranking import evaluate_owner
from .scoring import assess_direction, assess_pair_offline
from .simulator import run_scenario, scenario_names
from .topics import (ApprovedTopicStatement, DisplayGrant, DisplayIdentity,
                     SharedIntroductionContext, SimulationError, grounded_bridges,
                     introduction_card, neutral_invitation, project_display)


def interview_journey(as_of: datetime) -> tuple[dict[str, Any], Any, Any]:
    corpus = load_corpus()
    lumen, original = corpus.owner("lumen"), corpus.owner("harbor")
    sparse = original.to_dict()
    for claim in sparse["claims"]:
        if claim["claim_id"] == "harbor-communication":
            claim["approval"] = "unreviewed"
    harbor = load_owner(sparse)
    before = assess_direction(lumen, harbor, as_of)
    definition = DimensionDefinition("communication.feedback", "Current contextual feedback preference", "communication")
    question = InterviewQuestion("feedback-gap", definition,
                                 "Tell me about feedback that helped you improve something.",
                                 "The selected project policy has no approved current feedback preference.", 2)
    session = InterviewSession("harbor", "collaboration", harbor.intent.version,
                               "synthetic-feedback-review", (question,))
    session.bind_baseline(harbor)
    session.next_question()
    proposal = session.respond(
        action="answer", text="In this fictional project I asked for direct comments on the draft and a concrete example.",
        annotated_value="direct", origin="model_inferred",
        uncertainty="One scripted example; approval does not independently verify it.",
        context="Fictional portable learning exhibit: feedback on draft work")
    session.stop()
    review = session.prepare_review()
    session.acknowledge_summary()
    unchanged = assess_direction(lumen, harbor, as_of)
    assert proposal is not None
    revision = session.commit_review([ReviewChoice(proposal.proposal_id, True, retain_source=True)])
    reviewed_harbor = revision.apply_to(harbor, as_of)
    after = assess_direction(lumen, reviewed_harbor, as_of)
    return ({"before_review": before.to_dict(), "after_summary_only": unchanged.to_dict(),
             "review": review, "reviewed_revision": revision.summary(), "after_exact_review": after.to_dict(),
             "session": session.audit_summary(), "manual_fixture_annotation": True}, lumen, reviewed_harbor)


def _permitted_goal(owner: Any, peer_id: str, as_of: datetime) -> ApprovedTopicStatement | None:
    claim = resolve_usable(owner, as_of).by_dimension("intent.problem_tags")
    if claim is None:
        return None
    grant = DisclosureGrant(f"synthetic-grant-{owner.owner_id}", claim.claim_id, claim.version,
                            claim_digest(claim), "collaboration", peer_id,
                            f"peer-goal-{owner.owner_id}", ("dimension", "value", "complete"),
                            ("current_goal",), as_of, as_of + timedelta(minutes=5),
                            f"scripted-exact-owner-approval-{owner.owner_id}")
    peer = project_for_peer(owner, grant, peer_id, "collaboration", as_of, question_id="current_goal")
    if not isinstance(peer.value, tuple) or not peer.value:
        return None
    tag = peer.value[0]
    title = tag.replace("-", " ")
    return ApprovedTopicStatement(owner.alias, claim.version, owner.owner_id, peer_id,
                                  "collaboration", tag, title,
                                  f"{owner.alias} currently wants to work on a {title}.", peer.expires_at)


def introduction_journey(left: Any, right: Any, as_of: datetime) -> dict[str, Any]:
    pair = assess_pair_offline(left, right, as_of)
    if not pair.qualifies:
        return {"state": "not_offered", "reason": "Both current owner policies must qualify."}
    statements = (_permitted_goal(left, right.owner_id, as_of),
                  _permitted_goal(right, left.owner_id, as_of))
    bridges = grounded_bridges((statements[0],) if statements[0] is not None else (),
                               (statements[1],) if statements[1] is not None else (),
                               left_id=left.owner_id, right_id=right.owner_id,
                               purpose="collaboration", as_of=as_of)
    display = []
    for owner, peer in ((left, right), (right, left)):
        identity = DisplayIdentity(owner.owner_id, owner.alias, 1)
        grant = DisplayGrant(owner.owner_id, peer.owner_id, "collaboration", 1,
                             identity.value_digest, as_of + timedelta(minutes=5))
        display.append(project_display(identity, grant, peer.owner_id, "collaboration", as_of))
    context = SharedIntroductionContext("synthetic-intro-context", 1, "collaboration",
                                         (left.owner_id, right.owner_id), tuple(display),
                                         bridges[0] if bridges else neutral_invitation())
    proposal = ChannelProposal("synthetic-intro", 1, context, as_of + timedelta(days=7),
                                as_of + timedelta(minutes=5))
    first = acceptance_for(proposal, left.owner_id, as_of + timedelta(minutes=5))
    try:
        open_channel(proposal, (first,), as_of=as_of, session_expires_at=as_of + timedelta(minutes=10))
    except SimulationError as error:
        first_only = str(error)
    else:
        raise RuntimeError("Single acceptance incorrectly opened a channel")
    second = acceptance_for(proposal, right.owner_id, as_of + timedelta(minutes=5))
    channel = open_channel(proposal, (first, second), as_of=as_of,
                           session_expires_at=as_of + timedelta(minutes=10))
    opened = channel.summary()
    queued = channel.queue_human_message(left.owner_id,
                                        "Could we test a fold-flat exhibit with one replaceable tactile panel?",
                                        "synthetic-human-message-1", as_of, explicit_human_send=True)
    before_connection = queued.to_dict()
    channel.connect((left.owner_id, right.owner_id), channel.membership_epoch, as_of)
    channel.deliver(queued.message.message_id, as_of, acknowledge=False)
    after_lost_ack = queued.to_dict()
    retry = channel.deliver(queued.message.message_id, as_of, acknowledge=True)
    enable_private_coach(channel, left.owner_id, explicit_owner_choice=True)
    draft = private_draft(channel, CoachRequest("synthetic-kickstart", left.owner_id, True, context.value_digest), as_of=as_of)
    return {"card": introduction_card(context), "one_acceptance_result": first_only,
            "opened_channel_before_first_human_message": opened,
            "queued_while_offline": before_connection, "after_lost_ack": after_lost_ack,
            "retry_receipt": retry, "private_draft": draft.to_dict(), "channel_after_retry": channel.summary(),
            "authority": "Every acceptance/send/coach action here is an explicitly scripted fictional event."}


def build_demo(*, as_of: datetime, seed: int = 1729) -> dict[str, Any]:
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("INVALID_SEED: use an unsigned 32-bit integer")
    corpus = load_corpus()
    population = list(corpus.owners)
    random.Random(seed).shuffle(population)  # Ranking must remain invariant to this ordering.
    lumen, harbor = corpus.owner("lumen"), corpus.owner("harbor")
    evaluation = evaluate_owner(lumen, population, as_of)
    pair = assess_pair_offline(lumen, harbor, as_of)
    policy_comparison = {preset: assess_pair_offline(lumen, harbor, as_of,
                                                    threshold_a=selectivity(preset),
                                                    threshold_b=selectivity(preset)).to_dict()
                         for preset in ("open", "selective", "close_fit")}
    interview, reviewed_left, reviewed_right = interview_journey(as_of)
    social = [run_scenario(name, as_of) for name in scenario_names()]
    interviews = [interview_scenario(name, as_of) for name in interview_names()]
    return {"manifest": run_manifest(as_of=time_text(as_of), seed=seed),
            "fixture_report": verify(), "evaluation": evaluation, "worked_pair": pair.to_dict(),
            "local_explanation": render_pair_local(pair), "selectivity_comparison": policy_comparison,
            "interview_journey": interview, "introduction_journey": introduction_journey(reviewed_left, reviewed_right, as_of),
            "social_scenarios": social, "interview_scenarios": interviews,
            "limits": ["Synthetic people and manually annotated conversations only.",
                       "No real interview, model, private-data ingestion, keys or network transport.",
                       "Match scores are policy calculations, not compatibility probabilities.",
                       "Memory-only queues do not establish encrypted persistence or crash recovery."]}


def write_demo(report: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "demo.json", report)
    write_json(output / "run_manifest.json", report["manifest"])
    write_json(output / "fixture_report.json", report["fixture_report"])
    write_jsonl(output / "decisions.jsonl", report["evaluation"]["assessments"])
    write_jsonl(output / "protocol_traces.jsonl", [s for s in report["social_scenarios"] if "trace" in s])
    write_jsonl(output / "social_traces.jsonl", report["social_scenarios"])
    write_jsonl(output / "interview_traces.jsonl", report["interview_scenarios"])
    (output / "demo.html").write_text(render_html(report), encoding="utf-8")


def render_html(report: dict[str, Any]) -> str:
    """Self-contained escaped report; no scripts, requests, fonts or telemetry."""
    def esc(value: Any) -> str:
        return escape(str(value), quote=True)
    def score(record: dict[str, str]) -> str:
        return esc(record["display"])
    pair = report["worked_pair"]
    rows = []
    for key, label in (("a_to_b", "Lumen → Harbor"), ("b_to_a", "Harbor → Lumen")):
        direction = pair[key]
        rows.append(f'<tr><th>{label}</th><td>{score(direction["preference_fit"])}</td>'
                    f'<td>{score(direction["supported_fit"])}</td><td>{score(direction["coverage"])}</td>'
                    f'<td>{esc(direction["status"].replace("_", " "))}</td></tr>')
    choices = []
    for preset, result in report["selectivity_comparison"].items():
        choices.append(f'<div class="policy"><span>{esc(preset.replace("_", " "))}</span>'
                       f'<strong>{score(result["a_to_b"]["threshold"])}</strong>'
                       f'<small>{"Both qualify" if result["qualified_pair"] else "One direction fails"}</small></div>')
    intro = report["introduction_journey"]
    if "card" in intro:
        topic = intro["card"]["context"]["topic"]
        intro_html = f'<h3>{esc(topic["title"].capitalize())}</h3><p>{esc(topic["opening_question"])}</p>'
        intro_html += '<ul>' + ''.join(f'<li>{esc(s)}</li>' for s in topic["statements"]) + '</ul>'
        intro_html += '<p class="muted">One supported thread. No shared intensity inferred. Opening requires both owners to approve this exact context.</p>'
    else:
        intro_html = f'<p>{esc(intro["reason"])}</p>'
    scenarios = report["social_scenarios"] + report["interview_scenarios"]
    scenario_html = ''.join(f'<details><summary>{esc(s.get("scenario_id", s.get("scenario", "Scenario"))).replace("_", " ").replace("-", " ")}</summary><pre>{esc(json_text(s))}</pre></details>' for s in scenarios)
    limits = ''.join(f'<li>{esc(line)}</li>' for line in report["limits"])
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>AgentMatch · R0 reference</title><style>
:root{{--ink:#172e36;--muted:#516870;--paper:#f5f3eb;--line:#d0d9d4;--accent:#17634d;--soft:#e1eee7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1080px;margin:auto;padding:48px 28px 80px}}a{{color:var(--accent)}}header{{padding:28px 0 40px;border-bottom:1px solid var(--line)}}
.eyebrow{{font:700 12px/1.5 monospace;letter-spacing:.13em;text-transform:uppercase;color:var(--accent)}}h1{{font-size:clamp(38px,7vw,72px);line-height:1.08;letter-spacing:-.045em;margin:20px 0}}h2{{font-size:28px;letter-spacing:-.025em;margin:0 0 16px}}h3{{font-size:23px;margin:8px 0}}p{{max-width:72ch}}.lead{{font-size:21px;color:var(--muted)}}
.badge{{display:inline-block;padding:5px 11px;background:var(--soft);border-radius:20px;font-weight:650;font-size:12px;margin-right:6px}}nav{{display:flex;flex-wrap:wrap;gap:20px;padding:20px 0;font-size:14px}}section{{padding:36px 0;border-bottom:1px solid var(--line)}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.stat,.card{{padding:24px;border:1px solid var(--line);border-radius:15px;background:#fffefa}}.stat strong{{display:block;font-size:32px;letter-spacing:-.03em}}.stat span,.muted{{color:var(--muted)}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:14px 10px;border-bottom:1px solid var(--line)}}th{{font-weight:650}}.scroll{{overflow:auto}}.policy{{border-left:3px solid var(--accent);padding-left:18px}}.policy span,.policy strong,.policy small{{display:block}}.policy strong{{font-size:30px}}.policy span{{text-transform:capitalize}}details{{background:#fffefa;border:1px solid var(--line);border-radius:10px;margin:10px 0}}summary{{cursor:pointer;padding:14px 18px;font-weight:650}}pre{{margin:0;overflow:auto;padding:20px;border-top:1px solid var(--line);font:12px/1.6 monospace;max-height:450px}}code{{font-family:monospace;background:var(--soft);padding:2px 6px;border-radius:4px}}.note{{padding:18px 22px;border-left:3px solid var(--accent);background:var(--soft)}}footer{{padding-top:28px;font-size:13px;color:var(--muted);overflow-wrap:anywhere}}@media(max-width:650px){{main{{padding:24px 18px 48px}}.grid{{grid-template-columns:1fr}}th,td{{padding:10px 7px}}}}
</style></head><body><main>
<header><div class="eyebrow">AgentMatch / reference release 0.1.0</div><h1>Useful connections.<br>Inspectable decisions.</h1>
<p class="lead">A working, offline test of the matching and consent rules. Every person and conversation in this report is fictional.</p>
<span class="badge">R0 · synthetic only</span><span class="badge">No model required</span><span class="badge">No server required</span></header>
<nav aria-label="Report sections"><a href="#matching">Matching</a><a href="#interview">Interview</a><a href="#introduction">Introduction</a><a href="#scenarios">Scenarios</a><a href="#scope">Release scope</a></nav>
<section><div class="grid"><div class="stat"><strong>40</strong><span>Fictional owners</span></div><div class="stat"><strong>200</strong><span>Annotated conversations</span></div><div class="stat"><strong>{report["evaluation"]["scanned_candidates"]}</strong><span>Candidates scanned for Lumen</span></div></div></section>
<section id="matching"><div class="eyebrow">01 / evidence before ranking</div><h2>Lumen &amp; Harbor</h2>
<p>Both want to build a portable learning exhibit. Their current contributions complement one another; their preferred working arrangements are not identical. The engine keeps those differences visible.</p>
<div class="scroll"><table><thead><tr><th>Direction</th><th>Preference F</th><th>Supported S</th><th>Coverage C</th><th>Decision</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="muted">F determines whether the owner's chosen preferences are met. S discounts evidence and ranks qualifying pairs. These numbers are not success probabilities.</p>
<div class="grid">{''.join(choices)}</div><p>At close fit, Lumen's direction passes exactly; Harbor's does not. Neither owner's threshold overrides the other.</p>
<details><summary>Inspect the exact rational values and atom contributions</summary><pre>{esc(json_text(pair))}</pre></details></section>
<section id="interview"><div class="eyebrow">02 / a specific review changes the snapshot</div><h2>A useful gap, one question.</h2>
<p>The scripted interview fills Harbor's missing feedback preference. Before review, Lumen's coverage is 0.9 and preference fit is 0.75. A summary acknowledgment changes neither. Approving the exact inferred claim brings coverage to 1 and preference fit to 0.85, while retaining its weaker evidence origin.</p>
<div class="note">Six questions by default. An explicit extension allows twenty in total. A refusal never becomes a personality label.</div>
<details><summary>Inspect the interview, review and matching change</summary><pre>{esc(json_text(report["interview_journey"]))}</pre></details></section>
<section id="introduction"><div class="eyebrow">03 / humans choose to open the channel</div><h2>Introduction context</h2><div class="card">{intro_html}</div>
<p>The context panel contains no pretend human messages. The simulation then queues an explicitly scripted human send, loses its acknowledgment, and retries the same ID without displaying it twice. A coaching suggestion remains a private draft.</p>
<details><summary>Inspect acceptance, queue and retry records</summary><pre>{esc(json_text(intro))}</pre></details></section>
<section id="scenarios"><div class="eyebrow">04 / failure paths are part of the product</div><h2>{len(scenarios)} reproducible scenarios</h2><p>These are inspectable in-memory traces. Their policy outcomes are verified by the separate test suite; they are not evidence of real encrypted transport.</p>{scenario_html}</section>
<section id="scope"><div class="eyebrow">05 / the next release has a separate gate</div><h2>What this build establishes</h2><p>Executable matching arithmetic, claim review, disclosure projections and consent state machines. The next release adds encrypted local storage and owner-device inference after its own validation.</p><ul>{limits}</ul>
<p>Run <code>python3 run_agentmatch.py verify --offline</code> from the source package. The flag selects the suite; the supplied Linux isolation harness separately proves socket denial.</p></section>
<footer>Frozen scenario clock: {esc(report["manifest"]["as_of"])} · Contract {esc(report["manifest"]["contract_version"])}<br>Package fingerprint: {esc(report["manifest"]["code_and_data_sha256"])}<br>Self-contained report. No external assets, scripts, tracking or requests.</footer>
</main></body></html>'''
