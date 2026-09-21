"""SO CLOSE: smoothed finish converged to exactly 0.28 = the threshold, but
sticky stayed False — meaning no single RAW assessment had finish_ready
(finish raw peaked 0.40 at it5 but tests 0.50/impl 0.51 were fine... wait
it5: finish 0.40 >= 0.28 ✓, tests 0.50 >= 0.30 ✓, req? Unknown — if req <
0.35 at it5, finish_ready was False (all three must pass together). That's
the miss: req never crossed 0.35 in the raw assessments.

The final smoothed state: impl 0.45, tests 0.34, finish 0.28. The job is
genuinely done and Jev's smoothed view is stable and close — but 'sticky'
never armed because finish_ready (all three simultaneously) never fired.

Fix within the calibration: req threshold 0.30 (observed req raw ~0.44 at
it5-6, smoothed likely ~0.40). One more run with req 0.30. If Jev's req
score is similar, sticky arms at a tests+finish peak and FINISH fires on
the next idle assessment."""
import subprocess
print("adjust req threshold to 0.30")