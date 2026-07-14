"""
VoxCPM2 cloning adapter.

Behaviour is lifted verbatim from `ai-module-pipeline-rnd/voice_cloning/Module2_Voice_Cloning.ipynb`
(Sections 2–4): load `openbmb/VoxCPM2`, then `model.generate(text=..., reference_wav_path=...,
cfg_value=2.0, inference_timesteps=10)`. VoxCPM2 is zero-shot — the reference WAV *is* the profile,
so it needs no transcript — and it takes a style/emotion instruction as a parenthetical prefix on
the text, e.g. "(warm, gentle, slightly slower)She opened the door…".

torch / voxcpm are imported lazily inside load() so that merely importing this module (e.g. to list
available models) never pulls in the ~5GB GPU stack.

"""

import numpy as np

from .base import CloningAdapter


class VoxCPMAdapter(CloningAdapter):
    name = "voxcpm"
    requires_reference_text = False   # zero-shot: reference audio is enough
    supports_style_prompt = True      # style given as a parenthetical prefix on the text

    # Notebook defaults — kept as adapter-level tunables so a comparison run can sweep them.
    CFG_VALUE = 2.0
    INFERENCE_TIMESTEPS = 10
    HF_MODEL_ID = "openbmb/VoxCPM2"

    def __init__(self, cfg_value: float = CFG_VALUE, inference_timesteps: int = INFERENCE_TIMESTEPS):
        self.cfg_value = cfg_value
        self.inference_timesteps = inference_timesteps
        self._model = None
        self.device = None

    def load(self) -> None:
        import torch
        from voxcpm import VoxCPM

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # optimize=False disables torch.compile, which is extremely RAM-intensive and can freeze
        # low-RAM systems while compiling this 2B model (same note as the notebook).
        self._model = VoxCPM.from_pretrained(
            self.HF_MODEL_ID,
            load_denoiser=True,
            optimize=False,
        )

    def unload(self) -> None:
        self._model = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @property
    def sample_rate(self) -> int:
        if self._model is None:
            raise RuntimeError("VoxCPMAdapter.load() must be called before sample_rate is available.")
        return self._model.tts_model.sample_rate

    def generate(
        self,
        text: str,
        reference_wav_path: str,
        reference_text: str | None = None,   # unused: VoxCPM2 is zero-shot
        style: str | None = None,
    ) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("VoxCPMAdapter.load() must be called before generate().")

        # Style is a parenthetical instruction prepended to the text (notebook Section 4).
        full_text = f"{style}{text}" if style else text

        wav = self._model.generate(
            text=full_text,
            reference_wav_path=reference_wav_path,
            cfg_value=self.cfg_value,
            inference_timesteps=self.inference_timesteps,
        )
        return np.asarray(wav)
