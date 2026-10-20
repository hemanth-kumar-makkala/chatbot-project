import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path("c:/Users/makka/Downloads/Projects/chatbots/chatbot-project")
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.models.ollama import OllamaAdapter
from app.ports.types import ChatMessage, ModelRole
from app.core.prompts import AnswerOutput, format_answer_prompt

async def main():
    adapter = OllamaAdapter(model_map={ModelRole.ANSWER: "qwen3-vl:4b", ModelRole.JUDGE: "qwen3-vl:4b"})
    
    evidence = {
        "c1": "[Document: front_desk_policy_v1.pdf, Page: 1] Horizon Valley Health Clinic SOP.\nFront desk intake fax number is 555-0101. Routine appointment arrival window is 15 minutes prior to scheduled visit.",
        "c2": "[Document: front_desk_policy_v2.pdf, Page: 1] Horizon Valley Health Clinic SOP.\nFront desk intake fax number is 555-0102. Routine appointment arrival window is 20 minutes prior to scheduled visit.",
    }
    
    prompt = format_answer_prompt(
        question="HORIZON VALLEY HEALTH CLINIC data please",
        evidence_text=evidence,
        mode="explain",
    )
    
    res = await adapter.generate(
        role=ModelRole.ANSWER,
        messages=[
            ChatMessage(role="user", content=prompt),
            ChatMessage(role="assistant", content="</think>"),
        ],
        schema=AnswerOutput,
        max_tokens=768,
    )
    
    print("Content:", res.content)
    print("Structured:", res.structured_output)

if __name__ == "__main__":
    asyncio.run(main())
