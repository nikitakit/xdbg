import inspect
import sys, os
from IPython.core.interactiveshell import InteractiveShell
from ipykernel.comm.comm import Comm
from .exec_scope import ExecScope
import ast
import types
import importlib

class ReturnRewriter(ast.NodeTransformer):
    def __init__(self, debugger):
        self.debugger = debugger

    def visit_FunctionDef(self, node):
        return node

    def visit_AsyncFunctionDef(self, node):
        return node

    def visit_ClassDef(self, node):
        return node

    def visit_Return(self, node):
        expr_args = self.debugger.get_return_call_ast()
        if expr_args is None:
            return node
        else:
            expr, args = expr_args
            if node.value is not None:
                args.append(node.value)
            return expr

class FrameTracker():
    def __init__(self):
        self.shell = get_ipython()
        self.main_module = self.shell.user_module
        if hasattr(self.shell, '_xdbg_frame_tracker'):
            raise ValueError("Can't create a second FrameTracker, use FrameTracker.get_instance() instead")
        self.shell._xdbg_frame_tracker = self

        # Set up a comm for our current frontend
        # If the frontend is reloaded in the future, register a comm target so
        # the frontend can replace our comm instance
        # TODO: support multiple simultaneous comms
        self.comm = Comm(target_name="xdbg")
        self.shell.kernel.comm_manager.register_target("xdbg", self.change_comm)

        self.frames = []
        self.frames.append({
            'temporary': True,
            'module': self.main_module,
            'frame_name': '__main__'
        })

        # Initialize the return handler
        self.shell.ast_transformers.append(ReturnRewriter(self))

    @staticmethod
    def get_instance():
        shell = get_ipython()
        if not hasattr(shell, '_xdbg_frame_tracker'):
            frame_tracker = FrameTracker()
            assert shell._xdbg_frame_tracker == frame_tracker

        return shell._xdbg_frame_tracker

    def change_comm(self, comm, msg):
        if self.comm is not None and self.comm is not comm:
            self.comm.close()
        self.comm = comm

    def get_return_call_ast(self):
        if not self.frames or self.frames[-1]['temporary']:
            return None
        module = ast.parse('get_ipython()._xdbg_frame_tracker.exit_frame(1)')
        expr = module.body[0]
        args = module.body[0].value.args
        args.pop()
        return expr, args

    def enter_module(self, module):
        if not self.frames or not self.frames[-1]['temporary']:
            return

        if self.frames[-1]['module'] == module:
            return

        frame = self.frames[-1]
        frame['module'] = module
        frame['frame_name'] = module.__name__

        if '_oh' not in module.__dict__:
            module._oh = {}
        self.shell.user_module = module
        self.shell.user_ns = module.__dict__
        self.comm.send(data={'scope': module.__name__})

    def enter_frame(self, module_name, locals_dict, frame_name=None, closure_dict=None):
        if '_oh' not in locals_dict:
            locals_dict['_oh'] = {}

        try:
            module = sys.modules[module_name]
        except KeyError:
            module = self.main_module

        if 'get_ipython' not in module.__dict__:
            module.get_ipython = get_ipython

        frame = {
            'frame_name': frame_name if frame_name is not None else "<unknown>",
            'old_module': self.shell.user_module,
            'old_locals': self.shell.user_ns,
            'module': module,
            'locals': locals_dict,
            'has_returned': False,
            'return_value': None,
            'exec_scope': ExecScope(module.__dict__,
                locals_dict,
                shell=self.shell,
                closure_dict=closure_dict),
            'old_run_ast_nodes': self.shell.run_ast_nodes,
            'temporary': False,
        }

        if frame_name is None:
            try:
                stack = inspect.stack()
                frame_name = '<{}>.{}'.format(module_name, stack[1].function)
                frame['frame_name'] = frame_name
            except:
                pass
        self.comm.send(data={'scope': frame_name})

        self.frames.append(frame)

        self.shell.user_module = frame['module']
        self.shell.user_ns = frame['locals']

        print('[xdbg] Entered:', frame['frame_name'])
        if closure_dict is None and inspect.currentframe().f_back.f_code.co_freevars:
            # There's no reliable way to get closure info at runtime... but,
            # the %break obj syntax that creates proxy objects can get closure
            # info
            print('[xdgb] Warning: nonlocals copied by value')
        self.shell.execution_count += 1 # Needed to keep ID's unique
        self.shell.run_ast_nodes = frame['exec_scope'].shell_substitute_run_ast_nodes

        # Need to continue the main kernel loop without returning from here
        try:
            while not frame['has_returned']:
                self.shell.get_ipython().kernel.do_one_iteration()
            return frame['return_value']
        except:
            raise
        finally:
            print('[xdbg] Exited:', frame['frame_name'])
            self.shell.run_ast_nodes = frame['old_run_ast_nodes']
            self.shell.user_module = frame['old_module']
            self.shell.user_ns = frame['old_locals']
            if self.frames:
                self.comm.send(data={'scope': self.frames[-1]['frame_name']})


    def exit_frame(self, val=None):
        frame = self.frames.pop()
        frame['return_value'] = val
        frame['has_returned'] = True
