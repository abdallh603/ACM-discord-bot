# Activity UI decisions

The subject is a shared competitive-programming tournament, and the main task is following
the bracket while controlling your own next match. The interface is a match desk inside a
voice room, rather than a general website or promotional landing page.

Tokens: midnight `#121a27` canvas, slate `#172230` panes, steel `#2b3a4d` structural borders,
brass `#efbc79` ready/action emphasis, mint `#9ad4bf` confirmed advancement, and cool
`#99a9bf` secondary text. Bahnschrift/Aptos/Segoe UI headings give compact sports-desk
type; Segoe UI keeps controls familiar. Monospaced numerals are reserved for timers.

Layout: a narrow activity rail; a voice-room header; tournament title and shared selection;
a bracket canvas beside the selected match desk; and a quiet room-participant footer.
The desktop eight-player bracket fits the panel; larger fields scroll within the canvas.
Mobile keeps the page width fixed, lets only the bracket scroll sideways, and stacks the desk.

Review against the brief: the initial wide columns clipped the champion at desktop size,
so column width now follows the available canvas. A generic stats-card row was omitted;
the bracket and actual player state carry the information. The only strong accent is the
ready action; mint marks results rather than decoration. Future slots never invent opponents
or results. Ready checks, hidden problems, server timers, connection status, and explicit
preview labeling describe what is actually happening.

Desktop and mobile screenshots were reviewed, and a headless browser test exercises the real
preview HTTP app. No test claims that Discord hosting, OAuth credentials, URL mapping,
distribution, or a native launch are already configured.
