"""Briefing offline de trechos fornecidos; não extrai áudio ou vídeo."""

from .briefing import (
    Briefing, BriefingError, ConsentRequired, InferenceError, PreparedBriefing,
    Source, build_local, prepare, run_inference,
)

__all__ = ["Briefing", "BriefingError", "ConsentRequired", "InferenceError",
           "PreparedBriefing", "Source", "build_local", "prepare", "run_inference"]
