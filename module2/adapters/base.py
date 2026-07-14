"""
The contract every voice-cloning model must implement.

Module 2 exists to compare cloning models (VoxCPM2 today; dots.tts, OpenVoice, … tomorrow) on the
*same* reference voice and the *same* test sentences, then judge which sounds best. The only way
`runner.py` can stay model-agnostic is if every model is reachable through one identical interface —
that interface is `CloningAdapter`.

The three class attributes capture everything that genuinely differs between models (drawn from the
two R&D notebooks): whether the model needs the reference *transcript* (VoxCPM2 doesn't, dots.tts
does), and whether it understands a style/emotion instruction. The runner reads those flags instead
of special-casing any particular model by name.
"""

from abc import ABC, abstractmethod

import numpy as np


class CloningAdapter(ABC):
    # ── Static description of the model (override in each subclass) ────────────
    name: str = ""                       # registry key, e.g. "voxcpm"
    requires_reference_text: bool = False  # True if generate() needs the reference transcript
    supports_style_prompt: bool = False    # True if the model reacts to a style/emotion instruction

    @abstractmethod
    def load(self) -> None:
        """Load weights into memory/VRAM. Heavy and slow — called once before a batch of generate()."""

    def unload(self) -> None:
        """Release weights/VRAM. Safe default is a no-op; GPU adapters should override to free memory."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output sample rate in Hz. Only valid after load()."""

    @abstractmethod
    def generate(
        self,
        text: str,
        reference_wav_path: str,
        reference_text: str | None = None,
        style: str | None = None,
    ) -> np.ndarray:
        """
        Clone the voice in `reference_wav_path` and speak `text`, returning a 1-D float waveform at
        `self.sample_rate`.

        `reference_text` (transcript of the reference clip) and `style` (an emotion/delivery
        instruction) are always passed by the runner when it has them. An adapter whose model does
        not use one simply ignores that argument — the runner never needs to know which is which.
        """
