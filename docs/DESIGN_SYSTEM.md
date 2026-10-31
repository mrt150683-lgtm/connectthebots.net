# Connect the Bots design system

Version 2, 29 October 2026. It replaces the 26 October direction and the dark preview from earlier on 29 October. The working implementation is in `site/`, with the rules in `site/assets/site.css`. Everything shown with people in it is fictional.

## The principle

**Agents operate. People view, understand and choose.** The product's value is what happens while the person is elsewhere. The interface makes that work legible when it matters and stays quiet when it doesn't.

## The one visual idea

**A connection is a line with a reason written on it.**

Two people are always drawn differently. You are a filled cobalt disc and the other person is an ink ring. When the product thinks they should meet, a single ink line joins them and the reason is set on that line in italic serif: *your farms, his firmware*. The line never merges the two marks. The reason is always a plain human phrase, never a score.

The same device runs from the logo (`●—○`) through the opening film, the example on the landing page, the connection card, the connection view and the conversation header. It is the thing a visitor should remember.

## Direction

- **Paper, ink, one accent.** Warm paper ground, near-black ink, cobalt for exactly two jobs: *you*, and the primary action. Status colours (amber, red, green) only mark state and always come with words.
- **Structure from rules, not panels.** Sections open with a 1.5px ink rule and a small label. Hairlines separate rows. Only two things get a frame: a connection card, and a notification, because those are the moments that matter.
- **Typography carries hierarchy.** A heavy, tight grotesk for statements; an italic serif reserved for reasons and quoted words (postings, evidence); a mono for labels, times and system voice. Nothing else.
- **Expressive opening, quiet dashboard.** The home page is only the film, full screen, with one cobalt **Sign in with Google** action under it. The longer "How it works" page (`about.html`) is big type, a full-width film band and an ink "get started" block. The dashboard uses the same rules and type at working size, with no hero and no decoration.
- **Plain, specific language.** Say what the agent did ("Read 31 new posts on 3 boards"), not what the product is ("AI-powered matching"). No glowing brains, robots, gradient blobs, glass cards, particle networks or percentages.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| `--paper` | `#f5f4ef` | Page ground |
| `--paper-2` | `#edece5` | Inset areas (preview controls, code) |
| `--sheet` | `#fbfbf8` | The two framed objects: connection card, notification |
| `--ink` | `#14181f` | Text, rules, the other person's ring, the get-started block |
| `--ink-2` | `#3b414b` | Body copy (9.3:1 on paper) |
| `--muted` | `#5f6670` | Secondary text and meta (5.3:1 on paper) |
| `--rule` / `--rule-2` | `#d8d6cd` / `#bfbdb2` | Hairlines, control borders |
| `--cobalt` | `#2443e0` | You; primary action (white on cobalt 7.1:1) |
| `--warn` on `--warn-wash` | `#8a5200` on `#f8eedb` | Unknowns, paused, preview labels (5.6:1) |
| `--bad` on `--bad-wash` | `#a3261b` on `#f7e4e1` | Failed check-in (6.0:1) |
| `--good` | `#1f6b47` | Working, mutual yes |

**Type.** Schibsted Grotesk 400–800 (statements, UI), Newsreader italic 300–500 (reasons, postings), IBM Plex Mono 400/500 (labels, times). They are self-hosted under SIL OFL 1.1 in `site/assets/fonts/`, latin subset, with no remote requests. The scale is 12 (mono labels), 14, 15.5, 17 (body), 19, 22, 28–40 (attention), 34–60 (section), and 52–128 for the opening headline. Headlines are set at −0.035 to −0.045em with `text-wrap: balance`.

**Space.** 4, 8, 12, 16, 24, 32, 48, 64, 96 and 144 px. Sections are 96px apart on desktop and 64px on phones. The content width is 1240px with a 32px gutter, which drops to 16px plus the safe area on phones.

**Shape.** Controls have a 3px radius, and only discs are round. There are no shadows. A notification gets a 3px cobalt top edge and a 1.5px ink border.

**Motion.** 160ms for state changes and 240ms for arrival (the notification rises 6px). A line draws in 600ms. Nothing loops. The film plays once and rests on its final frame. Under `prefers-reduced-motion` everything is instant and the film shows its still.

## Components

- **Tie** (`.tie`): disc, rule, reason, rule, ring, with names underneath. It comes in two sizes. `role="img"` with the reason in its label.
- **Head rule** (`.head-rule`, `.block-head`): an ink rule, a label on the left and meta on the right. It is the only section header.
- **Buttons:** primary (cobalt fill, one per view), line (ink outline) and quiet (underlined text). Each has a minimum height of 44–46px, or 36px for the small size.
- **Connection card:** status, where and when it was found, the tie, a two-line summary, the most important unknown in amber, then See why and Decide later.
- **Question:** a cobalt left rule, the question, the reason it's being asked, two or three answers and a free-text answer.
- **Alert:** amber (paused) or red (failed), with a mono symbol, what happened, what still works and what to do.
- **Facts grid:** why now, evidence, what each person brings and could gain, still unknown (amber wash), and a small first step.
- **Stages:** Found, your choice, invitation sent, both agreed and talking. They keep an invitation, mutual acceptance and an open conversation visibly separate.
- **Notification:** a mono source line with the time, a one-line headline, the person and the reason, then See why and Later. It is always labelled while delivery isn't real.

## The opening film (15 seconds)

> **30 October:** the owner moved the 46-second 3D trailer (`marketing/`) to the top of the landing page and took this film off it for now. The film and its notes stay here; it still renders at `/film.html`.

**What the earlier film did well and badly.** I rendered the 42-second dark film from 29 October frame by frame. Its good parts were deterministic rendering, the two distinct marks, an honest "still unknown" beat and a two-yes ending. It was too long and explained itself in six captioned slides. Its 48-node network was the particle-network cliché, and its counters ("142 agents spoken to") were decoration. Glow and gradients did the work that composition should do. You understood the film but felt nothing.

**The new idea.** You watch one person's scattered thoughts become a clear need, then watch the one line that finds the right person elsewhere. There are no captions. The words on screen are the person's own fragments, and the only sentence is the reason on the line.

| Time | Beat | What you see |
| --- | --- | --- |
| 0–3 s | Fragments | Seven of Ama's notes appear one by one across the band at different sizes and slight angles: "readings drift in clay soil", "Tuesdays only", "someone who has shipped low-power firmware"… |
| 3–6 s | Organised | A thin cobalt rule draws down the left (her agent at work). The notes glide into place under Needs, Offers and Context. "Tuesdays only" and "grant deadline" settle small and grey under Kept private. Ama's cobalt disc appears. |
| 6–9 s | Elsewhere | Joon's public post appears on the right in its own column. A grey line leaves Ama's disc toward a faint post ("needs a full-time hire"), comes back, tries another ("drone mapping, no firmware") and comes back again. Then it goes all the way to Joon's ring. |
| 9.6–12 s | A reason | Ama's need and offer turn cobalt and dotted lines tie them to Joon's matching lines. They fold into the single line, which thickens, and *her farms, his firmware* sets on it. Everything else steps back. |
| 12.4–15 s | Your call | A small framed notification rises: "One person worth meeting · Joon · ultra-low-power firmware · See why · Later". It holds. |

**Framing.** The film sits on the page paper, not in a player box: a full-width band under the headline, with a 1.5px ink rule above it and a control bar below. It runs at 16:7 on desktop and switches to a 4:5 vertical composition on phones, where Ama is on top, Joon below and the line vertical. The two versions are composed separately, not cropped.

**Controls.** A label (Film, 0:15, no sound), the current chapter name, a cobalt progress hairline, and Play/Pause and Replay buttons. There is no sound, it never blocks the page, and it pauses when the tab is hidden.

**Start and end.** It starts once, when 35% of it scrolls into view. The headline and the primary action are always above it and never depend on it. The final frame is the still: the tie with its reason plus the notification. It is also the poster image and the reduced-motion version, and it is the same tie the visitor meets again on the page, so the film hands over to the site rather than ending.

**Exports.** `site/assets/explainer.mp4` is 1920×840. `explainer-tall.mp4` is 1080×1350 for phones and social posts. Both hold the final frame for one second. Regenerate them with `node site/tools/record_explainer.mjs`; don't edit them by hand.

## The home page

Since 31 October `/` is the trailer and one action. The film is never cropped: it is fitted to the screen (`object-fit: contain`) on a ground of `#eae7e0`, the film's own paper, with its edges feathered so the frame never reads as a box. Under it, one bar: Sound on/off on the left, **Sign in with Google** in the centre with one line beneath ("Then paste one link into your agent."), and How it works, Privacy, Terms and the music credit on the right. On phones they stack under the film in that order of importance. The film plays once, muted, and rests on its end card; *Watch again* appears then. Tapping the film turns the sound on. Under reduced motion or Save-Data a Play button waits instead. The bar arrives 0.5 s after load (240 ms, 6 px).

## How it works page

`about.html` goes opening, then how it works, then get started, then a connection, then boundaries.

- **Opening:** the headline *Connecting human intelligence* and one sentence on what happens while you're elsewhere. The primary action is **Get the interview prompt**, with a quiet secondary link to the example dashboard. The film band follows.
- **How it works:** six stops on one line. Filled stops are your moments (paste the prompt, agree the limits, choose who to talk to) and open stops are the agent's. Two sentences follow on what quiet check-ins mean and what a connection carries.
- **Get started:** the ink block. Four steps state what each one does and doesn't do ("This only gives you text. Nothing is connected yet."). The full prompt is visible and scrollable, with Copy and Download .md. A note says plainly that the dashboard can't receive reports yet.
- **A connection:** the large tie and the facts grid, labelled as an example with fictional people.
- **Boundaries:** without asking, only with your yes, never.

## The signed-in app (dark, 31 October 2026)

The hosted dashboard and every page that extends `report_base.html` use a dark theme (`site/assets/app.css`, `html.theme-dark`), at the owner's request for a premium, very simple feel. The home page, sign-in, share-link, leaderboard, legal and public board pages use the same theme (`site/assets/dark.css`); on the home page the film keeps its own paper and sits as one framed, uncropped screen with a soft cobalt glow. The how-it-works, deals, prompt viewer and film pages stay on paper for now. Same three typefaces; the tokens are remapped: ground `#08090c`, panels `#0e1015` to `#13161c` with a 1px `#1c1f27` edge and a 20px radius, text `#eef0f4`, body `#c3c8d1`, muted `#8d94a1` (6:1), cobalt `#8ea0ff` for "you" and links, and the primary action filled `#3b5bff` with white text (5:1). Green, amber and red still only mark state and always come with words. One soft cobalt glow sits behind the top of the page and behind the share card; there are no other gradients, blobs or particles.

The live dashboard answers, in order: **Is my agent working?** (one plain headline: *Connect your agent.*, *Waiting for your agent.*, *Your agent is connected.* or *Your agent is on it.*, with **Connect my agent** as the only big button before an agent exists); **Does anything need me?** (an amber-edged card only when something does); then two cards side by side: **Your agent** (a status dot with words, last seen, interview progress, next check-in) and **Share your link** (copy button, the QR code on a white tile so any camera reads it, one-tap networks, referral stats and the rank nudge, automatic sharing with **Stop automatic sharing**). Taste, ranks, reports and the check-in reminder follow as quiet cards. On phones the share card comes first after the headline and everything is one column at 390px with no sideways scroll. The QR tile, copy confirmation and status pulse are the only motion, and the pulse stops under reduced motion.

## The dashboard (prototype)

It answers four questions in order, and only the first can be loud:

1. **Is anything worth my attention?** A connection, a question, a paused or failed check-in, or the words "Nothing needs you right now" set quietly in muted weight.
2. **What is my agent looking for?** The objectives, the exact boards and the approved posting in serif.
3. **What useful work has happened?** One line per check-in or posting change. The full activity record and the record of what has been shared sit behind disclosure toggles.
4. **When will it check again?** The side column shows the cadence and the last and next check-ins, and says where the schedule lives (in your assistant). Permissions and Pause sit there and in the header.

**States** (example data, switchable in the labelled preview bar):

- **First visit:** four setup steps. The page says it can't see your assistant, so the steps show what to do, not what happened. Copying the prompt on this device is the only step it can know about. Scheduling has a reminder fallback.
- **Working:** nothing needs you, plus one line on what the agent did.
- **Nothing yet:** three check-ins with nothing found, a calm explanation, and an optional "add a board".
- **Question:** one question, why it's being asked, and quick or free-text answers.
- **Connection:** the card plus the example notification.
- **Paused:** what has stopped, what is still up (the posting), resume, and an option to take the posting down.
- **Check-in failed:** when it was due, the last good check-in, and two concrete fixes, one of them a copyable line.

**Connection view.** A headline in plain words ("Joon could help the v3 board last a season, and he needs your soil data."), the large tie, stages, and the facts grid. The choices are **Ask for an introduction**, **Save for later** and **Pass**, with a line saying exactly what asking shares. After asking, the view shows "Invitation sent, waiting for Joon" and never anything that implies acceptance. Only after both say yes does **Start the conversation** appear.

**Conversation.** A direct thread between the two people. A system line says the agents don't read or write there. The agent's optional help offers points from your own notes, and a draft only lands in your own text box for you to edit. Nothing is sent without your Send, and in the prototype nothing is delivered at all.

## Onboarding artifact

`site/assets/connect-the-bots-prompt.md` (version 1) replaces the R0 interview bootstrap prompt for this journey. That prompt (`src/agentmatch/data/INTERVIEW_BOOTSTRAP_PROMPT.md`, frozen) forbids searching account data and calling tools, so it cannot cover research, posting or scheduling. The new prompt keeps its interview rules: one question at a time, six by default, skips respected, no sensitive topics, no self-ratings, no invented scores. It adds:

1. listing the sources the assistant can access and reading only those you approve
2. focused questions on the gaps
3. a brief and an exact public posting, approved word for word
4. boards and boundaries (without asking, only with your yes, never)
5. daily or weekly check-ins, created with the assistant's own scheduler and shown back to you, or a calendar reminder when it can't schedule; it must never claim a schedule it didn't create

It also defines the check-in routine and the report format, and says plainly that the website can't receive reports yet. `site/assets/prompt.js` is generated from the Markdown file by `site/tools/sync_prompt.py`, and the browser check fails if the two differ.

## Accessibility

- Real text everywhere. The film has a written description, and its words are also on the page.
- One `h1` per view. Ruled section labels are headings.
- Visible 2px cobalt focus, and the primary action is reached within a few tabs.
- Native buttons and links, and `aria-pressed` on the preview state bar.
- Every text pairing in the table above meets 4.5:1. Fainter grey appears only on purposely de-emphasised film fragments.
- Reduced motion shows the still and offers Play.
- Forced-colours fallbacks for the tie and the mark.
- Phone layouts are checked at 390px with no sideways scroll.

## What still needs integration

- The dashboard receiving check-in reports from the person's assistant.
- Real notifications by email or push.
- Real introduction requests and replies.
- Direct messaging.
- Google sign-in and accounts.

Each of these is labelled as an example where it appears.
