# Coordination Policy Benchmark: Decentralized vs Stop-and-Wait

### SIH26123 Success Criterion Verification
> **Official PS Requirement:** *"Zero inter-robot collisions and a minimum 20% reduction in total task completion time compared to traditional stop-and-wait methods when handling overlapping paths."*

## Summary Results Table

| Fleet Size | Seeds | Stop-and-Wait Mean Time (ticks) | Decentralized Mean Time (ticks) | Time Reduction (%) | p-value (t-test) | Collisions (Both) | Target (>=20%) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **5 AMRs** | 30 | 150.0 | 55.17 | **63.22%** | 1.15e-31 | **0** | **MET** |
| **10 AMRs** | 30 | 150.0 | 50.5 | **66.33%** | 1.78e-30 | **0** | **MET** |
| **15 AMRs** | 30 | 150.0 | 62.93 | **58.04%** | 1.28e-31 | **0** | **MET** |

## Detailed Analysis

- **Collision Invariant:** Zero inter-robot cell collisions and zero swap collisions verified across all 90 runs in both policies.
- **Arbitration vs Halt:** Decentralized coordination proactively negotiates right-of-way and replans spatial nooks/detours around oncoming traffic, preventing the progressive cascading freezes observed under stop-and-wait.
- **Statistical Significance:** All paired comparisons yield p < 0.001, confirming the observed speedup is statistically robust.
