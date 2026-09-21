"""Step 2: the hook method on FactoryRuntime + call it in _run_worker finally."""

src = open("src/foreman/runtime.py").read()

hook_method = '''
    async def _verify_tests_after_worker(self, record: WorkerRecord) -> None:
        """Run the repository's pytest suite after a coding worker completes
        and emit a TEST_RESULT event with the parsed summary.

        Backends with buffered/silent output (hermes on Windows) otherwise
        leave the supervisor without any executed-test evidence, so
        tests_sufficient can never converge. The run is bounded by
        test_command_timeout and failures are non-fatal: the hook is
        evidence-gathering, not a gate.
        """
        if not self.config.verify_tests_on_complete:
            return
        pytest_exe = Path(self.repository) / ".venv" / "Scripts" / "python.exe"
        if not pytest_exe.exists():
            pytest_exe = Path(sys.executable)
        command = [str(pytest_exe), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.repository,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception as error:
            await self._worker_emit(
                record.worker_id,
                EventType.TEST_RESULT,
                {"error": f"test verification launch failed: {error}"},
            )
            return
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self.config.test_command_timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await self._worker_emit(
                record.worker_id,
                EventType.TEST_RESULT,
                {"error": f"test verification timed out after {self.config.test_command_timeout}s"},
            )
            return
        output = stdout.decode("utf-8", errors="replace")
        summary = ""
        passed = failed = 0
        for line in output.splitlines():
            match = re.search(r"(\\d+)\\s+passed(?:,\\s*(\\d+)\\s+failed)?", line)
            if match:
                passed = int(match.group(1))
                failed = int(match.group(2) or 0)
                summary = line.strip()[:200]
                break
        await self._worker_emit(
            record.worker_id,
            EventType.TEST_RESULT,
            {
                "worker_id": record.worker_id,
                "source": "foreman_verification",
                "passed": passed,
                "failed": failed,
                "summary": summary or "no pytest summary line found",
            },
        )

'''

if "_verify_tests_after_worker" not in src:
    # insert the method before start_worker
    src = src.replace(
        "    async def start_worker(self, worker_type: WorkerType) -> WorkerRecord:",
        hook_method + "\n    async def start_worker(self, worker_type: WorkerType) -> WorkerRecord:",
        1,
    )
    # call it in the finally block after the terminal event emits
    src = src.replace(
        """            await self.emit(
                terminal_type,
                {
                    "worker_id": record.worker_id,
                    "worker_type": record.worker_type.value,
                    "status": record.status.value,
                    "exit_code": record.exit_code,
                    "termination_reason": record.termination_reason,
                },
            )""",
        """            await self.emit(
                terminal_type,
                {
                    "worker_id": record.worker_id,
                    "worker_type": record.worker_type.value,
                    "status": record.status.value,
                    "exit_code": record.exit_code,
                    "termination_reason": record.termination_reason,
                },
            )
            if record.worker_type is WorkerType.CODING and record.status is WorkerStatus.COMPLETED:
                await self._verify_tests_after_worker(record)""",
        1,
    )
    open("src/foreman/runtime.py", "w").write(src)
    print("runtime hook added")
else:
    print("hook already present")