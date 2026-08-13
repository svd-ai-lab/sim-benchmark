# OpenFOAM detector calibration

`tools/calibrate_detectors.py` checks the OpenFOAM diagnostic detector against
a healthy solver log and controlled broken variants.

| stage | positive fixtures | negative fixtures | true positives | false positives |
|---|---:|---:|---:|---:|
| `L3_convergence` | 2 | 4 | 2 | 0 |
| `L4_conservation` | 1 | 5 | 1 | 0 |

The fixtures cover a missing end marker, excessive final residual and excessive
continuity error. Detector annotations are diagnostic; the public CFD task
score is gated by evaluator-owned reproduction and serialized OpenFOAM mesh and
solution evidence.
