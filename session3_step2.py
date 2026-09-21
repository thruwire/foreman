"""Step 2: EMA smoothing in policy.decide + config knob."""
src = open("src/foreman/policy.py").read()

if "smoothed" not in src:
    # config knob first
    cfg = open("src/foreman/config.py").read()
    if "score_smoothing_alpha" not in cfg:
        cfg = cfg.replace(
            "    cold_start_grace_seconds: float = Field(default=0.0, ge=0.0)",
            "    cold_start_grace_seconds: float = Field(default=0.0, ge=0.0)\n"
            "    score_smoothing_alpha: float = Field(default=1.0, ge=0.0, le=1.0)\n"
            "    use_smoothed_scores: bool = False",
        )
        cfg = cfg.replace(
            '            "FOREMAN_COLD_START_GRACE_SECONDS": ("cold_start_grace_seconds", float),',
            '            "FOREMAN_COLD_START_GRACE_SECONDS": ("cold_start_grace_seconds", float),\n'
            '            "FOREMAN_SCORE_SMOOTHING_ALPHA": ("score_smoothing_alpha", float),\n'
            '            "FOREMAN_USE_SMOOTHED_SCORES": ("use_smoothed_scores", _environment_bool),',
        )
        open("src/foreman/config.py", "w").write(cfg)
        print("config.py: smoothing knobs added")

    # policy: EMA update + smoothed-threshold evaluation
    src = src.replace(
        """    def decide(self, state: FactoryState, assessment: FactoryAssessment) -> Intervention:
        iteration = max(1, state.iteration)""",
        """    def decide(self, state: FactoryState, assessment: FactoryAssessment) -> Intervention:
        iteration = max(1, state.iteration)

        # EMA score smoothing: per-assessment scores are independent and
        # oscillate across backends (observed hermes: impl 0.75 ↔ 0.45,
        # tests 0.16 ↔ 0.43 alternating while work is stable). Thresholding
        # the smoothed estimate tracks genuine movement instead of noise.
        # alpha=1 disables smoothing (default, upstream behavior).
        smoothing_fields = (
            "implementation_complete", "tests_sufficient", "requirements_satisfied",
            "ready_to_finish", "needs_human", "needs_verification",
            "meaningful_progress", "worker_stuck", "work_off_track",
            "agents_md_drift",
        )
        if self.config.use_smoothed_scores and self.config.score_smoothing_alpha < 1.0:
            alpha = self.config.score_smoothing_alpha
            prev = state.smoothed_scores
            for name in smoothing_fields:
                raw = float(getattr(assessment, name))
                prev_val = prev.get(name)
                smoothed = raw if prev_val is None else alpha * raw + (1 - alpha) * prev_val
                state.smoothed_scores[name] = smoothed
                setattr(assessment, name, smoothed)""",
        1,
    )
    open("src/foreman/policy.py", "w").write(src)
    print("policy.py: EMA smoothing added")
else:
    print("policy.py already has smoothing")