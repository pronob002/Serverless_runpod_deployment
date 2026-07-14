"""
Pydantic request/response models for the FastAPI routes in app.py.

Kept in one file, separate from app.py, purely so the OpenAPI schema FastAPI generates from these
(visible at /docs and /redoc) is easy to scan for frontend integration without wading through route
handlers.
"""

from typing import Optional

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Human-readable error message.")


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    module1_active: bool = Field(..., description="Whether a capture analysis is currently running.")
    module2_active: bool = Field(..., description="Whether a cloning run is currently running.")
    cuda_available: bool = Field(..., description="Whether torch reports a usable GPU in this process.")


# ── Module 1 ────────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    status: str = Field(..., examples=["analyzing"])
    session_id: str = Field(..., description="Unique id for this attempt; use it to query Module 2.")
    name: str = Field(..., description="Slugged version of the user-typed recording name.")
    file: str = Field(..., description="Filename the uploaded clip was saved as, under recordings/.")
    auto_clone: bool = Field(..., description="Whether a passing result will auto-start Module 2 cloning.")


class ConfigStep(BaseModel):
    id: str = Field(..., description="Matches the frontend's record_<n> step id.")
    headline: str
    subtext: str
    durationSec: int


class ConfigResponse(BaseModel):
    preRollSec: int = Field(..., description="Countdown before recording starts; excluded from the clip.")
    totalSec: int = Field(..., description="Total expected recorded duration across all steps.")
    steps: list[ConfigStep]


# ── Module 2 ────────────────────────────────────────────────────────────────

class Module2RunRequest(BaseModel):
    session_id: str = Field(..., description="Module 1 session id, as returned by /upload.")
    model: str = Field(..., description="Cloning model key — see GET /module2/models.")


class Module2RunResponse(BaseModel):
    status: str = Field(..., examples=["running"])
    session_id: str
    model: str


class Module2ModelsResponse(BaseModel):
    models: list[str] = Field(..., description="Registry keys accepted by POST /module2/run.")


class Module2SessionInfo(BaseModel):
    session_id: str
    models_done: list[str] = Field(..., description="Models already cloned for this session.")


class Module2SessionsResponse(BaseModel):
    sessions: list[Module2SessionInfo]


class Module2StatusResponse(BaseModel):
    active: bool
    session_id: Optional[str] = Field(None, description="Set only while a cloning run is active.")
    model: Optional[str] = None


class Module2ClipManifestEntry(BaseModel):
    file: str = Field(..., description="Filename under output/<session>/module2/<model>/.")
    sentence_id: str
    style_id: str
    style_applied: bool
    text: str
    duration_sec: float
    gen_sec: float = Field(..., description="Wall-clock time the model took to generate this clip.")
    rtf: Optional[float] = Field(None, description="Real-time factor: gen_sec / duration_sec.")


class Module2ManifestReference(BaseModel):
    calm_tag: str
    expressive_tag: str
    reference_used: str


class Module2Manifest(BaseModel):
    model: str
    generated_at_utc: str
    device: Optional[str] = Field(None, description="'cuda' or 'cpu' — which device generated this run.")
    sample_rate: int
    reference: Module2ManifestReference
    adapter_params: dict = Field(..., description="Model-specific generation params used for this run.")
    clips: list[Module2ClipManifestEntry]


class Module2ReferenceInfo(BaseModel):
    calm_tag: str
    calm_file: str = Field(..., description="Filename of the original reference clip for playback.")


class Module2ResultResponse(BaseModel):
    manifest: Module2Manifest
    reference: Optional[Module2ReferenceInfo] = None
