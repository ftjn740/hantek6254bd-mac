# v0.1.0 release candidate

- The Mac front panel includes hardware edge triggering, single-shot and
  continuous display, selectable sample rates and depths, measurements,
  persistence, averaging, CSV export, and raw record replay.
- The USB transport uses PyUSB with board startup and command handling
  based on the BSD-licensed libhantek6xx4 protocol and observed 6254BD
  behavior. See [third-party notices](THIRD_PARTY_NOTICES.md).
- The sample record and the Chinese acceptance report document the earlier
  CH1/CH2 SAMPLE baseline and provide an offline example.

Before a public release, rerun both hardware acceptance scripts with a
confirmed 1 kHz SAMPLE input on CH1 and CH2. During the transport migration,
hardware edge and Single checks passed in individual runs. The full live
suite did not pass with the current input. Both the baseline and the new
transport captured long flat sections and a burst with crossings near
20 kHz on CH1. The baseline snapshot reproduced the trigger tolerance
failure. The earlier hardware report applies to the earlier SAMPLE setup.
