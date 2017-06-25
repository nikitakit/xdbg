import inspect
import sys, os
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer import StatelessInputTransformer
from IPython.core.splitinput import LineInfo
from IPython.core.magic import (Magics, magics_class, line_magic,
                                cell_magic, line_cell_magic)
from .frame_tracker import FrameTracker
from .breakpoint_hooks import BaseBreakpointTable, add_breakpoint, materialize_breakpoints
import ast
import types
import importlib

def error(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)

class BreakpointTable(BaseBreakpointTable):
    def __init__(self, debugger):
        self.debugger = debugger
        self.counter = 0
        self.b_names = {}
        self.b_enabled = {}
        self.b_temporary = {}
        self.b_ignore_count = {}

    def new_breakpoint(self, func, lineno):
        num = self.counter
        self.counter += 1

        self.b_names[num] = "{}:{}".format(func.__name__, lineno)
        self.b_enabled[num] = True
        self.b_temporary[num] = False
        self.b_ignore_count[num] = 0

        return num

    def remove_breakpoint(self, num):
        del self.b_names[num]
        del self.b_enabled[num]
        del self.b_temporary[num]
        del self.b_ignore_count[num]

    def list_breakpoints(self):
        res = []
        for num in sorted(self.b_names.keys()):
            res.append((num, self.b_names[num], self.b_enabled[num], self.b_temporary[num], self.b_ignore_count[num]))
        return res

    def modify_breakpoints(self, breakpoints, enabled=None, temporary=None, ignore_count=None):
        for num in breakpoints:
            assert num in self.b_names
            if enabled is not None:
                self.b_enabled[num] = enabled
            if temporary is not None:
                self.b_temporary[num] = temporary
            if ignore_count is not None:
                self.b_ignore_count[num] = ignore_count

    def breakpoint_exists(self, num):
        return num in self.b_names

    def __call__(self, num, module_name, locals_dict):
        """
        Called whenever a breakpoint is hit.
        Returns a tuple (do_return, return_value)
        """
        if not self.b_enabled.get(num, False):
            return False, None

        old_ignore_count = self.b_ignore_count[num]
        self.b_ignore_count[num] = max(0, old_ignore_count - 1)
        if old_ignore_count > 0:
            return False, None

        res = self.debugger.frame_tracker.enter_frame(module_name, locals_dict, stack_skip=2)
        if self.b_temporary[num]:
            self.remove_breakpoint(num)

        return True, res

@magics_class
class Debugger(Magics):
    def __init__(self, shell):
        super().__init__(shell)
        # self.shell is set by super

        self.frame_tracker = FrameTracker(self.shell)
        self.breakpoint_table = BreakpointTable(self)

        # Initialize magics
        self.path_mapping = {}
        self.shell.register_magics(self)

    @property
    def main_module(self):
        return self.frame_tracker.main_module

    def print_breakpoints(self, enabled=None):
        breakpoints = self.breakpoint_table.list_breakpoints()
        if not breakpoints:
            print('No breakpoints')
            return

        if enabled is not None:
            breakpoints = [b for b in breakpoints if b[2] == enabled]
            if not breakpoints:
                if enabled:
                    print('No breakpoints are enabled')
                else:
                    print('No breakpoints are disabled')
                return

        print("Breakpoints:")
        for num, b_name, b_enabled, b_temporary, b_ignore_count in breakpoints:
            print('{}\t{}\t'.format(num, b_name),
                  '   ' if b_enabled else 'dis',
                  '(ign {})'.format(b_ignore_count) if b_ignore_count > 0 else '',
                  '(temp)' if b_temporary else '')

    @line_magic
    def makescope(self, name):
        """
        Import a module without running the code inside
        """
        if name in sys.modules:
            return
        spec = importlib.util.find_spec(name)
        sys.modules[name] = importlib.util.module_from_spec(spec)

    @line_magic
    def scope(self, line):
        # TODO(nikita): do I want %scope to accept import specifiers, or names
        # of global variables?

        if line.endswith('.py'):
            return error("Scope accepts module names, not file paths")

        if not line or line == "__main__":
            self.frame_tracker.enter_module(self.main_module)
        elif line in sys.modules:
            self.frame_tracker.enter_module(sys.modules[line])
        else:
            return error("Module not found: {}".format(line))

    @line_magic('break')
    def break_(self, args, temporary=False):
        args = args.split()

        if len(args) == 0:
            self.print_breakpoints()
        elif len(args) > 2:
            return error("Syntax: %break [func [lineno]]")
        else:
            try:
                func = self.frame_tracker.eval(args[0])
            except:
                return error("Not found: {}".format(args[0]))

            if len(args) == 1:
                num = add_breakpoint(self.breakpoint_table, func)
                self.breakpoint_table.modify_breakpoints([num], temporary=temporary)
                print('New breakpoint', num)
            elif len(args) == 2:
                try:
                    lineno = int(args[1])
                except ValueError:
                    lineno = '?'

                if lineno == '?':
                    lines, starting_lineno = inspect.getsourcelines(func)
                    for line, i in zip(lines, range(starting_lineno, starting_lineno + len(lines))):
                        print('{}  '.format(i), line, end='')
                    print()
                else:
                    num = add_breakpoint(self.breakpoint_table, func, lineno)
                    self.breakpoint_table.modify_breakpoints([num], temporary=temporary)
                    print('New breakpoint', num)

    @line_magic
    def tbreak(self, args):
        return self.break_(args, temporary=True)

    def modify_breakpoints(self, args, enabled=None):
        if not args or args == '?':
            self.print_breakpoints(enabled=(None if enabled is None else (not enabled)))
            return

        breakpoints = args.split()
        for i, breakpoint in enumerate(breakpoints):
            try:
                breakpoint = int(breakpoint)
            except ValueError:
                return error("Invalid breakpoint number:", breakpoint)

            if not self.breakpoint_table.breakpoint_exists(breakpoint):
                return error("Invalid breakpoint number:", breakpoint)
            breakpoints[i] = breakpoint

        self.breakpoint_table.modify_breakpoints(breakpoints, enabled=enabled)
        print("Modified:", *breakpoints)

    @line_magic
    def enable(self, args):
        self.modify_breakpoints(args, enabled=True)

    @line_magic
    def disable(self, args):
        self.modify_breakpoints(args, enabled=False)

    @line_magic
    def ignore(self, args):
        args = args.split()
        if not args or len(args) > 2:
            return error("Syntax: %ignore bpnumber [count]")

        if args[0] == '?':
            self.print_breakpoints()
            return

        try:
            args = [int(x) for x in args]
        except ValueError:
            return error("Syntax: %ignore bpnumber [count]")

        if len(args) == 1:
            num = args[0]
            count = 0
        else:
            num, count = args

        if count < 0:
            return error("Count cannot be negative")

        if not self.breakpoint_table.breakpoint_exists(num):
            return error("Invalid breakpoint number:", num)

        self.breakpoint_table.modify_breakpoints([num], ignore_count=count)
