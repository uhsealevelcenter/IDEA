import os
import asyncio
import aiohttp
import nest_asyncio
from dotenv import load_dotenv

nest_asyncio.apply()
load_dotenv(".env")

from lmi import CommonLLMNames

# llm_openai = CommonLLMNames.OPENAI_TEST.value
llm_openai = "gpt-4.1-2025-04-14"

import pathlib
# from paperqa_pymupdf import parse_pdf_to_pages as pymupdf_parser

custom_summary_json_system = (
    "Provide a summary of the relevant information"
    " that could help answer the question based on the excerpt."
    " Your summary, combined with many others,"
    " will be given to the model to generate an answer."
    " Respond with the following JSON format:"
    '\n\n{{\n  "summary": "...",\n  "relevance_score": 0-10,\n  "used_images": "..."\n}}'
    "\n\nwhere `summary` is relevant information from the text - {summary_length} words."
    " `relevance_score` is an integer 0-10 for the relevance of `summary` to the question."
    " `used_images` is a boolean flag indicating"
    " if any images present in a multimodal message were used,"
    " and if no images were present it should be false."
    "\n\nThe excerpt may or may not contain relevant information."
    " If not, leave `summary` empty, and make `relevance_score` be 0."
    "\n\n**IMPORTANT: When describing figures, tables, or images,"
    " always include the page number where they appear"
    " (e.g., 'Figure 2 on page 5 shows...'). This is critical for user reference.**"
)

from paperqa.prompts import (
    CONTEXT_INNER_PROMPT,
    CONTEXT_OUTER_PROMPT,
    citation_prompt,
    default_system_prompt,
    env_reset_prompt,
    env_system_prompt,
    qa_prompt,
    select_paper_prompt,
    structured_citation_prompt,
    summary_json_prompt,
    summary_json_multimodal_system_prompt,
    summary_json_system_prompt,
    summary_prompt,
)
from paperqa.settings import (
    AgentSettings,
    AnswerSettings,
    IndexSettings,
    ParsingSettings,
    PromptSettings,
    Settings,
    MultimodalOptions,
)

settings = Settings(
    llm=llm_openai,
    llm_config={
        "model_list": [
            {
                "model_name": llm_openai,
                "litellm_params": {
                    "model": llm_openai,
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            }
        ],
        # "rate_limit": {
        #     llm_openai: "30000 per 1 minute",
        # },
    },
    summary_llm="gpt-4.1-2025-04-14",
    # summary_llm_config={
    #     "rate_limit": {
    #         "gpt-4.1-2025-04-14": "30000 per 1 minute",
    #     },
    # },
    embedding="text-embedding-3-small",
    embedding_config={},
    temperature=0.1,
    batch_size=1,
    verbosity=1,
    manifest_file=None,
    paper_directory=pathlib.Path.cwd().joinpath("papers"),
    index_directory=pathlib.Path.cwd().joinpath("papers/index"),
    answer=AnswerSettings(
        evidence_k=5,
        evidence_detailed_citations=True,
        evidence_retrieval=True,
        evidence_summary_length="about 100 words",
        evidence_skip_summary=False,
        answer_max_sources=5,
        max_answer_attempts=None,
        answer_length="about 200 words, but can be longer",
        max_concurrent_requests=10

    ),
    parsing=ParsingSettings(
        chunk_size=5000,
        overlap=250,
        citation_prompt=citation_prompt,
        structured_citation_prompt=structured_citation_prompt,
        multimodal=MultimodalOptions.ON_WITHOUT_ENRICHMENT,
        # parse_pdf=pymupdf_parser,  # Use pymupdf for better image extraction
        reader_config={
            "chunk_chars": 5000,
            "overlap": 250,
            "full_page": True,  # <-- Add this for pypdf media extraction
        },
    ),
    prompts=PromptSettings(
        summary=summary_prompt,
        qa=qa_prompt,
        select=select_paper_prompt,
        pre=None,
        post=None,
        system=default_system_prompt,
        use_json=True,
        summary_json=summary_json_prompt,
        summary_json_system=custom_summary_json_system,
        context_outer=CONTEXT_OUTER_PROMPT,
        context_inner=CONTEXT_INNER_PROMPT,
    ),
    agent=AgentSettings(
        agent_llm=llm_openai,
        agent_llm_config={
            "model_list": [
                {
                    "model_name": llm_openai,
                    "litellm_params": {
                        "model": llm_openai,
                    },
                }
            ],
            # "rate_limit": {
            #     llm_openai: "30000 per 1 minute",
            # },
        },
        agent_prompt=env_reset_prompt,
        agent_system_prompt=env_system_prompt,
        search_count=8,
        index=IndexSettings(
            paper_directory=pathlib.Path.cwd().joinpath("papers"),
            index_directory=pathlib.Path.cwd().joinpath("papers/index"),
        ),
    ),
)


# async def main():
from paperqa import Docs
from paperqa.agents.search import get_directory_index

# Step 1: Build/reuse the persistent index using agent infrastructure
# This will persist the index to disk (in settings.agent.index.index_directory)
# On subsequent runs, it will reuse the existing index and only add new files
index = await get_directory_index(settings=settings)

# You can check what files are indexed:
print(f"Index name: {index.index_name}")
print(f"Indexed files: {list((await index.index_files).keys())}")

# Step 2: Create a Docs object and add documents from the index
# We load documents fresh here to have full media content
docs = Docs()
paper_directory = settings.agent.index.paper_directory

for file_path in (await index.index_files).keys():
    full_path = paper_directory / file_path
    if full_path.exists():
        print(f"Adding document: {file_path}")
        await docs.aadd(full_path, settings=settings)

# Step 3: Query directly with Docs.aquery() - this returns PQASession WITHOUT auto-filtering
# This means media content is preserved!
session = await docs.aquery(
    query="What does Figure 4 show in the OceanAI paper? If there is text in the figure, please include all of the text in the answer",
    # query="Summarize OceanAI paper",
    settings=settings
)

# Step 4: Extract media from contexts (session.contexts still has media because we didn't use ask())
payload = []
for context in session.contexts:
    payload.append(
        {
            "context_id": context.id,
            "chunk_name": context.text.name,
            "media": [
                {
                    "id": str(media.to_id()),
                    "page": media.info.get("page_num"),
                    "type": media.info.get("type"),
                    "description": media.info.get("enriched_description"),
                    "dataUrl": media.to_image_url(),  # data:image/...;base64,....
                }
                for media in context.text.media
            ],
        }
    )
print("answer: ", session.answer)
print(f"\nExtracted {len(payload)} contexts with media")
if __name__ == "__main__":
    asyncio.run(main())