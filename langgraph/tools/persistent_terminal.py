"""
Persistent Terminal Tool
Provides a persistent shell session for executing commands with maintained state.
"""

import time
import pexpect
from langchain_core.tools import tool


class PersistentTerminal:
    """
    Persistent terminal session for an LLM agent.
    
    Commands are sent directly into a live interactive shell (not a spawned
    subshell), so cd / export / source / shell functions persist across calls.
    Each command is bracketed by a unique random sentinel that also carries the
    exit code, which makes output capture and success detection reliable.
    """
    
    def __init__(self, shell: str = '/bin/bash', timeout: int = 1800):
        self.timeout = timeout
        import uuid
        # A fresh, hard-to-collide marker base for this session.
        self._marker = f"__AGENT_{uuid.uuid4().hex}__"
        # spawn with a generous master timeout; per-command timeouts override.
        self.shell = pexpect.spawn(
            shell, encoding='utf-8', timeout=timeout, echo=False
        )
        self.shell.setwinsize(50, 200)  # avoid line-wrapping in captured output
        
        # Turn off echo at the tty level too, and set a stable, unique prompt.
        self._bootstrap()
    
    def _bootstrap(self):
        """Initialize shell with disabled echo and empty prompt."""
        # Wait for initial shell startup
        time.sleep(0.1)
        # Clear any prompt command
        self.shell.sendline('unset PROMPT_COMMAND')
        time.sleep(0.1)
        # Set empty prompt to avoid prompt in output
        self.shell.sendline('export PS1=""')
        time.sleep(0.1)
        # Disable terminal echo
        self.shell.sendline('stty -echo')
        time.sleep(0.2)
        # Drain any pending output
        try:
            self.shell.expect('.+', timeout=1)
        except pexpect.TIMEOUT:
            pass
    
    def run(self, command: str, progress_callback=None) -> tuple[bool, str, float]:
        """
        Execute a command in the persistent shell with progress monitoring.
        
        Args:
            command: Shell command to execute
            progress_callback: Optional function to call with elapsed time updates
        
        Returns:
            (success, output, elapsed_time) tuple
        """
        import re
        
        start_time = time.time()
        last_update = start_time
        
        print(f"⏱️  Command started at {time.strftime('%H:%M:%S')}")
        
        start = f"{self._marker}_START"
        # End sentinel embeds the real exit code of the user's command.
        end = f"{self._marker}_END"
        
        # echo start; run command; echo "END <exit-code>" regardless of success.
        # Note: no `set -e`. The command runs with normal shell semantics.
        wrapped = f'echo {start}; {command}\n__rc=$?; echo "{end} $__rc"'
        
        self.shell.sendline(wrapped)
        
        # Find the start marker first so we don't capture the echoed wrapper.
        try:
            self.shell.expect_exact(start, timeout=self.timeout)
        except (pexpect.TIMEOUT, pexpect.EOF):
            elapsed_time = time.time() - start_time
            return False, "Shell desynchronized before command start.", elapsed_time
        
        # Capture everything up to the END marker + exit code with progress monitoring.
        pattern = re.compile(re.escape(end) + r" (\d+)")
        try:
            # Monitor for progress while waiting
            while True:
                try:
                    self.shell.expect(pattern, timeout=5)
                    # Got the end marker
                    exit_code = int(self.shell.match.group(1))
                    output = self.shell.before
                    
                    # Give shell a moment to settle
                    time.sleep(0.05)
                    
                    elapsed_time = time.time() - start_time
                    success = (exit_code == 0)
                    
                    # Show completion time
                    mins, secs = divmod(int(elapsed_time), 60)
                    if mins > 0:
                        print(f"✅ Completed in {mins}m {secs}s")
                    else:
                        print(f"✅ Completed in {secs}s")
                    
                    return success, self._clean(output), elapsed_time
                    
                except pexpect.TIMEOUT:
                    elapsed = time.time() - start_time
                    
                    # Show progress every 5 seconds
                    if time.time() - last_update >= 5:
                        mins, secs = divmod(int(elapsed), 60)
                        if mins > 0:
                            print(f"⏳ Still running... {mins}m {secs}s elapsed")
                        else:
                            print(f"⏳ Still running... {secs}s elapsed")
                        last_update = time.time()
                        
                        if progress_callback:
                            progress_callback(elapsed)
                    
                    # Check for hard timeout
                    if elapsed > self.timeout:
                        partial = self.shell.before or ""
                        self._interrupt()
                        elapsed_time = time.time() - start_time
                        mins, secs = divmod(int(elapsed_time), 60)
                        return False, f"❌ Command timed out after {mins}m {secs}s\nPartial output:\n{self._clean(partial)}", elapsed_time
                    
                    # Continue waiting
                    continue
                    
        except pexpect.EOF:
            elapsed_time = time.time() - start_time
            return False, "Shell process exited unexpectedly.", elapsed_time
    
    def _interrupt(self):
        """Send Ctrl-C to interrupt the current command."""
        try:
            self.shell.sendcontrol('c')
            time.sleep(0.1)
            # Drain any pending output
            try:
                self.shell.expect('.+', timeout=0.5)
            except pexpect.TIMEOUT:
                pass
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass  # best effort
    
    @staticmethod
    def _clean(text: str) -> str:
        """Strip leading/trailing whitespace from output."""
        return text.strip('\r\n')
    
    def close(self):
        """Close the shell session."""
        try:
            self.shell.sendline('exit')
            self.shell.close(force=True)
        except:
            pass


# Global persistent terminal instance (one per solver instance)
_persistent_terminals = {}


def run_terminal(command: str, session_id: str = 'default') -> str:
    """
    Execute a shell command in a persistent terminal session.
    The shell state (environment variables, working directory, etc.) persists across calls.
    Use this for system commands, file operations, installing packages, etc.
    
    Args:
        command: The shell command to execute (e.g., "ls -la", "pip install numpy")
        session_id: Identifier for the persistent shell session (default: 'default')
        
    Returns:
        The output from running the command (stdout/stderr)
    """
    # Get or create persistent terminal for this session
    if session_id not in _persistent_terminals:
        _persistent_terminals[session_id] = PersistentTerminal()
    
    terminal = _persistent_terminals[session_id]
    success, output, elapsed_time = terminal.run(command)
    
    mins, secs = divmod(int(elapsed_time), 60)
    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    
    if success:
        return f"✓ Command executed successfully in {time_str}.\nOutput:\n{output if output else '(no output)'}"
    else:
        return f"✗ Command failed after {time_str}.\nOutput:\n{output}"


# Wrap for LangChain tool decorator
@tool
def run_terminal_tool(command: str) -> str:
    """
    Execute a shell command in a persistent terminal session.
    The shell state (environment variables, working directory, etc.) persists across calls.
    Use this for system commands, installing packages, running scripts, etc.
    
    Args:
        command: The shell command to execute (e.g., "ls -la", "pip install numpy", "python script.py")
        
    Returns:
        The output from running the command (stdout/stderr)
    """
    return run_terminal(command, session_id='solver_session')


@tool
def write_file_tool(filepath: str, content: str, append: bool = False) -> str:
    """
    Write content to a file. Use this for creating or modifying files.
    Handles newlines, encoding, and file permissions properly.
    
    Args:
        filepath: Path to the file (relative or absolute)
        content: Content to write to the file (newlines are preserved)
        append: If True, append to file; if False (default), overwrite
        
    Returns:
        Success message with file stats, or error message
        
    Examples:
        - write_file_tool("script.py", "def hello():\\n    print('Hello')")
        - write_file_tool("data.txt", "new line\\n", append=True)
    """
    import os
    
    try:
        # Determine mode
        mode = 'a' if append else 'w'
        
        # Create parent directories if they don't exist
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        
        # Write the file
        with open(filepath, mode, encoding='utf-8') as f:
            f.write(content)
        
        # Calculate stats
        lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        chars = len(content)
        action = "Appended" if append else "Wrote"
        
        return f"✓ {action} {chars} characters ({lines} lines) to {filepath}"
        
    except PermissionError:
        return f"✗ Permission denied: Cannot write to {filepath}"
    except IsADirectoryError:
        return f"✗ Error: {filepath} is a directory, not a file"
    except Exception as e:
        return f"✗ Failed to write {filepath}: {str(e)}"
