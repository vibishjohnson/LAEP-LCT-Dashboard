"""
Method C1 quarterly refresh candidates (Phase 13A).

Validated VEH0135 source version -> immutable source set -> C1 candidate (all quarters from March 2025)
-> QA -> historical restatement review -> recorded decision.

Uses the approved, unmodified C1 code (experiments/ev_redistribution_c1/c1_method.py, run_c1.py and
integration_writer.py); only the source path and the expanding quarter list are parameters. Writes only
inside the candidate workspace (default <repo>/c1_candidates, git-ignored). Never writes production
files, never promotes, and a decision recorded here is not a release authorisation.
"""
