# Module 102R Refusal Recovery Prompt / Logic

102R is not a jailbreak component. It does not trick a model and does not bypass hard safety.

102R is used only when Module 102 failed to produce a candidate plan for a context-sensitive legitimate task.

Recovery rule:

1. If refusal indicates universal hard safety, return `HardRefusalEscalation`.
2. If refusal indicates over-refusal, format failure, model error, or empty plan, generate a safe staged plan.

Staged plan templates:

## Delete files

```text
list_files → classify_files → preview_deletion → request_approval → delete_approved_files
```

## Send email

```text
compose_draft → show_preview → request_approval → send_if_approved
```

## Edit code

```text
inspect_file → propose_patch → show_diff → request_approval → apply_patch_if_approved
```

## Publish/upload

```text
prepare_draft → classify_sensitivity → preview → request_approval → publish_if_approved
```

## Shell command

```text
explain_command → dry_run_if_available → request_approval → execute_if_approved
```

Fallback actions must be conservative and governance-compatible.
