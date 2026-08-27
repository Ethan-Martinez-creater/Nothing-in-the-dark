from enum import StrEnum


class CaseStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    STATUS = "status"
    PROGRESS = "progress"
    ARTIFACT = "artifact"
    WARNING = "warning"
    ERROR = "error"


class ArtifactKind(StrEnum):
    DATASET = "dataset"
    OPINION_ANALYSIS = "opinion_analysis"
    PROPAGATION_GRAPH = "propagation_graph"
    FACT_CHECK = "fact_check"
    REPORT = "report"

