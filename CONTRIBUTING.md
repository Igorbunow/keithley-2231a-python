# Contributing

[English](CONTRIBUTING.md) | [Русский](docs/CONTRIBUTING.ru.md)

Contributions are welcome for documentation, tests, safe features, and measured hardware behaviour. Be explicit about how a claim is verified.

1. Fork, branch and run `python -m pip install -e .` then `python -m unittest discover -s tests -v`.
2. Prefer focused pull requests. Preserve backward compatibility of `keithley2231a` imports unless an API break is documented.
3. Any new SCPI output/setting command requires bounds checking, defensive readback where available, simulator tests and an entry in `docs/VERIFICATION.md` with **HW UNVERIFIED** until real evidence exists.
4. Any real-equipment result must include a redacted report based on `docs/hardware_reports/TEMPLATE.md`: firmware, code revision, external load/wiring, actual readings and failure behavior. Never publish serial numbers or private network details.
5. No automatic output enable in examples. External Series/Parallel terminal wiring must be stated explicitly when relevant. Do not claim SCPI switches physical contacts.
6. Keep English and Russian documentation consistent, especially status disclaimers and safe-operation warnings.
7. Never include firmware binaries, copyrighted vendor PDF content or source snippets without compatible redistribution rights. Preserve third-party license notices.
8. MIT contributions imply you have the right to submit your contribution under MIT; retain proper attribution for other libraries.

## Pull request checklist

- [ ] Simulator tests passed locally; new tests added for new behavior.
- [ ] Hardware feature status is truthful; no hardware claims from an emulator.
- [ ] Hardware dangers and failure modes are documented.
- [ ] Documentation updated in both languages.
- [ ] No output enables automatically during initial connection/probe.
- [ ] Third-party license and trademark obligations observed.

Report security vulnerabilities privately to project maintainers once a public GitHub security advisory channel is configured. Until then, avoid posting actionable vulnerabilities in public issue titles/details.
