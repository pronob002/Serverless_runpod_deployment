"""
The fixed generation matrix used to compare cloning models.

Every model is asked to speak the *same* sentences at the *same* style points, so listening to
`module2/voxcpm/door_calm.wav` next to `module2/dots_tts/door_calm.wav` is a fair A/B — the only
variable is the model. Change these and every model compared afterwards uses the new set.

STYLE_VARIATIONS come from the notebook's Section 4 (`EMOTION_VARIATIONS`): the style string is a
free-text delivery instruction. Models that support style prompts (`supports_style_prompt=True`)
use it; models that don't ignore it and just re-render the same text in their cloned voice.
"""

# (sentence_id, text) — sentence_id becomes part of the output filename, so keep it filename-safe.
TEST_SENTENCES = [
    ("door", "She opened the door slowly, wondering what she might find on the other side."),
    ("book", "Chapter one. It was the best of times, it was the worst of times."),
]

# (style_id, style_instruction) — "" means no instruction (neutral clone).
STYLE_VARIATIONS = [
    ("calm", ""),
    ("warm", "(warm, gentle, slightly slower)"),
    ("expressive", "(energetic, expressive, clear emphasis)"),
]
