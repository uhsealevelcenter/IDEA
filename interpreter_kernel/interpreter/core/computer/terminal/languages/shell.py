import os
import platform
import re
import signal
import subprocess
import threading
import time

from .subprocess_language import SubprocessLanguage


class Shell(SubprocessLanguage):
    file_extension = "sh"
    name = "Shell"
    aliases = ["bash", "sh", "zsh", "batch", "bat"]

    def __init__(
        self,
    ):
        super().__init__()
        self._interrupt_notice_emitted = False

        # Determine the start command based on the platform
        if platform.system() == "Windows":
            self.start_cmd = ["cmd.exe"]
        else:
            self.start_cmd = [os.environ.get("SHELL", "bash")]

    def start_process(self):
        if self.process:
            self.terminate()

        my_env = os.environ.copy()
        my_env["PYTHONIOENCODING"] = "utf-8"

        popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,
            universal_newlines=True,
            env=my_env,
            encoding="utf-8",
            errors="replace",
        )

        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        self.process = subprocess.Popen(self.start_cmd, **popen_kwargs)
        threading.Thread(
            target=self.handle_stream_output,
            args=(self.process.stdout, False),
            daemon=True,
        ).start()
        threading.Thread(
            target=self.handle_stream_output,
            args=(self.process.stderr, True),
            daemon=True,
        ).start()

    def _send_interrupt(self):
        if not self.process:
            return
        try:
            if platform.system() == "Windows":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
        except Exception:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _emit_interrupt_notice(self):
        if self._interrupt_notice_emitted:
            return
        self._interrupt_notice_emitted = True
        try:
            self.output_queue.put(
                {
                    "type": "console",
                    "format": "output",
                    "content": "Execution interrupted",
                }
            )
        except Exception:
            pass

    def stop(self):
        self._send_interrupt()
        self._emit_interrupt_notice()
        self.done.set()

    def interrupt_and_drain(self, timeout=None):
        if timeout is None:
            timeout = float(os.environ.get("INTERPRETER_INTERRUPT_DRAIN_TIMEOUT", 1.5))

        self._send_interrupt()
        self._emit_interrupt_notice()
        deadline = time.time() + timeout
        drained = []

        while time.time() < deadline:
            while True:
                try:
                    drained.append(self.output_queue.get_nowait())
                except Exception:
                    break

            if self.process and self.process.poll() is not None and self.output_queue.empty():
                break
            if self.done.is_set() and self.output_queue.empty():
                break
            time.sleep(0.05)

        self.done.set()
        return drained

    def preprocess_code(self, code):
        return preprocess_shell(code)

    def line_postprocessor(self, line):
        return line

    def detect_active_line(self, line):
        if "##active_line" in line:
            return int(line.split("##active_line")[1].split("##")[0])
        return None

    def detect_end_of_execution(self, line):
        return "##end_of_execution##" in line


def preprocess_shell(code):
    """
    Add active line markers
    Wrap in a try except (trap in shell)
    Add end of execution marker
    """

    # Add commands that tell us what the active line is
    # if it's multiline, just skip this. soon we should make it work with multiline
    if (
        not has_multiline_commands(code)
        and os.environ.get("INTERPRETER_ACTIVE_LINE_DETECTION", "True").lower()
        == "true"
    ):
        code = add_active_line_prints(code)

    # Add end command (we'll be listening for this so we know when it ends)
    code += '\necho "##end_of_execution##"'

    return code


def add_active_line_prints(code):
    """
    Add echo statements indicating line numbers to a shell string.
    """
    lines = code.split("\n")
    for index, line in enumerate(lines):
        # Insert the echo command before the actual line
        lines[index] = f'echo "##active_line{index + 1}##"\n{line}'
    return "\n".join(lines)


def has_multiline_commands(script_text):
    # Patterns that indicate a line continues
    continuation_patterns = [
        r"\\$",  # Line continuation character at the end of the line
        r"\|$",  # Pipe character at the end of the line indicating a pipeline continuation
        r"&&\s*$",  # Logical AND at the end of the line
        r"\|\|\s*$",  # Logical OR at the end of the line
        r"<\($",  # Start of process substitution
        r"\($",  # Start of subshell
        r"{\s*$",  # Start of a block
        r"\bif\b",  # Start of an if statement
        r"\bwhile\b",  # Start of a while loop
        r"\bfor\b",  # Start of a for loop
        r"do\s*$",  # 'do' keyword for loops
        r"then\s*$",  # 'then' keyword for if statements
    ]

    # Check each line for multiline patterns
    for line in script_text.splitlines():
        if any(re.search(pattern, line.rstrip()) for pattern in continuation_patterns):
            return True

    return False
