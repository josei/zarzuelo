"""Fuente única del esquema (plan §2, §5). La base, la API y los tipos TS se derivan de aquí."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1"


class Reuse(str, Enum):
    reusable = "reusable"
    consult_only = "consult_only"


class ClaimStatus(str, Enum):
    asserted = "asserted"
    disputed = "disputed"
    attributed = "attributed"
    legendary = "legendary"
    deprecated = "deprecated"


class SourceKind(str, Enum):
    structured = "structured"
    extracted = "extracted"
    derived = "derived"


class NarrativeType(str, Enum):
    anecdota = "anécdota"
    recepcion = "recepción"
    contexto = "contexto_histórico"
    genesis = "génesis"
    leyenda = "leyenda"
    curiosidad = "curiosidad"


class NarrativeStatus(str, Enum):
    documented = "documented"
    reported = "reported"
    legendary = "legendary"


class Document(BaseModel):
    id: str
    source: str
    url: str
    stable_id: str  # p. ej. oldid de Wikipedia
    retrieved_at: str
    sha256: str
    license: str
    reuse: Reuse
    mime: str = "text/plain"
    raw_path: Optional[str] = None


class Passage(BaseModel):
    id: str
    document_id: str
    char_start: int = Field(ge=0)
    char_end: int
    text: str
    sha256: str
    lang: Optional[str] = None
    ocr_conf: Optional[float] = None

    @model_validator(mode="after")
    def _span(self) -> "Passage":
        if self.char_end <= self.char_start:
            raise ValueError("char_end debe ser mayor que char_start")
        return self


class Entity(BaseModel):
    id: str  # QID o ID propio prefijado
    type: Literal["work", "person", "venue", "company", "number", "recording", "place", "other"]
    label: str
    labels_alt: list[str] = []
    external_ids: dict[str, str] = {}
    provisional: bool = False


class Evidence(BaseModel):
    passage_id: str
    quote: str
    quote_start: int = Field(ge=0)
    quote_end: int


class Claim(BaseModel):
    id: str
    subject_id: str
    property: str
    value: Any  # entidad (str "Q..."), literal, fecha EDTF o cantidad
    value_type: Literal["entity", "literal", "edtf", "quantity", "name"] = "literal"
    qualifiers: dict[str, Any] = {}
    status: ClaimStatus = ClaimStatus.asserted
    hedge: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_kind: SourceKind
    extracted_by: dict[str, str] = {}
    evidence: list[Evidence] = []
    created_at: Optional[str] = None


class ClaimRelation(BaseModel):
    claim_a: str
    claim_b: str
    relation: Literal["conflicts_with", "supports", "refines"]


class Narrative(BaseModel):
    id: str
    type: NarrativeType
    title: str
    summary: str
    time_edtf: Optional[str] = None
    place_id: Optional[str] = None
    status: NarrativeStatus
    confidence: float = Field(default=0.0, ge=0, le=1)
    entities: list[tuple[str, str]] = []  # (entity_id, role)
    evidence: list[Evidence] = []


class Concept(BaseModel):
    id: str
    scheme: str = "genres"
    pref_label: str
    alt_labels: list[str] = []
    hidden_labels: list[str] = []
    scope_note: Optional[str] = None
    scope_source: Optional[str] = None  # regla: el vocabulario lleva fuente por concepto
    broader: list[str] = []
    related: list[str] = []


class Segment(BaseModel):
    """Unidad de una salida generada (§6). Sin cita no se publica."""
    id: str
    text: str
    claim_ids: list[str] = []
    narrative_ids: list[str] = []
    passage_ids: list[str] = []

    @property
    def cited(self) -> bool:
        return bool(self.claim_ids or self.narrative_ids or self.passage_ids)


class Output(BaseModel):
    id: str
    kind: Literal["quiz", "card", "timeline", "anecdote", "summary"]
    kb_version: str
    segments: list[Segment] = []
    validation_report: dict[str, Any] = {}


# Propiedades permitidas en extracción (§8.4 tarea 1)
ALLOWED_PROPERTIES = {
    "composer", "librettist", "premiere_date", "premiere_venue", "genre", "denomination",
    "birth_date", "death_date", "birth_place", "death_place", "opening_date", "closing_date",
    "location", "company", "performer", "role", "acts", "based_on", "revival_date", "coordinates",
}
