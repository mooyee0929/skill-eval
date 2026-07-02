from skill_eval.runner import render_cmd_template


def test_template_with_skill_slot() -> None:
    template = ["claude", "-p", "{prompt}", "--append-system-prompt", "{skill}", "--model", "{model}"]
    cmd = render_cmd_template(
        template, prompt="draw it", model="m1", max_turns=4, skill_text="SKILL RULES"
    )
    assert cmd == ["claude", "-p", "draw it", "--append-system-prompt", "SKILL RULES", "--model", "m1"]


def test_template_drops_skill_flag_for_baseline() -> None:
    template = ["claude", "-p", "{prompt}", "--append-system-prompt", "{skill}"]
    cmd = render_cmd_template(template, prompt="draw it", model="m", max_turns=4, skill_text=None)
    assert cmd == ["claude", "-p", "draw it"]


def test_template_prefixes_prompt_when_no_skill_slot() -> None:
    template = ["gemini", "-p", "{prompt}"]
    cmd = render_cmd_template(template, prompt="draw it", model="m", max_turns=4, skill_text="RULES")
    assert cmd[0] == "gemini"
    assert cmd[2].startswith("RULES") and cmd[2].endswith("draw it")


def test_template_max_turns_placeholder() -> None:
    template = ["cli", "--max-turns", "{max_turns}", "-p", "{prompt}"]
    cmd = render_cmd_template(template, prompt="x", model="m", max_turns=7, skill_text=None)
    assert "7" in cmd
