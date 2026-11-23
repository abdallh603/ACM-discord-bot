# RoundForge tournament artwork

Generated with the built-in image-generation tool for this project. No external brand logos,
university marks, or real people's photographs are included. The originals were copied into
this package; the bot attaches these files to tournament messages. They are included in
Python packages and Docker images.

- `tournament-banner.png`: registration and running bracket panel.
- `tournament-champion.png`: replaces the banner when a champion is confirmed.

## Final banner prompt

Use case: stylized-concept. Asset type: wide Discord embed banner for RoundForge, a competitive programming community bot. Create polished original digital artwork depicting a forged metal tournament trophy shaped subtly like angle brackets, with branching bracket-inspired light paths converging toward it, a dark charcoal arena backdrop, amber forge glow and restrained cool blue highlights. Wide cinematic composition, readable at small Discord embed size, no busy detail, no people, no university logos, no Discord or Codeforces logos, no watermark. Exact text, large and crisp: 'ROUNDFORGE' and below it 'BLITZ TOURNAMENT'. Do not add other text. Opaque background.

## Final champion prompt

Use case: stylized-concept. Asset type: wide Discord tournament champion celebration banner, matching RoundForge competitive programming brand. Original polished digital illustration: a central forged steel and gold trophy with a subtle angle-bracket silhouette, luminous amber laurel wreath framing it, tasteful gold sparks and confetti over a deep charcoal arena with restrained electric blue accents. Wide composition with clean space and bold legible headline. Exact text: 'ROUNDFORGE' above 'BLITZ CHAMPION'. No other text, no people, no external brand logos, no watermark, opaque background. This is a generic winner banner; real champion names will be in the Discord embed outside the image.

## Profile card

Profile cards are rendered dynamically by `profile_card.py` from the member's actual Discord
avatar and the avatar URL returned by the official Codeforces API. These photographs are
not AI generated. If an avatar is absent or unavailable, initials are shown. The illustrative
preview in `docs/profile-card-preview.png` uses placeholder names and initials.
