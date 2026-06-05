def run_text_llm(llm, params):

    # ## Setup (Chat Completions ONLY)
    # if llm.execution_instructions:
    #     try:
    #         # Add the system message
    #         params["messages"][0][
    #             "content"
    #         ] += "\n" + llm.execution_instructions
    #     except:
    #         print('params["messages"][0]', params["messages"][0])
    #         raise

    ## Setup (Responses-style Completions)

    # --- 1) Ensure Responses shape; accept legacy Chat Completions as input ---
    if "instructions" not in params and "messages" in params:
        msgs = params.get("messages", []) or []

        # Pull out system → instructions (if present), keep the rest as input
        sys_txt = ""
        if msgs and msgs[0].get("role") == "system":
            sys_txt = msgs[0].get("content", "")
            msgs = msgs[1:]

        # Normalize each message to Responses-style role/content parts
        try:
            msgs = [llm._ensure_responses_message_shape(m) for m in msgs]
        except Exception:
            # Fallback: best-effort coercion
            norm = []
            for m in msgs:
                role = m.get("role", "user")
                c = m.get("content", "")
                if isinstance(c, str):
                    c = [{"type": "input_text", "text": c}]
                elif isinstance(c, list):
                    # try to keep as-is
                    pass
                else:
                    c = [{"type": "input_text", "text": str(c)}]
                norm.append({"role": role, "content": c})
            msgs = norm

        params["instructions"] = sys_txt
        params["input"] = msgs
        # Remove legacy key so litellm.responses() won't see it
        del params["messages"]

    # Sanity defaults if caller already provided Responses shape
    params.setdefault("instructions", "")
    params.setdefault("input", [])

    # --- 2) Append execution instructions to Responses.instructions ---
    if llm.execution_instructions:
        instr = params.get("instructions", "")
        # If some upstream provided a list of text parts, flatten to string
        if isinstance(instr, list):
            instr = "".join(
                p.get("text", "")
                for p in instr
                if isinstance(p, dict) and p.get("type") in ("input_text", "text")
            )
        # Append and store back as a plain string (what Responses expects)
        params["instructions"] = (instr + "\n" + llm.execution_instructions).strip()

    ## Convert output to LMC format
    inside_code_block = False
    accumulated_block = ""
    language = None

    for chunk in llm.completions(**params):
        if llm.interpreter.verbose:
            print("Chunk in coding_llm", chunk)

        if "choices" not in chunk or not chunk["choices"]:
            # This happens sometimes
            continue

        choice0 = chunk["choices"][0]
        delta = choice0.get("delta")

        # Finish-only chunk from the adapter (no delta present)
        if delta is None:
            finish = choice0.get("finish_reason")
            if finish == "stop":
                break
            if finish == "error":
                raise RuntimeError("Model returned an error finish.")
            # Unknown non-delta chunk; skip
            continue

        content = delta.get("content", "")

        if content == None:
            continue

        accumulated_block += content

        if accumulated_block.endswith("`"):
            # We might be writing "```" one token at a time.
            continue

        # Did we just enter a code block?
        if "```" in accumulated_block and not inside_code_block:
            inside_code_block = True
            accumulated_block = accumulated_block.split("```")[1]

        # Did we just exit a code block?
        if inside_code_block and "```" in accumulated_block:
            return

        # If we're in a code block,
        if inside_code_block:
            # If we don't have a `language`, find it
            if language is None and "\n" in accumulated_block:
                language = accumulated_block.split("\n")[0]

                # Default to python if not specified
                if language == "":
                    if llm.interpreter.os == False:
                        language = "python"
                    elif llm.interpreter.os == True: # Modified from "False" to "True"
                        # OS mode does this frequently. Takes notes with markdown code blocks
                        language = "text"
                else:
                    # Removes hallucinations containing spaces or non letters.
                    language = "".join(char for char in language if char.isalpha())

            # If we do have a `language`, send it out
            if language:
                yield {
                    "type": "code",
                    "format": language,
                    "content": content.replace(language, ""),
                }

        # If we're not in a code block, send the output as a message
        if not inside_code_block:
            yield {"type": "message", "content": content}
