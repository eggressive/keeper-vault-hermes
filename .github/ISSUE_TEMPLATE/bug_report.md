---
name: Bug report
about: Something does not work as expected
title: ""
labels: bug
assignees: ""
---

## What happens

<!-- What you observe. Include the exact warning or error text if there is one. -->

## What you expect

<!-- What should happen instead. -->

## Steps to reproduce

1.
2.
3.

## Environment

| Item | Value |
|------|-------|
| Hermes Agent version or commit | |
| KSM CLI version (`ksm --version`) | |
| Python version | |
| OS | |

## Configuration

<!-- Paste the relevant `secrets.keeper` block from config.yaml. -->

```yaml

```

## Logs

<!-- Redact secrets. Warnings and error kinds only, never resolved values. -->

```

```

## Checklist

- [ ] I have redacted every credential, token and secret value from this report.
- [ ] I ran `pytest tests/ -v` and can say whether it passes locally.
- [ ] I checked that `ksm` is on `PATH`, or that `binary_path` points at a real executable.
