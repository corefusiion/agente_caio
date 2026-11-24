"""Offline in-memory video-project contract. NOT a security sandbox."""

from dataclasses import dataclass
import hashlib
import json
import re

_MAX_TEXT_BYTES = 32_768
_MAX_PROJECTS = 16
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?\Z", re.ASCII)
_VERSION = "video-project-mock-v1"


@dataclass(frozen=True)
class Proposal:
    name: str
    briefing: str
    revised_plan: str


@dataclass(frozen=True)
class Review:
    """The full data to show an operator, plus the digest to approve."""
    proposal: Proposal
    digest: str
    artifact_names: tuple[str, ...] = ("ANALISE.json", "RELATORIO.json")


def _validate_text(value: str, field: str) -> None:
    if type(value) is not str or not value or len(value) > _MAX_TEXT_BYTES:
        raise ValueError(f"{field} must be nonempty text within the size limit")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    if len(encoded) > _MAX_TEXT_BYTES or "\x00" in value:
        raise ValueError(f"{field} exceeds limits or contains NUL")


def _canonical(proposal: Proposal) -> bytes:
    if type(proposal) is not Proposal:
        raise ValueError("expected Proposal")
    if type(proposal.name) is not str or not _NAME.fullmatch(proposal.name):
        raise ValueError("invalid virtual project name")
    _validate_text(proposal.briefing, "briefing")
    _validate_text(proposal.revised_plan, "revised plan")
    obj = {"version": _VERSION, "name": proposal.name,
           "briefing": proposal.briefing, "revised_plan": proposal.revised_plan,
           "artifact_names": ["ANALISE.json", "RELATORIO.json"]}
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def prepare_review(proposal: Proposal) -> Review:
    """Build a review package; both texts remain data and are never interpreted."""
    return Review(proposal, hashlib.sha256(_canonical(proposal)).hexdigest())


class VirtualTemporaryRoot:
    """Disposable in-memory namespace supplied by the operator; never an OS path.

    Only deterministic simulation, no system isolation or security boundary.
    """
    def __init__(self) -> None:
        self.__projects: dict[str, dict[str, str]] = {}

    def simulate(self, review: Review, approval: str) -> dict[str, object]:
        """Simulate after approval of the *exact* displayed digest. No IO or execution.

        Approval text is a contract, not authenticated authorization. Caller must
        show the full review to a human and enforce authorization out of band.
        """
        if type(review) is not Review:
            raise ValueError("expected Review")
        expected = prepare_review(review.proposal)
        if review != expected:
            raise ValueError("review package was changed")
        if type(approval) is not str or approval != "APROVO SIMULACAO " + expected.digest:
            raise ValueError("explicit approval of this review is required")
        name = expected.proposal.name
        if name in self.__projects:
            raise ValueError("project already exists; no overwrite")
        if len(self.__projects) >= _MAX_PROJECTS:
            raise ValueError("virtual root is full")

        analysis = json.dumps({"aviso": "CONTEUDO NAO CONFIAVEL; NAO EXECUTAR",
                               "briefing_literal": expected.proposal.briefing,
                               "plano_revisado_literal": expected.proposal.revised_plan},
                              ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        # These are actual local checks, not results of a project test suite.
        parsed = json.loads(analysis)
        checks = [
            {"name": "briefing literal preservado", "passed": parsed["briefing_literal"] == expected.proposal.briefing},
            {"name": "plano revisado literal preservado", "passed": parsed["plano_revisado_literal"] == expected.proposal.revised_plan},
            {"name": "artefato JSON valido", "passed": isinstance(parsed, dict)},
        ]
        report = {"mode": "simulacao em memoria; nenhum arquivo real criado",
                  "review_digest_sha256": expected.digest,
                  "activities": ["registrou briefing e plano como dados literais em ANALISE.json virtual",
                                 "executou verificacoes locais de preservacao e formato"],
                  "checks": checks,
                  "project_tests": "nao executados",
                  "video_analysis": "nao realizada",
                  "code_execution": "nao realizada",
                  "network_access": "nao realizado pelo simulador"}
        report_json = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        # Commit only once all validation and generation succeeded.
        self.__projects[name] = {"ANALISE.json": analysis, "RELATORIO.json": report_json}
        return report

    def read_virtual_artifact(self, project: str, artifact: str) -> str:
        """Read a fixed-name, immutable string from this virtual root only."""
        if type(project) is not str or not _NAME.fullmatch(project):
            raise ValueError("invalid virtual project name")
        if type(artifact) is not str or artifact not in ("ANALISE.json", "RELATORIO.json"):
            raise ValueError("invalid virtual artifact")
        return self.__projects[project][artifact]
