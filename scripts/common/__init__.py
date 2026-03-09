"""
Paper-Agent common utilities package.

Provides shared modules for all pipeline skills:
- json_extractor: Fault-tolerant JSON extraction from LLM output
- config_loader: profile.yaml and seed_papers.json readers
- path_manager: pipeline_data/{run_id}/ path management (global contract core)
- state_manager: pipeline_state.json state machine read/write
"""

from common.json_extractor import extract_json, extract_json_array, extract_json_object
from common.config_loader import load_profile, load_seed_papers
from common.path_manager import PathManager
from common.state_manager import StateManager, SkillStatus

__all__ = [
    "extract_json",
    "extract_json_array",
    "extract_json_object",
    "load_profile",
    "load_seed_papers",
    "PathManager",
    "StateManager",
    "SkillStatus",
]
