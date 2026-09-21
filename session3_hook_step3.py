"""Insert the test hook: in _run_worker's finally, after the terminal event
emits, if the worker was a COMPLETED coding worker, run the repo's pytest
(bounded) and emit TEST_RESULT. Add config knobs: verify_tests_on_complete
(default False for upstream compat), test_command_timeout."""

src = open("src/foreman/runtime.py").read()

# 1. Config knobs
cfg = open("src/foreman/config.py").read()
if "verify_tests_on_complete" not in cfg:
    cfg = cfg.replace(
        "    use_smoothed_scores: bool = False",
        "    use_smoothed_scores: bool = False\n"
        "    verify_tests_on_complete: bool = False\n"
        "    test_command_timeout: float = Field(default=120.0, gt=0.0)",
    )
    cfg = cfg.replace(
        '            "FOREMAN_USE_SMOOTHED_SCORES": ("use_smoothed_scores", _environment_bool),',
        '            "FOREMAN_USE_SMOOTHED_SCORES": ("use_smoothed_scores", _environment_bool),\n'
        '            "FOREMAN_VERIFY_TESTS_ON_COMPLETE": ("verify_tests_on_complete", _environment_bool),\n'
        '            "FOREMAN_TEST_COMMAND_TIMEOUT": ("test_command_timeout", float),',
    )
    open("src/foreman/config.py", "w").write(cfg)
    print("config knobs added")
else:
    print("config knobs already present")