"""
PaperQA Settings Configuration

This module provides a comprehensive Settings object for PaperQA based on
the configuration from image_extraction_compact.ipynb notebook.

The settings use the upgraded PaperQA library's utility classes for full
customization of LLM, parsing, prompts, and agent behavior.
"""

from pathlib import Path
from typing import Optional, Union

from paperqa import Settings
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
    summary_prompt,
)
from paperqa.settings import (
    AgentSettings,
    AnswerSettings,
    IndexSettings,
    MultimodalOptions,
    ParsingSettings,
    PromptSettings,
)

# Try to import pymupdf parser for better image extraction
try:
    from paperqa_pymupdf import parse_pdf_to_pages as pymupdf_parser
    PDF_PARSER = pymupdf_parser
except ImportError:
    PDF_PARSER = None  # Will use default parser

# Default LLM model - prefixed with openai/ for LiteLLM provider routing
DEFAULT_LLM = "openai/gpt-5.2-2025-12-11"

# Custom summary prompt that emphasizes page numbers for figures/tables
CUSTOM_SUMMARY_JSON_SYSTEM = (
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


def create_pqa_settings(
    paper_directory: Union[str, Path],
    index_directory: Union[str, Path],
    llm: str = DEFAULT_LLM,
    summary_llm: Optional[str] = None,
    embedding: str = "text-embedding-3-small",
    verbosity: int = 1,
) -> Settings:
    """
    Create a comprehensive PaperQA Settings object.
    
    This function creates a fully configured Settings object based on the
    image_extraction_compact.ipynb notebook configuration, with customizable
    paths for multi-tenant support.
    
    Parameters:
        paper_directory: Path to the directory containing papers
        index_directory: Path to the directory for storing indexes
        llm: The LLM model to use (default: gpt-5.2-2025-12-11)
        summary_llm: The LLM for summaries (default: same as llm)
        embedding: The embedding model to use (default: text-embedding-3-small)
        verbosity: Logging verbosity level (default: 1)
    
    Returns:
        Settings: A fully configured PaperQA Settings object
    """
    # Convert to Path objects
    paper_directory = Path(paper_directory)
    index_directory = Path(index_directory)
    
    # Use same LLM for summary if not specified
    if summary_llm is None:
        summary_llm = llm
    
    # Build parsing settings based on PaperQA 2026.1.5+ source
    # Valid fields: page_size_limit, use_doc_details, reader_config, multimodal,
    #               citation_prompt, structured_citation_prompt, disable_doc_valid_check,
    #               defer_embedding, parse_pdf, configure_pdf_parser, doc_filters,
    #               use_human_readable_clinical_trials, enrichment_llm, enrichment_llm_config,
    #               enrichment_page_radius, enrichment_prompt
    #
    # MultimodalOptions:
    #   - OFF: No multimodal support
    #   - ON_WITH_ENRICHMENT: Full multimodal with LLM enrichment (images sent to vision LLM for description)
    #   - ON_WITHOUT_ENRICHMENT: Images extracted but not described by LLM
    #
    # Using ON enables the vision LLM to describe figures/images in the answer
    parsing_kwargs = {
        "citation_prompt": citation_prompt,
        "structured_citation_prompt": structured_citation_prompt,
        "multimodal": MultimodalOptions.ON_WITH_ENRICHMENT,  # Enable vision LLM to describe images
        "enrichment_llm": llm,  # Use same LLM for image enrichment (must support vision)
        "reader_config": {
            "chunk_chars": 5000,
            "overlap": 250,
            "full_page": True,
        },
    }
    
    # Add pymupdf parser if available (better image extraction)
    if PDF_PARSER is not None:
        parsing_kwargs["parse_pdf"] = PDF_PARSER
    
    settings = Settings(
        # LLM Configuration
        llm=llm,
        llm_config={
            "model_list": [
                {
                    "model_name": llm,
                    "litellm_params": {
                        "model": llm,
                        "temperature": 1,  # Required for gpt-5+ models
                        "max_tokens": 16384,
                    },
                }
            ],
        },
        summary_llm=summary_llm,
        
        # Embedding Configuration
        embedding=embedding,
        embedding_config={},
        
        # General Settings
        temperature=1,  # Required for gpt-5+ models
        batch_size=1,
        verbosity=verbosity,
        manifest_file=None,
        
        # Directory Configuration
        paper_directory=paper_directory,
        index_directory=index_directory,
        
        # Answer Settings
        answer=AnswerSettings(
            evidence_k=5,
            evidence_retrieval=True,
            evidence_summary_length="about 100 words",
            evidence_skip_summary=False,
            answer_max_sources=5,
            max_answer_attempts=None,
            answer_length="about 200 words, but can be longer",
            max_concurrent_requests=10,
        ),
        
        # Parsing Settings
        parsing=ParsingSettings(**parsing_kwargs),
        
        # Prompt Settings
        prompts=PromptSettings(
            summary=summary_prompt,
            qa=qa_prompt,
            select=select_paper_prompt,
            pre=None,
            post=None,
            system=default_system_prompt,
            use_json=True,
            summary_json=summary_json_prompt,
            summary_json_system=CUSTOM_SUMMARY_JSON_SYSTEM,
            context_outer=CONTEXT_OUTER_PROMPT,
            context_inner=CONTEXT_INNER_PROMPT,
        ),
        
        # Agent Settings
        agent=AgentSettings(
            agent_llm=llm,
            agent_llm_config={
                "model_list": [
                    {
                        "model_name": llm,
                        "litellm_params": {
                            "model": llm,
                        },
                    }
                ],
            },
            agent_prompt=env_reset_prompt,
            agent_system_prompt=env_system_prompt,
            search_count=8,
            index=IndexSettings(
                paper_directory=paper_directory,
                index_directory=index_directory,
                use_absolute_paper_directory=False,
                sync_with_paper_directory=True,
                recurse_subdirectories=False,
            ),
        ),
    )
    
    return settings


# Convenience function to get default settings with just paths
def get_default_settings(
    paper_directory: Union[str, Path],
    index_directory: Union[str, Path],
) -> Settings:
    """
    Get default PaperQA settings with specified paths.
    
    This is a convenience function that creates settings with all defaults
    except for the paper and index directories.
    
    Parameters:
        paper_directory: Path to the directory containing papers
        index_directory: Path to the directory for storing indexes
    
    Returns:
        Settings: A configured PaperQA Settings object
    """
    return create_pqa_settings(
        paper_directory=paper_directory,
        index_directory=index_directory,
    )
