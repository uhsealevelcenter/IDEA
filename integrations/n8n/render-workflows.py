import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Render inactive n8n workflow imports with IDEA's shared prompt.")
    parser.add_argument("output_directory", type=Path)
    output = parser.parse_args().output_directory.resolve()
    templates = Path(__file__).resolve().parent
    if output == templates:
        parser.error("Choose an output directory outside the workflow templates directory.")

    prompt = (templates.parents[1] / "langgraph/utils/system_prompt.md").read_text(encoding="utf-8").strip()
    marker = "{{IDEA_BASE_SYSTEM_PROMPT}}"
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("idea-live-streaming.json", "sea-coordinator.json"):
        workflow = json.loads((templates / filename).read_text(encoding="utf-8"))
        coordinator = next(node for node in workflow["nodes"] if node["name"] == workflow["name"])
        options = coordinator["parameters"]["options"]
        if not options["systemMessage"].endswith(marker):
            raise ValueError(f"{filename} is missing the shared prompt marker.")
        options["systemMessage"] = options["systemMessage"].removesuffix(marker) + prompt
        (output / filename).write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
