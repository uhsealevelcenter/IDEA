"""
This is NOT jupyter language, this is just python. 
Gotta split this out, generalize it, and move all the python additions to python.py, which imports this
"""

import ast
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm
from jupyter_client import KernelManager

from ..base_language import BaseLanguage

DEBUG_MODE = False

# When running from an executable, ipykernel calls itself infinitely
# This is a workaround to detect it and launch it manually
if "ipykernel_launcher" in sys.argv:
    if sys.path[0] == "":
        del sys.path[0]

    from ipykernel import kernelapp as app

    app.launch_new_instance()
    sys.exit(0)


class JupyterLanguage(BaseLanguage):
    file_extension = "py"
    name = "Python"
    aliases = ["py"]

    def __init__(self, computer):
        self.computer = computer

        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel()
        self.kc = self.km.client()
        self.kc.start_channels()
        while not self.kc.is_alive():
            time.sleep(0.1)
        time.sleep(0.5)

        self.listener_thread = None
        self.finish_flag = False
        self._message_queue = None
        self._capture_output_active = False
        self._interrupt_requested = False
        self._interrupt_sent = False
        self._drain_deadline = None
        self._interrupt_notice_emitted = False
        self._pending_interrupt_notice = False
        self._drain_timeout = float(
            os.environ.get("INTERPRETER_INTERRUPT_DRAIN_TIMEOUT", 1.5)
        )
        self._stale_drain_timeout = float(
            os.environ.get("INTERPRETER_STALE_DRAIN_TIMEOUT", 0.2)
        )
        self._drain_lock = threading.Lock()

        # DISABLED because sometimes this bypasses sending it up to us for some reason!
        # Give it our same matplotlib backend
        # backend = matplotlib.get_backend()

        # Use Agg, which bubbles everything up as an image.
        # Not perfect (I want interactive!) but it works.
        backend = "Agg"

        code = f"""
import matplotlib
matplotlib.use('{backend}')
        """.strip()

        # Use Inline actually, it's better I think
        code = """
%matplotlib inline
import matplotlib.pyplot as plt
""".strip()

        for _ in self.run(code):
            pass

        # DISABLED because it doesn't work??
        # Disable color outputs in the terminal, which don't look good in OI and aren't useful
        # code = """
        # from IPython.core.getipython import get_ipython
        # get_ipython().colors = 'NoColor'
        # """
        # self.run(code)

    def terminate(self):
        self.kc.stop_channels()
        self.km.shutdown_kernel()

    def run(self, code):
        self._ensure_previous_listener_stopped()
        self._drain_stale_outputs()
        while not self.kc.is_alive():
            time.sleep(0.1)

        self.last_output_time = time.time()
        self.last_output_message_time = time.time()

        ################################################################
        ### OFFICIAL OPEN INTERPRETER GOVERNMENT ISSUE SKILL LIBRARY ###
        ################################################################

        # try:
        #     functions = string_to_python(code)
        # except:
        #     # Non blocking
        #     functions = {}

        # if self.computer.save_skills and functions:
        #     skill_library_path = self.computer.skills.path

        #     if not os.path.exists(skill_library_path):
        #         os.makedirs(skill_library_path)

        #     for filename, function_code in functions.items():
        #         with open(f"{skill_library_path}/{filename}.py", "w") as file:
        #             file.write(function_code)

        self.finish_flag = False
        self._interrupt_requested = False
        self._interrupt_sent = False
        self._drain_deadline = None
        self._interrupt_notice_emitted = False
        self._pending_interrupt_notice = False
        try:
            try:
                preprocessed_code = self.preprocess_code(code)
            except:
                # Any errors produced here are our fault.
                # Also, for python, you don't need them! It's just for active_line and stuff. Just looks pretty.
                preprocessed_code = code
            message_queue = queue.Queue()
            self._message_queue = message_queue
            if self._pending_interrupt_notice:
                self._emit_interrupt_notice()
            self._execute_code(preprocessed_code, message_queue)
            yield from self._capture_output(message_queue)
        except KeyboardInterrupt:
            # Treat Ctrl+C as an interrupt request, not a traceback-producing error.
            self._request_interrupt()
            self._drain_after_interrupt()
        except GeneratorExit:
            self._request_interrupt()
            self._drain_after_interrupt()
            raise  # gotta pass this up!
        except:
            content = traceback.format_exc()
            yield {"type": "console", "format": "output", "content": content}

    def _execute_code(self, code, message_queue):
        def iopub_message_listener():
            max_retries = 100
            while True:
                if self.finish_flag:
                    if self._interrupt_requested and not self._interrupt_sent:
                        try:
                            self.km.interrupt_kernel()
                        except Exception:
                            pass
                        self._interrupt_sent = True
                    return
                if self._interrupt_requested and not self._interrupt_sent:
                    if DEBUG_MODE:
                        print("interrupting kernel!!!!!")
                    try:
                        self.km.interrupt_kernel()
                    except Exception:
                        pass
                    self._interrupt_sent = True
                # For async usage
                if (
                    hasattr(self.computer.interpreter, "stop_event")
                    and self.computer.interpreter.stop_event.is_set()
                ):
                    self._request_interrupt()
                try:
                    input_patience = int(
                        os.environ.get("INTERPRETER_TERMINAL_INPUT_PATIENCE", 15)
                    )
                    if (
                        time.time() - self.last_output_time > input_patience
                        and time.time() - self.last_output_message_time > input_patience
                    ):
                        self.last_output_message_time = time.time()

                        text = f"{self.computer.interpreter.messages}\n\nThe program above has been running for over 15 seconds. It might require user input. Are there keystrokes that the user should type in, to proceed after the last command?"
                        if time.time() - self.last_output_time > 500:
                            text += f" If you think the process is frozen, or that the user wasn't expect it to run for this long (it has been {time.time() - self.last_output_time} seconds since last output) then say <input>CTRL-C</input>."

                        messages = [
                            {
                                "role": "system",
                                "type": "message",
                                "content": "You are an expert programming assistant. You will help the user determine if they should enter input into the terminal, per the user's requests. If you think the user would want you to type something into stdin, enclose it in <input></input> XML tags, like <input>y</input> to type 'y'.",
                            },
                            {"role": "user", "type": "message", "content": text},
                        ]
                        model = self.computer.interpreter.llm.model
                        params = {
                            "messages": messages,
                            "model": model,
                            "stream": True,
                        }
                        if not model.startswith(("gpt-5", "o4", "o3")):
                            params["temperature"] = 0
                        if self.computer.interpreter.llm.api_key:
                            params["api_key"] = self.computer.interpreter.llm.api_key

                        response = ""
                        for chunk in litellm.completion(**params):
                            content = chunk.choices[0].delta.content
                            if type(content) == str:
                                response += content

                        # Parse the response for input tags
                        input_match = re.search(r"<input>(.*?)</input>", response)
                        if input_match:
                            user_input = input_match.group(1)
                            # Check if the user input is CTRL-C
                            if user_input.upper() == "CTRL-C":
                                self._request_interrupt()
                                if self._drain_deadline is None:
                                    self._drain_deadline = (
                                        time.time() + self._drain_timeout
                                    )
                            else:
                                self.kc.input(user_input)
                                self._request_interrupt()
                                self.finish_flag = True

                    msg = self.kc.iopub_channel.get_msg(timeout=0.05)
                    self.last_output_time = time.time()
                except queue.Empty:
                    continue
                except Exception as e:
                    max_retries -= 1
                    if max_retries < 0:
                        raise
                    print("Jupyter error, retrying:", str(e))
                    continue

                if DEBUG_MODE:
                    print("-----------" * 10)
                    print("Message received:", msg["content"])
                    print("-----------" * 10)

                if (
                    msg["header"]["msg_type"] == "status"
                    and msg["content"]["execution_state"] == "idle"
                ):
                    # Set finish_flag and return when the kernel becomes idle
                    if DEBUG_MODE:
                        print("from thread: kernel is idle")
                    self.finish_flag = True
                    return
                for chunk in self._iopub_msg_to_chunks(msg):
                    message_queue.put(chunk)

        self.listener_thread = threading.Thread(target=iopub_message_listener)
        # self.listener_thread.daemon = True
        self.listener_thread.start()

        if DEBUG_MODE:
            print(
                "thread is on:", self.listener_thread.is_alive(), self.listener_thread
            )

        self.kc.execute(code)

    def detect_active_line(self, line):
        if "##active_line" in line:
            # Split the line by "##active_line" and grab the last element
            last_active_line = line.split("##active_line")[-1]
            # Split the last active line by "##" and grab the first element
            try:
                active_line = int(last_active_line.split("##")[0])
            except:
                active_line = 0
            # Remove all ##active_line{number}##\n
            line = re.sub(r"##active_line\d+##\n", "", line)
            return line, active_line
        return line, None

    def _capture_output(self, message_queue):
        self._capture_output_active = True
        try:
            while True:
                # For async usage
                if (
                    hasattr(self.computer.interpreter, "stop_event")
                    and self.computer.interpreter.stop_event.is_set()
                ):
                    self._request_interrupt()
                    self._emit_interrupt_notice()
                    if self._drain_deadline is None:
                        self._drain_deadline = time.time() + self._drain_timeout

                try:
                    output = message_queue.get(timeout=0.1)
                    if DEBUG_MODE:
                        print(output)
                    yield output
                    continue
                except KeyboardInterrupt:
                    # Interrupt execution but avoid surfacing a traceback to the user.
                    self._request_interrupt()
                    self._emit_interrupt_notice()
                    if self._drain_deadline is None:
                        self._drain_deadline = time.time() + self._drain_timeout
                    continue
                except queue.Empty:
                    pass

                if self.finish_flag:
                    if DEBUG_MODE:
                        print("we're done")
                    break

                if self._drain_deadline and time.time() > self._drain_deadline:
                    self.finish_flag = True
                    if DEBUG_MODE:
                        print("drain timeout reached")
                    break
        finally:
            self._capture_output_active = False
            self._drain_deadline = None

    def stop(self):
        self._request_interrupt()
        if self._drain_deadline is None:
            self._drain_deadline = time.time() + self._drain_timeout

    def interrupt_and_drain(self, timeout=None):
        if timeout is None:
            timeout = self._drain_timeout
        self._request_interrupt()
        if self._capture_output_active:
            if self._drain_deadline is None:
                self._drain_deadline = time.time() + timeout
            return []
        deadline = time.time() + timeout
        drained = []

        if self.listener_thread and self.listener_thread.is_alive() and self._message_queue:
            while time.time() < deadline:
                while True:
                    try:
                        drained.append(self._message_queue.get_nowait())
                    except queue.Empty:
                        break
                if self.finish_flag and self._message_queue.empty():
                    return drained
                time.sleep(0.05)
            self.finish_flag = True
            return drained

        drained.extend(self._drain_iopub_channel(timeout))
        return drained

    def _request_interrupt(self):
        self._interrupt_requested = True
        self._emit_interrupt_notice()

    def _emit_interrupt_notice(self):
        if self._interrupt_notice_emitted:
            return
        self._interrupt_notice_emitted = True
        if self._message_queue is None:
            self._pending_interrupt_notice = True
            return
        self._message_queue.put(
            {
                "type": "console",
                "format": "output",
                "content": "Execution interrupted",
            }
        )

    def _ensure_previous_listener_stopped(self):
        if self.listener_thread and self.listener_thread.is_alive():
            self._request_interrupt()
            self.listener_thread.join(timeout=self._drain_timeout)
            if self.listener_thread.is_alive():
                self.finish_flag = True
                self.listener_thread.join(timeout=0.2)

    def _drain_stale_outputs(self):
        if self._message_queue is not None:
            self._drain_queue(self._message_queue)
        if self.listener_thread and self.listener_thread.is_alive():
            return
        self._drain_iopub_channel(self._stale_drain_timeout)

    def _drain_after_interrupt(self):
        with self._drain_lock:
            if self.listener_thread and self.listener_thread.is_alive():
                self.listener_thread.join(timeout=self._drain_timeout)
                self._drain_queue(self._message_queue)
                return
            self._drain_iopub_channel(self._drain_timeout)

    def _drain_queue(self, q):
        if q is None:
            return
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _drain_iopub_channel(self, timeout):
        drained = []
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                msg = self.kc.iopub_channel.get_msg(timeout=0.05)
            except queue.Empty:
                break
            except Exception:
                break
            if (
                msg.get("header", {}).get("msg_type") == "status"
                and msg.get("content", {}).get("execution_state") == "idle"
            ):
                break
            drained.extend(self._iopub_msg_to_chunks(msg))
        return drained

    def _iopub_msg_to_chunks(self, msg):
        chunks = []
        content = msg.get("content", {})
        msg_type = msg.get("msg_type") or msg.get("header", {}).get("msg_type")

        if msg_type == "stream":
            line, active_line = self.detect_active_line(content.get("text", ""))
            if active_line:
                chunks.append(
                    {
                        "type": "console",
                        "format": "active_line",
                        "content": active_line,
                    }
                )
            chunks.append(
                {"type": "console", "format": "output", "content": line}
            )
        elif msg_type == "error":
            tb = content.get("traceback", [])
            text = "\n".join(tb)
            ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
            text = ansi_escape.sub("", text)
            chunks.append(
                {
                    "type": "console",
                    "format": "output",
                    "content": text,
                }
            )
        elif msg_type in ["display_data", "execute_result"]:
            data = content.get("data", {})
            if "image/png" in data:
                chunks.append(
                    {
                        "type": "image",
                        "format": "base64.png",
                        "content": data["image/png"],
                    }
                )
            elif "image/jpeg" in data:
                chunks.append(
                    {
                        "type": "image",
                        "format": "base64.jpeg",
                        "content": data["image/jpeg"],
                    }
                )
            elif "text/html" in data:
                chunks.append(
                    {
                        "type": "code",
                        "format": "html",
                        "content": data["text/html"],
                    }
                )
            elif "text/plain" in data:
                chunks.append(
                    {
                        "type": "console",
                        "format": "output",
                        "content": data["text/plain"],
                    }
                )
            elif "application/javascript" in data:
                chunks.append(
                    {
                        "type": "code",
                        "format": "javascript",
                        "content": data["application/javascript"],
                    }
                )
        return chunks

    def preprocess_code(self, code):
        return preprocess_python(code)


def preprocess_python(code):
    """
    Add active line markers
    Wrap in a try except
    """

    code = code.strip()

    # Add print commands that tell us what the active line is
    # but don't do this if any line starts with ! or %
    if (
        not any(line.strip().startswith(("!", "%")) for line in code.split("\n"))
        and os.environ.get("INTERPRETER_ACTIVE_LINE_DETECTION", "True").lower()
        == "true"
    ):
        code = add_active_line_prints(code)

    # Wrap in a try except (DISABLED)
    # code = wrap_in_try_except(code)

    # Remove any whitespace lines, as this will break indented blocks
    # (are we sure about this? test this)
    code_lines = code.split("\n")
    code_lines = [c for c in code_lines if c.strip() != ""]
    code = "\n".join(code_lines)

    return code


def add_active_line_prints(code):
    """
    Add print statements indicating line numbers to a python string.
    """
    # Replace newlines and comments with pass statements, so the line numbers are accurate (ast will remove them otherwise)
    code_lines = code.split("\n")
    in_multiline_string = False
    for i in range(len(code_lines)):
        line = code_lines[i]
        if '"""' in line or "'''" in line:
            in_multiline_string = not in_multiline_string
        if not in_multiline_string and (line.strip().startswith("#") or line == ""):
            whitespace = len(line) - len(line.lstrip(" "))
            code_lines[i] = " " * whitespace + "pass"
    processed_code = "\n".join(code_lines)
    try:
        tree = ast.parse(processed_code)
    except:
        # If you can't parse the processed version, try the unprocessed version before giving up
        tree = ast.parse(code)
    transformer = AddLinePrints()
    new_tree = transformer.visit(tree)
    return ast.unparse(new_tree)


class AddLinePrints(ast.NodeTransformer):
    """
    Transformer to insert print statements indicating the line number
    before every executable line in the AST.
    """

    def insert_print_statement(self, line_number):
        """Inserts a print statement for a given line number."""
        return ast.Expr(
            value=ast.Call(
                func=ast.Name(id="print", ctx=ast.Load()),
                args=[ast.Constant(value=f"##active_line{line_number}##")],
                keywords=[],
            )
        )

    def process_body(self, body):
        """Processes a block of statements, adding print calls."""
        new_body = []

        # In case it's not iterable:
        if not isinstance(body, list):
            body = [body]

        for sub_node in body:
            if hasattr(sub_node, "lineno"):
                new_body.append(self.insert_print_statement(sub_node.lineno))
            new_body.append(sub_node)

        return new_body

    def visit(self, node):
        """Overridden visit to transform nodes."""
        new_node = super().visit(node)

        # If node has a body, process it
        if hasattr(new_node, "body"):
            new_node.body = self.process_body(new_node.body)

        # If node has an orelse block (like in for, while, if), process it
        if hasattr(new_node, "orelse") and new_node.orelse:
            new_node.orelse = self.process_body(new_node.orelse)

        # Special case for Try nodes as they have multiple blocks
        if isinstance(new_node, ast.Try):
            for handler in new_node.handlers:
                handler.body = self.process_body(handler.body)
            if new_node.finalbody:
                new_node.finalbody = self.process_body(new_node.finalbody)

        return new_node


def wrap_in_try_except(code):
    # Add import traceback
    code = "import traceback\n" + code

    # Parse the input code into an AST
    parsed_code = ast.parse(code)

    # Wrap the entire code's AST in a single try-except block
    try_except = ast.Try(
        body=parsed_code.body,
        handlers=[
            ast.ExceptHandler(
                type=ast.Name(id="Exception", ctx=ast.Load()),
                name=None,
                body=[
                    ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id="traceback", ctx=ast.Load()),
                                attr="print_exc",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        )
                    ),
                ],
            )
        ],
        orelse=[],
        finalbody=[],
    )

    # Assign the try-except block as the new body
    parsed_code.body = [try_except]

    # Convert the modified AST back to source code
    return ast.unparse(parsed_code)


def string_to_python(code_as_string):
    parsed_code = ast.parse(code_as_string)

    # Initialize containers for different categories
    import_statements = []
    functions = []
    functions_dict = {}

    # Traverse the AST
    for node in ast.walk(parsed_code):
        # Check for import statements
        if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
            for alias in node.names:
                # Handling the alias in import statements
                if alias.asname:
                    import_statements.append(f"import {alias.name} as {alias.asname}")
                else:
                    import_statements.append(f"import {alias.name}")
        # Check for function definitions
        elif isinstance(node, ast.FunctionDef):
            if node.name.startswith("_"):
                # ignore private functions
                continue
            docstring = ast.get_docstring(node)
            body = node.body
            if docstring:
                body = body[1:]

            code_body = ast.unparse(body[0]).replace("\n", "\n    ")

            func_info = {
                "name": node.name,
                "docstring": docstring,
                "body": code_body,
            }
            functions.append(func_info)

    for func in functions:
        # Consolidating import statements and function definition
        function_content = "\n".join(import_statements) + "\n\n"
        function_content += f"def {func['name']}():\n    \"\"\"{func['docstring']}\"\"\"\n    {func['body']}\n"

        # Adding to dictionary
        functions_dict[func["name"]] = function_content

    return functions_dict
