# The fictional R0 corpus

The bundle contains **40 wholly fictional owners, 200 conversations and 800 messages**. Every owner has five conversations, each with two agent questions and two owner answers. These are authored software fixtures, not anonymised real people or people available for introduction. No public archive, user history, private memory or employer material supplied these records.

`src/agentmatch/data/synthetic/corpus.json` contains the source conversations and separately authored typed claims. The program does not infer the claims from those conversations. Each claim cites an owner-authored source message; the loader rejects an agent utterance, a quoted statement, another owner's message or a missing source as direct evidence. This validates source linkage, not the psychological truth of the claim.

`manifest.json` records the counts, version, provenance and SHA-256 digests of the two data files. Hashes detect a mismatched package; they are not signatures or a claim of trusted authorship. `expected_outcomes.json` is a separate set of 22 hand-authored oracle records. It is never matching input. Several records describe explicitly stated scenario mutations or independent component fixtures, rather than expected results on an unchanged base pair. `fixtures verify` validates those records and references. `fixtures.run_oracles(as_of)` actually executes all 22 authored numerical, lifecycle, permission and ranking expectations; the unit suite calls it and fails on any discrepancy. No expectation is generated from or rewritten by the scorer.

## Why these examples exist

The main worked pair is **Lumen and Harbor**, building a portable learning exhibit. Their value is complementary: Harbor offers fabrication, copyediting and accessibility review; Lumen offers budgeting. Both want the same project, but they do not meet all of each other's wishes. The independent arithmetic is:

| Direction | Preference fit F | Supported fit S | Coverage C |
| --- | ---: | ---: | ---: |
| Lumen → Harbor | 0.85 | 0.82 | 1 |
| Harbor → Lumen | 0.80 | 0.77 | 1 |

The lower supported fit is 0.77. It is an offline ranking value, not a probability of a successful introduction. Their communication claims remain approved contextual interpretations with a 0.7 evidence factor. General approval does not turn those interpretations into verified facts.

Other pairs explore concrete projects rather than generic shared interests:

| Fictional pair | Project and useful first question |
| --- | --- |
| Aerolith / Bramble | Lichen microhabitats: how can a map distinguish an unobserved wall from an empty habitat? |
| Cairn / Delta | Museum sound captions: what should a caption convey about a rhythm with no words? |
| Ember / Fennel | Fermentation notes: which daily observations remain useful without making a burdensome form? |
| Gossamer / Helix | Repairable textile patterns: how can the repair route be visible before the garment exists? |
| Indigo / Juniper | Rainwater observation kits: which simple measurement can a beginner repeat? |
| Kestrel / Lattice | Tactile pathway maps: how can route features remain separate from an individual's experience? |
| Mosaic / Northstar | Decimal notation: how can a workshop distinguish finite `0.999` from repeating `0.999…`? |
| Opal / Prism | Bird observation cards: how can an uncertain sighting remain useful? |
| Quartz / Rivet | Repair instructions: which step wrongly assumes the fault has already been found? |
| Saffron / Thistle | Pantry labels: which distinction survives an ingredient substitution? |
| Umber / Vesper | Oral-history indexing: how can an editor's inference remain separate from a speaker's recollection? |
| Willow / Xenon | Sonification: what should a missing observation sound like? |
| Yarrow / Zephyr | Seed-library records: how can a growing observation remain distinct from a theory about it? |
| Aster / Birch | Transit transfers: what changes the difficulty of a transfer without changing total minutes? |
| Cobalt / Dune | Language practice: how can a useful clarification count as success? |
| Estuary / Flint | Ceramic sample records: which missing setup detail makes two samples incomparable? |
| Grove / Hazel | Raised-symbol tiles: how could someone discover the rule through touch and a short prompt? |
| Islet / Jade | Sensor exhibits: what should the demo say about things its sensor cannot observe? |
| Kelp / Loam | Shoreline notebooks: how does repeated observation differ from repeated transcription? |

These are topic hypotheses supported by manually supplied project statements. They do not assert that an agent discovered scientifically validated human compatibility. A shared project can supply a grounded starter; the system does not invent three extra starters to fill a card.

## Built-in difficult records

Willow's optional evidence is unreviewed; Xenon's intent is expired; Yarrow's goal has an unresolved contradiction; Zephyr has an incomplete offer set; Aster requires paid work while the partner requires volunteer work; Dune's hard availability is unreviewed; Estuary's communication claim is revoked; Flint has no capacity; Grove has a deliberately disabled sensitive canary; Hazel blocks Grove; Islet already has a pending offer to Jade; Jade's rhythm claim has expired; and Kelp and Loam offer complete empty contribution sets. These choices are authored negative cases, not personal judgments.

The sensitive canary is the literal fictional marker `SYNTHETIC_DISABLED_SENSITIVE_CANARY`. It has no matching or disclosure permission. It exists to test omission before any eventual encryption; finding it in an outbound record is a defect. Source text and source IDs are local-only inputs, not peer-visible card fields.

## Loader boundary and extension

The R0 CLI accepts only this packaged, versioned synthetic corpus. It exposes no arbitrary transcript path, user import or live-person onboarding. Files are bounded at 2 MB, decoded without duplicate JSON keys or nonfinite numbers, checked against exact owner and conversation counts, and validated before scoring. Manifest file names are an exact allowlist rather than filesystem paths supplied by a fixture.

A developer can fork the source, author a new fictional corpus, update its manifest and add independent expected outcomes. That is a new fixture release, not a way to label real histories as safe inputs. Real history requires the later encrypted owner workflow and its separate storage, review, inference and permission gates.

Run `agentmatch fixtures verify` for structural checks and `agentmatch fixtures list` to inspect available owner and intent IDs. The default pair is `lumen` / `harbor`; their intent IDs are `lumen-collaboration` / `harbor-collaboration`.
