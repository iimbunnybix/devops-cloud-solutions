"""Stop a failed command inside a shell pipeline from passing a CI step.

The deploy job ran ``./run_migrations.sh | tee migrate.log``. A shell
pipeline's exit status is the status of its LAST command, so when the
migration failed and ``tee`` succeeded the step reported 0, CI went green,
and the new release was deployed against a database that had not been
migrated. ``set -e`` alone does not help, because it also sees only that 0.

The fix models bash's pipeline status (default and ``pipefail``) so the
difference is testable, and adds a linter that flags every pipeline in a CI
step script that runs without ``set -o pipefail`` in effect.
"""
from __future__ import annotations

from dataclasses import dataclass

PIPEFAIL_OPTION = "pipefail"
SUCCESS = 0
QUOTE_CHARS = "'\""


def naive_pipeline_status(exit_codes: list[int]) -> int:
    """Bash's default: the pipeline's status is the last command's status."""
    if not exit_codes:
        raise ValueError("a pipeline has at least one command")
    return exit_codes[-1]


def pipeline_status(exit_codes: list[int], pipefail: bool) -> int:
    """Bash's pipeline status, with or without ``set -o pipefail``.

    With pipefail it is the rightmost non-zero status, or 0 if every command
    succeeded (as documented in ``man bash``, section "Pipelines").
    """
    if not exit_codes:
        raise ValueError("a pipeline has at least one command")
    if not pipefail:
        return naive_pipeline_status(exit_codes)
    status = SUCCESS
    for code in exit_codes:
        if code != SUCCESS:
            status = code
    return status


def step_passes(exit_codes: list[int], pipefail: bool) -> bool:
    """Would CI mark a step whose only command is this pipeline green?"""
    return pipeline_status(exit_codes, pipefail) == SUCCESS


@dataclass(frozen=True)
class Finding:
    """A pipeline that can hide a failure, with its 1-based line number."""
    line_number: int
    text: str


def operator_at(line: str, index: int, current: str) -> tuple[str, int]:
    """The unquoted operator starting at ``index`` and its length, or ("", 0)."""
    pair = line[index:index + 2]
    if line[index] == "#" and (not current or current[-1].isspace()):
        return "comment", 0
    if pair in ("&&", "||"):
        return "separator", 2
    if line[index] == ";":
        return "separator", 1
    if pair == "|&":
        return "pipe", 2  # "|&" pipes stderr too; it is still a pipe
    if line[index] == "|":
        return "pipe", 1
    return "", 0


def split_pipelines(line: str) -> list[list[str]]:
    """Split one shell line into pipelines, each a list of stage texts.

    ``;``, ``&&`` and ``||`` separate pipelines; a single ``|`` (or ``|&``)
    separates stages. Quoted or escaped characters and anything after an
    unquoted ``#`` comment are not operators.
    """
    pipelines: list[list[str]] = []
    stages: list[str] = []
    current, quote, index = "", "", 0
    while index < len(line):
        char = line[index]
        # Backslash escapes the next character, except inside single quotes.
        if char == "\\" and quote != "'":
            current += line[index:index + 2]
            index += 2
            continue
        operator, length = ("", 0) if quote else operator_at(line, index, current)
        if operator == "comment":
            break
        if operator == "separator":
            pipelines.append(stages + [current.strip()])
            stages, current = [], ""
        elif operator == "pipe":
            stages.append(current.strip())
            current = ""
        else:
            if char == quote:
                quote = ""
            elif not quote and char in QUOTE_CHARS:
                quote = char
            current += char
            length = 1
        index += length
    pipelines.append(stages + [current.strip()])
    return [pipeline for pipeline in pipelines if any(pipeline)]


def pipefail_setting(command: str) -> bool | None:
    """True/False if ``command`` turns pipefail on/off, None if unrelated."""
    words = command.split()
    if not words or words[0] != "set":
        return None
    setting = None
    for word, next_word in zip(words[1:], words[2:]):
        if next_word != PIPEFAIL_OPTION:
            continue
        # Bash accepts the option letter anywhere in a cluster: -o, -euo, -oeu.
        if word.startswith("-") and "o" in word:
            setting = True
        elif word.startswith("+") and "o" in word:
            setting = False
    return setting


def lint_script(script: str) -> list[Finding]:
    """Flag every pipeline that runs while pipefail is not enabled."""
    findings = []
    pipefail = False
    for line_number, line in enumerate(script.splitlines(), start=1):
        for pipeline in split_pipelines(line):
            if len(pipeline) == 1:
                setting = pipefail_setting(pipeline[0])
                if setting is not None:
                    pipefail = setting
            elif not pipefail:
                findings.append(Finding(line_number, line.strip()))
                break  # one finding per line is enough to fix it
    return findings


def lint_steps(steps: dict[str, str]) -> dict[str, list[Finding]]:
    """Lint each CI step's script separately: each step runs in a new shell."""
    results = {}
    for step_name, script in steps.items():
        findings = lint_script(script)
        if findings:
            results[step_name] = findings
    return results
