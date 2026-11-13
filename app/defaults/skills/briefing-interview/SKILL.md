---
name: briefing-interview
description: Interview the user one question at a time (with ask_user) until the brief for a plan or a big deliverable is clear, then summarise the agreed brief and confirm it. Use when the user says "grill me", "interview me", "quiz me on this", "help me think this through", or before a large deck, report, model or analysis whose brief is vague.
---

# Briefing interview

The goal is a brief the user recognises as exactly what they want, reached in as few questions as possible. You do the
thinking; the user only makes the choices that are truly theirs.

## Before the first question
1. Read what you already have: the request, the files in the folder, earlier messages, memory. Write down (for
   yourself) what is already settled and what is open.
2. Look up what can be looked up instead of asking: facts in the files, numbers in the data, public facts on the web,
   names and dates in connected apps. Never ask the user something you could find in a minute.
3. Order the open points by how much the answer would change the result. Start with the one that changes it most.
   Typical order: purpose (what decision or action should follow), audience, scope and constraints (deadline, length,
   budget, must-include, must-avoid), success criteria (how the user will judge it), then the key design choices.

## Asking
- Ask ONE question per ask_user call, then wait. Each question gets 2 to 5 concrete options, written as the user
  would say them, with `recommended: true` on the one you would pick. Put the reason for the recommendation in that
  option's description, in one short sentence ("Recommended: a board deck is presented, so fewer words read better").
- Make options real alternatives that lead to different results. Do not offer an option you would not build.
- After each answer, say in one line what it settles, update your picture, and look up anything the answer opens up
  before asking the next question.
- When an answer conflicts with an earlier one or with the facts you found, say so plainly and ask which wins.
- If the user picks "Other" with their own words, take their words as the answer; ask a follow-up only if it is
  still ambiguous.
- If the user says "you decide", "use your judgement" or "enough questions", stop asking, use your recommendations
  for what is still open, and list them as assumptions.

## When to stop
Stop when purpose, audience, constraints, success criteria and every decision that would change the result are
settled. Do not ask about details you can sensibly decide yourself (wording, ordering, small formatting).

## Closing
1. Write the agreed brief in a short block: Purpose, Audience, Scope and constraints, Success criteria, Key decisions,
   Assumptions (what you chose for the user), Sources to use or leave out.
2. Confirm it with one last ask_user call: "Is this the brief?" with the options "Yes, go ahead" (recommended),
   "Change something", "Not yet, keep asking".
3. On "Yes", do the work (or hand it to the right tool, passing the brief on: e.g. for make_deck give audience,
   density, slides and theme as fields). On "Change something", ask what to change and confirm again.
