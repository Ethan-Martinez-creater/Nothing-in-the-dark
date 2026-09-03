"""V3 Intelligence fixed constants (V3 plan doc §4.1).

执行智能体不得自行重新选择版本号或阈值；算法发生不兼容修改时必须提升
对应版本，不能静默覆盖旧语义。这些版本字符串必须写入：

- InvestigationQualityRecord.algorithm_version  (QUALITY_ALGORITHM_VERSION)
- CrossInvestigationLinkRecord.algorithm_version (CROSS_INTELLIGENCE_VERSION)
- DerivedSignalRecord.detector_version           (ADVANCED_SIGNAL_VERSION)
"""

from __future__ import annotations

V3_INTELLIGENCE_VERSION = "v3.1.0"
QUALITY_ALGORITHM_VERSION = "quality-1.0.0"
WORKSPACE_ENTITY_VERSION = "workspace-entity-1.0.0"
CROSS_INTELLIGENCE_VERSION = "cross-intel-1.0.0"
ADVANCED_SIGNAL_VERSION = "advanced-signal-1.0.0"

# Fixed bounded-output limits (V3 plan doc §4.1).
MAX_ENTITY_ALIASES = 20
MAX_LINK_EVIDENCE_REFS = 50
MAX_ENTITY_RECENT_POSTS = 20
MAX_RELATED_INVESTIGATIONS = 100
MAX_INTELLIGENCE_CONNECTIONS = 200

# Investigation quality grades (V3 plan doc §6).
QUALITY_GRADE_STRONG = "strong"
QUALITY_GRADE_ACCEPTABLE = "acceptable"
QUALITY_GRADE_NEEDS_ATTENTION = "needs_attention"
QUALITY_GRADE_WEAK = "weak"
QUALITY_GRADE_INSUFFICIENT = "insufficient_data"

# Fixed overall_score -> grade mapping thresholds (V3 plan doc §6):
#   >= 85            strong
#   70 - 84.999      acceptable
#   50 - 69.999      needs_attention
#   < 50             weak
#   no computable dimension -> insufficient_data
QUALITY_GRADE_STRONG_THRESHOLD = 85.0
QUALITY_GRADE_ACCEPTABLE_THRESHOLD = 70.0
QUALITY_GRADE_NEEDS_ATTENTION_THRESHOLD = 50.0
